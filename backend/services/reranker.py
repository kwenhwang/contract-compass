"""Cohere Rerank — 검색 후보 재정렬.

흐름:
  1) Dense + BM25 + RRF로 top 20~30 후보 검색
  2) Cohere Rerank로 (query, candidates) 1:1 점수 재계산
  3) 재정렬된 top_n을 LLM 컨텍스트로

모델: rerank-multilingual-v3.0 (한국어 포함 100개 언어)
한도: Trial Key 월 1000건 (단위 = rerank API 호출 1회)
"""
from __future__ import annotations

import os
from typing import Any

_client: Any = None
_available: bool | None = None


def _get_key() -> str:
    # 1) 환경변수 우선
    if k := os.environ.get("COHERE_API_KEY"):
        return k
    # 2) pydantic settings (.env)
    try:
        from backend.config import get_settings
        return get_settings().cohere_api_key or ""
    except Exception:
        return ""


def _get_client():
    global _client, _available
    if _available is False:
        return None
    if _client is not None:
        return _client
    key = _get_key()
    if not key:
        _available = False
        return None
    try:
        import cohere
        _client = cohere.ClientV2(api_key=key)
        _available = True
        return _client
    except Exception:
        _available = False
        return None


def _get_endpoint() -> str:
    """내부망 로컬 reranker(TEI 등) 주소 — env 우선, settings 폴백. 미설정 시 Cohere."""
    if e := os.environ.get("RERANK_ENDPOINT"):
        return e.rstrip("/")
    try:
        from backend.config import get_settings
        return (get_settings().rerank_endpoint or "").rstrip("/")
    except Exception:
        return ""


def is_available() -> bool:
    return bool(_get_endpoint()) or _get_client() is not None


def _rerank_local(query: str, candidates: list[dict], top_n: int, endpoint: str) -> list[dict]:
    """TEI(text-embeddings-inference) rerank API — 폐쇄망에서 Cohere 대체.

    POST {endpoint}/rerank {"query", "texts"} → [{"index", "score"}, ...]
    """
    import httpx
    docs = [(c.get("content") or "")[:1500] for c in candidates]
    resp = httpx.post(
        f"{endpoint}/rerank",
        json={"query": query, "texts": docs},
        timeout=15.0,
    )
    resp.raise_for_status()
    results = sorted(resp.json(), key=lambda r: -float(r.get("score", 0.0)))[:top_n]
    ordered: list[dict] = []
    for r in results:
        c = dict(candidates[int(r["index"])])
        c["_rerank_score"] = round(float(r.get("score", 0.0)), 4)
        ordered.append(c)
    return ordered


def rerank(
    query: str,
    candidates: list[dict],
    top_n: int = 10,
    model: str = "rerank-multilingual-v3.0",
) -> list[dict]:
    """검색 후보를 재정렬 — 로컬 reranker(RERANK_ENDPOINT) 우선, 없으면 Cohere. 실패 시 입력 그대로.

    candidates: [{chunk_id, content, ...}, ...]
    Returns: top_n 개수 (재정렬됨)
    """
    if not candidates:
        return candidates[:top_n]
    if endpoint := _get_endpoint():
        try:
            return _rerank_local(query, candidates, top_n, endpoint)
        except Exception as e:
            import sys
            print(f"[Reranker] local fallback→cohere: {e}", file=sys.stderr)
    client = _get_client()
    if not client:
        return candidates[:top_n]

    # Cohere는 입력 토큰 길이 제한 — content 512자로 자름
    docs = [(c.get("content") or "")[:1500] for c in candidates]
    try:
        res = client.rerank(
            model=model,
            query=query,
            documents=docs,
            top_n=min(top_n, len(docs)),
        )
        # rerank 결과의 relevance_score는 0~1
        ordered: list[dict] = []
        for r in res.results:
            i = r.index
            c = dict(candidates[i])
            c["_rerank_score"] = round(float(r.relevance_score), 4)
            ordered.append(c)
        return ordered
    except Exception as e:
        # 한도 초과·네트워크 오류 등 — 입력 그대로 fallback
        import sys
        print(f"[Reranker] fallback: {e}", file=sys.stderr)
        return candidates[:top_n]
