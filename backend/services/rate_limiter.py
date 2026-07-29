"""자체 in-memory per-IP rate limiter (외부 의존 0).

LLM 호출 비용·시간 보호 — Cohere rerank·Gemini complete 호출당 3~10s + API 비용.
무제한 호출 시 비용·서버 부하 노출되므로 IP별 sliding window 제한.

한도 (LLM 엔드포인트):
  - 1분 10회 / 1시간 100회 / 1일 500회
특징:
  - sliding window (정확한 윈도우 기준)
  - 캐시 hit는 caller가 record() 안 부르면 카운트 제외
  - 화이트리스트 ENV (RATE_LIMIT_WHITELIST="127.0.0.1,...")
  - FastAPI Dependency, 초과 시 HTTPException(429)
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import HTTPException, Request, status

from backend.config import BASE_DIR

logger = logging.getLogger(__name__)

LIMITS_LLM = {"minute": 10, "hour": 100, "day": 500}
WINDOWS = {"minute": 60, "hour": 3600, "day": 86400}

_WHITELIST = set(
    ip.strip()
    for ip in (os.environ.get("RATE_LIMIT_WHITELIST", "127.0.0.1") or "").split(",")
    if ip.strip()
)


class _IPCounter:
    __slots__ = ("times",)
    def __init__(self) -> None:
        self.times: deque[float] = deque()
    def trim(self, now: float, max_window: float) -> None:
        while self.times and self.times[0] < now - max_window:
            self.times.popleft()
    def count_within(self, now: float, window: float) -> int:
        return sum(1 for t in self.times if t >= now - window)


class RateLimiter:
    def __init__(self) -> None:
        self._ips: dict[str, _IPCounter] = defaultdict(_IPCounter)
        self._lock = Lock()
        self._blocked_count: dict[str, int] = defaultdict(int)

    def _get_raw_ip(self, request: Request) -> str:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def check(self, request: Request, limits: dict[str, int] = LIMITS_LLM) -> str:
        ip = self._get_raw_ip(request)
        if ip in _WHITELIST:
            return ip
        now = time.time()
        max_w = max(WINDOWS.values())
        with self._lock:
            counter = self._ips[ip]
            counter.trim(now, max_w)
            for key, max_calls in limits.items():
                window_s = WINDOWS[key]
                cur = counter.count_within(now, window_s)
                if cur >= max_calls:
                    self._blocked_count[ip] += 1
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail={
                            "error": "rate_limit_exceeded",
                            "window": key, "limit": max_calls, "current": cur,
                            "retry_after": window_s,
                        },
                        headers={"Retry-After": str(window_s)},
                    )
        return ip

    def record(self, ip: str) -> None:
        if ip in _WHITELIST:
            return
        with self._lock:
            self._ips[ip].times.append(time.time())

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            return {
                "tracked_ips": len(self._ips),
                "blocked_total": sum(self._blocked_count.values()),
                "blocked_per_ip": dict(self._blocked_count),
                "active_last_hour": sum(
                    1 for c in self._ips.values()
                    if c.times and c.times[-1] >= now - WINDOWS["hour"]
                ),
            }


def _send_telegram_alert(text: str) -> None:
    """운영자 텔레그램 경보 발신 (외부 의존 0 — 표준 urllib).

    - env `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID` 둘 다 있어야 발송, 없으면 조용히 스킵.
    - 발송 실패(네트워크·4xx 등)는 절대 요청 처리에 전파하지 않음 — 삼키고 warning 1줄.
    - 동기 호출 + 3s 타임아웃: 하루 최대 2회(80%·100%)라 요청 지연 영향 미미.
    """
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        logger.debug("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 미설정 — 비용가드 경보 스킵")
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception:  # noqa: BLE001 — 경보 실패가 본 요청을 죽이면 안 됨
        logger.warning("텔레그램 비용가드 경보 발송 실패 (요청 처리엔 무영향)")


class DailyCallCap:
    """전역 일일 호출 상한(서킷브레이커) — 유료 LLM 비용 폭주 방지.

    IP당 한도(RateLimiter)와 별개로 **모든 LLM 경로 합산** 일일 상한을 강제한다.
    - 날짜별 영속 카운터(파일): 프로세스 재시작에도 유지 (UTC 날짜 기준, 날짜 바뀌면 자동 리셋).
    - 상한 초과 시 과금 유발 대신 우아하게 429 반환(앱 크래시 금지).
    - 카운터 조회 실패(파일 손상·권한 등)가 정상 요청을 막지 않도록 방어(fail-open on read).
    - 화이트리스트 무관하게 전역 예산으로 카운트(비용은 IP를 가리지 않음).
    - 운영자 경보(2026-07-18): 캡의 80%·100%를 처음 넘는 순간 텔레그램 1회씩 발송.
      발송 여부는 카운터 파일 `alerted` 필드에 마킹 — 같은 날 중복 발송 없음, 날짜 리셋과 함께 리셋.

    주의(멀티워커): 파일 잠금은 프로세스 내 Lock만 — 다중 uvicorn 워커 동시 증가 시 미세한
    경쟁으로 실제 카운트가 상한을 소폭 초과할 수 있음(soft cap). 비용 폭주 방지 목적엔 충분.
    """

    #: 텔레그램 경보 임계(캡 대비 %). 각 임계는 하루 1회만 발송.
    ALERT_THRESHOLDS_PCT: tuple[int, ...] = (80, 100)

    def __init__(self, cap: int, path: str) -> None:
        self._cap = cap
        self._path = Path(path)
        self._lock = Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _read(self) -> tuple[str, int]:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return str(data.get("date", "")), int(data.get("count", 0))

    def _read_alerted(self) -> list[int]:
        """오늘자 파일에 기록된 발송 완료 임계(%) 목록. 실패·과거 날짜 → []."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if str(data.get("date", "")) != self._today():
                return []
            return [int(x) for x in data.get("alerted", [])]
        except Exception:  # noqa: BLE001 — 손상/없음은 미발송으로 간주
            return []

    def _write(self, date: str, count: int, alerted: list[int] | None = None) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict = {"date": date, "count": count}
            if alerted:
                payload["alerted"] = sorted(alerted)
            self._path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:  # noqa: BLE001 — 영속 실패가 요청을 막지 않음
            pass

    def _notify(self, pct: int, count: int, date: str) -> None:
        """임계 도달 텔레그램 경보 — 어떤 예외도 record() 경로로 전파 금지."""
        icon = "🚨" if pct >= 100 else "⚠️"
        text = (
            f"{icon} 계약나침반 LLM 비용가드: 오늘 OpenAI 호출 "
            f"{count}/{self._cap} ({pct}%) 도달 — {date}"
        )
        try:
            _send_telegram_alert(text)
        except Exception:  # noqa: BLE001 — 경보는 부수 기능, 본 요청 보호가 우선
            logger.warning("비용가드 경보 처리 중 예외 (요청 처리엔 무영향)")

    def current(self) -> int:
        """오늘(UTC) 누적 호출 수. 파일 없음/손상/날짜 불일치 → 0."""
        try:
            date, count = self._read()
        except Exception:  # noqa: BLE001 — 조회 실패는 0으로 간주(요청 허용)
            return 0
        return count if date == self._today() else 0

    def check(self) -> None:
        """상한 초과 시 429. cap<=0이면 비활성(무제한). 조회 실패는 통과(fail-open)."""
        if self._cap <= 0:
            return
        try:
            cur = self.current()
        except Exception:  # noqa: BLE001 — 방어: 카운터 조회 실패가 정상요청을 막지 않음
            return
        if cur >= self._cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "daily_cap_exceeded",
                    "message": "일시적으로 이용이 많습니다. 잠시 후 다시 시도해 주세요.",
                    "limit": self._cap,
                },
                headers={"Retry-After": "3600"},
            )

    def record(self) -> int:
        """실제 LLM 호출 1건 반영(원자적 read-modify-write). 날짜 넘어가면 리셋 후 1.

        임계(80%·100%)를 처음 넘는 호출이면 발송 마킹 후 텔레그램 경보 1회 발송.
        마킹을 먼저 영속(락 안)하고 발송은 락 밖에서 — 중복 발송 방지 + 락 점유 최소화.
        """
        pending_alerts: list[int] = []
        with self._lock:
            date, count = self._today(), 0
            alerted: list[int] = []
            try:
                pdate, pcount = self._read()
                if pdate == date:
                    count = pcount
                    alerted = self._read_alerted()
            except Exception:  # noqa: BLE001 — 파일 없음/손상 시 오늘 0부터
                pass
            count += 1
            if self._cap > 0:
                for pct in self.ALERT_THRESHOLDS_PCT:
                    if pct not in alerted and count * 100 >= self._cap * pct:
                        alerted.append(pct)
                        pending_alerts.append(pct)
            self._write(date, count, alerted)
        for pct in pending_alerts:
            self._notify(pct, count, date)
        return count


