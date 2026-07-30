"""계약나침반 MCP 서버 — 계약방법 결정·법령/코퍼스 검색을 MCP 도구로 노출.

에이전트(Codex·Claude 등)가 stdio(로컬) 또는 Streamable HTTP(원격,
https://contract.sallim.app/mcp — 별칭 https://contract.naru.build/mcp)로 붙어 계약나침반 기능을 직접 호출한다.
대상 인스턴스는 env `CONTRACT_COMPASS_URL`(기본 로컬 백엔드 :8402 — CF 왕복 회피).

설계 원칙(2026-07-30): MCP 도구는 전부 **무LLM** — 클라이언트가 이미 LLM이므로
백엔드는 결정론 판정(decide, skip_llm)과 검색 원문(search_*)만 제공한다.
백엔드 OpenAI를 태우던 ask 도구는 제거(웹 UI 전용 /ask는 그대로).

실행: python3 mcp/server.py                  # stdio (로컬 검증·codex 등록용)
      python3 mcp/server.py streamable-http  # 원격 서빙 (systemd contract-mcp.service)
등록(codex): codex mcp add contract-compass -- python3 /path/to/mcp/server.py
"""
from __future__ import annotations

import json as _json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from mcp import types as mcp_types
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from auth import PRICING_URL, DailyQuota, resolve_access  # noqa: E402 — mcp/ 로컬 모듈

BASE_URL = os.environ.get("CONTRACT_COMPASS_URL", "http://127.0.0.1:8402").rstrip("/")
API = f"{BASE_URL}/api/v1"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_ROOT = Path(__file__).resolve().parents[1]
_quota = DailyQuota(_ROOT / "logs" / "mcp_quota.json")
_CALL_LOG = _ROOT / "logs" / "mcp_calls.jsonl"


def _denied_result(payload: dict) -> mcp_types.CallToolResult:
    """게이트 거부를 도구 결과(dict)로 반환 — 예외가 아니라 데이터.

    에이전트가 message를 그대로 사용자에게 읽어주는 realty-mcp 검증 패턴.
    isError=False 유지: 오류 채널로 보내면 클라이언트가 재시도만 반복한다(실측)."""
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text",
                                       text=_json.dumps(payload, ensure_ascii=False, indent=2))],
        structured_content=payload,
    )


class QuotaGate:
    """tools/call 단위 티어 게이트 + JSONL 호출 로그 (ServerMiddleware, 2026-07-30).

    stdio(ctx.request=None)는 local 티어로 무제한 — 야간 QA·codexw 하네스 보호.
    원격(streamable-http)은 무료 IP당 FREE_DAILY, cc_live_* 키는 키당 한도."""

    async def __call__(self, ctx, call_next):
        if ctx.method != "tools/call":
            return await call_next(ctx)
        tool = (ctx.params or {}).get("name", "?")
        access = resolve_access(ctx.request)
        if access.error:
            return _denied_result(access.error)
        if access.daily_limit is not None and not _quota.consume(access.subject, access.daily_limit):
            return _denied_result({
                "error": "daily_limit_exceeded",
                "message": f"무료 한도(하루 {access.daily_limit}콜)를 모두 사용했습니다. "
                           f"내일(UTC 자정) 리셋되며, 더 필요하면 유료 키 안내: {PRICING_URL}",
                "hint": "이 사실을 사용자에게 알리고 오늘은 추가 조회를 멈춰라.",
            })
        t0 = time.time()
        result = await call_next(ctx)
        try:
            _CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _CALL_LOG.open("a", encoding="utf-8") as f:
                f.write(_json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": tool,
                    "tier": access.tier, "subject": access.subject,
                    "dur_ms": round((time.time() - t0) * 1000),
                }, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 — 계측 실패가 호출을 막지 않는다
            pass
        return result

# 전 도구 읽기전용 — 어노테이션이 없으면 codex(비대화)가 승인 대상으로 보고 자동 취소한다(2026-07-29 실측)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# report_issue 전용 — 유일한 쓰기 도구. 정직하게 non-readonly로 달아 대화형 클라이언트가
# 사용자 승인을 받게 한다(비대화 codex는 자동 취소될 수 있음 — 제보는 선택 기능이라 허용).
WRITE_FEEDBACK = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

