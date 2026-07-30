"""지방계약 우대기업 수의계약(제25조제1항제5호바목) 회귀 — 네트워크 불필요.

2026-07-31 수리한 두 결함을 박제한다:

1. **플래그 배선 누락** — 바목은 여성기업·장애인기업·사회적기업 등을 한 목으로 묶는데
   룰이 `is_social_enterprise` 하나만 봤다. 클라이언트가 "여성기업"이라며
   `is_women_enterprise`만 세우면 수의계약 후보가 통째로 사라지고 경쟁입찰이
   1순위로 나왔다(제보: "여성기업 특례를 판정하지 못했습니다"). 조용히 틀리는
   유형 — 답이 그럴듯해서 사용자가 손해를 보고도 모른다.

2. **목(目) 인용 밀림** — 「청년창업기업」 다목이 신설되며 이후 목이 한 칸씩 밀렸는데
   룰의 legal_basis가 옛 목을 그대로 인용하고 있었다(소기업 다→라, 학술연구 라→마,
   우대기업 마→바). 결정론 룰엔진의 근거 조문이 틀리면 제품의 핵심 주장이 무너진다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.rule_engine import RuleEngine  # noqa: E402

RULES_PATH = ROOT / "rules" / "contract_rules.json"


@pytest.fixture(scope="module")
def engine():
    return RuleEngine(str(RULES_PATH))


@pytest.fixture(scope="module")
def rules_by_id():
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return {r["rule_id"]: r for r in data["rules"]}


@pytest.mark.parametrize("flag", [
    "is_women_enterprise",      # 바목 1) 여성기업지원법 제2조제1호
    "is_disabled_enterprise",   # 바목 2) 장애인기업활동법 제2조제2호
    "is_social_enterprise",     # 바목 3) 사회적기업 육성법 제2조제1호
    "is_preferential_enterprise",
])
def test_any_preferential_flag_triggers_negotiation(engine, flag):
    params = {"contract_type": "service", "estimated_price": 50_000_000, flag: True}
    ids = [h["rule_id"] for h in engine.match(params, org_type="local")]
    assert "LOCAL_SVC_NEGO_SOCIAL" in ids, f"{flag}로 수의계약 후보가 누락됨: {ids}"


def test_product_contract_also_covered(engine):
    """바목은 물품의 제조·구매계약도 포함한다."""
    params = {"contract_type": "product", "estimated_price": 50_000_000,
              "is_women_enterprise": True}
    ids = [h["rule_id"] for h in engine.match(params, org_type="local")]
    assert "LOCAL_PRD_NEGO_SOCIAL" in ids


def test_no_flag_does_not_trigger(engine):
    """우대기업이 아니면 발동하지 않는다 — 과잉 매칭은 위법한 수의계약을 부른다."""
    params = {"contract_type": "service", "estimated_price": 50_000_000}
    ids = [h["rule_id"] for h in engine.match(params, org_type="local")]
    assert "LOCAL_SVC_NEGO_SOCIAL" not in ids


@pytest.mark.parametrize("price,expected", [
    (20_000_000, False),   # '2천만원 초과'라 경계 미달
    (20_000_001, True),
    (100_000_000, True),   # '1억원 이하' 경계 포함
    (100_000_001, False),
])
def test_price_band(engine, price, expected):
    params = {"contract_type": "service", "estimated_price": price,
              "is_women_enterprise": True}
    ids = [h["rule_id"] for h in engine.match(params, org_type="local")]
    assert ("LOCAL_SVC_NEGO_SOCIAL" in ids) is expected, f"{price:,}원에서 기대와 다름: {ids}"


def test_national_org_unaffected(engine):
    """지방계약법 근거이므로 국가기관 판정에 새면 안 된다(org 게이트 회귀)."""
    params = {"contract_type": "service", "estimated_price": 50_000_000,
              "is_women_enterprise": True}
    ids = [h["rule_id"] for h in engine.match(params, org_type="national")]
    assert "LOCAL_SVC_NEGO_SOCIAL" not in ids


@pytest.mark.parametrize("ct,rule_id", [
    ("service", "LOCAL_SVC_NEGO_YOUTH"),
    ("product", "LOCAL_PRD_NEGO_YOUTH"),
])
def test_youth_startup_rule(engine, ct, rule_id):
    """다목(청년창업기업 2천만 초과 5천만 이하) — 목만 밀리고 정작 룰이 없었다."""
    params = {"contract_type": ct, "estimated_price": 40_000_000, "is_youth_startup": True}
    ids = [h["rule_id"] for h in engine.match(params, org_type="local")]
    assert rule_id in ids


@pytest.mark.parametrize("price,expected", [
    (20_000_000, False),   # '초과'라 경계 미달
    (20_000_001, True),
    (50_000_000, True),    # '5천만원 이하' 경계 포함
    (50_000_001, False),   # 이 위는 다목이 아니다 — 라목(소기업)·바목(우대기업) 영역
])
def test_youth_startup_price_band(engine, price, expected):
    params = {"contract_type": "service", "estimated_price": price, "is_youth_startup": True}
    ids = [h["rule_id"] for h in engine.match(params, org_type="local")]
    assert ("LOCAL_SVC_NEGO_YOUTH" in ids) is expected, f"{price:,}원: {ids}"


def test_youth_startup_requires_flag(engine):
    params = {"contract_type": "service", "estimated_price": 40_000_000}
    ids = [h["rule_id"] for h in engine.match(params, org_type="local")]
    assert "LOCAL_SVC_NEGO_YOUTH" not in ids


@pytest.mark.parametrize("rule_id,expected_mok", [
    ("LOCAL_SVC_NEGO_YOUTH", "제25조제1항제5호다목"),      # 청년창업기업
    ("LOCAL_SVC_NEGO_SMALLBIZ", "제25조제1항제5호라목"),   # 소기업·소상공인
    ("LOCAL_PRD_NEGO_SMALLBIZ", "제25조제1항제5호라목"),
    ("LOCAL_SVC_NEGO_ACADEMIC", "제25조제1항제5호마목"),   # 학술연구·원가계산
    ("LOCAL_SVC_NEGO_SOCIAL", "제25조제1항제5호바목"),     # 우대기업
    ("LOCAL_PRD_NEGO_SOCIAL", "제25조제1항제5호바목"),
])
def test_legal_basis_mok_is_current(rules_by_id, rule_id, expected_mok):
    """목 밀림 재발 방지 — 시행령 제25조 개정 시 여기가 먼저 깨진다."""
    basis = " ".join(rules_by_id[rule_id].get("legal_basis") or [])
    assert expected_mok in basis, f"{rule_id} 근거 조문이 낡음: {basis}"
