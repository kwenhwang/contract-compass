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


# 원문 청크의 항·호·목 표지 중복 아티팩트("① ①", "3. 3.", "가. 가.") 정규화.
# 색인 시점 파싱 잔재 — 재색인 없이도 API 반환 시점에 정리한다.
_DUP_MARKER_RE = re.compile(
    r"([①-⑳])\s*\1|(\d{1,2}\.)\s*\2|([가-힣]\.)\s*\3"
)


def _clean_markers(text: str) -> str:
    return _DUP_MARKER_RE.sub(lambda m: m.group(1) or m.group(2) or m.group(3), text or "")


# 실무 약어 → 정식 검색어 (glossary.json aliases가 단일 소스, 로드 실패 시 최소셋)
@lru_cache(maxsize=1)
def _abbrev_map() -> dict[str, str]:
    m = {"종심제": "종합심사낙찰제", "적심": "적격심사", "예가": "예정가격"}
    try:
        import json as _json
        from backend.config import BASE_DIR
        for e in _json.loads((BASE_DIR / "data" / "glossary.json").read_text(encoding="utf-8")):
            term = e.get("term", "")
            for a in e.get("aliases") or []:
                if a and a != term:
                    m.setdefault(a, term)
    except Exception:
        pass
    return m


def _keyword_variants(keyword: str) -> list[str]:
    """검색 키워드 변형 — 원문 그대로 → 공백 접합 → 약어 확장 순으로 시도."""
    out: list[str] = []
    for cand in (keyword, keyword.replace(" ", ""), _abbrev_map().get(keyword.replace(" ", ""), "")):
        if cand and cand not in out:
            out.append(cand)
    return out


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
    # 긴 조문은 색인 시 parent가 2,000자에서 잘려 저장됨 — 자식 청크(항 단위)를
    # law_ref 순서로 조립해 조문 전문을 복원한다. (2026-07-29 Codex 적대 테스트 발견)
    if meta.get("chunk_level") == "parent":
        try:
            kids = col.get(
                where={"parent_ref": meta.get("law_ref", "")},
                include=["documents", "metadatas"],
            )
            def _order(km: dict) -> tuple:
                kr = km.get("law_ref", "")
                hang = re.search(r"제(\d+)항", kr)
                cont = re.search(r"\(계속(\d+)\)", kr)
                return (int(hang.group(1)) if hang else 999, int(cont.group(1)) if cont else 0)
            pairs = sorted(zip(kids.get("documents") or [], kids.get("metadatas") or []),
                           key=lambda p: _order(p[1] or {}))
            if pairs:
                header = f"{meta.get('law_name','')} {article}"
                parts = []
                for kdoc, km in pairs:
                    body = re.sub(r"^" + re.escape((km or {}).get("law_ref", "")) + r"\s*", "", kdoc or "")
                    parts.append(body.strip())
                doc = header + "\n" + "\n".join(parts)
        except Exception:
            pass  # 조립 실패 시 parent 축약본이라도 반환
    return LawArticleResponse(
        law_name=meta.get("law_name", ""),
        article=article,
        content=_clean_markers(doc),
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
    # 상세 인용("…제26조제1항제5호가목")의 항·호·목 꼬리는 필터에서 제외 —
    # 조문은 조 단위로 저장되므로 남겨두면 매치가 전부 걸러져 0건이 된다.
    keyword = re.sub(r"제\s*\d+\s*[항호목](?:의\d+)?", "", keyword).strip()

    seen_refs: set[str] = set()
    results: list[LawSearchHit] = []

    # 1. 조문번호 정확 매치 우선
    if article_match:
        article = article_match.group(0)
        r = col.get(where={"article_titles": article}, include=["documents", "metadatas"])
        for doc, meta in zip(r.get("documents") or [], r.get("metadatas") or []):
            law_name = meta.get("law_name") or ""
            law_ref = meta.get("law_ref") or ""
            # 키워드가 있다면 law_name 또는 본문에 포함되어야 함 (공백 변형 허용)
            if keyword and not any(v in law_name or v in doc for v in _keyword_variants(keyword)):
                continue
            if law_ref in seen_refs:
                continue
            seen_refs.add(law_ref)
            results.append(LawSearchHit(
                law_name=law_name,
                article=article,
                content=_clean_markers(doc),
                snippet=_clean_markers(_make_snippet(doc, keyword or article)),
                law_ref=law_ref,
            ))

    # 2. 키워드 본문 substring 검색 (조문번호 없거나 추가 결과)
    # 원문 그대로 → 공백 접합("지명 경쟁"→"지명경쟁") → 약어 확장("종심제"→
    # "종합심사낙찰제") 순서로 시도, 결과가 나오는 첫 변형에서 멈춘다.
    if keyword:
        for variant in _keyword_variants(keyword):
            r = col.get(
                where_document={"$contains": variant},
                include=["documents", "metadatas"],
                limit=50,
            )
            found_any = False
            for doc, meta in zip(r.get("documents") or [], r.get("metadatas") or []):
                found_any = True
                law_ref = meta.get("law_ref") or ""
                if law_ref in seen_refs:
                    continue
                seen_refs.add(law_ref)
                results.append(LawSearchHit(
                    law_name=meta.get("law_name") or "",
                    article=meta.get("article_titles") or "",
                    content=_clean_markers(doc),
                    snippet=_clean_markers(_make_snippet(doc, variant)),
                    law_ref=law_ref,
                ))
                if len(results) >= 30:
                    break
            if found_any:
                break

    return results
