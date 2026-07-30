"""backend/services/law_history.py 단위테스트 — 네트워크 불필요.

시점 조회에서 틀리면 조용히 위험한 것 두 가지를 박제한다:
  1. **경계 선택** — 시행일 당일은 그 판이 적용된다(≤). 하루 어긋나면 개정 전후가
     통째로 뒤바뀐 답이 나오는데, 겉보기엔 멀쩡하다.
  2. **정확 일치** — 연혁 목록에 시행령·시행규칙이 섞이면 엉뚱한 법의 본문을 집는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.law_history import (  # noqa: E402
    extract_article, neighbors, norm_name, parse_versions, pick_asof,
    split_article_ref,
)

# law.go.kr eflaw 응답 축약 — 동명 법령과 그 시행령이 함께 오는 실제 상황을 재현
SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LawSearch><target>eflaw</target><totalCnt>4</totalCnt>
<law id="1"><법령일련번호>199735</법령일련번호><현행연혁코드>연혁</현행연혁코드>
  <법령명한글>국가를 당사자로 하는 계약에 관한 법률</법령명한글>
  <공포일자>20171219</공포일자><공포번호>15219</공포번호>
  <제개정구분명>일부개정</제개정구분명><시행일자>20180320</시행일자></law>
<law id="2"><법령일련번호>283877</법령일련번호><현행연혁코드>현행</현행연혁코드>
  <법령명한글>국가를 당사자로 하는 계약에 관한 법률</법령명한글>
  <공포일자>20260310</공포일자><공포번호>21418</공포번호>
  <제개정구분명>일부개정</제개정구분명><시행일자>20260611</시행일자></law>
<law id="3"><법령일련번호>252681</법령일련번호><현행연혁코드>연혁</현행연혁코드>
  <법령명한글>국가를 당사자로 하는 계약에 관한 법률</법령명한글>
  <공포일자>20230000</공포일자><공포번호>19590</공포번호>
  <제개정구분명>타법개정</제개정구분명><시행일자>20231019</시행일자></law>
<law id="4"><법령일련번호>999999</법령일련번호><현행연혁코드>현행</현행연혁코드>
  <법령명한글>국가를 당사자로 하는 계약에 관한 법률 시행령</법령명한글>
  <공포일자>20260519</공포일자><공포번호>36338</공포번호>
  <제개정구분명>타법개정</제개정구분명><시행일자>20260603</시행일자></law>
</LawSearch>"""

NAME = "국가를 당사자로 하는 계약에 관한 법률"

ARTICLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<법령><기본정보><시행일자>20180320</시행일자></기본정보><조문>
<조문단위><조문번호>26</조문번호><조문여부>조문</조문여부>
  <조문내용>제26조(지체상금)</조문내용>
  <항><항내용>① 각 중앙관서의 장은 지체상금을 부과한다.</항내용>
    <호><호내용>1. 공사의 경우</호내용></호>
    <호><호내용>2. 물품의 경우</호내용></호>
  </항></조문단위>
<조문단위><조문번호>27</조문번호><조문가지번호>2</조문가지번호><조문여부>조문</조문여부>
  <조문내용>제27조의2(과징금)</조문내용>
  <항><항내용>① 과징금을 부과할 수 있다.</항내용></항></조문단위>
<조문단위><조문번호>30</조문번호><조문여부>전문</조문여부>
  <조문내용>제5장 보칙</조문내용></조문단위>
<조문단위><조문번호>30</조문번호><조문여부>조문</조문여부>
  <조문내용>제30조(위임)</조문내용>
  <항><항내용>① 대통령령으로 정한다.</항내용></항></조문단위>
</조문></법령>"""


def _versions():
    return parse_versions(SEARCH_XML, NAME)


def test_parse_versions_exact_match_only():
    """시행령이 섞여 들어오면 안 된다 — 3건만 채택, 시행일 오름차순."""
    vs = _versions()
    assert [v.ef_date for v in vs] == ["20180320", "20231019", "20260611"]
    assert all(v.name == NAME for v in vs)
    assert vs[-1].is_current and not vs[0].is_current


def test_pick_asof_selects_version_in_force():
    vs = _versions()
    assert pick_asof(vs, "20200101").ef_date == "20180320"
    assert pick_asof(vs, "20240101").ef_date == "20231019"
    assert pick_asof(vs, "20260701").ef_date == "20260611"


def test_pick_asof_boundary_is_inclusive():
    """시행일 당일은 그 판이 적용된다. 하루 전은 직전 판."""
    vs = _versions()
    assert pick_asof(vs, "20231019").ef_date == "20231019"
    assert pick_asof(vs, "20231018").ef_date == "20180320"


def test_pick_asof_before_first_version_is_none():
    assert pick_asof(_versions(), "19900101") is None


def test_neighbors():
    vs = _versions()
    chosen = pick_asof(vs, "20240101")
    assert neighbors(vs, chosen) == ("20180320", "20260611")
    assert neighbors(vs, vs[-1])[1] is None  # 최신판은 다음이 없다


def test_split_article_ref():
    assert split_article_ref("제27조") == ("27", "")
    assert split_article_ref("제27조의2") == ("27", "2")
    assert split_article_ref("제27조 제1항") is None  # 조문 단위만 받는다


def test_extract_article_assembles_hang_and_ho():
    txt = extract_article(ARTICLE_XML, "제26조")
    assert txt.startswith("제26조(지체상금)")
    assert "① 각 중앙관서의 장은 지체상금을 부과한다." in txt
    assert "1. 공사의 경우" in txt and "2. 물품의 경우" in txt


def test_extract_article_branch_number():
    """제27조와 제27조의2는 다른 조문 — 가지번호가 정확히 일치해야 한다."""
    assert extract_article(ARTICLE_XML, "제27조") is None
    assert "과징금" in extract_article(ARTICLE_XML, "제27조의2")


def test_extract_article_skips_chapter_heading():
    """장 표제 노드(조문여부='전문')가 실조문을 가리면 안 된다."""
    txt = extract_article(ARTICLE_XML, "제30조")
    assert "제30조(위임)" in txt
    assert "제5장" not in txt


def test_extract_article_missing_returns_none():
    assert extract_article(ARTICLE_XML, "제99조") is None


def test_norm_name_absorbs_middot():
    assert norm_name("공기업·준정부기관 계약사무규칙") == norm_name("공기업ㆍ준정부기관계약사무규칙")


def test_malformed_xml_is_safe():
    assert parse_versions("<not xml", NAME) == []
    assert extract_article("<not xml", "제1조") is None
