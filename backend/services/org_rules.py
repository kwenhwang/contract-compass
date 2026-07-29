"""기관유형(org_type)별 소액수의 한도 + 수의사유 분석 (2026-06-13, 대회).

나라장터 계약정보는 전 기관(지자체·국가기관·공기업) 혼재 + 수의사유(prvtcntrctRsn)를 제공한다.
기관유형마다 적용 법령(지방계약법/국가계약법/공기업 계약사무규칙)과 소액수의 한도가 달라, 공기업 룰만으로는
지자체 계약을 정확히 판정할 수 없다. 여기서 기관유형 판별 + 사유 분석을 담당한다.

근거(확인된 값만 인코딩, 미확인은 None=사유 기반 판정에 위임):
- 지자체(지방계약법 시행령 제25조①5호가목) / 국가(국가계약법 시행령 제26조①5호가목):
  물품·용역 소액수의 = 추정가격 **2천만원 이하** (나라장터 prvtcntrctRsn 문구로 확인).
- 공기업(공기업·준정부기관 계약사무규칙 기준): 물품·용역 1억 / 일반공사 4억 — 단 공기업은 rule_engine으로 정확 판정하므로
  여기 SOAK_LIMIT은 비(非)공기업 보조 판정용.
"""
from __future__ import annotations

import re

# 소액수의 한도(이하면 수의 정당). None = 한도 미확인 → 사유 기반 판정에 위임(추정 금지).
# 공사 4억 = 종합공사 최고 한도 (2026-07-17 정정: 기존 None은 지자체·국가 공사 수의
# 37,138건을 통째로 무검사시켰음. 법 원문 확보 — 국가령 26조①5호가목·지방령 25조①5호가목
# 공히 종합 4억/전문 2억/그 밖의 법령 1.6억 '이하'. 나라장터 데이터에 공사 종별이 없어
# 최고 한도(4억)를 적용 — 전문·기타 공사의 2~4억 구간은 놓치지만 오탐은 없는 보수 방향).
SOAK_LIMIT: dict[str, dict[str, int | None]] = {
    "local": {"product": 20_000_000, "service": 20_000_000, "construction": 400_000_000},
    "national": {"product": 20_000_000, "service": 20_000_000, "construction": 400_000_000},
    "public_corp": {"product": 100_000_000, "service": 100_000_000, "construction": 400_000_000},
}

_ORG_MAP = [
    ("지방자치단체", "local"), ("교육", "local"), ("지방공기업", "local"),
    # 지자체 출자·출연기관(1,285건)은 지방계약법 준용 — 기존엔 public_corp 폴백(2026-07-17)
    ("지자체", "local"),
    ("국가기관", "national"), ("준정부", "public_corp"), ("공기업", "public_corp"),
    ("기타공공기관", "public_corp"),
]


def detect_org_type(record: dict) -> str:
    """계약 record → org_type. 나라장터 기관구분(발주기관구분) 우선, 없으면 public_corp."""
    div = str(record.get("발주기관구분") or "")
    for kw, ot in _ORG_MAP:
        if kw in div:
            return ot
    return "public_corp"


def org_type_is_inferred(record: dict) -> bool:
    """발주기관구분이 _ORG_MAP 어디에도 매칭 안 돼 기본값(public_corp)으로 폴백했는지.
    True면 한도가 추정 적용 → UI/리포트에 '기관유형 추정' 주의 표시(누락 위험 투명화)."""
    div = str(record.get("발주기관구분") or "")
    return not any(kw in div for kw, _ in _ORG_MAP)


# 수의사유(prvtcntrctRsn) 유형 분류 — 정당성 판단이 아니라 검토 분류용
_REASON_TYPES = [
    ("rebid_single", ["1인", "1인뿐", "재공고", "유찰", "입찰자가 없"]),       # 재공고 유찰 (시행령 26/25조 1항)
    ("specific", ["특정인", "특허", "신기술", "독점", "유일", "지정정보처리",     # 특정인·특허
                  "특수한설비", "특수설비", "10인미만", "10인 미만"]),           # 지명경쟁 정당사유
    ("urgent", ["긴급", "천재", "재해", "비상", "재난"]),                       # 긴급
    ("delegated_certified", ["위탁", "대행", "성능인증", "우수조달", "우수제품",  # 위탁·인증·조합추천 등
                              "조달청우수", "조달사업에 관한 법률", "타법령",
                              "조합", "장애인", "사회적기업", "여성기업", "중소기업자",
                              "유공자", "자활", "보훈", "집단촌", "사회복지",
                              # 2026-06-14 감사 보강: 'other'에서 법정 지정사유로 재분류(~600건)
                              "농공단지", "클라우드", "디지털서비스", "호환성", "부품",
                              "제조공급자", "설치조립", "기술혁신"]),
    ("small_amount", ["이하", "소액", "추정가격"]),                            # 금액 기준
]

# 법령상 정당 가능성이 높은 사유(수의 허용 명문 근거) — 검토 우선순위 down-rank 대상
STATUTORY_REASONS = {"rebid_single", "specific", "urgent", "delegated_certified"}

_AMT_UNIT = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000}


def _krw_to_int(eok: str | None, cheonman: str | None) -> int:
    v = 0
    if eok:
        v += int(eok) * 100_000_000
    if cheonman:
        v += int(cheonman) * 10_000_000
    return v


