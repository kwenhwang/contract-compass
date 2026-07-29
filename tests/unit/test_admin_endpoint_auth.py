"""관리자 전용 엔드포인트 인증 회귀 테스트 (2026-07-27 P0 근본수리).

배경: `GET /api/v1/feedback/board` 가 무인증 200으로 의견 원문 전량을 공개하고 있었다.
nginx IP 허용은 임시 봉합이므로 백엔드를 fail-closed 로 전환했고, 이 파일이 그 불변식을 고정한다.

불변식
  1. 토큰 없음 / 틀린 토큰 → 401 (본문 유출 없음)
  2. ADMIN_TOKEN 미설정 배포 → 503 (무인증 통과 금지 — fail-open 회귀 방지)
  3. 올바른 토큰 → 200 + 정상 페이로드

서버·LLM·네트워크 불필요 — TestClient 로 라우트만 태운다.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.api import deps  # noqa: E402
from backend.api.v1 import feedback as feedback_mod  # noqa: E402

_TOKEN = "test-admin-token-0727"


@pytest.fixture
def client(monkeypatch):
    """ADMIN_TOKEN 이 설정된 정상 배포 상태의 앱."""

    class _S:
        admin_token = _TOKEN

    monkeypatch.setattr(deps, "get_settings", lambda: _S())
    app = FastAPI()
    app.include_router(feedback_mod.router, prefix="/api/v1")
    return TestClient(app)


# ── 1. 미인증 fail-closed ────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("headers", [{}, {"X-Admin-Token": "wrong"}, {"X-Admin-Token": ""}])
def test_board_rejects_unauthenticated(client, headers):
    r = client.get("/api/v1/feedback/board", headers=headers)
    assert r.status_code == 401, f"무인증 접근이 {r.status_code} 로 통과 — P0 재발"
    # 401 본문에 의견 원문이 섞여 나가지 않아야 한다.
    assert "items" not in r.json()


# ── 2. 미설정 배포는 통과가 아니라 거부 ─────────────────────────────────────
@pytest.mark.unit
def test_board_denies_when_token_unset(monkeypatch):
    """ADMIN_TOKEN 미설정 = 잘못된 배포. 무인증 통과(fail-open) 금지."""

    class _S:
        admin_token = ""

    monkeypatch.setattr(deps, "get_settings", lambda: _S())
    app = FastAPI()
    app.include_router(feedback_mod.router, prefix="/api/v1")
    r = TestClient(app).get("/api/v1/feedback/board")
    assert r.status_code == 503
    assert "items" not in r.json()


# ── 3. 정상 토큰은 통과 ──────────────────────────────────────────────────────
@pytest.mark.unit
def test_board_allows_with_token(client, tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_mod, "_LOG_PATH", tmp_path / "feedback.jsonl")
    r = client.get("/api/v1/feedback/board", headers={"X-Admin-Token": _TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert "total" in body and "items" in body

