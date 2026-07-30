"""contract-mcp 키 인증·티어·쿼터 단위 테스트 (2026-07-30, realty 이식 검증)."""
import hashlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

import auth  # noqa: E402

pytestmark = pytest.mark.unit


def _req(headers=None, query=None, host="203.0.113.5"):
    """auth가 쓰는 인터페이스만 흉내 — headers.get/query_params.get(소문자 키)."""
    r = MagicMock()
    r.headers = {k.lower(): v for k, v in (headers or {}).items()}  # dict.get 그대로 사용
    r.query_params = query or {}
    r.client = MagicMock(host=host)
    return r


def _install_key(tmp_path, monkeypatch, *, active=True, expires="2099-01-01T00:00:00+00:00", daily=77):
    key = "cc_live_" + "ab" * 32
    kf = tmp_path / "keys.json"
    kf.write_text(json.dumps({"keys": [{
        "key_hash": hashlib.sha256(key.encode()).hexdigest(),
        "key_prefix": key[:16], "is_active": active,
        "expires_at": expires, "daily_limit": daily}]}))
    monkeypatch.setattr(auth, "KEYS_PATH", kf)
    monkeypatch.setattr(auth, "_keys_mtime", -1.0)
    monkeypatch.setattr(auth, "_keys_cache", {})
    return key


def test_stdio_is_local_unlimited():
    a = auth.resolve_access(None)
    assert a.tier == "local" and a.daily_limit is None


def test_loopback_unlimited():
    a = auth.resolve_access(_req(host="127.0.0.1"))
    assert a.tier == "local" and a.daily_limit is None


def test_anonymous_free_tier_by_ip():
    a = auth.resolve_access(_req(headers={"x-real-ip": "198.51.100.3"}))
    assert a.tier == "free" and a.subject == "198.51.100.3" and a.daily_limit == auth.FREE_DAILY


def test_valid_key_paid_tier(tmp_path, monkeypatch):
    key = _install_key(tmp_path, monkeypatch, daily=77)
    a = auth.resolve_access(_req(headers={"authorization": f"Bearer {key}"}))
    assert a.tier == "paid" and a.daily_limit == 77 and a.error is None


def test_key_via_query_fallback(tmp_path, monkeypatch):
    """ChatGPT 커넥터 경로 — 헤더 없이 ?key=."""
    key = _install_key(tmp_path, monkeypatch)
    a = auth.resolve_access(_req(query={"key": key}))
    assert a.tier == "paid"


def test_invalid_and_expired_key_return_structured_error(tmp_path, monkeypatch):
    _install_key(tmp_path, monkeypatch)
    bad = auth.resolve_access(_req(headers={"authorization": "Bearer cc_live_wrong"}))
    assert bad.error and bad.error["error"] == "invalid_key" and auth.PRICING_URL in bad.error["message"]
    key = _install_key(tmp_path, monkeypatch, expires="2020-01-01T00:00:00+00:00")
    exp = auth.resolve_access(_req(headers={"authorization": f"Bearer {key}"}))
    assert exp.error and exp.error["error"] == "key_expired"


def test_daily_quota_consume_and_block(tmp_path):
    q = auth.DailyQuota(tmp_path / "q.json")
    assert all(q.consume("1.2.3.4", 3) for _ in range(3))
    assert q.consume("1.2.3.4", 3) is False  # 소진 — 소비 없음
    assert q.consume("5.6.7.8", 3) is True   # 다른 subject는 독립
    assert q.used("1.2.3.4") == 3
