"""pytest 공용 fixture.

rule_engine.match는 순수 함수라 서버·LLM 없이 직접 호출 가능 —
룰 수정 시 가장 먼저 깨지는 안전망.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def rule_engine():
    from backend.services.rule_engine import RuleEngine
    return RuleEngine(str(ROOT / "rules" / "contract_rules.json"))
