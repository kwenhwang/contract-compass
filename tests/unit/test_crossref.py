"""backend/services/crossref.py 단위테스트 — 네트워크·색인 불필요.

지키려는 것은 두 방향이다:
  1. 실측 미정비 인용(지방보조금법 제21조⑤ → "제2항 각 호")을 **잡는다**.
  2. 정상 조문·외부 조 인용·호 없는 조문을 **잡지 않는다**. 법률 도구에서
     무고한 경고는 신뢰를 깎으므로 오탐이 미탐보다 비싸다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.crossref import detect_crossref_anomalies  # noqa: E402

# law.go.kr 현행 원문(2026-07-31 대조) 축약 — 제2항은 공시 조항이라 각 호가 없고
# 실제 행위 열거는 제3항 각 호다. 제5항이 그 제2항을 가리킨다.
JIBANG_BOJOGEUM_21 = """지방자치단체 보조금 관리에 관한 법률 제21조
① 지방보조사업자는 지방보조금으로 취득하거나 그 효용이 증가된 것으로서 대통령령으로 정하는 중요한 재산에 대해서는 그 현재액과 증감을 명백히 하여야 한다.
② 지방자치단체의 장은 제1항에 따라 중요재산의 현황을 보고받은 경우 대통령령으로 정하는 바에 따라 그 보고받은 현황을 공시하여야 한다.
③ 지방보조사업자는 해당 지방보조사업을 완료한 후에도 지방자치단체의 장의 승인 없이 중요재산에 대하여 다음 각 호의 행위를 하여서는 아니 된다.
1. 교부 목적 외 용도로의 사용
2. 양도, 교환 또는 대여
3. 담보의 제공
④ 지방보조사업자가 다음 각 호의 어느 하나에 해당하는 경우에는 지방자치단체의 장의 승인을 받지 아니하고도 제3항 각 호의 행위를 할 수 있다.
1. 지방보조사업자가 지방보조금의 전부를 지방자치단체에 반환한 경우
2. 지방자치단체의 장이 정한 기간이 지난 경우
⑤ 지방자치단체의 장은 지방보조사업자가 승인 없이 중요재산에 대하여 제2항 각 호의 행위를 한 경우에는 다음 각 호의 금액의 전부 또는 일부의 반환을 명할 수 있다.
1. 중요재산을 취득하기 위하여 사용된 지방보조금에 해당하는 금액
2. 중요재산의 효용가치 증가액에 해당하는 금액
"""


def test_detects_dangling_ho_reference():
    """제5항 → '제2항 각 호' 이상을 잡고, 각 호 보유 항을 후보로 제시한다."""
    found = detect_crossref_anomalies(JIBANG_BOJOGEUM_21)
    assert len(found) == 1, f"1건이어야 하는데 {found}"
    a = found[0]
    assert a["kind"] == "dangling_ho_reference"
    assert a["hang"] == 5
    assert a["referenced"] == 2
    assert 3 in a["candidates"]  # 실제 행위 열거는 제3항
    assert "제2항" in a["message"]


def test_valid_reference_is_silent():
    """제4항의 '제3항 각 호'는 정상 — 경고하지 않는다."""
    ok = """제10조
① 총칙이다.
② 다음 각 호의 행위를 금지한다.
1. 첫째
2. 둘째
③ 제2항 각 호의 행위를 한 경우 제재한다.
"""
    assert detect_crossref_anomalies(ok) == []


def test_external_article_reference_ignored():
    """다른 조를 가리키는 '제30조제2항 각 호'는 같은 조 정합성 대상이 아니다."""
    txt = """제5조
① 다음 각 호를 정한다.
1. 하나
② 국유재산법 제30조제2항 각 호의 행위는 예외로 한다.
"""
    assert detect_crossref_anomalies(txt) == []


def test_article_without_ho_structure_is_silent():
    """각 호를 쓰지 않는 조문이면 판단 근거가 없으므로 침묵한다."""
    txt = """제3조
① 이 법의 목적은 다음과 같다.
② 제1항 각 호의 사항은 대통령령으로 정한다.
"""
    assert detect_crossref_anomalies(txt) == []


def test_missing_hang_not_flagged():
    """본문에 없는 항(조립 누락 가능)은 이상으로 단정하지 않는다."""
    txt = """제7조
① 다음 각 호를 정한다.
1. 하나
② 제9항 각 호에 따른다.
"""
    assert detect_crossref_anomalies(txt) == []


def test_empty_and_plain_text():
    assert detect_crossref_anomalies("") == []
    assert detect_crossref_anomalies("제1조(목적) 이 법은 …을 목적으로 한다.") == []


def test_date_is_not_mistaken_for_ho():
    """'<개정 2023.4.11>' 같은 날짜를 호 표지로 오인하지 않는다."""
    txt = """제8조
① 다음 각 호의 행위를 금지한다. <개정 2023.4.11>
1. 하나
② 제1항 각 호의 행위를 한 자는 처벌한다.
"""
    assert detect_crossref_anomalies(txt) == []
