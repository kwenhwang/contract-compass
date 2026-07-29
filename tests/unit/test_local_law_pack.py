"""지자체 룰별 법령셋 → LLM 컨텍스트 빌더 테스트 (2026-06-14)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.local_law_pack import build_llm_context, get_pack, list_rules  # noqa: E402

pytestmark = pytest.mark.unit


def test_pack_has_local_rules():
    rules = list_rules()
    assert any(r.startswith("LOCAL_") for r in rules)
    assert "LOCAL_SVC_PRD_소액수의_2천만" in rules


def test_pack_carries_legal_set():
    p = get_pack("LOCAL_SVC_PRD_소액수의_2천만")
    assert p["적용"]["org_type"] == "local"
    assert p["적용"]["estimated_price_lte"] == 20_000_000
    sources = [a["source"] for a in p["법령셋"] if a]
    assert any("지방계약법 시행령 제25조" in s for s in sources)


def test_llm_context_contains_article_body():
    ctx = build_llm_context("LOCAL_SVC_PRD_소액수의_2천만")
    assert "지방계약법 시행령 제25조" in ctx
    assert "수의계약" in ctx
    assert "법령셋 범위 내에서만" in ctx  # 환각 차단 지시 포함


def test_award_rate_pack_has_ruling():
    """적격심사 룰 팩은 예규 낙찰하한율 후보를 LLM 컨텍스트에 포함."""
    ctx = build_llm_context("LOCAL_CST_적격심사_시설공사")
    assert "낙찰하한율" in ctx
    assert "%" in ctx


def test_unknown_rule_empty():
    assert build_llm_context("NOPE_999") == ""
