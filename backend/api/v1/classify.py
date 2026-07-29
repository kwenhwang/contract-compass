"""사업개요 → 물품분류 AI 추천 + 검토자 승인 기록."""
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from backend.api.deps import get_llm, get_usage_logger
from backend.config import BASE_DIR
from backend.services.rate_limiter import rate_limit_llm, record_llm_call
from backend.services.llm.base import LLMProvider
from backend.services.usage_logger import extract_client_meta
from backend.services.thresholds import ANNOUNCEMENT_LIMIT

router = APIRouter(prefix="/classify", tags=["classify"])

_DATA_DIR = BASE_DIR / "data"
_LOG_PATH = BASE_DIR / "logs" / "classification_approvals.jsonl"

# 사용자 의견 반영 별칭 사전 — 사업개요에 자연 표현이 들어와도 정확한 sme 코드로 매칭.
#   #18: '동영상제작서비스' 품명을 사업개요에 그대로 쓰는 경우가 드물어 자연 표현으로
_SME_ALIASES: dict[str, list[str]] = {
    "8213160301": ["홍보용 동영상", "동영상 제작", "홍보 영상", "홍보용 영상"],
    "4111250401": ["수도계량기", "수도 계량기", "수도미터", "수도 미터"],
    # 2026-06-05 F2-2: 수질계측기 → 프로세스제어반(상·하수 측정용 계측기 포함) 매핑
    "4111249801": ["수질계측기", "수질 계측기", "수질측정기", "상·하수 계측", "상하수 계측"],
}


class ProductCandidate(BaseModel):
    code: str
    name: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    note: str = ""  # 특이사항 (중기부 경쟁제품 고시 비고 — 적용 제외·한정 조건)
    direct_purchase: bool = False  # 공사용자재 직접구매 대상 품목 여부


class ClassifyRequest(BaseModel):
    session_id: str
    description: str = Field(..., min_length=3, max_length=500)
    contract_type: Literal["service", "product", "construction", "public_procurement"]


class ClassifyResponse(BaseModel):
    g2b_candidates: list[ProductCandidate]
    is_sme_competition: bool
    sme_candidates: list[ProductCandidate]
    reasoning: str


class ApprovalRequest(BaseModel):
    session_id: str
    g2b_code: str | None = None
    sme_code: str | None = None
    decision: Literal["approved", "rejected"]
    reviewer_note: str | None = None


class ClassifyByCodeResponse(BaseModel):
    code: str
    name: str | None = None
    is_sme_competition: bool
    applicable_standard: Literal["조달청", "중기부", "직접발주"] | None = None
    description: str
    note: str = ""  # 특이사항 (고시 비고)
    direct_purchase: bool = False  # 공사용자재 직접구매 대상


