"""법령 검색 다단어 AND 폴백 단위 테스트 (2026-07-29).

"수의계약 사유" 같은 다단어 자연어 질의가 전체 문구 substring 매치에 실패해도
토큰 전부 포함($and $contains) 조문으로 회수되는지 검증. chroma 미사용(fake col).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import backend.api.v1.law as law  # noqa: E402

pytestmark = pytest.mark.unit


def test_keyword_tokens_split_and_expand():
    assert law._keyword_tokens("수의계약 사유") == ["수의계약", "사유"]
    # 약어는 토큰 단위로 확장
    assert law._keyword_tokens("종심제 점수") == ["종합심사낙찰제", "점수"]
    # 1글자 토큰·중복 제거
    assert law._keyword_tokens("수의계약 및 수의계약") == ["수의계약"]


class _FakeCol:
    """전체 문구 substring은 0건, 토큰별 $contains만 매치되는 가짜 컬렉션.

    2026-07-30: AND-전체 → 토큰별 부분 매치(2개 이상) 순위 방식으로 바뀜에 따라
    단일 $contains 질의에 응답한다. query()(시맨틱 폴백)는 없음 — 호출 시
    AttributeError가 나고 search_law가 삼켜 빈 결과 유지(의도된 degrade).
    """

    DOC = "국가계약법 시행령 제26조\n수의계약에 의할 수 있는 사유는 다음과 같다."
    META = {"law_name": "국가계약법 시행령", "article_titles": "제26조", "law_ref": "국가계약법 시행령 제26조"}

    def get(self, where=None, where_document=None, include=None, limit=None):
        if where_document and "$contains" in where_document:
            term = where_document["$contains"]
            # 전체 문구("수의계약 사유" 그대로)는 DOC에 없어 0건 — 폴백 경로 강제
            if term in self.DOC:
                return {"documents": [self.DOC], "metadatas": [self.META]}
        return {"documents": [], "metadatas": []}


def test_multiword_and_fallback(monkeypatch):
    monkeypatch.setattr(law, "_get_collection", lambda: _FakeCol())
    hits = law.search_law(q="수의계약 사유")
    assert len(hits) == 1
    assert hits[0].law_ref == "국가계약법 시행령 제26조"
    # 2개 미만 매치는 통과선 미달 → 0건 (시맨틱 폴백은 fake col에 없어 degrade)
    assert law.search_law(q="수의계약 낙찰하한율") == []


def test_partial_match_survives_missing_token(monkeypatch):
    """토큰 하나가 코퍼스에 아예 없어도(예규 용어 등) 2개 이상 매치면 회수 — 2026-07-30."""
    monkeypatch.setattr(law, "_get_collection", lambda: _FakeCol())
    hits = law.search_law(q="수의계약 사유 낙찰하한율")
    assert len(hits) == 1 and hits[0].law_ref == "국가계약법 시행령 제26조"


def test_single_keyword_path_unchanged(monkeypatch):
    """단일 키워드는 부분 매치 폴백을 타지 않는다 (변형 substring 경로 결과 그대로)."""
    monkeypatch.setattr(law, "_get_collection", lambda: _FakeCol())
    assert law.search_law(q="수의계약")[0].law_ref == "국가계약법 시행령 제26조"  # substring 직접 매치
    assert law.search_law(q="입찰보증금") == []  # 미매치 단일 토큰은 폴백 없이 0건
