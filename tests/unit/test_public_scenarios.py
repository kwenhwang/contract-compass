"""공개 회귀셋 — 계약유형×기관유형×금액 경계의 룰 결정 스냅샷 검증.

기대값은 법령 경계값 기준으로 검토된 스냅샷(tests/scenarios_public.json).
룰 변경으로 이 테스트가 깨지면, 법령 근거로 재검토 후 스냅샷을 갱신할 것
(맹목 갱신 금지 — 각 시나리오 desc의 경계 의미를 확인).
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

from backend.services.rule_engine import RuleEngine  # noqa: E402

_DATA = json.loads((ROOT / "tests" / "scenarios_public.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def engine():
    return RuleEngine(str(ROOT / "rules" / "contract_rules.json"))


@pytest.mark.parametrize("sc", _DATA["scenarios"], ids=[s["desc"] for s in _DATA["scenarios"]])
def test_scenario(engine, sc):
    matched = engine.match(dict(sc["params"]), org_type=sc["org_type"])
    top = matched[0] if matched else None
    assert (top["rule_id"] if top else None) == sc["expect_rule_id"], (
        f"{sc['desc']}: 1순위 룰 변경 {sc['expect_rule_id']} → {top['rule_id'] if top else None}"
    )
    if sc["expect_method"] is not None:
        assert top.get("result", {}).get("method") == sc["expect_method"]
    assert [r["rule_id"] for r in matched[:3]] == sc["expect_top3"], (
        f"{sc['desc']}: top3 후보 변경"
    )