@lru_cache(maxsize=1)
def _load_g2b() -> list[dict]:
    with open(_DATA_DIR / "g2b_categories.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_sme() -> list[dict]:
    with open(_DATA_DIR / "sme_competition_products.json", encoding="utf-8") as f:
        return json.load(f)


def _build_catalog_text() -> str:
    g2b = _load_g2b()
    sme = _load_sme()
    g2b_lines = "\n".join(f"  {it['code']} | {it['name']} ({it.get('parent', '')})" for it in g2b)
    sme_lines = "\n".join(f"  {it['code']} | {it['name']} ({it.get('category', '')})" for it in sme)
    return f"[G2B 분류번호 후보]\n{g2b_lines}\n\n[중소기업자간 경쟁제품 지정 목록]\n{sme_lines}"


_SYSTEM_PROMPT = """당신은 공공조달 물품분류 전문 AI입니다.
사용자가 입력한 사업개요를 분석하여 다음 3가지를 추천하세요:

1. **G2B 분류번호 후보** — 아래 [G2B 분류번호 후보] 목록에서 가장 적합한 1~3개 선택
2. **중소기업자간 경쟁제품 지정 여부** — 아래 [중소기업자간 경쟁제품 지정 목록]과 매칭되면 true
3. **SME 후보** — true일 경우 가장 적합한 SME 품목 1~3개

[응답 형식 — 반드시 유효한 JSON]
{
  "g2b_candidates": [{"code": "정확한 코드", "name": "정확한 품명", "confidence": 0.0~1.0}],
  "is_sme_competition": true,
  "sme_candidates": [{"code": "정확한 코드", "name": "정확한 품명", "confidence": 0.0~1.0}],
  "reasoning": "사업개요의 어느 단어가 어느 품목과 매칭되는지 2~3문장으로 설명"
}

[엄격한 규칙]
- code와 name은 반드시 위 목록에 있는 값을 정확히 그대로 사용. 새로 만들지 마세요.
- 사업개요와 매칭되는 품목이 없으면 빈 배열 [] 반환.
- confidence는 사업개요-품명 의미 유사도 (1.0=정확히 일치, 0.5=관련 있음, 0.0=무관).
- is_sme_competition=false면 sme_candidates는 반드시 [].
"""


# ── 단계 0: 계약유형 AI 추천 (#29) ─────────────────────────────
class ContractTypeRequest(BaseModel):
    description: str = Field(..., min_length=2, max_length=500)


class ContractTypeCandidate(BaseModel):
    contract_type: Literal["service", "product", "construction"]
    label: str
    confidence: float = Field(ge=0.0, le=1.0)


class ContractTypeResponse(BaseModel):
    suggested: Literal["service", "product", "construction"]
    confidence: float
    reason: str
    candidates: list[ContractTypeCandidate]
    method: Literal["keyword", "llm"]


# 키워드 결정론 — 비결정성 최소화. 강신호 우선
_TYPE_KEYWORDS: dict[str, list[str]] = {
    "construction": ["공사", "시공", "건설", "신설", "증설", "철거", "포장", "토목", "건축", "조경",
                      "전기공사", "정보통신공사", "소방시설", "관로", "배관설치", "옹벽", "준설"],
    "product": ["제조", "구매", "구입", "납품", "장비", "기자재", "자재", "물품", "조달", "설비구입",
                "펌프", "밸브", "계측기", "판넬", "제어장치", "모터"],
    # 2026-06-02 F3-1: 정보시스템·유지관리 등 보강 (47억 정보시스템 케이스 → product 오분류 방지)
    "service": ["용역", "연구", "관리", "점검", "설계", "감리", "진단", "조사", "컨설팅", "운영",
                "유지보수", "청소", "경비", "위탁", "분석", "평가", "교육", "정보화", "소프트웨어", "시스템구축",
                "정보시스템", "유지관리", "정보보호", "보안관제", "데이터", "클라우드", "DB", "데이터베이스",
                "시스템운영", "시스템관리", "전산", "웹", "앱", "어플리케이션", "SI"],
}
_TYPE_LABEL = {"service": "용역", "product": "물품", "construction": "공사"}


_SVC_STRONG = ["감리", "컨설팅", "정보시스템", "유지관리", "정보보호", "보안관제", "전산", "연구용역"]


def _keyword_scores(desc: str) -> dict[str, int]:
    scores = {t: sum(1 for kw in kws if kw in desc) for t, kws in _TYPE_KEYWORDS.items()}
    # 2026-06-02 F4-7: SVC 강신호 단어가 있으면 service 점수 +10 (CST에 "공사" 단어 있어도 service 우선)
    if any(kw in desc for kw in _SVC_STRONG):
        scores["service"] = scores.get("service", 0) + 10
    return scores


_TYPE_SYSTEM = """당신은 공공조달 계약유형 분류 전문가입니다.
사업개요를 보고 계약유형을 판정하세요: service(용역)·product(물품)·construction(공사).
- product: 물품 제조·구매·납품 (장비·기자재·자재)
- construction: 시설 공사·설치·시공 (건설·전기·정보통신·소방 등)
- service: 용역 (연구·관리·점검·설계·감리·정보화 등)
반드시 JSON: {"contract_type":"service|product|construction","confidence":0.0~1.0,"reason":"한 문장"}"""


@router.post("/contract-type", response_model=ContractTypeResponse)
async def suggest_contract_type(
    req: ContractTypeRequest,
    request: Request,
    llm: LLMProvider = Depends(get_llm),
    client_ip: str = Depends(rate_limit_llm),
    usage_logger=Depends(get_usage_logger),
) -> ContractTypeResponse:
    desc = req.description.strip()

    def _finish(resp: ContractTypeResponse, channel: str) -> ContractTypeResponse:
        # Q2 관측성 (2026-06-13): 분류 결정 채널(keyword/llm/llm_fallback)을 운영 로그에 기록.
        # 실서비스 룰/LLM 비율을 사후 집계 가능 (tools/aggregate_decision_channel.py).
        try:
            _ip, _ua = extract_client_meta(request)
            usage_logger.log_classify(
                description_len=len(desc), suggested=resp.suggested,
                decision_channel=channel, confidence=resp.confidence,
                client_ip=_ip, user_agent=_ua,
            )
        except Exception:
            pass
        return resp

    scores = _keyword_scores(desc)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    top_type, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0

    # 결정론: 최고 점수가 1+ 이고 2위와 명확히 차이나면 키워드로 확정
    if top_score >= 1 and top_score > second_score:
        total = sum(scores.values()) or 1
        cands = [
            ContractTypeCandidate(contract_type=t, label=_TYPE_LABEL[t], confidence=round(s / total, 2))
            for t, s in ranked if s > 0
        ]
        return _finish(ContractTypeResponse(
            suggested=top_type, confidence=round(min(0.95, 0.5 + top_score * 0.15), 2),
            reason=f"사업개요에 '{_TYPE_LABEL[top_type]}' 관련 키워드 {top_score}개 매칭",
            candidates=cands or [ContractTypeCandidate(contract_type=top_type, label=_TYPE_LABEL[top_type], confidence=0.6)],
            method="keyword",
        ), "keyword")

    # 애매(동률·무매칭) → LLM 보조
    try:
        record_llm_call(client_ip)  # 실제 LLM 호출 — IP별 + 전역 일일 상한 카운트
        raw = await llm.complete(_TYPE_SYSTEM, f"[사업개요]\n{desc}", json_mode=True)
        data = json.loads(raw)
        ct = data.get("contract_type")
        if ct not in _TYPE_LABEL:
            ct = top_type if top_score else "service"
        usage_logger.record_llm_success()
        return _finish(ContractTypeResponse(
            suggested=ct, confidence=float(data.get("confidence", 0.5)),
            reason=data.get("reason", "사업개요 기반 AI 추정"),
            candidates=[ContractTypeCandidate(contract_type=ct, label=_TYPE_LABEL[ct], confidence=float(data.get("confidence", 0.5)))],
            method="llm",
        ), "llm")
    except Exception as _llm_err:
        usage_logger.log_llm_failure(event="classify_type", error=str(_llm_err))
        ct = top_type if top_score else "service"
        return _finish(ContractTypeResponse(
            suggested=ct, confidence=0.4, reason="키워드 약함 — 기본 추정 (사용자 확인 권장)",
            candidates=[ContractTypeCandidate(contract_type=ct, label=_TYPE_LABEL[ct], confidence=0.4)],
            method="keyword",
        ), "llm_fallback")


@router.post("/product", response_model=ClassifyResponse)
async def classify_product(
    req: ClassifyRequest,
    llm: LLMProvider = Depends(get_llm),
    client_ip: str = Depends(rate_limit_llm),
) -> ClassifyResponse:
    catalog = _build_catalog_text()
    user_msg = f"""{catalog}

[사업개요]
{req.description}

[계약유형]
{req.contract_type}"""

    # F18 (2026-06-09): graceful fallback — LLM quota·504·JSON 실패 시 결정론 사전매칭만으로 응답.
    # 기존: HTTPException 500 → 사용자 발표 시연 중 분류 화면이 깨짐.
    # 이후: LLM 실패해도 품명 직접 매칭 + alias로 SME 후보 정상 노출.
    llm_fallback_reason: str | None = None
    try:
        record_llm_call(client_ip)  # 실제 LLM 호출 — IP별 + 전역 일일 상한 카운트
        raw = await llm.complete(_SYSTEM_PROMPT, user_msg, json_mode=True)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"g2b_candidates": [], "sme_candidates": [], "reasoning": ""}
            llm_fallback_reason = "LLM JSON 파싱 실패 — 품명 직접매칭만 적용"
    except Exception as _e:
        data = {"g2b_candidates": [], "sme_candidates": [], "reasoning": ""}
        llm_fallback_reason = f"LLM 일시 불가 ({type(_e).__name__}) — 품명 직접매칭만 적용"

    # 코드 검증 — LLM이 환각으로 만든 코드 거르기
    g2b_codes = {it["code"] for it in _load_g2b()}
    sme_all = _load_sme()
    sme_codes = {it["code"] for it in sme_all}

    sme_index = {it["code"]: it for it in sme_all}
    g2b_valid = [c for c in data.get("g2b_candidates", []) if c.get("code") in g2b_codes]
    sme_valid = [c for c in data.get("sme_candidates", []) if c.get("code") in sme_codes]
    # LLM 후보에도 고시 특이사항·공사용자재 대상 채움 (#30)
    for c in sme_valid:
        src = sme_index.get(c["code"], {})
        c["note"] = src.get("note", "")
        c["direct_purchase"] = src.get("direct_purchase", False)

    # #28: 결정론 사전매칭 — 품명이 사업개요에 그대로 포함되면 LLM 무관하게 항상 검출.
    # LLM 기반 추천의 비결정성(재시도·계약유형별 누락)을 보정. 결정론 후보를 최상위로.
    # #18: 별칭 사전 — 사용자가 자연 표현으로 입력하지만 품명이 다른 케이스.
    #   동영상제작서비스는 품명 그대로 사업개요에 안 들어가므로 '홍보용 동영상' 등으로.
    desc = req.description
    sme_by_code = {it["code"]: it for it in sme_all}
    det_sme: list[dict] = []
    seen: set[str] = set()
    for it in sme_all:
        if len(it["name"]) >= 2 and it["name"] in desc and it["code"] not in seen:
            det_sme.append({"code": it["code"], "name": it["name"], "confidence": 1.0,
                            "note": it.get("note", ""), "direct_purchase": it.get("direct_purchase", False)})
            seen.add(it["code"])
    for code, aliases in _SME_ALIASES.items():
        if code in seen:
            continue
        if any(a in desc for a in aliases):
            it = sme_by_code.get(code)
            if it:
                det_sme.append({"code": code, "name": it["name"], "confidence": 0.95,
                                "note": it.get("note", ""), "direct_purchase": it.get("direct_purchase", False)})
                seen.add(code)
    det_codes = {d["code"] for d in det_sme}
    merged_sme = det_sme + [c for c in sme_valid if c.get("code") not in det_codes]

    # 2026-07-16: g2b 후보 ↔ 중기간 목록 교차 확인 (결정론) — 두 목록은 같은 물품분류
    # 코드 체계(교집합 250개)인데 LLM이 sme_candidates를 비우면 중기간 지정품목이
    # '중기간 아님'으로 위음성 처리되던 문제 정정 (예: '정수장 계측기' → g2b 1순위
    # 종합계측기(지정품목)인데 is_sme_competition=false로 응답).
    merged_codes = {c.get("code") for c in merged_sme}
    for c in g2b_valid:
        code = c.get("code")
        if code in sme_codes and code not in merged_codes:
            src = sme_index.get(code, {})
            merged_sme.append({"code": code, "name": src.get("name", c.get("name", "")),
                               "confidence": c.get("confidence", 0.8),
                               "note": src.get("note", ""),
                               "direct_purchase": src.get("direct_purchase", False)})
            merged_codes.add(code)

    return ClassifyResponse(
        g2b_candidates=[ProductCandidate(**c) for c in g2b_valid[:3]],
        is_sme_competition=len(merged_sme) > 0,
        sme_candidates=[ProductCandidate(**c) for c in merged_sme[:5]],
        reasoning=(
            (llm_fallback_reason + " | " if llm_fallback_reason else "")
            + data.get("reasoning", "")
            + (" (품명 직접매칭 포함)" if det_sme else "")
        ),
    )


