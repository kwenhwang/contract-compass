"""룰 legal_basis → law_registry 키 해석 회귀 테스트 (2026-07-28).

배경 — 두 층의 결함이 겹쳐 "비어 보이지 않는 오첨부"가 있었다.
  1. 레지스트리 키는 단축형("공기업계약사무규칙 제7조의2"), 룰 인용은 정식형
     ("공기업·준정부기관 계약사무규칙 제7조의2") → 포함 매칭이 실패.
  2. 실패하면 `resolve_registry_keys`가 method_law_keys 폴백으로 넘어가
     **다른 조문**(일반경쟁 제7조·제42조 등)을 의견서 "관계 법규 조문" 부록에 붙였다.
     laws_applied가 0이 아니라서 2026-07-16 점검(0건 탐지)에 걸리지 않았다.

이 테스트가 지키는 불변식:
  "레지스트리가 그 조문을 갖고 있으면, 그 조문을 인용한 룰은 반드시 그 키로 해석된다."
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.law_pack import resolve_registry_keys  # noqa: E402
from tools.lib.lawgo import norm_name  # noqa: E402

_ART = re.compile(r"제\d+조(?:의\d+)?")


@pytest.fixture(scope="module")
def rules() -> list[dict]:
    return json.loads((ROOT / "rules" / "contract_rules.json").read_text(encoding="utf-8"))["rules"]


@pytest.fixture(scope="module")
def registry() -> dict:
    return json.loads((ROOT / "rules" / "law_registry.json").read_text(encoding="utf-8"))["registry"]


def test_every_rule_resolves_some_law(rules):
    """laws_applied=0 이면 의견서에 조문 부록이 아예 안 붙는다 (2026-07-16 결함)."""
    zero = [r["rule_id"] for r in rules
            if not resolve_registry_keys(r.get("legal_basis") or [],
                                         (r.get("result") or {}).get("method", ""))]
    assert not zero, f"legal_basis→registry 해석이 빈 룰: {zero}"


def test_cited_article_resolves_when_registry_has_it(rules, registry):
    """레지스트리 보유 조문을 인용했으면 그 키로 해석돼야 한다 (폴백 오첨부 차단).

    레지스트리 코퍼스 밖 인용(계약예규·전기공사업법 등)은 대상이 아니다 — 그건
    문서화된 한계이지 결함이 아니다.
    """
    owners: dict[str, set[str]] = {}
    for key, entry in registry.items():
        for art in _ART.findall(key):
            owners.setdefault(art, set()).add(norm_name(entry.get("law_name", "")))

    failures = []
    for rule in rules:
        basis = rule.get("legal_basis") or []
        got = set(resolve_registry_keys(basis, (rule.get("result") or {}).get("method", "")))
        for text in basis:
            for art in _ART.findall(text):
                if any(art in k for k in got):
                    continue
                cited_law = norm_name(text.split(art)[0])
                in_corpus = any(
                    owner and (owner in cited_law or cited_law.endswith(owner))
                    for owner in owners.get(art, set())
                )
                if in_corpus:
                    failures.append(f"{rule['rule_id']}: '{text[:60]}' → {art} 미해석")
    assert not failures, "레지스트리 보유 조문인데 미해석(폴백이 다른 조문을 붙임):\n  " + \
        "\n  ".join(failures)


@pytest.mark.parametrize(("basis", "want_key"), [
    # 정식 법령명 인용 ↔ 단축형 레지스트리 키
    ("공기업·준정부기관 계약사무규칙 제7조의2", "공기업계약사무규칙 제7조의2"),
    ("공기업ㆍ준정부기관 계약사무규칙 제7조의2", "공기업계약사무규칙 제7조의2"),
    ("중소기업제품 구매촉진 및 판로지원에 관한 법률 제6조", "중소기업제품구매촉진법 제6조"),
    ("공공기관의 운영에 관한 법률 제44조", "공공기관운영법 제44조"),
    ("국가를 당사자로 하는 계약에 관한 법률 제7조", "국가계약법 제7조"),
    # 단축형 그대로도 계속 동작해야 한다
    ("공기업계약사무규칙 제7조의2", "공기업계약사무규칙 제7조의2"),
])
def test_key_aliases(basis, want_key):
    assert want_key in resolve_registry_keys([basis], "")


def test_article_boundary_not_confused():
    """제26조 인용이 제26조의2로, 또는 그 반대로 새지 않아야 한다."""
    got = resolve_registry_keys(["국가계약법 시행령 제2조 제3호 (고시금액 정의)"], "")
    assert "시행령 제2조" in got
    got2 = resolve_registry_keys(["판로지원법 시행령 제2조의2"], "")
    assert "판로지원법 시행령 제2조의2" in got2 and "시행령 제2조" not in got2


def test_no_rule_cites_repealed_article(registry):
    """레지스트리 본문이 '삭제'뿐인 조문은 인용 근거가 될 수 없다.

    (2026-07-28: PRD_006이 「국가계약법 시행령 제4조」를 국제입찰 근거로 인용했는데
     원문이 '제4조 삭제'였다. 국제입찰 범위는 국가계약법 제4조·시행령 제2조 제3호.)
    """
    dead = [k for k, v in registry.items()
            if all(re.fullmatch(r"제\d+조(?:의\d+)?\s*삭제\s*", a.get("body", ""))
                   for a in v.get("articles", []) if a.get("body"))
            and v.get("articles")]
    assert not dead, f"본문이 '삭제'인 레지스트리 항목: {dead}"
