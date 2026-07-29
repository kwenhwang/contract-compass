"""계약나침반 MCP 서버 — 계약방법 결정·법령 검색·계약 Q&A를 MCP 도구로 노출.

에이전트(Codex·Claude 등)가 stdio(로컬) 또는 Streamable HTTP(원격,
https://contract.naru.build/mcp)로 붙어 계약나침반 기능을 직접 호출한다.
대상 인스턴스는 env `CONTRACT_COMPASS_URL`(기본 로컬 백엔드 :8402 — CF 왕복과
ask 로그인 게이트의 엣지 IP 뭉침을 피한다).

실행: python3 mcp/server.py                  # stdio (로컬 검증·codex 등록용)
      python3 mcp/server.py streamable-http  # 원격 서빙 (systemd contract-mcp.service)
등록(codex): codex mcp add contract-compass -- python3 /path/to/mcp/server.py

ask 게이트 통과: 백엔드 /ask는 익명 IP당 2회/일 후 로그인을 요구한다(chat_access).
이 서버는 `SUPABASE_JWT_SECRET`(백엔드와 동일 값)으로 자체 HS256 JWT를 서명해
로그인 사용자로 통과한다 — 시크릿 미설정이면 익명 폴백(2회/일)으로 동작.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx
import jwt as _jwt
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

BASE_URL = os.environ.get("CONTRACT_COMPASS_URL", "http://127.0.0.1:8402").rstrip("/")
API = f"{BASE_URL}/api/v1"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _ask_headers() -> dict[str, str]:
    """ask 로그인 게이트(chat_access) 통과용 내부 JWT — 요청마다 서명(exp 5분)."""
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        return {}
    now = int(time.time())
    token = _jwt.encode(
        {"sub": "contract-mcp", "email": "mcp@internal", "aud": "authenticated",
         "iat": now, "exp": now + 300},
        secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}

# 전 도구 읽기전용 — 어노테이션이 없으면 codex(비대화)가 승인 대상으로 보고 자동 취소한다(2026-07-29 실측)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

server = MCPServer(
    name="contract-compass",
    title="계약나침반",
    instructions=(
        "한국 공공계약(국가계약법·지방계약법) 계약방법 결정 도우미. "
        "decide_contract_method로 결정론 룰엔진 판정을, ask_contract_question으로 "
        "법령 RAG 기반 Q&A를, search_law로 조문 원문을 조회한다. "
        "도구는 한 번에 하나씩 순차 호출하라 — 특히 ask_contract_question을 병렬로 "
        "여러 개 쏘면 지연이 누적돼 클라이언트 타임아웃으로 실패한다. "
        "도구가 {'error': ...}를 반환하면 그 hint를 따르고, 도구 근거 없이 "
        "자체 지식으로 법령 수치를 단정하지 마라. "
        "답변은 정보 제공용이며 법적 자문이 아니다."
    ),
    version="1.0.0",
)


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
def ask_contract_question(question: str) -> dict:
    """공공계약 Q&A — 법령·계약예규·공공계약 실무가이드 RAG 검색 + AI 답변.

    Args:
        question: 자연어 질문 (예: "소액수의계약 금액 한도는?")
    """
    d = _post("/ask", {"question": question}, headers=_ask_headers())
    if _is_error(d):
        return d
    return {
        "answer": d.get("answer", ""),
        "sources": [
            {
                "title": s.get("section_title"),
                "type": s.get("source_type"),
                "excerpt": (s.get("excerpt") or s.get("content") or "")[:300],
            }
            for s in (d.get("sources") or [])[:6]
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
                          "재검색하거나, ask_contract_question으로 질문하라.")
    return result


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


if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "streamable-http":
        server.run("streamable-http",
                   host="0.0.0.0",
                   port=int(os.environ.get("MCP_PORT", "8403")),
                   stateless_http=True)
    else:
        server.run()  # stdio — codex 등록·로컬 검증 경로 유지