_limiter: RateLimiter | None = None
_daily_cap: DailyCallCap | None = None

# 기본 상한·카운터 경로 (env로 조정). 카운터는 data/ 아래 날짜별 JSON.
_DEFAULT_CAP_FILE = str(BASE_DIR / "data" / "openai_daily_cap.json")


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def get_daily_cap() -> DailyCallCap:
    global _daily_cap
    if _daily_cap is None:
        cap, path = 500, _DEFAULT_CAP_FILE
        # 우선순위: 명시 env(테스트·운영 오버라이드) → 앱 설정(.env, pydantic) → 기본값.
        env_cap = os.environ.get("OPENAI_DAILY_CALL_CAP")
        env_path = os.environ.get("OPENAI_DAILY_CAP_FILE")
        if env_cap is not None or env_path is not None:
            try:
                cap = int(env_cap) if env_cap is not None else cap
            except (TypeError, ValueError):
                pass
            path = env_path or path
        else:
            try:
                from backend.config import get_settings
                s = get_settings()
                cap = s.openai_daily_call_cap
                path = s.openai_daily_cap_file or path
            except Exception:  # noqa: BLE001 — 설정 로드 실패 시 안전 기본값
                pass
        _daily_cap = DailyCallCap(cap, path)
    return _daily_cap


def rate_limit_llm(request: Request) -> str:
    """FastAPI Dependency — 모든 LLM 경로 공용.

    IP당 한도(sliding window) + 전역 일일 상한(서킷브레이커) 둘 다 검사.
    통과 시 raw IP 반환 — 실제 LLM 호출(캐시 미스) 시점에 caller가 record_llm_call(ip) 호출.
    """
    ip = get_rate_limiter().check(request, LIMITS_LLM)
    get_daily_cap().check()
    return ip


def record_llm_call(ip: str) -> None:
    """실제 LLM 호출 1건 기록 — IP별 카운터 + 전역 일일 카운터 동시 반영.

    캐시 히트는 caller가 이 함수를 부르지 않으므로 카운트 제외(과금 없는 호출은 예산 미차감).
    """
    get_rate_limiter().record(ip)
    get_daily_cap().record()
