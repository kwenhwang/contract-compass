"""채팅 접근 게이팅 단위 테스트 (2026-07-29) — 익명 2회/일 → 로그인.

파일 IO만 사용 — 서버·LLM·네트워크 불필요.
"""
import sys
import time
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import backend.services.chat_access as ca  # noqa: E402

pytestmark = pytest.mark.unit

SECRET = "test-secret"


class _FakeSettings:
    def __init__(self, tmp_path, secret=SECRET, free=2):
        self.supabase_jwt_secret = secret
        self.chat_free_daily = free
        self.chat_quota_file = str(tmp_path / "quota.json")


class _FakeRequest:
    def __init__(self, ip="1.2.3.4", token: str | None = None):
        self.headers = {"x-forwarded-for": ip}
        if token:
            self.headers["authorization"] = f"Bearer {token}"
        self.client = None


def _patch(monkeypatch, tmp_path, **kw):
    s = _FakeSettings(tmp_path, **kw)
    monkeypatch.setattr(ca, "get_settings", lambda: s)
    monkeypatch.setattr(ca, "_quota", None)  # 싱글턴 리셋
    return s


def _token(email="u@example.com", aud="authenticated", secret=SECRET, exp_delta=3600):
    return pyjwt.encode(
        {"sub": "uid-1", "email": email, "aud": aud, "exp": int(time.time()) + exp_delta},
        secret, algorithm="HS256",
    )


def test_anonymous_two_free_then_login_required(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    r = _FakeRequest()
    a1 = ca.chat_access(r)
    assert a1["anonymous"] and a1["free_remaining"] == 1
    a2 = ca.chat_access(r)
    assert a2["free_remaining"] == 0
    with pytest.raises(HTTPException) as e:
        ca.chat_access(r)
    assert e.value.status_code == 401
    assert e.value.detail["error"] == "login_required"


def test_quota_is_per_ip(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    ca.chat_access(_FakeRequest(ip="1.1.1.1"))
    ca.chat_access(_FakeRequest(ip="1.1.1.1"))
    # 다른 IP는 독립 한도
    assert ca.chat_access(_FakeRequest(ip="2.2.2.2"))["free_remaining"] == 1


def test_quota_persists_across_instances(monkeypatch, tmp_path):
    s = _patch(monkeypatch, tmp_path)
    ca.chat_access(_FakeRequest())
    ca.chat_access(_FakeRequest())
    # 재시작 시뮬레이션 — 새 인스턴스가 같은 파일을 읽음
    monkeypatch.setattr(ca, "_quota", None)
    monkeypatch.setattr(ca, "get_settings", lambda: s)
    with pytest.raises(HTTPException):
        ca.chat_access(_FakeRequest())


def test_valid_jwt_bypasses_quota(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, free=0)  # 익명 무료 0회여도
    a = ca.chat_access(_FakeRequest(token=_token()))
    assert a["user"] == "u@example.com"
    assert not a["anonymous"]


def test_bad_jwt_rejected(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    for bad in (_token(secret="wrong"), _token(aud="other"), _token(exp_delta=-10), "garbage"):
        with pytest.raises(HTTPException) as e:
            ca.chat_access(_FakeRequest(token=bad))
        assert e.value.status_code == 401
        assert e.value.detail["error"] == "invalid_token"


def test_client_ip_priority_cf_first(monkeypatch, tmp_path):
    """CF-Connecting-IP > X-Real-IP > XFF — 위조 가능한 XFF가 신뢰 헤더를 못 이긴다."""
    _patch(monkeypatch, tmp_path)
    r = _FakeRequest(ip="9.9.9.9")  # XFF=9.9.9.9
    r.headers["cf-connecting-ip"] = "1.2.3.4"
    r.headers["x-real-ip"] = "5.6.7.8"
    assert ca._client_ip(r) == "1.2.3.4"
    del r.headers["cf-connecting-ip"]
    assert ca._client_ip(r) == "5.6.7.8"
    del r.headers["x-real-ip"]
    assert ca._client_ip(r) == "9.9.9.9"


def test_secret_unset_fails_closed(monkeypatch, tmp_path):
    """시크릿 미설정: 토큰 제시 시 503(fail-closed) — 무인증 통과 금지."""
    _patch(monkeypatch, tmp_path, secret="")
    with pytest.raises(HTTPException) as e:
        ca.chat_access(_FakeRequest(token=_token()))
    assert e.value.status_code == 503
    # 익명 무료 한도는 시크릿 없어도 동작
    assert ca.chat_access(_FakeRequest())["anonymous"]
