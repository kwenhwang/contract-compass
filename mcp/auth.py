"""contract-mcp 키 인증·티어·일일 쿼터 (2026-07-30 — realty-mcp auth.py 이식).

구조: 키 없는 호출 = free 티어(IP당 일한도) / `cc_live_*` 키 = paid 티어(키당 일한도).
키 저장은 data/mcp_keys.json — sha256 해시만 저장, 평문은 발급(issue_key.py) 1회만 노출.
SDK 내장 auth층은 OAuth 전제로 전송층 401을 강제해 "키 없는 무료 티어"와 양립하지
않는다 — 그래서 미들웨어(server.py QuotaGate)에서 tools/call 단위로 게이트한다.

IP 신뢰 순서(cf-connecting-ip > x-real-ip > xff 첫 항목)는 backend/services/
chat_access.py에서 실측 검증된 순서를 따른다(Cloudflare 경유 전제).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[1]
KEYS_PATH = Path(os.environ.get("CONTRACT_MCP_KEYS_FILE", str(_ROOT / "data" / "mcp_keys.json")))
FREE_DAILY = int(os.environ.get("CONTRACT_MCP_FREE_DAILY", "50"))
PAID_DAILY_DEFAULT = int(os.environ.get("CONTRACT_MCP_PAID_DAILY", "2000"))

# 구매·갱신 안내의 단일 진실원 — 한도·키 거부 메시지가 이 주소를 가리킨다.
PRICING_URL = os.environ.get("CONTRACT_MCP_PRICING_URL", "https://contract.sallim.app/mcp/pricing")

# 루프백 = 운영자 로컬·야간 QA·codexw 하네스. 무료 쿼터를 태우면 회귀가 스스로
# 막히므로 무제한. 외부 트래픽은 전부 nginx 경유라 x-real-ip가 실IP로 덮인다.
UNLIMITED_IPS = {"127.0.0.1", "::1"}


@dataclass
class Access:
    tier: str                    # "free" | "paid" | "local"
    subject: str                 # 쿼터 키: free=IP, paid=key_prefix
    daily_limit: Optional[int]   # None = 무제한
    error: Optional[dict] = None  # 키가 제시됐으나 무효·만료 — 구조화 거부 응답


# ---------------------------------------------------------------- 키 저장소

_keys_cache: dict[str, dict] = {}
_keys_mtime: float = -1.0
_keys_lock = threading.Lock()


def _load_keys() -> dict[str, dict]:
    """mcp_keys.json을 mtime 캐시로 읽는다. 손상 시 마지막 정상 캐시 유지 —
    파일 한 줄 깨졌다고 유료 사용자 전체를 잠그면 안 된다."""
    global _keys_cache, _keys_mtime
    try:
        mtime = KEYS_PATH.stat().st_mtime
    except OSError:
        return {}
    with _keys_lock:
        if mtime != _keys_mtime:
            try:
                data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
                _keys_cache = {r["key_hash"]: r for r in data.get("keys", []) if r.get("key_hash")}
                _keys_mtime = mtime
            except Exception:
                pass
    return _keys_cache


# ---------------------------------------------------------------- 요청 판정

def client_ip(request) -> str:
    for h in ("cf-connecting-ip", "x-real-ip"):
        v = request.headers.get(h)
        if v:
            return v.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def _extract_key(request) -> Optional[str]:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    # ChatGPT 커넥터는 커스텀 헤더를 못 붙인다 — ?key= 쿼리 폴백
    try:
        return request.query_params.get("key")
    except Exception:
        return None


def resolve_access(request) -> Access:
    """요청 → 티어 판정. request=None은 stdio 로컬 경로(HTTP 요청 자체가 없음)."""
    if request is None:
        return Access(tier="local", subject="stdio", daily_limit=None)

    key = _extract_key(request)
    if key:
        rec = _load_keys().get(hashlib.sha256(key.encode()).hexdigest())
        if rec is None or not rec.get("is_active", False):
            return Access("free", "invalid", None, error={
                "error": "invalid_key",
                "message": "API 키가 유효하지 않습니다(회수됐거나 오타). "
                           f"키를 빼면 무료 티어로 계속 쓸 수 있습니다. 구매·재발급: {PRICING_URL}",
            })
        expires = rec.get("expires_at")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp_dt:
                    return Access("free", rec.get("key_prefix", "?"), None, error={
                        "error": "key_expired",
                        "message": f"API 키가 {str(expires)[:10]}에 만료됐습니다. 갱신: {PRICING_URL}",
                    })
            except ValueError:
                pass  # 만료일이 못 읽히면 키를 잠그지 않는다 — 발급 CLI가 포맷을 보장
        return Access("paid", rec.get("key_prefix", "paid"),
                      int(rec.get("daily_limit") or PAID_DAILY_DEFAULT))

    ip = client_ip(request)
    if ip in UNLIMITED_IPS:
        return Access("local", ip, None)
    return Access("free", ip, FREE_DAILY)


# ---------------------------------------------------------------- 일일 쿼터

class DailyQuota:
    """subject(IP 또는 key_prefix)별 일일 카운터 — 파일 영속, UTC 날짜 리셋.

    realty-mcp DailyQuota 동일 이식(조상은 backend AnonDailyQuota).
    soft cap(경쟁 시 소폭 초과 허용) — 남용 방지 목적엔 충분하다.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if str(data.get("date", "")) != self._today():
                return {}
            return {str(k): int(v) for k, v in (data.get("counts") or {}).items()}
        except Exception:
            return {}

    def _write(self, counts: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"date": self._today(), "counts": counts}), encoding="utf-8")
        except Exception:
            pass  # 영속 실패가 조회를 막지 않는다

    def consume(self, subject: str, limit: int) -> bool:
        """한도 내면 1 소비하고 True, 소진이면 False (소비 없음)."""
        with self._lock:
            counts = self._read()
            if counts.get(subject, 0) >= limit:
                return False
            counts[subject] = counts.get(subject, 0) + 1
            self._write(counts)
            return True

    def used(self, subject: str) -> int:
        with self._lock:
            return self._read().get(subject, 0)
