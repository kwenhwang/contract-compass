"""법령 레지스트리 ↔ 원문 XML 정합 회귀 테스트 (2026-07-17).

배경: 적대 점검에서 레지스트리 본문 전수가 요약·재작성문인데 공포번호를 붙여
원문처럼 표시되던 결함 발견(제26조는 호 번호까지 실제와 상이). 원문 교체 후,
이 테스트가 이후 드리프트(법령 개정 시 XML만 갱신하고 레지스트리 방치 등)를 차단.

원리: 레지스트리 body(공백 제거)가 대응 XML 조문 원문(공백 제거)에 부분 문자열로
존재해야 한다. XML이 개정되어 문구가 바뀌면 즉시 실패 → 레지스트리 재생성 신호.

2026-07-28: 키↔XML 매핑과 본문 정규화를 `tools/build_law_registry.py`(생성기)로
이관하고 여기서 import한다 — 사본이 갈라져 "테스트는 통과하는데 생성기 출력은 다른"
상태를 막는다. 정규화 규칙 자체의 회귀는 `test_no_duplicate_markers`가 독립 검증한다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.build_law_registry import (  # noqa: E402
    LAWS, NON_XML_KEYS, REGISTRY_SOURCES, article, promulgation,
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


@pytest.fixture(scope="module")
def registry() -> dict:
    return json.loads((ROOT / "rules" / "law_registry.json").read_text(encoding="utf-8"))["registry"]


@pytest.mark.parametrize("key", sorted(REGISTRY_SOURCES))
def test_registry_body_matches_statute_original(registry, key):
    fname, jo, br = REGISTRY_SOURCES[key]
    xml_path = LAWS / fname
    if not xml_path.exists():
        pytest.skip(f"원문 XML 없음: {fname}")
    orig = _norm(article(xml_path, jo, br)[1])
    entry = registry[key]
    assert entry.get("articles"), f"{key}: articles 비어 있음"
    for art in entry["articles"]:
        body = _norm(art.get("body", ""))
        assert body, f"{key}: body 비어 있음"
        assert body in orig, (
            f"{key}: 레지스트리 본문이 원문과 불일치 — 법령 개정 후 레지스트리 미갱신이거나 "
            f"요약본 회귀. `python3 tools/build_law_registry.py`로 재생성할 것."
        )


@pytest.mark.parametrize("key", sorted(REGISTRY_SOURCES))
def test_registry_promulgation_matches_xml(registry, key):
    """공포정보도 원문에서 파생돼야 한다 — 의견서에 인쇄되는 값이다.

    (2026-07-28: 개정된 XML을 받고도 promulgation 문자열을 수기로 두던 결함 차단.
     law.go.kr 현행과의 대조는 tools/check_law_freshness.py가 담당.)
    """
    fname, _, _ = REGISTRY_SOURCES[key]
    xml_path = LAWS / fname
    if not xml_path.exists():
        pytest.skip(f"원문 XML 없음: {fname}")
    assert registry[key].get("promulgation") == promulgation(xml_path), (
        f"{key}: promulgation이 XML 기본정보와 불일치 — "
        f"`python3 tools/build_law_registry.py`로 재생성할 것."
    )


@pytest.mark.parametrize("key", sorted(REGISTRY_SOURCES))
def test_no_duplicate_markers(registry, key):
    """항·호·목 마커 중복 아티팩트 회귀 차단 (생성기 정규화와 독립 검증).

    law.go.kr XML은 `<항번호>①</항번호>` 다음 `<항내용>① …</항내용>`처럼 마커를
    내용에도 담는다. 둘을 그대로 이어 붙이면 의견서에 "① ① 각 중앙관서의 장은…"이
    찍힌다(2026-07-28 발견 — 당시 레지스트리 전 항목에 존재).
    """
    for art in registry[key].get("articles", []):
        body = art.get("body", "")
        dup = re.search(r"([①-⑳]|\d+\.|[가-하]\.)\s*\1(?![\d가-힣])", body)
        assert not dup, (
            f"{key}: 마커 중복 '{dup.group(0)!r}' — 생성기가 항·호·목 번호를 "
            f"내용과 겹쳐 붙였다. tools/build_law_registry.ordered_parts 확인."
        )


def test_no_registry_entry_left_unaccounted(registry):
    """XML 미보유 항목이 늘면 생성기 명단을 갱신해 의식적으로 관리 (침묵 확장 방지)."""
    unexpected = set(registry) - set(REGISTRY_SOURCES) - NON_XML_KEYS
    assert not unexpected, f"관리 명단에 없는 레지스트리 항목: {unexpected}"
