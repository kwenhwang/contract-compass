"""사용 로그 서비스 — 일별 회전 파일에 기록 (logs/usage_YYYYMMDD.jsonl)."""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.config import BASE_DIR

_LOG_DIR = BASE_DIR / "logs"
_LEGACY_PATH = _LOG_DIR / "usage.jsonl"

logger = logging.getLogger(__name__)


def _mask_ip(ip: str | None) -> str | None:
    """개인정보 보호용 IP 마스킹 — IPv4 마지막 옥텟·IPv6 뒤쪽 64bit 제거.
    네트워크 대역 단위 식별은 가능하나 단말 식별은 불가.
    """
    if not ip:
        return None
    ip = ip.strip()
    if "." in ip:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
    if ":" in ip:
        groups = ip.split(":")[:4]
        return ":".join(groups) + "::"
    return ip


def extract_client_meta(request) -> tuple[str | None, str | None]:
    """FastAPI Request → (마스킹된 IP, user-agent 200자). nginx 프록시는 X-Forwarded-For 우선."""
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            raw_ip = xff.split(",")[0].strip()
        elif request.client:
            raw_ip = request.client.host
        else:
            raw_ip = None
        ua = (request.headers.get("user-agent") or "")[:200] or None
        return _mask_ip(raw_ip), ua
    except Exception:
        return None, None


def extract_device_id(request) -> str | None:
    """프론트가 localStorage에 영구 저장해 보내는 익명 기기 ID(X-Device-Id, 2026-07-21).

    NAT 공유 IP 뒤에서 IP·UA만으론 구분 안 되는 접속자를 브라우저 단위로 센다.
    구버전 클라이언트(헤더 미전송)는 None — 집계 시 IP 기준 추정과 병행 참고할 것.
    """
    try:
        return (request.headers.get("x-device-id") or "")[:80] or None
    except Exception:
        return None



_ROTATE_MAX_BYTES = 50 * 1024 * 1024


def rotate_if_oversize(path: Path, max_bytes: int = _ROTATE_MAX_BYTES) -> None:
    """append 직전 호출 — 파일이 max_bytes 초과면 <이름>.YYYYMMDD.jsonl로 rename 후 새 파일 시작.

    append-only 라이브 로그(pilot_applies·usage_events)의 무한성장 가드(2026-07-21).
    데이터는 rename으로 전부 보존, 같은 날 재회전 시 .YYYYMMDD.N.jsonl.
    어떤 예외도 삼킨다 — 회전 실패가 로깅·서비스를 막으면 안 됨(기존 append 그대로 진행).
    """
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        target = path.with_name(f"{path.stem}.{stamp}{path.suffix}")
        n = 1
        while target.exists():
            target = path.with_name(f"{path.stem}.{stamp}.{n}{path.suffix}")
            n += 1
        path.rename(target)
    except Exception:  # noqa: BLE001 — fail-safe
        pass


def _today_path() -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _LOG_DIR / f"usage_{date_str}.jsonl"


def iter_all_log_records():
    """레거시 usage.jsonl + 일별 회전 파일 전체를 순회."""
    paths = sorted(_LOG_DIR.glob("usage_*.jsonl")) + ([_LEGACY_PATH] if _LEGACY_PATH.exists() else [])
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except Exception:
                        pass


class UsageLogger:
    def __init__(self):
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._llm_calls = 0
        self._llm_failures = 0

    def _write(self, record: dict) -> None:
        try:
            with open(_today_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def log_step1(
        self,
        *,
        session_id: str,
        contract_type: str,
        estimated_price: int,
        service_type: str | None,
        is_sme: bool,
        top_rule_id: str,
        pass_score,
        lower_limit_rate: str | None,
        duration_ms: int,
        client_ip: str | None = None,
        user_agent: str | None = None,
        decision_channel: str = "rule",
        llm_fallback_reason: str | None = None,
    ) -> None:
        # decision_channel: 최종 계약방법 결정 채널. step1/step2의 method는 항상 룰 lookup이므로
        # 기본 "rule". LLM 보조설명이 실패(fallback)했으면 "llm_fallback"으로 정직 기록.
        self._write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "step1",
            "session_id": session_id,
            "contract_type": contract_type,
            "estimated_price": estimated_price,
            "service_type": service_type,
            "is_sme": is_sme,
            "rule_id": top_rule_id,
            "pass_score": pass_score,
            "lower_limit_rate": lower_limit_rate,
            "duration_ms": duration_ms,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "decision_channel": decision_channel,
            "llm_fallback_reason": llm_fallback_reason,
        })

    def log_step2(
        self,
        *,
        session_id: str,
        rule_id: str,
        method: str,
        confidence: float,
        duration_ms: int,
        client_ip: str | None = None,
        user_agent: str | None = None,
        decision_channel: str = "rule",
        llm_fallback_reason: str | None = None,
    ) -> None:
        # 주의: 'method'는 계약방법 문자열(예: 일반경쟁입찰)이지 결정 채널이 아님.
        # 룰/LLM 채널 관측은 'decision_channel' 필드를 사용 (admin 집계와 충돌 방지).
        self._write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "step2",
            "session_id": session_id,
            "rule_id": rule_id,
            "method": method,
            "confidence": confidence,
            "duration_ms": duration_ms,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "decision_channel": decision_channel,
            "llm_fallback_reason": llm_fallback_reason,
        })

    def log_classify(
        self,
        *,
        description_len: int,
        suggested: str | None,
        decision_channel: str,
        confidence: float | None = None,
        duration_ms: int = 0,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """계약유형 분류 결정 채널 기록 (Q2 한계 해소 — 실서비스 룰/LLM 비율 관측).

        decision_channel: "keyword"(결정론 키워드 사전) | "llm"(모호 케이스 LLM 보조)
                          | "llm_fallback"(LLM 실패 → 키워드 1위).
        """
        self._write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "classify_type",
            "description_len": description_len,
            "suggested": suggested,
            "decision_channel": decision_channel,
            "confidence": confidence,
            "duration_ms": duration_ms,
            "client_ip": client_ip,
            "user_agent": user_agent,
        })

    def log_llm_failure(self, *, event: str, error: str) -> None:
        """LLM 실패 기록. 실패율이 일정 이상이면 경고 로그 출력."""
        self._llm_calls += 1
        self._llm_failures += 1
        self._write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "llm_failure",
            "step": event,
            "error": str(error)[:200],
        })
        # 10회 중 5회 이상 실패 시 경고
        if self._llm_calls >= 10 and self._llm_failures / self._llm_calls >= 0.5:
            logger.warning(
                "LLM 실패율 높음: %d/%d (%.0f%%) — API 키·한도·네트워크 점검 필요",
                self._llm_failures, self._llm_calls,
                self._llm_failures / self._llm_calls * 100,
            )

    def record_llm_success(self) -> None:
        self._llm_calls += 1

    def get_llm_failure_rate(self) -> dict:
        if self._llm_calls == 0:
            return {"calls": 0, "failures": 0, "rate": 0.0}
        return {
            "calls": self._llm_calls,
            "failures": self._llm_failures,
            "rate": round(self._llm_failures / self._llm_calls, 3),
        }
