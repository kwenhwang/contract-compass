import json
import os
import time
from collections import deque
from datetime import datetime
from typing import AsyncIterator
from google import genai
from google.genai import types
from backend.config import BASE_DIR
from .base import LLMProvider


class GeminiQuotaExceeded(RuntimeError):
    """Free-tier 안전 한도 초과 — 호출 측 except Exception에서 자동 fallback."""


# 2026-06-02: 일일 카운터를 디스크에 persist (backend 재시작 시에도 유지)
_QUOTA_STATE_PATH = BASE_DIR / "logs" / "gemini_quota_state.json"


class _QuotaGuard:
    """Sliding-window 한도 가드 — Gemini Free Tier 안전 마진(80%) 안에서만 호출 허용.

    Free Tier (gemini-flash-lite): RPM 15, RPD 500 → 안전 마진 RPM 12, RPD 400.
    환경변수 GEMINI_RPM_LIMIT·GEMINI_RPD_LIMIT으로 조정 가능.

    RPM은 in-memory deque (분 단위 슬라이딩). RPD는 디스크 persist (재시작 무관 일일 누적).
    날짜가 바뀌면 RPD 카운터 자동 리셋.
    """

    def __init__(self) -> None:
        self.rpm_limit = int(os.getenv("GEMINI_RPM_LIMIT", "12"))
        self.rpd_limit = int(os.getenv("GEMINI_RPD_LIMIT", "400"))
        self._minute: deque[float] = deque()
        self.blocked_count = 0
        # 디스크에서 오늘 카운트 로드
        self._rpd_today = 0
        self._date = self._today_str()
        self._load_state()

    @staticmethod
    def _today_str() -> str:
        return datetime.now().strftime("%Y%m%d")

    def _load_state(self) -> None:
        if _QUOTA_STATE_PATH.exists():
            try:
                d = json.loads(_QUOTA_STATE_PATH.read_text(encoding="utf-8"))
                if d.get("date") == self._date:
                    self._rpd_today = int(d.get("rpd", 0))
                    self.blocked_count = int(d.get("blocked", 0))
                    return
            except Exception:
                pass
        # state 파일 없거나 날짜 다르면 → 오늘 usage 로그에서 LLM 호출 카운트 보강
        try:
            usage_log = BASE_DIR / "logs" / f"usage_{self._date}.jsonl"
            if usage_log.exists():
                llm_events = {"step1", "step2", "form", "ask", "ask_stream",
                              "classify_contract_type", "classify_product"}
                n = 0
                for line in usage_log.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        ev = json.loads(line).get("event", "")
                        if ev in llm_events:
                            n += 1
                    except Exception:
                        continue
                self._rpd_today = n
                self._save_state()
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            _QUOTA_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _QUOTA_STATE_PATH.write_text(json.dumps({
                "date": self._date,
                "rpd": self._rpd_today,
                "blocked": self.blocked_count,
            }), encoding="utf-8")
        except Exception:
            pass

    def _rollover_if_new_day(self) -> None:
        today = self._today_str()
        if today != self._date:
            self._date = today
            self._rpd_today = 0
            self.blocked_count = 0
            self._save_state()

    def check_and_record(self) -> None:
        self._rollover_if_new_day()
        now = time.time()
        # 만료된 timestamp 제거
        while self._minute and now - self._minute[0] > 60:
            self._minute.popleft()

        if len(self._minute) >= self.rpm_limit:
            self.blocked_count += 1
            self._save_state()
            raise GeminiQuotaExceeded(
                f"RPM 한도 도달 ({self.rpm_limit}/min) — 잠시 후 다시 시도하세요"
            )
        if self._rpd_today >= self.rpd_limit:
            self.blocked_count += 1
            self._save_state()
            raise GeminiQuotaExceeded(
                f"RPD 한도 도달 ({self.rpd_limit}/일) — 내일 다시 시도하세요"
            )

        self._minute.append(now)
        self._rpd_today += 1
        self._save_state()

    def stats(self) -> dict:
        self._rollover_if_new_day()
        return {
            "rpm_used": len(self._minute),
            "rpm_limit": self.rpm_limit,
            "rpd_used": self._rpd_today,
            "rpd_limit": self.rpd_limit,
            "blocked_count": self.blocked_count,
            "date": self._date,
        }


_GUARD = _QuotaGuard()


def quota_stats() -> dict:
    return _GUARD.stats()


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-3.1-flash-lite"):
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._embed_model = "text-embedding-004"

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        """스트리밍 응답 — 글자 단위 yield. quota 초과 시 GeminiQuotaExceeded raise."""
        _GUARD.check_and_record()
        stream = self._client.models.generate_content_stream(
            model=self._model,
            contents=user,
            config=types.GenerateContentConfig(
                temperature=0.1,
                system_instruction=system,
            ),
        )
        for chunk in stream:
            if chunk.text:
                yield chunk.text

    async def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        _GUARD.check_and_record()
        user_content = user
        if json_mode:
            user_content += "\n\n반드시 유효한 JSON만 출력하세요."
        response = self._client.models.generate_content(
            model=self._model,
            contents=user_content,
            config=types.GenerateContentConfig(
                temperature=0.1,
                system_instruction=system,
            ),
        )
        text = response.text or ""
        # 마크다운 코드블록 제거 (```json ... ``` 형태)
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].rstrip()
        if not text.strip():
            # fail-closed(2026-07-20): 빈 응답이 유효 답변으로 캐시·반환되는 것을 차단
            raise RuntimeError(f"LLM 빈 응답 (model={self._model})")
        return text

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # embed_content는 free tier 토큰 한도 별도 (보통 매우 관대) — guard 통과
        results = []
        for text in texts:
            _GUARD.check_and_record()
            resp = self._client.models.embed_content(
                model=self._embed_model,
                contents=text,
            )
            results.append(resp.embeddings[0].values)
        return results
