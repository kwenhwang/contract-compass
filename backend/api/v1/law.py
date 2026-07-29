"""법령 조문 원문 조회 — 법령 조문 컬렉션에서 단일 조문 조회."""
import re
from functools import lru_cache
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
import chromadb

from backend.config import get_settings

router = APIRouter(prefix="/law", tags=["law"])

# 약칭/모호한 표현 → DB에 저장된 정규 law_name
# DB는 "국가계약법 시행령" 등 약칭으로 저장됨
_LAW_ALIASES = {
    "시행령": "국가계약법 시행령",
    "시행규칙": "국가계약법 시행규칙",
    "공기업·준정부기관 계약사무규칙": "공기업ㆍ준정부기관 계약사무규칙",
    "공기업ㆍ준정부기관 계약사무규칙": "공기업ㆍ준정부기관 계약사무규칙",
}


@lru_cache(maxsize=1)
def _get_collection():
    settings = get_settings()
    client = chromadb.PersistentClient(settings.chroma_path)
    return client.get_collection(settings.collection_law_articles)


class LawArticleResponse(BaseModel):
    law_name: str
    article: str
    content: str
    law_ref: str


class LawSearchHit(BaseModel):
    law_name: str
    article: str
    content: str
    snippet: str
    law_ref: str


def _make_snippet(text: str, q: str, around: int = 80) -> str:
    idx = text.find(q)
    if idx < 0:
        return text[:200].strip()
    start = max(0, idx - around)
    end = min(len(text), idx + len(q) + around)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


@router.get("/article", response_model=LawArticleResponse)
def get_article(ref: str = Query(..., min_length=2, max_length=100)):
    """조문 참조 문자열로 원문 조회. 예: '시행령 제30조', '국가계약법 시행령 제26조 제1항'."""
    article_match = re.search(r"제\d+조(?:의\d+)?", ref)
    if not article_match:
        raise HTTPException(404, "조문번호(제N조)를 찾을 수 없습니다")
    article = article_match.group(0)

    law_part = ref[: article_match.start()].strip().rstrip("ㆍ·,")
    target_law = _LAW_ALIASES.get(law_part, law_part) if law_part else ""

    col = _get_collection()
    results = col.get(
        where={"article_titles": article},
        include=["documents", "metadatas"],
    )
    docs = results.get("documents") or []
    metas = results.get("metadatas") or []
    if not docs:
        raise HTTPException(404, f"{article} 조문을 찾을 수 없습니다")

    if not target_law:
        raise HTTPException(404, "법령명을 식별할 수 없습니다 (예: '시행령 제30조')")

    # 1단계: law_name 정확 일치
    best = None
    for doc, meta in zip(docs, metas):
        if (meta.get("law_name") or "") == target_law:
            best = (doc, meta)
            break
    # 2단계: target_law가 law_name에 포함 (예: "국가계약법" → "국가계약법 시행령" 매치 방지를 위해 같은 접미사 확인)
    if best is None:
        for doc, meta in zip(docs, metas):
            law_name = meta.get("law_name") or ""
            # target_law의 마지막 토큰(시행령/시행규칙/법 등)이 law_name 끝부분과 일치할 때만 채택
            if law_name == target_law:
                best = (doc, meta)
                break
            # "국가계약법" 입력 시 "국가계약법 시행령"으로 가지 않도록: target이 law_name보다 길거나 같을 때만 부분 매치 허용
            if len(target_law) >= len(law_name) and law_name and law_name in target_law:
                best = (doc, meta)
                break

    if best is None:
        raise HTTPException(404, f"{ref!r}에 해당하는 조문을 찾을 수 없습니다 (검색된 법령: {[m.get('law_name') for m in metas]})")

    doc, meta = best
    return LawArticleResponse(
        law_name=meta.get("law_name", ""),
        article=article,
        content=doc,
        law_ref=meta.get("law_ref", ""),
    )


@router.get("/search", response_model=list[LawSearchHit])
def search_law(q: str = Query(..., min_length=1, max_length=50)) -> list[LawSearchHit]:
    """법령 키워드 또는 조문번호로 조문 검색.

    - "제26조" → 모든 법령의 제26조 반환
    - "수의계약" → 본문에 '수의계약' 포함된 조문
    - "시행령 제26조" → 정확 매치 우선 + 키워드 매치
    """
    col = _get_collection()
    q = q.strip()
    if not q:
        return []

    article_match = re.search(r"제\d+조(?:의\d+)?", q)
    keyword = re.sub(r"제\d+조(?:의\d+)?", "", q).strip()

    seen_refs: set[str] = set()
    results: list[LawSearchHit] = []

    # 1. 조문번호 정확 매치 우선
    if article_match:
        article = article_match.group(0)
        r = col.get(where={"article_titles": article}, include=["documents", "metadatas"])
        for doc, meta in zip(r.get("documents") or [], r.get("metadatas") or []):
            law_name = meta.get("law_name") or ""
            law_ref = meta.get("law_ref") or ""
            # 키워드가 있다면 law_name 또는 본문에 포함되어야 함
            if keyword and keyword not in law_name and keyword not in doc:
                continue
            if law_ref in seen_refs:
                continue
            seen_refs.add(law_ref)
            results.append(LawSearchHit(
                law_name=law_name,
                article=article,
                content=doc,
                snippet=_make_snippet(doc, keyword or article),
                law_ref=law_ref,
            ))

    # 2. 키워드 본문 substring 검색 (조문번호 없거나 추가 결과)
    if keyword:
        r = col.get(
            where_document={"$contains": keyword},
            include=["documents", "metadatas"],
            limit=50,
        )
        for doc, meta in zip(r.get("documents") or [], r.get("metadatas") or []):
            law_ref = meta.get("law_ref") or ""
            if law_ref in seen_refs:
                continue
            seen_refs.add(law_ref)
            results.append(LawSearchHit(
                law_name=meta.get("law_name") or "",
                article=meta.get("article_titles") or "",
                content=doc,
                snippet=_make_snippet(doc, keyword),
                law_ref=law_ref,
            ))
            if len(results) >= 30:
                break

    return results
