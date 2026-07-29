"""지자체 룰별 법령셋 → LLM 컨텍스트 빌더 (2026-06-14, SaaS 결정론 근거형).

룰엔진이 매칭한 지자체 룰(LOCAL_*)의 **법령셋**(지방계약법 시행령 조문 + 행안부 예규 별표
+ 낙찰하한율)을 LLM에 그대로 전달하기 위한 자료팩 빌더. 국가 law_pack의 지방 버전.

설계 원칙(law_pack과 동일):
- RAG 검색이 아니라 **룰 case → 결정론 lookup**으로 법령셋 제공(같은 입력 → 같은 컨텍스트).
- LLM은 이 법령셋 안에서만 근거를 인용(환각 차단). 최종 method/낙찰하한율은 룰값.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PACK = Path(__file__).resolve().parent.parent.parent / "rules" / "local_law_pack.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(_PACK.read_text(encoding="utf-8"))


def list_rules() -> list[str]:
    return list(_load().get("packs", {}).keys())


def get_pack(rule_id: str) -> dict | None:
    return _load().get("packs", {}).get(rule_id)


def build_llm_context(rule_id: str) -> str:
    """룰의 법령셋을 LLM 결정론 컨텍스트(문자열)로 조립. 없으면 빈 문자열."""
    pack = get_pack(rule_id)
    if not pack:
        return ""
    lines = [f"[적용 룰] {rule_id} — {pack.get('설명', '')}",
             f"[근거조문] {pack.get('근거조문', '')}".rstrip()]
    for art in pack.get("법령셋", []):
        if art:
            lines.append(f"\n◦ {art['source']}\n{art['body']}")
    eg = pack.get("예규_별표")
    if eg:
        cands = ", ".join(f"{c['낙찰하한율']}%(배점{c.get('입찰가격_배점')})"
                          for c in eg.get("낙찰하한율_후보", []))
        lines.append(f"\n◦ {eg['source']}\n낙찰하한율(평점산식): {cands or '확인필요'}")
    if pack.get("낙찰하한율"):
        lines.append(f"\n[낙찰하한율] {pack['낙찰하한율']}%")
    lines.append("\n※ 위 법령셋 범위 내에서만 근거 인용. 계약방법·낙찰하한율 최종값은 룰엔진 결정값을 따른다.")
    return "\n".join(lines)