@router.get("/by-code/{code}", response_model=ClassifyByCodeResponse)
def lookup_by_code(
    code: str,
    estimated_price: int | None = None,
    contract_type: str | None = None,
) -> ClassifyByCodeResponse:
    """10자리 분류번호 → 중기간 경쟁제품 여부.

    `estimated_price`(원 단위)와 `contract_type`을 함께 주면 적용 심사기준까지 반환:
    - 추정가격 ≥ 고시금액(물품·용역 2.3억) → 조달청 525호
    - 추정가격 < 고시금액 → 중기부 기준
    - contract_type=service면서 SME → 직접발주 옵션 안내 추가
    """
    code = code.strip()
    sme_index = {it["code"]: it for it in _load_sme()}
    item = sme_index.get(code)
    if not item:
        return ClassifyByCodeResponse(
            code=code,
            is_sme_competition=False,
            description="중소기업자간 경쟁제품 지정 목록에 없습니다. 일반 경쟁입찰 적용 가능.",
        )

    # 고시금액 기준 (물품·용역 2.3억원) — thresholds.py 단일 소스
    THRESHOLD = ANNOUNCEMENT_LIMIT
    if estimated_price is not None and estimated_price >= THRESHOLD:
        standard: Literal["조달청", "중기부", "직접발주"] = "조달청"
        desc = (
            f"'{item['name']}'은(는) 중소기업자간 경쟁제품입니다. "
            f"추정가격이 고시금액(2.3억원) 이상이므로 **조달청에 위탁**하여 "
            f"「조달청 중소기업자간 경쟁물품에 대한 계약이행능력심사 세부기준(제525호)」을 적용합니다."
        )
    elif estimated_price is not None and estimated_price < THRESHOLD:
        standard = "중기부"
        desc = (
            f"'{item['name']}'은(는) 중소기업자간 경쟁제품입니다. "
            f"추정가격이 고시금액(2.3억원) 미만이므로 **중소벤처기업부 「중소기업자간 경쟁제품 중 "
            f"물품의 구매에 관한 계약이행능력심사 세부기준」**을 적용합니다."
        )
    else:
        standard = None
        desc = (
            f"'{item['name']}'은(는) 중소기업자간 경쟁제품입니다. "
            f"추정가격을 입력하면 적용 심사기준(조달청 vs 중기부)을 안내해 드립니다."
        )

    # 용역인 경우 직접발주 옵션 안내 추가
    if contract_type == "service":
        desc += " 용역인 경우 「공기업·준정부기관 계약사무규칙」에 따라 직접 발주도 가능합니다."

    # #30: 고시 특이사항·공사용자재 직접구매 대상 안내
    if item.get("note"):
        desc += f" ⚠️ 특이사항: {item['note']}"
    if item.get("direct_purchase"):
        desc += " 🏗️ 공사용자재 직접구매 대상 품목입니다(판로지원법 제12조)."

    return ClassifyByCodeResponse(
        code=code,
        name=item["name"],
        is_sme_competition=True,
        applicable_standard=standard,
        description=desc,
        note=item.get("note", ""),
        direct_purchase=item.get("direct_purchase", False),
    )


