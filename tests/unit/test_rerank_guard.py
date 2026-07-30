"""rerank 무산출 가드 회귀 테스트 (2026-07-30 P0).

배경: COHERE_API_KEY 미설정으로 rerank가 완전 침묵 무동작인 동안, BM25-only 후보의
하드코딩 relevance(0.6)가 dense 문턱(0.60)과 경계가 같아 검증 없이 항상 통과했다.
가드 불변식:
  1. rerank 산출물이 하나도 없으면 BM25-only 후보는 배제된다.
  2. 같은 상황에서 dense 문턱은 0.60→0.80, internal/law 0.85→0.92로 상향된다.
  3. rerank 산출물이 있으면 기존 동작 그대로(문턱·후보 무변경).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.api.v1.ask import _apply_rerank_guard, _chunk_passes  # noqa: E402


def _c(**kw) -> dict:
    base = {"chunk_id": "x", "content": "본문", "relevance_score": 0.6}
    base.update(kw)
    return base


def test_guard_excludes_bm25_only_when_no_rerank():
    chunks = [_c(chunk_id="a"), _c(chunk_id="b", bm25_only=True, relevance_score=0.6)]
    out, no_rerank = _apply_rerank_guard(chunks)
    assert no_rerank is True
    assert [c["chunk_id"] for c in out] == ["a"]


def test_guard_noop_when_rerank_active():
    chunks = [_c(chunk_id="a", _rerank_score=0.9),
              _c(chunk_id="b", bm25_only=True, _rerank_score=0.4)]
    out, no_rerank = _apply_rerank_guard(chunks)
    assert no_rerank is False
    assert out == chunks  # BM25-only도 rerank 점수로 검증됐으므로 유지


def test_guard_empty_input_is_not_flagged():
    out, no_rerank = _apply_rerank_guard([])
    assert out == [] and no_rerank is False


def test_thresholds_raised_without_rerank():
    # 일반 소스: 0.60은 통과였으나 무산출 시 0.80 미만은 컷
    assert _chunk_passes(_c(relevance_score=0.60), no_rerank=False)
    assert not _chunk_passes(_c(relevance_score=0.60), no_rerank=True)
    assert _chunk_passes(_c(relevance_score=0.80), no_rerank=True)
    # internal/law: 0.85→0.92 상향
    law = _c(source_type="law", relevance_score=0.85)
    assert _chunk_passes(law, no_rerank=False)
    assert not _chunk_passes(law, no_rerank=True)
    assert _chunk_passes(_c(source_type="law", relevance_score=0.92), no_rerank=True)


def test_rerank_score_takes_precedence():
    # rerank 점수가 있으면 dense 문턱 무관 — cross-encoder 기준만 적용
    assert _chunk_passes(_c(relevance_score=0.0, _rerank_score=0.05), no_rerank=False)
    assert not _chunk_passes(_c(source_type="law", relevance_score=0.99, _rerank_score=0.10),
                             no_rerank=False)
