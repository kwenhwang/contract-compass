#!/usr/bin/env python3
"""MCP 도구 결정론 회귀 — LLM 없이 도구를 직접 두들겨 구조 성질을 검사한다.

mcp-tool-design §1-4의 2층 평가 중 '크론 가능한 층': LLM 평가(codexw 배터리)는
합성 품질을, 이 하네스는 **수리한 결함의 재발**을 잡는다. 전 케이스가 2026-07-30
실측으로 발견·수리된 결함의 R* 회귀다(미검증 문항 금지 — 스킬 §1-4 함정).

exit 0=전부 PASS / 1=회귀 존재 / 2=수집 실패(MCP 미도달)
사용: python3 tools/mcp_regression.py   (localhost:8403 — 루프백 무제한 티어)
"""
from __future__ import annotations

import json
import sys

import httpx

MCP = "http://localhost:8403/mcp"
H = {"Content-Type": "application/json",
     "Accept": "application/json, text/event-stream"}


class Session:
    def __init__(self) -> None:
        self.c = httpx.Client(timeout=60)
        r = self.c.post(MCP, headers=H, content=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "mcp-regression", "version": "1"}}}))
        r.raise_for_status()
        self.h = dict(H)
        sid = r.headers.get("mcp-session-id")
        if sid:
            self.h["mcp-session-id"] = sid
        self.c.post(MCP, headers=self.h, content=json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self._id = 10

    def call(self, tool: str, args: dict) -> dict:
        self._id += 1
        r = self.c.post(MCP, headers=self.h, content=json.dumps({
            "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}))
        r.raise_for_status()
        line = [l for l in r.text.splitlines() if l.startswith("data:")]
        d = json.loads(line[0][5:]) if line else json.loads(r.text)
        return json.loads(d["result"]["content"][0]["text"])


CASES = [
    # (id, tool, args, 검사 함수 — 2026-07-30 수리 결함과 1:1)
    ("R1-지역제한-150억",       # 룰 3분할·시행규칙 2026.4.24 개정 반영 회귀
     "search_law", {"query": "지방계약법 시행규칙 제24조", "top_k": 3},
     lambda d: any("150억" in h.get("content", "") for h in d.get("hits", []))),
    ("R2-부정당별표-개월수",     # 별표 PDF 적재(fetch_law_tables) 회귀
     "search_references", {"query": "담합한 자 제재기간", "top_k": 6},
     lambda d: any("개월" in h.get("excerpt", "") and "제한기준" in h.get("source", "") + h.get("section", "")
                   for h in d.get("hits", []))),
    ("R3-판례본문",             # 판례 라이브 프록시(law.go.kr·lawproxy) 회귀
     "get_case", {"kind": "prec", "case_id": "204256"},
     lambda d: "우수조달물품" in d.get("issue", "") and "제27조" in d.get("referenced_laws", "")),
    ("R4-긴쿼리-422회귀",        # search_law max_length 50→200 회귀
     "search_law", {"query": "적격심사 낙찰하한율 100억 미만 공사는 몇 퍼센트인지 아주 길게 묻는 검증용 질의문", "top_k": 3},
     lambda d: "error" not in d),
    ("R5-룰엔진-소액수의",       # decide 결정론(skip_llm) 경로 회귀
     "decide_contract_method", {"contract_type": "product", "estimated_price": 15000000,
                                "org_type": "local", "project_name": "회귀검사"},
     lambda d: any("수의" in (c.get("method") or "") for c in d.get("candidates", []))),
    ("R6-지명입찰-동의어",       # 지방계약법령 '지명입찰' 용어차 동의어 확장 회귀
     "search_references", {"query": "지자체 용역 지명경쟁 금액", "top_k": 8},
     lambda d: any("지명입찰" in h.get("excerpt", "") or "제22조" in h.get("excerpt", "")
                   for h in d.get("hits", []))),
    ("R7-절단-가시화",           # search_law 조용한 절단 금지(total_found·note)
     "search_law", {"query": "수의계약", "top_k": 3},
     lambda d: d.get("total_found", 0) >= d.get("count", 0) and
               (d.get("total_found") == d.get("count") or "note" in d)),
    ("R8-지방판정-국가근거혼입",   # decide 지방 3중 결함(국가룰 SVC_002 혼입·왜절 모순·
     "decide_contract_method",   # 20,000,001→"2,000만원" 반올림) 수리 회귀 — 2026-07-30
     {"contract_type": "service", "estimated_price": 20000001,
      "org_type": "local", "project_name": "회귀검사"},
     lambda d: (lambda t: "국가계약법" not in t and "국가를 당사자" not in t
                and "2,000만원" not in t
                and any((c.get("rule_id") or "").startswith("LOCAL")
                        for c in d.get("candidates", [])))(
                    json.dumps(d, ensure_ascii=False))),
    ("R9-판례본문미제공-가시화",   # 검색엔 뜨나 본문 미제공 판례가 빈 필드로 침묵하던
     "get_case", {"kind": "prec", "case_id": "417684"},   # 결함(배터리 제보) 수리 회귀
     lambda d: d.get("error") == "case_body_unavailable" and "hint" in d),
    ("R10-전자조달법-수록",       # 나라장터 투찰 질문에서 404였던 전자조달법 3종
     "get_law_article", {"ref": "전자조달법 제7조"},        # 법령팩 수록(배터리 업체-059) 회귀
     lambda d: "전자입찰" in d.get("content", "") and d.get("law_name") == "전자조달법"),
    ("R11-별표-무공백-계약미체결",  # 별표 PDF 무공백 추출로 '계약 미체결' 행이 검색
     "search_references",         # 불능이던 결함(배터리 업체-062) — 공백 복원+700자
     {"query": "계약을 체결 또는 이행하지 않은 자 부정당업자 제재기간", "top_k": 10},  # 청크 회귀
     lambda d: any("별표" in h.get("section", "") and "계약을 체결" in h.get("excerpt", "")
                   for h in d.get("hits", []))),
]


def main() -> int:
    try:
        s = Session()
    except Exception as e:  # noqa: BLE001
        print(f"[ERR ] MCP 미도달: {type(e).__name__}: {e}")
        return 2
    fails = 0
    for cid, tool, args, check in CASES:
        try:
            d = s.call(tool, args)
            ok = bool(check(d))
        except Exception as e:  # noqa: BLE001
            ok, d = False, {"exception": f"{type(e).__name__}: {e}"}
        print(f"[{'PASS' if ok else 'FAIL'}] {cid}")
        if not ok:
            fails += 1
            print(f"       └ {json.dumps(d, ensure_ascii=False)[:200]}")
    print(f"\n결과: PASS {len(CASES) - fails} / FAIL {fails} (총 {len(CASES)})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
