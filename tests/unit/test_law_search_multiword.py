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
    """전체 문구 $contains는 0건, 토큰 $and는 1건 반환하는 가짜 컬렉션."""

    DOC = "국가계약법 시행령 제26조\n수의계약에 의할 수 있는 사유는 다음과 같다."
    META = {"law_name": "국가계약법 시행령", "article_titles": "제26조", "law_ref": "국가계약법 시행령 제26조"}

    def get(self, where=None, where_document=None, include=None, limit=None):
        if where_document and "$and" in where_document:
            terms = [c["$contains"] for c in where_document["$and"]]
            if all(t in self.DOC for t in terms):
                return {"documents": [self.DOC], "metadatas": [self.META]}
        return {"documents": [], "metadatas": []}


def test_multiword_and_fallback(monkeypatch):
    monkeypatch.setattr(law, "_get_collection", lambda: _FakeCol())
    hits = law.search_law(q="수의계약 사유")
    assert len(hits) == 1
    assert hits[0].law_ref == "국가계약법 시행령 제26조"
    # 매치 실패 토큰이면 여전히 0건 (AND 의미 보존)
    assert law.search_law(q="수의계약 낙찰하한율") == []


def test_single_keyword_path_unchanged(monkeypatch):
    """단일 키워드는 AND 폴백을 타지 않는다 (기존 변형 경로 결과 그대로)."""
    monkeypatch.setattr(law, "_get_collection", lambda: _FakeCol())
    assert law.search_law(q="수의계약") == []  # fake col의 substring 경로는 항상 0건