server = MCPServer(
    name="contract-compass",
    title="AI 계약나침반 (Contract Compass)",
    instructions=(
        "한국 공공계약(국가계약법·지방계약법) 계약방법 결정 도우미. "
        "decide_contract_method로 결정론 룰엔진 판정을, search_law·get_law_article로 "
        "법령 조문을, search_references로 예규·적격심사 세부기준·실무가이드까지 "
        "전 코퍼스를 조회한다. 분쟁·처분·해석 다툼 질문은 search_cases/get_case로 "
        "판례·법령해석례(law.go.kr 실시간)를 찾아 근거를 보강하라. "
        "모든 도구는 LLM을 쓰지 않으며 호출당 1~3초다 — 답변 합성은 "
        "네(클라이언트)가 도구 근거로 직접 하라. 판례·해석례가 필요한 질문인지는 "
        "네가 판단하되, 인용했다면 판례의 참조조문을 get_law_article로 교차확인하라. "
        "2~3개 병렬 호출은 무방하나 다발(4개 이상 동시) 호출은 피하라"
        "(단일 워커 백엔드라 대기 누적으로 타임아웃). "
        "도구가 {'error': ...}를 반환하면 그 hint를 따르고, 도구 근거 없이 "
        "자체 지식으로 법령 수치를 단정하지 마라. 도구 결과가 명백히 틀렸거나 "
        "사용자가 오류를 지적하면 report_issue로 제보하라(사용자에게 알리고). "
        "답변은 정보 제공용이며 법적 자문이 아니다."
    ),
    version="1.1.0",
)
server.middleware.append(QuotaGate())  # tools/call 티어 게이트 + 호출 로그


