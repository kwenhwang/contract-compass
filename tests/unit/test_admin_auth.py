"""admin 인증 하드닝(B1) 단위 테스트 — fail-closed 검증 (2026-07-17).

require_admin은 ADMIN_TOKEN 미설정 시 통과(fail-open)하던 과거 동작을 버리고,
미설정이면 503으로 **거부**(fail-closed)해야 한다. 서버 불필요.
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import backend.api.deps as deps  # noqa: E402

pytestmark = pytest.mark.unit


class _FakeSettings:
    def __init__(self, token: str):
        self.admin_token = token


def _patch_token(monkeypatch, token: str):
    monkeypatch.setattr(deps, "get_settings", lambda: _FakeSettings(token))


def test_require_admin_rejects_when_token_unset(monkeypatch):
    """토큰 미설정 = fail-closed → 503(무인증 통과 금지)."""
    _patch_token(monkeypatch, "")
    with pytest.raises(HTTPException) as ei:
        deps.require_admin(x_admin_token=None)
    assert ei.value.status_code == 503


def test_require_admin_rejects_wrong_token(monkeypatch):
    _patch_token(monkeypatch, "secret")
    with pytest.raises(HTTPException) as ei:
        deps.require_admin(x_admin_token="nope")
    assert ei.value.status_code == 401


def test_require_admin_rejects_missing_token_when_configured(monkeypatch):
    _patch_token(monkeypatch, "secret")
    with pytest.raises(HTTPException) as ei:
        deps.require_admin(x_admin_token=None)
    assert ei.value.status_code == 401


def test_require_admin_passes_correct_token(monkeypatch):
    _patch_token(monkeypatch, "secret")
    # 예외가 없어야 통과
    assert deps.require_admin(x_admin_token="secret") is None
