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
        "답변은 정보 제공용이며 법적 자문이 아니다."
    ),
    version="1.0.0",
)


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.get(f"{API}{path}", params=params)
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.post(f"{API}{path}", json=body, headers=headers)
        r.raise_for_status()
        return r.json()


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
def search_law(query: str, top_k: int = 8) -> list[dict]:
    """법령 조문 검색 — 키워드 또는 조문번호로 조문 스니펫 반환(상위 top_k건).

    전문이 필요하면 get_law_article(ref)로 이어서 조회.

    Args:
        query: "수의계약", "시행령 제26조", "제21조" 등
        top_k: 반환 건수 (기본 8, 최대 20)
    """
    hits = _get("/law/search", {"q": query})
    out = []
    for h in hits[: max(1, min(top_k, 20))]:
        h = dict(h)
        for k in ("content", "snippet"):
            if isinstance(h.get(k), str) and len(h[k]) > 400:
                h[k] = h[k][:400] + "…"
        out.append(h)
    return out


@server.tool(annotations=READ_ONLY)
def get_law_article(ref: str) -> dict:
    """법령 조문 원문 전체 조회.

    Args:
        ref: 정확한 조문 참조 (예: "국가계약법 시행령 제26조")
    """
    d = _get("/law/article", {"ref": ref})
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