# F34 (2026-06-11): 중기간 경쟁제품 JSON 목록 (웹 검색 모달용)
# 사용자 의견 "중기간 물품목록을 웹에서 보여주고 검색할수있게" 정면 fix
@router.get("/sme-products/list")
def list_sme_products() -> dict:
    sme = _load_sme()
    items = [
        {
            "code": it.get("code", ""),
            "name": it.get("name", ""),
            "category": it.get("category", ""),
            "note": it.get("note", ""),
            "direct_purchase": bool(it.get("direct_purchase")),
        }
        for it in sme
    ]
    return {"total": len(items), "items": items}


# #23: 중기간 경쟁제품 리스트 다운로드 (발주 전 최우선 확인 — 공사용자재 직접구매 대상 포함)
@router.get("/sme-products/download")
def download_sme_products() -> Response:
    sme = _load_sme()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["분류번호", "품명", "특이사항", "공사용자재 직접구매 대상"])
    for it in sme:
        w.writerow([
            it.get("code", ""), it.get("name", ""),
            it.get("note", ""), "대상" if it.get("direct_purchase") else "",
        ])
    # ﻿(BOM) — Excel 한글 깨짐 방지
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sme_competition_products.csv"'},
    )


# F20-B1 (2026-06-10): xlsx 다운로드 — 인터넷 클라우드 PC 호환 (csv 열림 문제 해결)
@router.get("/sme-products/download.xlsx")
def download_sme_products_xlsx() -> Response:
    import io as _io
    from openpyxl import Workbook
    sme = _load_sme()
    wb = Workbook()
    ws = wb.active
    ws.title = "중기간 경쟁제품"
    ws.append(["분류번호", "품명", "특이사항", "공사용자재 직접구매 대상"])
    for it in sme:
        ws.append([
            it.get("code", ""),
            it.get("name", ""),
            it.get("note", ""),
            "대상" if it.get("direct_purchase") else "",
        ])
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 36
    ws.column_dimensions["C"].width = 50
    ws.column_dimensions["D"].width = 20
    bio = _io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return Response(
        content=bio.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="sme_competition_products.xlsx"'},
    )


# #24: 공공SW사업 법제도 가이드 다운로드 (SW 용역 발주 참고자료, 2025.11 최신본)
_SW_GUIDE = BASE_DIR / "reference" / "(배포본)_공공SW사업_법제도관리감독_및_지원_가이드(2025.11).pdf"


@router.get("/sw-guide/download")
def download_sw_guide() -> FileResponse:
    if not _SW_GUIDE.exists():
        raise HTTPException(404, "SW 법제도 가이드 파일을 찾을 수 없습니다.")
    return FileResponse(
        str(_SW_GUIDE),
        media_type="application/pdf",
        filename="공공SW사업_법제도관리감독_및_지원_가이드_2025.11.pdf",
    )


@router.post("/approval")
async def record_approval(req: ApprovalRequest) -> dict:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": req.session_id,
        "g2b_code": req.g2b_code,
        "sme_code": req.sme_code,
        "decision": req.decision,
        "reviewer_note": req.reviewer_note,
    }
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"status": "ok"}