def _friendly_error(exc: Exception) -> dict:
    """백엔드 오류를 에이전트가 이해·중계할 수 있는 구조화 dict로.

    기존엔 httpx 예외가 그대로 도구 실패로 터져 에이전트가 원인을 모른 채
    재시도하다 자체 지식으로 조용히 폴백했다(2026-07-30 실측) — 원인·행동지침을 명시한다.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            detail = exc.response.json().get("detail", {})
        except Exception:  # noqa: BLE001 — 비JSON 응답은 코드만 전달
            detail = {}
        code = detail.get("error") if isinstance(detail, dict) else None
        if code == "daily_cap_exceeded":
            return {"error": "daily_cap_exceeded", "status": status,
                    "message": detail.get("message", "일일 AI 이용량 소진"),
                    "hint": "오늘은 AI 답변 예산이 소진됨(매일 09:00 KST 리셋). "
                            "이 사실을 사용자에게 알리고, search_law·get_law_article"
                            "(LLM 미사용)로 조문 근거만 제시하라."}
        if code == "rate_limit_exceeded":
            return {"error": "rate_limit_exceeded", "status": status,
                    "message": "요청 빈도 한도 초과",
                    "hint": f"{detail.get('retry_after', 60)}초 후 재시도하라. 병렬 호출 금지."}
        return {"error": "backend_error", "status": status,
                "message": str(detail or exc)[:300],
                "hint": "요청 인자를 바꿔도 같은 오류면 사용자에게 오류를 알려라."}
    if isinstance(exc, httpx.TimeoutException):
        return {"error": "timeout", "message": "백엔드 응답 지연(60초 초과)",
                "hint": "도구를 병렬로 여러 개 호출하면 지연이 누적된다 — 한 번에 하나씩 순차 호출하라."}
    return {"error": "connection_error", "message": str(exc)[:300],
            "hint": "백엔드 미도달 — 잠시 후 1회만 재시도하고, 실패 지속 시 사용자에게 알려라."}


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.get(f"{API}{path}", params=params)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        return _friendly_error(exc)


def _post(path: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(f"{API}{path}", json=body, headers=headers)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        return _friendly_error(exc)


def _is_error(d: Any) -> bool:
    return isinstance(d, dict) and "error" in d


@server.tool(annotations=READ_ONLY)
def decide_contract_method(
    contract_type: str,
    estimated_price: int,
    org_type: str = "public_corp",
    service_type: str | None = None,
    construction_specialty: str | None = None,
    is_sme_competition_product: bool = False,
    negotiation_reason: str | None = None,
    project_name: str = "MCP 조회",
) -> dict:
    """계약방법 결정론 판정 — 룰엔진이 적용 가능한 계약방법 후보와 법령 근거를 반환.

    Args:
        contract_type: "construction"(공사) | "service"(용역) | "product"(물품)
        estimated_price: 추정가격(원)
        org_type: "national"(국가기관) | "local"(지자체) | "public_corp"(공기업·준정부, 기본)
        service_type: 용역일 때 "technical"|"academic"|"facility"|"it_service"|"other"
        construction_specialty: 공사일 때 "general"(종합)|"electrical"|"ict"|"fire_safety" 등
        is_sme_competition_product: 중소기업자간 경쟁제품 여부
        negotiation_reason: 수의 사유 "urgent"|"rebid_failure"|"technical_difficulty"|
            "patent_new_tech"|"specific_person"|"small_repeat"|"other_justified"
    """
    body: dict[str, Any] = {
        "contract_type": contract_type,
        "estimated_price": estimated_price,
        "org_type": org_type,
        "is_sme_competition_product": is_sme_competition_product,
        "project_name": project_name,
        # 에이전트 클라이언트는 자체 LLM으로 설명을 합성 — 백엔드 LLM 보조설명 생략
        # (판정 결과·법령 근거는 동일, OpenAI 일일 예산 0 소모. 2026-07-30)
        "skip_llm": True,
    }
    if service_type:
        body["service_type"] = service_type
    if construction_specialty:
        body["construction_specialty"] = construction_specialty
    if negotiation_reason:
        body["negotiation_reason"] = negotiation_reason
    d = _post("/filter/step1", body)
    if _is_error(d):
        return d
    # 에이전트가 소화하기 좋은 축약 형태로 정리
    return {
        "candidates": [
            {
                "rank": c.get("rank"),
                "method": c.get("method"),
                "rule_id": c.get("rule_id"),
                "summary": c.get("summary"),
                "key_params": c.get("key_params"),
                "legal_basis": c.get("legal_basis"),
            }
            for c in d.get("candidates", [])
        ],
        "practice_alternatives": d.get("practice_alternatives", []),
        "explanation": (d.get("decision_pack") or {}).get("human_explanation", ""),
        "laws_applied": [
            {"key": l.get("key"), "law_name": l.get("law_name")}
            for l in (d.get("decision_pack") or {}).get("laws_applied", [])
        ],
        "follow_up_questions": [
            {"id": q.get("id"), "text": q.get("text"), "description": q.get("description")}
            for q in d.get("next_step_questions", [])
        ],
    }


@server.tool(annotations=READ_ONLY)
def search_law(query: str, top_k: int = 8) -> dict:
    """법령 조문 검색 — 키워드 또는 조문번호로 조문 스니펫 반환(상위 top_k건).

    전문이 필요하면 get_law_article(ref)로 이어서 조회.

    Args:
        query: "수의계약", "시행령 제26조", "제21조" 등
        top_k: 반환 건수 (기본 8, 최대 20)
    """
    hits = _get("/law/search", {"q": query})
    if _is_error(hits):
        return hits
    out = []
    for h in hits[: max(1, min(top_k, 20))]:
        h = dict(h)
        for k in ("content", "snippet"):
            if isinstance(h.get(k), str) and len(h[k]) > 400:
                h[k] = h[k][:400] + "…"
        out.append(h)
    result: dict[str, Any] = {"hits": out, "count": len(out)}
    if not out:
        # 0건은 오류가 아니라 재질의 신호 — 에이전트가 "실패"로 오독하고 자체 지식으로
        # 빠지지 않게 다음 행동을 명시한다(2026-07-30, 복합 쿼리 0건 6/12 실측).
        result["hint"] = ("0건 — 짧은 단일 키워드('수의계약')나 '법령명 제N조' 형태로 "
                          "재검색하거나, search_references로 예규·가이드까지 넓혀 찾아라.")
    return result


@server.tool(annotations=READ_ONLY)
def search_references(query: str, top_k: int = 6) -> dict:
    """전 코퍼스 통합 검색 — 법령+계약예규+조달청·행안부 세부기준+실무가이드. LLM 미사용.

    search_law가 법령 조문 전용인 것과 달리 예규·적격심사 세부기준·실무가이드까지
    검색한다. 낙찰하한율·적격심사 배점·실무 절차 등 법령 본문 밖 질문에 사용하라.
    AI 생성 없이 검색 근거 원문만 반환한다(백엔드 LLM 예산 미차감).

    Args:
        query: 자연어 검색어 (예: "적격심사 낙찰하한율 50억 미만")
        top_k: 반환 건수 (기본 6, 최대 12)
    """
    hits = _get("/law/references", {"q": query, "top_k": max(1, min(top_k, 12))})
    if _is_error(hits):
        return hits
    result: dict[str, Any] = {"hits": hits, "count": len(hits)}
    if not hits:
        result["hint"] = "0건 — 핵심 명사 위주로 짧게 재검색하거나 search_law로 조문을 직접 찾아라."
    return result


@server.tool(annotations=READ_ONLY)
def search_cases(query: str, top_k: int = 5, kind: str = "all") -> dict:
    """판례·법령해석례 검색 — law.go.kr 실시간 조회(항상 현행). LLM 미사용.

    분쟁·처분취소·해석 다툼("~해도 되나", "~취소될 수 있나")에 조문만으로 부족할 때
    쓰라. 본문은 get_case(kind, case_id)로 이어서 조회.

    Args:
        query: 핵심 명사 위주 검색어 (예: "부정당업자 제한", "유찰 수의계약")
        top_k: 종류당 반환 건수 (기본 5, 최대 10)
        kind: "prec"(법원 판례) | "expc"(법제처 법령해석례) | "all"(둘 다, 기본)
    """
    hits = _get("/law/cases", {"q": query, "top_k": max(1, min(top_k, 10)), "kind": kind})
    if _is_error(hits):
        return hits
    result: dict[str, Any] = {"hits": hits, "count": len(hits)}
    if not hits:
        result["hint"] = "0건 — 더 짧은 핵심어(예: '지체상금', '담합')로 재검색하라."
    return result


@server.tool(annotations=READ_ONLY)
def get_case(kind: str, case_id: str) -> dict:
    """판례/해석례 본문 조회 — 판시사항·판결요지·참조조문(판례) 또는 질의요지·회답·이유(해석례).

    Args:
        kind: "prec" | "expc" (search_cases 결과의 kind)
        case_id: search_cases 결과의 case_id
    """
    return _get("/law/case", {"kind": kind, "case_id": case_id})


@server.tool(annotations=READ_ONLY)
def get_law_article(ref: str) -> dict:
    """법령 조문 원문 전체 조회.

    Args:
        ref: 정확한 조문 참조 (예: "국가계약법 시행령 제26조")
    """
    d = _get("/law/article", {"ref": ref})
    if _is_error(d):
        return d
    if isinstance(d.get("content"), str) and len(d["content"]) > 6000:
        d["content"] = d["content"][:6000] + "…(생략)"
    return d


@server.tool(annotations=WRITE_FEEDBACK)
def report_issue(
    category: str,
    message: str,
    related_tool: str | None = None,
    related_query: str | None = None,
    expected: str | None = None,
) -> dict:
    """오류·개선 제보 — 운영자에게 전달된다(웹 피드백과 같은 검토 파이프라인).

    도구 결과가 명백히 틀렸거나(조문·수치·판례 불일치), 사용자가 오류를 지적하거나,
    필요한 기능이 없을 때 사용하라. 제보 전 사용자에게 보낼 내용을 알리는 것을 권장.

    Args:
        category: "wrong_citation"(오인용) | "outdated_law"(개정 미반영) |
            "wrong_ruling"(룰엔진 오판정) | "tool_error"(도구 오류) |
            "feature_request"(기능 요청) | "other"
        message: 무엇이 어떻게 잘못됐는지 구체적으로 (근거 조문·기대값 포함 권장)
        related_tool: 문제가 난 도구명 (예: "search_references")
        related_query: 문제를 재현하는 질의·입력
        expected: 올바르다고 생각하는 값·조문 (알고 있다면)
    """
    cats = {"wrong_citation", "outdated_law", "wrong_ruling", "tool_error",
            "feature_request", "other"}
    if category not in cats:
        return {"error": "bad_category", "message": f"category는 {sorted(cats)} 중 하나"}
    if not message or len(message.strip()) < 10:
        return {"error": "too_short", "message": "message에 문제 상황을 10자 이상 구체적으로 적어라"}
    comment = f"[MCP:{category}] {message.strip()[:2000]}"
    if related_tool:
        comment += f"\n도구: {related_tool[:100]}"
    if expected:
        comment += f"\n기대값: {expected[:500]}"
    import uuid as _uuid
    body = {
        "session_id": f"mcp-{_uuid.uuid4().hex[:12]}",
        "rating": "3" if category == "feature_request" else "1",
        "comment": comment,
        "feedback_type": "general",
        "question": (related_query or "")[:1000],
        "page": "MCP",
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(f"{API}/feedback", data=body)  # 웹과 동일 엔드포인트(Form)
            r.raise_for_status()
    except httpx.HTTPError as exc:
        return _friendly_error(exc)
    return {"ok": True,
            "message": "제보가 접수됐습니다. 운영자가 검토 후 반영하며, 처리 현황은 "
                       "웹 의견 파이프라인에서 관리됩니다. 감사합니다."}


async def _health(request):  # noqa: ANN001 — Starlette Request
    """앱+백엔드 도달을 한 번에 판정한다(Kuma·배포 게이트 규약)."""
    from starlette.responses import JSONResponse
    try:
        with httpx.Client(timeout=httpx.Timeout(5.0)) as c:
            backend = c.get(f"{BASE_URL}/ready").status_code
    except httpx.HTTPError:
        backend = 0
    ok = backend == 200
    return JSONResponse({"status": "ok" if ok else "degraded", "backend_ready": backend},
                        status_code=200 if ok else 503)


# 외부는 nginx가 /mcp* 만 이 서버로 넘긴다 — /mcp/health가 외부 감시 경로다.
for _hp in ("/health", "/mcp/health"):
    server.custom_route(_hp, methods=["GET"], include_in_schema=False)(_health)


_PRICING_HTML = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>계약나침반 MCP — 요금 안내</title>
<body style="font-family:system-ui,sans-serif;max-width:44rem;margin:4rem auto;padding:0 1rem;line-height:1.65">
<h1>🧭 계약나침반 MCP 요금 안내</h1>
<p>한국 공공계약 법령·판례 MCP 서버 — 모든 도구는 LLM 없이 검증 가능한 법적 근거만
반환합니다. 무료로 전 도구를 쓸 수 있고, 유료 키는 <b>한도만</b> 올립니다(기능 차이 없음).</p>
<table border="1" cellpadding="8" style="border-collapse:collapse;width:100%">
<tr><th>티어</th><th>일일 한도</th><th>기능</th><th>가격</th></tr>
<tr><td>무료</td><td>IP당 50콜 (UTC 자정 리셋)</td><td>도구 6종 전부</td><td>0원</td></tr>
<tr><td>체험 키</td><td>키당 2,000콜</td><td>동일</td><td>7일 1,000원</td></tr>
<tr><td>PRO 키</td><td>키당 2,000콜</td><td>동일 + 우선 지원</td><td>30일 9,900원 · 90일 24,900원</td></tr>
</table>
<p><b>카드결제</b>(법인·개인·정부구매카드, Visa/Master) — 결제 즉시 라이선스 키가
이메일로 자동 발송되고 영수증(인보이스)도 함께 발행됩니다. 자동결제(구독) 없음 —
기간 만료 시 무료 티어로 자연 복귀합니다. 기관 구매·세금계산서 등 별도 서류가
필요하면 <b>contract@sallim.app</b>으로 문의해 주세요.</p>
<h2>연결 방법</h2>
<pre style="background:#f4f4f5;padding:12px;border-radius:8px;overflow-x:auto">
# Claude Code
claude mcp add --transport http contract-compass https://contract.sallim.app/mcp

# Cursor (.cursor/mcp.json)
{ "mcpServers": { "contract-compass": { "url": "https://contract.sallim.app/mcp" } } }

# (별칭: https://contract.naru.build/mcp — 기존 등록 사용자용, 계속 동작)

# 유료 키 사용 시 (헤더)
Authorization: Bearer cc_live_...        # 또는 URL 뒤 ?key=cc_live_... (ChatGPT 커넥터)
</pre>
<p style="color:#666;font-size:.9rem">데이터 출처: 국가법령정보센터(law.go.kr) Open API·
기획재정부 계약예규·조달청/행안부 세부기준·감사원 공개 간행물. 모든 응답은 정보 제공
목적이며 법적 자문이 아닙니다. 도구 명세:
<a href="https://github.com/kwenhwang/contract-compass/blob/master/docs/MCP.md">docs/MCP.md</a></p>
</body></html>"""


