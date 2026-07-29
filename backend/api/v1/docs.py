from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from backend.config import BASE_DIR
from backend.models.request import DocSearchRequest
from backend.models.response import DocSearchResponse, RagSource
from backend.api.deps import get_rag_service

router = APIRouter(prefix="/docs", tags=["docs"])

# document_id → reference 파일 매핑 (원문 PDF/DOCX 표시용, 공개 가이드만)
_REFERENCE_DIR = BASE_DIR / "reference"
_DOC_FILES: dict[str, str] = {
    "service_engineering_guide":"엔지니어링사업발주가이드라인.pdf",
    "service_sw_guide_2024":    "(배포본)_공공SW사업_법제도관리감독_및_지원_가이드(2024.12).pdf",
    "service_sw_guide_2025":    "(배포본)_공공SW사업_법제도관리감독_및_지원_가이드(2025.11).pdf",
    "construction_건설엔지니어": "건설엔지니어링 질의회신 및 판례집_최종.pdf",
    "general_감사원공공계":      "(감사원)공공계약 실무가이드.pdf",
}


@router.get("/source/{document_id}")
async def get_source_document(document_id: str):
    """원문 PDF/DOCX 파일 반환 — SourceDrawer에서 PDF viewer로 표시.

    document_id는 chunk metadata의 document_id 그대로 (예: "service_2024").
    """
    fname = _DOC_FILES.get(document_id)
    if not fname:
        raise HTTPException(status_code=404, detail={"error": "unknown document_id", "id": document_id})
    fpath = _REFERENCE_DIR / fname
    if not fpath.exists():
        raise HTTPException(status_code=404, detail={"error": "file not found", "path": str(fpath)})
    media = "application/pdf" if fname.lower().endswith(".pdf") else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if fname.lower().endswith(".docx") else "application/octet-stream"
    )
    # inline 표시 (browser·PDF.js) — RFC 5987로 한글 파일명 안전 인코딩
    from urllib.parse import quote
    cd = f"inline; filename*=UTF-8''{quote(fname)}"
    return FileResponse(
        path=str(fpath),
        media_type=media,
        headers={"Content-Disposition": cd},
    )


@router.get("/source-list")
async def list_source_documents() -> dict:
    """등록된 원문 매핑 목록 — 프런트에서 어떤 document_id가 PDF 보기 가능한지 확인."""
    return {
        "documents": [
            {"document_id": did, "filename": fname,
             "available": (_REFERENCE_DIR / fname).exists()}
            for did, fname in _DOC_FILES.items()
        ]
    }


@router.post("/search", response_model=DocSearchResponse)
async def search_docs(req: DocSearchRequest, rag=Depends(get_rag_service)):
    content_chunks, law_chunks = rag.search_with_references(
        req.query, req.contract_type, top_k=req.top_k
    )
    results = [
        RagSource(
            chunk_id=c["chunk_id"],
            section_title=c["section_title"],
            excerpt=c["content"][:400],
            relevance_score=c["relevance_score"],
        )
        for c in content_chunks + law_chunks
    ]
    return DocSearchResponse(results=results)
