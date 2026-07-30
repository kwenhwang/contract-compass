"""법령 조문 원문 조회 — 법령 조문 컬렉션에서 단일 조문 조회."""
import re
from functools import lru_cache
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from pydantic import BaseModel
import chromadb

from backend.api.deps import get_rag_service
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
    from backend.services.embedding import GeminiEmbeddingFunction
    return client.get_collection(settings.collection_law_articles,
                                 embedding_function=GeminiEmbeddingFunction())


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


def _keyword_tokens(keyword: str) -> list[str]:
    """다단어 질의를 토큰 목록으로 — 각 토큰은 약어 확장 적용, 1글자 토큰 제외."""
    abbrev = _abbrev_map()
    tokens: list[str] = []
    for t in keyword.split():
        t = abbrev.get(t, t)
        if len(t) >= 2 and t not in tokens:
            tokens.append(t)
    return tokens


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


@router.get("/references")
def search_references(
    request: Request,
    q: str = Query(..., min_length=2, max_length=200),
    top_k: int = Query(6, ge=1, le=12),
    rag=Depends(get_rag_service),
) -> list[dict]:
    """전 코퍼스 통합 검색(법령+계약예규+조달청·행안부 세부기준+실무가이드) — LLM 미사용.

    MCP·에이전트용(2026-07-30): /ask는 검색 후 OpenAI 생성까지 수행해 일일 캡을
    차감하지만, 에이전트 클라이언트는 자신이 LLM이므로 검색 청크 원문만 있으면 된다.
    /law/search는 법령(law_articles) 전용이라 예규·세부기준 코퍼스(admin_rules 등)에
    닿는 무LLM 경로가 없던 공백을 메운다. 임베딩(Gemini)만 사용 — OpenAI 캡 미차감,
    IP 슬라이딩 윈도우 한도는 동일 적용.
    """
    from backend.services.rate_limiter import get_rate_limiter, LIMITS_LLM
    limiter = get_rate_limiter()
    # check만 하면 카운트가 안 쌓여 한도가 무력화된다(2026-07-30 실측) — 무LLM이지만
    # 임베딩 비용·외부 API 보호를 위해 요청 자체를 계상한다.
    limiter.record(limiter.check(request, LIMITS_LLM))
    chunks = rag.search_all(q.strip(), top_k=top_k)

    # excerpt를 청크 앞 600자 고정이 아니라 질의 토큰 첫 매치 주변으로 창을 잡는다 —
    # 별표류 긴 청크(1,200자)는 정답 행이 뒤쪽에 있으면 회수돼도 본문이 안 보였다
    # (2026-07-30 배터리 업체-062: 부정당 별표2 '계약 미체결' 행).
    q_tokens = [t for t in q.split() if len(t) >= 2]

    def _excerpt(content: str) -> str:
        if len(content) <= 600 or not q_tokens:
            return content[:600]
        # 300자 보폭으로 600자 창을 밀며 질의 토큰 매치 수가 최대인 창을 고른다
        # (동률이면 앞쪽 창 — 기존 head-600과 호환). 토큰 가중치는 길이(특이도 근사).
        best_start, best_score = 0, -1
        for start in range(0, len(content) - 300, 300):
            win = content[start:start + 600]
            score = sum(len(t) for t in q_tokens if t in win)
            if score > best_score:
                best_start, best_score = start, score
        return ("..." if best_start else "") + content[best_start:best_start + 600]

    def _row(c: dict) -> dict:
        content = c.get("content") or ""
        section = c.get("section_title") or ""
        # 별표(제재기준·심사기준 표) 청크는 답이 특정 행 하나에 있어 어떤 절단도
        # 손실이다 — 청크 자체가 1,200자 캡(fetch_law_tables)이므로 전문 반환.
        excerpt = content[:1400] if "별표" in section else _excerpt(content)
        return {
            "source": c.get("document_id") or "",
            "section": section,
            "source_type": c.get("source_type") or "",
            "excerpt": _clean_markers(excerpt),
            "relevance": round(float(c.get("relevance_score") or 0), 3),
        }

    return [_row(c) for c in chunks[: top_k * 2]]


