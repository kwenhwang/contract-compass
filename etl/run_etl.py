"""ETL 파이프라인 진입점 — data/source_docs/의 공개 문서(PDF/DOCX)를 인덱싱.

Usage:
  python -m etl.run_etl --all                  # data/source_docs/ 내 PDF·DOCX 전체
  python -m etl.run_etl --file <path>          # 단일 DOCX 또는 PDF
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etl.parsers.docx_parser import parse_docx
from etl.parsers.pdf_parser import parse_pdf
from etl.chunkers.semantic_chunker import chunk_document
from etl.loaders.chroma_loader import (
    get_client, init_collections, upsert_chunks, upsert_law_articles,
)
from backend.config import BASE_DIR

SOURCE_DOCS_DIR = BASE_DIR / "data" / "source_docs"
DATA_DIR = BASE_DIR / "etl" / "data"


def run_pipeline(doc_file: Path, client) -> str:
    """단일 파일 ETL. 파싱→청킹→ChromaDB upsert. 처리된 document_id 반환."""
    print(f"\n[1/3] 파싱: {doc_file.name}")
    if doc_file.suffix.lower() == ".pdf":
        raw = parse_pdf(doc_file)
    else:
        raw = parse_docx(doc_file)

    doc_id = raw["document_id"]
    raw_path = DATA_DIR / "raw" / f"raw_{doc_id}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"  섹션: {raw['total_sections']}, document_id: {doc_id}")

    print("[2/3] 청킹")
    chunks = chunk_document(raw)
    chunks_path = DATA_DIR / "chunks" / f"chunks_{doc_id}.jsonl"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with open(chunks_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    print(f"  청크 수: {len(chunks)}")

    print("[3/3] ChromaDB upsert")
    upsert_chunks(client, chunks_path)
    return doc_id


def _finalize(client):
    """법령 조문 레지스트리 upsert."""
    registry_file = BASE_DIR / "rules" / "law_registry.json"
    if registry_file.exists():
        print("\n법령 조문 레지스트리 로딩")
        registry = json.loads(registry_file.read_text(encoding="utf-8"))["registry"]
        upsert_law_articles(client, registry)


def main():
    parser = argparse.ArgumentParser(description="공개 문서 ETL 파이프라인 (public_guides 코퍼스)")
    parser.add_argument("--all", action="store_true",
                        help=f"소스 문서 전체 처리 ({SOURCE_DOCS_DIR}/*.pdf|*.docx)")
    parser.add_argument("--file", type=str, help="단일 파일 경로 (DOCX 또는 PDF)")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="제외할 파일명 부분 문자열")
    args = parser.parse_args()

    client = get_client()
    init_collections(client)

    if args.all:
        if not SOURCE_DOCS_DIR.exists():
            print(f"소스 문서 디렉터리 없음: {SOURCE_DOCS_DIR}")
            print("  운영자가 공개 간행물(PDF/DOCX)을 이 경로에 넣은 뒤 재실행하세요.")
            return
        doc_files = sorted(
            list(SOURCE_DOCS_DIR.glob("*.pdf")) + list(SOURCE_DOCS_DIR.glob("*.docx")))
        if not doc_files:
            print(f"처리할 PDF/DOCX 없음: {SOURCE_DOCS_DIR}")
        for f in doc_files:
            if any(pat in f.name for pat in args.exclude):
                print(f"제외: {f.name}")
                continue
            run_pipeline(f, client)

    elif args.file:
        run_pipeline(Path(args.file), client)

    else:
        parser.print_help()
        return

    _finalize(client)


if __name__ == "__main__":
    main()