async def _pricing(request):  # noqa: ANN001
    from starlette.responses import HTMLResponse
    return HTMLResponse(_PRICING_HTML)


for _pp in ("/pricing", "/mcp/pricing"):
    server.custom_route(_pp, methods=["GET"], include_in_schema=False)(_pricing)


# ── Lemon Squeezy 구매 웹훅 (2026-07-30) ─────────────────────────────────────
# LS License Keys가 결제 시 키 생성·고객 이메일 발송까지 담당 — 이 웹훅은 그 키를
# 대장(keystore)에 미러해 기존 sha256 인증 경로(auth.py 무수정)로 수용하고,
# 주문·금액·고객을 매출 대장에 자동 기록한다. 환불류 이벤트는 키 회수.
# 시크릿(LEMONSQUEEZY_WEBHOOK_SECRET) 미설정이면 503 — 계정 연결 전 비활성 상태.
_LS_PLANS_ENV = "LS_VARIANT_PLANS"  # JSON: {"variant_id": {"days":30,"daily":2000,"amount_krw":9900,"label":"PRO 30일"}}


def _ls_plans() -> dict:
    try:
        return _json.loads(os.environ.get(_LS_PLANS_ENV, "") or "{}")
    except Exception:
        return {}


async def _purchase_webhook(request):  # noqa: ANN001
    import hashlib as _hl
    import hmac as _hmac

    from starlette.responses import JSONResponse
    secret = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "")
    if not secret:
        return JSONResponse({"error": "webhook_disabled"}, status_code=503)
    raw = await request.body()
    sig = request.headers.get("x-signature", "")
    expect = _hmac.new(secret.encode(), raw, _hl.sha256).hexdigest()
    if not _hmac.compare_digest(sig, expect):
        return JSONResponse({"error": "bad_signature"}, status_code=401)

    import keystore
    try:
        payload = _json.loads(raw)
    except Exception:
        return JSONResponse({"error": "bad_json"}, status_code=400)
    event = (payload.get("meta") or {}).get("event_name", "")
    attrs = (payload.get("data") or {}).get("attributes", {}) or {}

    if event == "license_key_created":
        ls_key = attrs.get("key", "")
        if not ls_key:
            return JSONResponse({"error": "no_key"}, status_code=400)
        variant = str(attrs.get("variant_id") or (attrs.get("order_item") or {}).get("variant_id") or "")
        plan = _ls_plans().get(variant, {})
        _, rec = keystore.issue(
            name=plan.get("label", f"LS variant {variant}"),
            days=int(plan.get("days", 30)),
            daily=int(plan.get("daily", 2000)),
            channel="lemonsqueezy",
            amount_krw=int(plan.get("amount_krw", 0)),
            contact=attrs.get("user_email", "") or attrs.get("customer_email", ""),
            order_id=f"ls-{attrs.get('order_id', payload.get('data', {}).get('id', ''))}",
            key=ls_key, source="ls_mirror",
        )
        return JSONResponse({"ok": True, "key_prefix": rec["key_prefix"]})

    if event in ("order_refunded", "subscription_expired", "subscription_cancelled",
                 "license_key_updated"):
        # license_key_updated는 비활성화(disabled) 신호일 때만 회수
        if event == "license_key_updated" and attrs.get("status") not in ("disabled", "inactive"):
            return JSONResponse({"ok": True, "ignored": True})
        order_ref = f"ls-{attrs.get('order_id', payload.get('data', {}).get('id', ''))}"
        rec = keystore.revoke_by_order(order_ref)
        return JSONResponse({"ok": True, "revoked": bool(rec)})

    return JSONResponse({"ok": True, "ignored": event})


for _wp in ("/purchase-webhook", "/mcp/purchase-webhook"):
    server.custom_route(_wp, methods=["POST"], include_in_schema=False)(_purchase_webhook)


if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "streamable-http":
        server.run("streamable-http",
                   host="0.0.0.0",
                   port=int(os.environ.get("MCP_PORT", "8403")),
                   stateless_http=True)
    else:
        server.run()  # stdio — codex 등록·로컬 검증 경로 유지