@router.get("/search", response_model=list[LawSearchHit])
def search_law(q: str = Query(..., min_length=1, max_length=200)) -> list[LawSearchHit]:
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

    # 3. 다단어 부분 매치 폴백 — 위 변형(전체 문구 substring)이 전부 0건일 때,
    #    토큰별 substring 검색 후 매치 토큰 수로 순위. AND-전체는 토큰 하나만 코퍼스에
    #    없어도("낙찰하한율"류 예규 용어) 0건이 되므로, 2개 이상 매치를 통과선으로 한다.
    tokens = _keyword_tokens(keyword) if keyword else []
    if not results and len(tokens) >= 2:
        by_ref: dict[str, tuple[int, str, dict]] = {}  # ref → (매치수, doc, meta)
        for t in tokens[:6]:
            r = col.get(
                where_document={"$contains": t},
                include=["documents", "metadatas"],
                limit=100,
            )
            for doc, meta in zip(r.get("documents") or [], r.get("metadatas") or []):
                ref = meta.get("law_ref") or ""
                cnt = by_ref[ref][0] + 1 if ref in by_ref else 1
                by_ref[ref] = (cnt, doc, meta)
        ranked = sorted(
            ((cnt, doc, meta) for cnt, doc, meta in by_ref.values() if cnt >= 2),
            key=lambda x: -x[0],
        )
        for cnt, doc, meta in ranked:
            law_ref = meta.get("law_ref") or ""
            if law_ref in seen_refs:
                continue
            seen_refs.add(law_ref)
            hit_token = next((t for t in tokens if t in doc), tokens[0])
            results.append(LawSearchHit(
                law_name=meta.get("law_name") or "",
                article=meta.get("article_titles") or "",
                content=_clean_markers(doc),
                snippet=_clean_markers(_make_snippet(doc, hit_token)),
                law_ref=law_ref,
            ))
            if len(results) >= 30:
                break

    # 4. 시맨틱 폴백 (Gemini 임베딩) — substring이 전혀 안 걸리는 자연어 질의 구제.
    #    임베딩 호출 실패(쿼터 등)는 조용히 빈 결과 유지(검색 기능 자체는 죽이지 않음).
    if not results and keyword:
        try:
            qr = col.query(
                query_texts=[keyword], n_results=8,
                include=["documents", "metadatas"],
            )
            for doc, meta in zip((qr.get("documents") or [[]])[0],
                                 (qr.get("metadatas") or [[]])[0]):
                law_ref = meta.get("law_ref") or ""
                if law_ref in seen_refs:
                    continue
                seen_refs.add(law_ref)
                results.append(LawSearchHit(
                    law_name=meta.get("law_name") or "",
                    article=meta.get("article_titles") or "",
                    content=_clean_markers(doc),
                    snippet=_clean_markers(_make_snippet(doc, keyword)),
                    law_ref=law_ref,
                ))
        except Exception:  # noqa: BLE001 — 임베딩 장애 시 키워드 결과만으로 동작
            pass

    return results


# ── 판례·법령해석례 라이브 프록시 (law.go.kr DRF, 2026-07-30) ────────────────
# 코퍼스 인덱싱 대신 실시간 조회 — 항상 현행, 저장·재색인 부담 0. LLM 미사용.
# MCP search_cases/get_case 도구가 사용한다. 외부 API 장애는 502로 정직하게 전달.
_LAW_DRF = "http://www.law.go.kr/DRF"


def _law_oc() -> str:
    # run.sh는 .env를 export하지 않는다 — 키는 pydantic Settings(.env 로드)에서 읽는다.
    oc = (get_settings().law_api_key or "").strip()
    if not oc:
        raise HTTPException(503, "LAW_API_KEY 미설정 — 판례 조회 비활성")
    return oc


def _drf_get(path: str, params: dict) -> str:
    import httpx
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as c:
            r = c.get(f"{_LAW_DRF}/{path}", params=params)
            r.raise_for_status()
            return r.text
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"law.go.kr 조회 실패: {type(exc).__name__}")


def _cdata(tag: str, block: str) -> str:
    m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.S)
    return (m.group(1).strip() if m else "").replace("<br/>", " ")


