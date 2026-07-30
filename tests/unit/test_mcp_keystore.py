"""contract-mcp 키 대장(keystore) 단위 테스트 (2026-07-30 — 판매 파이프라인)."""
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

import keystore  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(keystore, "KEYS_PATH", tmp_path / "keys.json")
    monkeypatch.setattr(keystore, "CALL_LOG", tmp_path / "calls.jsonl")


def test_issue_self_returns_plaintext_and_extended_fields():
    key, rec = keystore.issue("크몽#1", 30, 2000, channel="kmong",
                              amount_krw=9900, contact="a@b.c", order_id="K1")
    assert key.startswith("cc_live_") and rec["key_hash"] == hashlib.sha256(key.encode()).hexdigest()
    assert rec["channel"] == "kmong" and rec["amount_krw"] == 9900
    assert rec["contact"] == "a@b.c" and rec["source"] == "self"


def test_issue_mirror_no_plaintext():
    """LS 미러 — 외부 키를 해시 등록, 평문 반환 없음."""
    key, rec = keystore.issue("LS 30일", 30, 2000, channel="lemonsqueezy",
                              key="ABCD-1234-EFGH-5678", source="ls_mirror", order_id="ls-77")
    assert key is None
    assert rec["key_hash"] == hashlib.sha256(b"ABCD-1234-EFGH-5678").hexdigest()
    assert rec["source"] == "ls_mirror"


def test_order_idempotency():
    """웹훅 재전송 — 같은 order_id는 재발급하지 않고 기존 레코드 반환."""
    _, first = keystore.issue("LS", key="K-1", order_id="ls-9", source="ls_mirror")
    key2, again = keystore.issue("LS", key="K-DIFFERENT", order_id="ls-9", source="ls_mirror")
    assert key2 is None and again["key_hash"] == first["key_hash"]
    assert len(keystore.list_keys()) == 1


def test_revoke_by_order_for_refund():
    keystore.issue("LS", key="K-2", order_id="ls-10", source="ls_mirror")
    rec = keystore.revoke_by_order("ls-10")
    assert rec and rec["is_active"] is False and rec["revoke_reason"] == "refund"
    assert keystore.revoke_by_order("ls-없음") is None


def test_report_aggregates_revenue_and_expiring():
    keystore.issue("A", days=2, channel="kmong", amount_krw=9900)     # D-7 임박
    keystore.issue("B", days=60, channel="lemonsqueezy", amount_krw=24900)
    out = keystore.report()
    assert "만료임박(D-7) 1" in out
    assert "9,900원" in out and "24,900원" in out and "34,800원" in out
    assert len(keystore.expiring_within(3)) == 1
