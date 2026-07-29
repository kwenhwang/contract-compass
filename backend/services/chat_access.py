"""채팅(ask) 접근 게이팅 (2026-07-29).

정책: 익명 사용자는 IP당 1일 chat_free_daily회(기본 2회) 무료 체험, 이후
Supabase(GoTrue) Google 로그인 필요. 로그인 사용자는 무료 한도 미적용
(기존 per-IP rate limit·전역 일일 캡은 그대로 적용 — 비용 폭주 가드는 별도 층).

- JWT 검증: GoTrue HS256(access_token) 로컬 검증 (SUPABASE_JWT_SECRET 공유).
  시크릿 미설정 시 로그인 검증 불가 → 무료 한도 소진자에게 503 (fail-closed,
  admin_token과 동일 철학 — 잘못된 열림 금지).
- 익명 카운터: 파일 영속(JSON, UTC 날짜별 리셋) — DailyCallCap과 같은 패턴.
  soft cap(경쟁 시 소폭 초과 허용)이며 체험 한도 목적엔 충분.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import jwt
from fastapi import HTTPException, Request

from backend.config import get_settings

logger = logging.getLogger(__name__)


class AnonDailyQuota:
    """익명 IP별 일일 무료 사용 카운터 — 파일 영속, UTC 날짜 리셋."""

    def __init__(self, limit: int, path: str) -> None:
        self._limit = limit
        self._path = Path(path)
        self._lock = Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if str(data.get("date", "")) != self._today():
                return {}
            return {str(k): int(v) for k, v in (data.get("counts") or {}).items()}
        except Exception:  # noqa: BLE001 — 없음/손상은 빈 카운터로 (fail-open on read)
            return {}

    def _write(self, counts: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"date": self._today(), "counts": counts}), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 — 영속 실패가 요청을 막지 않음
            pass

    def used(self, ip: str) -> int:
        with self._lock:
            return self._read().get(ip, 0)

    def consume(self, ip: str) -> bool:
        """한도 내면 1 소비하고 True, 소진이면 False (소비 없음)."""
        with self._lock:
            counts = self._read()
            if counts.get(ip, 0) >= self._limit:
                return False
            counts[ip] = counts.get(ip, 0) + 1
            self._write(counts)
            return True

    @property
    def limit(self) -> int:
        return self._limit


_quota: AnonDailyQuota | None = None


def get_anon_quota() -> AnonDailyQuota:
    global _quota
    if _quota is None:
        s = get_settings()
        _quota = AnonDailyQuota(s.chat_free_daily, s.chat_quota_file)
    return _quota


def verify_supabase_jwt(token: str) -> dict | None:
    """GoTrue access_token(HS256) 검증 — 유효하면 claims, 아니면 None."""
    secret = get_settings().supabase_jwt_secret
    if not secret:
        return None
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")
    except jwt.InvalidTokenError:
        return None


def _client_ip(request: Request) -> str:
    # 신뢰 순서 (contract.naru.build는 Cloudflare proxied — 2026-07-29 실측):
    # 1) CF-Connecting-IP: CF가 실클라이언트 IP로 덮어씀. 이게 없으면 nginx가 보는
    #    $remote_addr(X-Real-IP)는 CF 엣지 IP(회전)라 한도가 헛돈다.
    # 2) X-Real-IP: 우리 nginx가 덮어써 위조 불가 (CF 미경유 직접 접근 시).
    # 3) XFF 첫 엔트리: 클라이언트 주입 가능(nginx append) — 최후 폴백만.
    for h in ("cf-connecting-ip", "x-real-ip"):
        v = request.headers.get(h)
        if v:
            return v.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def chat_access(request: Request) -> dict:
    """FastAPI Dependency — ask 엔드포인트 공용 접근 게이트.

    반환: {"user": <email|sub|None>, "anonymous": bool, "free_remaining": int|None}
    거부: 401 login_required (익명 무료 소진) / 401 invalid_token (깨진 토큰) /
          503 (로그인 필요한데 서버에 JWT 시크릿 미설정 — fail-closed).
    """
    settings = get_settings()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        claims = verify_supabase_jwt(token)
        if claims:
            return {
                "user": claims.get("email") or claims.get("sub"),
                "anonymous": False,
                "free_remaining": None,
            }
        if not settings.supabase_jwt_secret:
            raise HTTPException(503, detail={
                "error": "auth_unavailable",
                "message": "서버에 로그인 검증이 설정되지 않았습니다.",
            })
        raise HTTPException(401, detail={
            "error": "invalid_token",
            "message": "로그인이 만료되었거나 유효하지 않습니다. 다시 로그인해 주세요.",
        })

    ip = _client_ip(request)
    quota = get_anon_quota()
    if quota.consume(ip):
        return {
            "user": None,
            "anonymous": True,
            "free_remaining": max(0, quota.limit - quota.used(ip)),
        }
    raise HTTPException(401, detail={
        "error": "login_required",
        "message": f"무료 체험 {quota.limit}회를 모두 사용했습니다. Google 로그인 후 계속 이용할 수 있습니다.",
    })
