"""금액 임계값 단일 소스.

고시금액(2.3억)·국제입찰(7.1억) 등 핵심 임계값이 여러 모듈에 매직넘버로 흩어져
있어 동기화 위험이 있었다. contract_rules.json의 thresholds 블록을 단일 소스로 삼아
모듈들이 이 상수를 참조하도록 통일한다.

※ 이 값들은 **공기업·준정부기관 프로파일**(기재부 고시금액 기준)이다. 국가기관·
지자체는 국제입찰 고시금액 등이 다르며, 검증된 지자체 값은 rules의 LOCAL_* 룰
conditions에 직접 인코딩돼 있다(미검증 값을 여기 추가하지 말 것 — 확인 후 인코딩).

룰 conditions의 raw integer는 그대로 둔다(named-ref 스키마는 과한 추상화).
대신 tools/audit_rule_schema.py가 thresholds == 룰 conditions 값 일관성을 검증한다.
"""
import json
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "rules" / "contract_rules.json"

with open(_RULES_PATH, encoding="utf-8") as _f:
    THRESHOLDS: dict[str, int] = json.load(_f).get("thresholds", {})

# 자주 쓰는 임계값 — 명시 상수 (fallback은 미정의 시 보호용 기본값)
ANNOUNCEMENT_LIMIT: int = THRESHOLDS.get("announcement_limit", 230_000_000)  # 고시금액 2.3억
SME_SMALL_ENTERPRISE_UPPER: int = THRESHOLDS.get("sme_small_enterprise_upper", 100_000_000)  # 소기업 상한 1억
INTERNATIONAL_BID_PRODUCT: int = THRESHOLDS.get("international_bid_product", 710_000_000)  # 물품 국제입찰 7.1억
INTERNATIONAL_BID_SERVICE: int = THRESHOLDS.get("international_bid_service", 710_000_000)  # 용역 국제입찰 7.1억
PETTY_CONTRACT_UPPER: int = THRESHOLDS.get("petty_contract_upper", 50_000_000)  # 소액수의 상한 5천만