def parse_reason_amount(reason: str) -> int | None:
    """수의사유에서 소액수의 한도 금액을 추출 → 등장 금액 중 **최댓값**을 반환.

    한도 비교의 기준은 '구간 상한'이지 '하한'이 아니다. 또 건설공사 사유처럼 여러 한도가
    나열될 수 있다. 모든 금액을 뽑아 최댓값을 쓰면 (오탐 최소·보수적) 다음을 모두 만족:
      '2천만원 이하'              → 20,000,000
      '2천만원 초과 1억원 이하'    → 100,000,000  (하한 2천만 아님)
      '2천만원 초과 5천만원 이하'  → 50,000,000
      '건설 4억, 전문 2억, 기타 1억6천만원 이하' → 400,000,000 (가장 큰 한도=건설공사)
    """
    amounts = parse_reason_amounts(reason)
    return max(amounts) if amounts else None


def parse_reason_amounts(reason: str) -> list[int]:
    """수의사유에 등장하는 모든 금액(원) — 유형별 한도 선택(screening)용.

    2026-07-17: '2,000만원'·'5,000만원'(콤마 만단위)·'3천5백만원' 표기가 전부
    미파싱돼 모순 검사가 불발되던 문제 정정.
    """
    if not reason:
        return []
    amounts: list[int] = []
    # 억(+천만) 조합
    for m in re.finditer(r"(\d+)\s*억(?:\s*(\d+)\s*천만)?", reason):
        amounts.append(_krw_to_int(m.group(1), m.group(2)))
    # 단독 천만 (앞에 억이 붙지 않은 것)
    for m in re.finditer(r"(?<![\d억])(\d+)\s*천만", reason):
        amounts.append(int(m.group(1)) * 10_000_000)
    # N천M백만 (예: 3천5백만원)
    for m in re.finditer(r"(\d)\s*천\s*(\d)\s*백\s*만", reason):
        amounts.append((int(m.group(1)) * 1000 + int(m.group(2)) * 100) * 10_000)
    # 콤마 만단위 (예: 2,000만원 / 5,000만원) — 위 천만 패턴과 별개 표기
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+)\s*만\s*원", reason):
        amounts.append(int(m.group(1).replace(",", "")) * 10_000)
    # 숫자 그대로(예: 100,000,000원)
    for m in re.finditer(r"(\d[\d,]{6,})\s*원", reason):
        amounts.append(int(m.group(1).replace(",", "")))
    return amounts


def classify_reason(reason: str) -> str:
    """수의사유 → 유형 코드 (rebid_single/specific/urgent/small_amount/other)."""
    if not reason:
        return "none"
    for code, kws in _REASON_TYPES:
        if any(k in reason for k in kws):
            return code
    return "other"


# ── 조문(법령 인용) 기반 정밀 분류 ─────────────────────────────────────────
# 나라장터 prvtcntrctRsn은 '계약 근거' 필드라 수의계약뿐 아니라 경쟁방식(2단계·협상)도 들어온다.
# 사유 텍스트에 인용된 [법령 제N조...]로 정밀 판정 → 키워드 분류가 'other'로 떨구던
# 법정 수의사유(방위산업체·비밀유지·문화재·GS인증 등)와 경쟁방식을 구분(상용 신뢰도).
_COMPETITIVE_CITATION = re.compile(
    r"2단계|규격.{0,8}가격입찰|협상에\s*의한|경쟁은 입찰의 방법|제18조|제43조|"
    r"지방계약법\s*제12조|국가계약법\s*제10조"
)
_STATUTORY_NEGOTIATED = re.compile(
    r"국가계약법\s*제26조|지방계약법\s*제25조|지방계약법\s*제22조|국가계약법\s*제23조|"
    r"판로지원|중소기업제품|성능인증|우수조달|우수제품|GS\s*인증|단체표준|벤처나라|새싹기업"
)


def _short_reason(r: str) -> str:
    """사유 라벨 — 법령 대괄호 앞 본문 우선, 없으면 앞부분."""
    head = re.split(r"\s*\[", r, 1)[0].strip()
    return (head or r.strip())[:60]


def classify_reason_detail(reason: str) -> dict:
    """조문 기반 정밀 분류. 반환 {category, statutory, competitive, label}.
    category: competitive_method(경쟁방식·수의아님) / rebid / statutory(법정 수의사유) / review(확인 필요) / none.
    """
    r = reason or ""
    if not r.strip():
        return {"category": "none", "statutory": False, "competitive": False, "label": ""}
    if _COMPETITIVE_CITATION.search(r):
        return {"category": "competitive_method", "statutory": True, "competitive": True,
                "label": "경쟁입찰 방식(2단계·협상 등) — 수의계약 아님"}
    if re.search(r"유찰|재공고|입찰자가 없|1인", r):
        return {"category": "rebid", "statutory": True, "competitive": False,
                "label": "유찰·재공고 후 수의 — 적법 가능"}
    if _STATUTORY_NEGOTIATED.search(r):
        return {"category": "statutory", "statutory": True, "competitive": False,
                "label": _short_reason(r)}
    return {"category": "review", "statutory": False, "competitive": False, "label": _short_reason(r)}
