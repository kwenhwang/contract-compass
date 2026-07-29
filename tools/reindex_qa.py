"""감사원 「공공계약 실무가이드」(공개 간행물) → public_guides 컬렉션 인덱싱.

원본 PDF는 repo에 포함되지 않는다 — 운영자가 data/source_docs/에 넣는다.
법령 인덱싱은 tools/index_laws.py, 행정규칙은 tools/index_admin_rules.py 담당.

실행:
  python3 tools/reindex_qa.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from etl.parsers.pdf_parser import parse_pdf                       # noqa: E402
from etl.chunkers.semantic_chunker import chunk_document           # noqa: E402
from etl.loaders.chroma_loader import (                            # noqa: E402
    PUBLIC_GUIDES_COLLECTION, _upsert_to_collection, get_client,
)

SOURCE_DOCS_DIR = ROOT / "data" / "source_docs"
DATA_DIR = ROOT / "etl" / "data"

# 파일명 매칭 패턴 — "(감사원)공공계약 실무가이드.pdf" 등 표기 흔들림 허용
GUIDE_GLOB = "*공공계약*실무가이드*.pdf"


def find_guide_pdf() -> Path | None:
    if not SOURCE_DOCS_DIR.exists():
        return None
    matches = sorted(SOURCE_DOCS_DIR.glob(GUIDE_GLOB))
    return matches[0] if matches else None


def delete_old_chunks(client, doc_id: str):
    """public_guides에서 해당 문서의 기존 청크 삭제."""
    try:
        col = client.get_collection(PUBLIC_GUIDES_COLLECTION)
    except Exception:
        return
    existing = col.get(where={"document_id": doc_id}, include=[])
    if existing["ids"]:
        col.delete(ids=existing["ids"])
        print(f"  [{PUBLIC_GUIDES_COLLECTION}] {doc_id} 기존 {len(existing['ids'])}개 청크 삭제")


def reindex_guide(client, pdf_file: Path):
    """실무가이드 파싱·청킹·재인덱싱."""
    print(f"\n[파싱] {pdf_file.name}")
    raw = parse_pdf(pdf_file)
    doc_id = raw["document_id"]

    raw_path = DATA_DIR / "raw" / f"raw_{doc_id}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  섹션: {raw['total_sections']}, document_id: {doc_id}")

    print("[청킹]")
    chunks = chunk_document(raw)
    chunks_path = DATA_DIR / "chunks" / f"chunks_{doc_id}.jsonl"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with open(chunks_path, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"  청크 수: {len(chunks)}")

    print(f"[ChromaDB upsert — {PUBLIC_GUIDES_COLLECTION}]")
    delete_old_chunks(client, doc_id)
    _upsert_to_collection(client, PUBLIC_GUIDES_COLLECTION, chunks)
    return doc_id


def main() -> int:
    pdf_file = find_guide_pdf()
    if pdf_file is None:
        print(f"감사원 공공계약 실무가이드 PDF를 찾지 못했습니다: {SOURCE_DOCS_DIR}/{GUIDE_GLOB}")
        print("  1) 감사원이 공개 배포하는 「공공계약 실무가이드」 PDF를 내려받아")
        print(f"  2) {SOURCE_DOCS_DIR}/ 에 넣은 뒤 재실행하세요.")
        return 1

    client = get_client()
    reindex_guide(client, pdf_file)

    print("\n=== 완료 ===")
    try:
        col = client.get_collection(PUBLIC_GUIDES_COLLECTION)
        print(f"  [{PUBLIC_GUIDES_COLLECTION}] {col.count()}개 청크")
    except Exception:
        pass
    print("  후속: python3 tools/build_faq_collection.py (Q&A 파생) → tools/build_bm25_index.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