@router.get("/cases")
def search_cases(
    request: Request,
    q: str = Query(..., min_length=2, max_length=100),
    top_k: int = Query(5, ge=1, le=10),
    kind: str = Query("all", pattern="^(prec|expc|all)$"),
) -> list[dict]:
    """판례(prec)·법령해석례(expc) 검색 — 사건명·기관·일자·일련번호 목록.

    본문은 /law/case?kind=&case_id= 로 이어서 조회. 검색어는 사건명 기준이므로
    '부정당업자 제한', '유찰 수의계약'처럼 핵심 명사 위주가 잘 잡힌다.
    """
    from backend.services.rate_limiter import get_rate_limiter, LIMITS_LLM
    limiter = get_rate_limiter()
    limiter.record(limiter.check(request, LIMITS_LLM))  # check만으론 카운트 미적립 — 계상 필수
    oc = _law_oc()
    out: list[dict] = []
    kinds = ["prec", "expc"] if kind == "all" else [kind]
    for k in kinds:
        xml = _drf_get("lawSearch.do", {"OC": oc, "target": k, "type": "XML",
                                        "display": top_k, "query": q})
        for block in re.findall(rf"<{k} id=.*?</{k}>", xml, re.S):
            if k == "prec":
                out.append({
                    "kind": "prec",
                    "case_id": _cdata("판례일련번호", block),
                    "title": _cdata("사건명", block),
                    "org": _cdata("법원명", block),
                    "case_no": _cdata("사건번호", block),
                    "date": _cdata("선고일자", block),
                })
            else:
                out.append({
                    "kind": "expc",
                    "case_id": _cdata("법령해석례일련번호", block),
                    "title": _cdata("안건명", block),
                    # 검색 응답은 회신기관/회신일자, 본문 응답은 해석기관/해석일자 — 명칭이 다르다
                    "org": _cdata("회신기관명", block) or _cdata("해석기관명", block),
                    "case_no": _cdata("안건번호", block),
                    "date": _cdata("회신일자", block) or _cdata("해석일자", block),
                })
    return out


@router.get("/case")
def get_case(
    request: Request,
    kind: str = Query(..., pattern="^(prec|expc)$"),
    case_id: str = Query(..., min_length=1, max_length=20),
) -> dict:
    """판례/해석례 본문 — 판시사항·판결요지·참조조문(판례), 질의요지·회답·이유(해석례)."""
    from backend.services.rate_limiter import get_rate_limiter, LIMITS_LLM
    limiter = get_rate_limiter()
    limiter.record(limiter.check(request, LIMITS_LLM))  # check만으론 카운트 미적립 — 계상 필수
    xml = _drf_get("lawService.do", {"OC": _law_oc(), "target": kind,
                                     "ID": case_id, "type": "XML"})
    def _f(tag: str, limit: int = 2500) -> str:
        v = _cdata(tag, xml)
        return v[:limit] + ("…(생략)" if len(v) > limit else "")
    # 2026-07-30 R9 (배터리 report_issue 제보): 검색(lawSearch)에는 뜨지만 본문
    # API가 "일치하는 판례가 없습니다"를 주는 판례(하급심 등 본문 미제공)를
    # 전 필드 빈 문자열로 조용히 넘기던 결함 — 구조화 오류+행동 지침으로 대체.
    if "일치하는" in xml or not (_cdata("사건명", xml) or _cdata("안건명", xml)):
        return {"error": "case_body_unavailable", "kind": kind, "case_id": case_id,
                "hint": "이 판례·해석례는 law.go.kr에 본문이 제공되지 않습니다"
                        "(하급심·타기관 제공 등). 검색 결과의 사건명·사건번호를 그대로"
                        " 인용하되 본문 근거가 필요하면 다른 판례를 조회하세요."}
    if kind == "prec":
        return {"kind": "prec", "case_id": case_id,
                "title": _f("사건명"), "org": _f("법원명"), "case_no": _f("사건번호"),
                "date": _f("선고일자"), "issue": _f("판시사항"),
                "summary": _f("판결요지"), "referenced_laws": _f("참조조문", 800)}
    return {"kind": "expc", "case_id": case_id,
            "title": _f("안건명"), "org": _f("해석기관명"), "case_no": _f("안건번호"),
            "date": _f("해석일자"), "question": _f("질의요지"),
            "answer": _f("회답"), "reasoning": _f("이유", 4000)}
