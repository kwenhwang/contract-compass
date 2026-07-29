"""공공SW사업 법제도 가이드(공개 배포본) 2024.12 → 2025.11 갱신 인덱싱.

ETL 경로(parse_pdf→chunk_document)로 2025.11 버전을 청킹해 public_guides 컬렉션에
적재한다. document_id는 service_sw_guide_2025로 override, 기존 2024 청크는 교체 삭제.
원본 PDF는 repo에 포함되지 않는다 — 운영자가 data/source_docs/에 넣는다.

--dry  : 파싱·청킹만 (인덱싱 안 함, 청크 수·샘플 출력)
실인덱싱: 기존 2024 청크 삭제 → 2025 upsert → BM25 재구축은 별도(tools/build_bm25_index.py)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from etl.parsers.pdf_parser import parse_pdf                       # noqa: E402
from etl.chunkers.semantic_chunker import chunk_document           # noqa: E402
from etl.loaders.chroma_loader import (                            # noqa: E402
    PUBLIC_GUIDES_COLLECTION, get_client, upsert_chunks,
)

SOURCE_DOCS_DIR = ROOT / "data" / "source_docs"
PDF_GLOB = "*공공SW사업*가이드*2025*.pdf"  # 예: (배포본)_공공SW사업_법제도관리감독_및_지원_가이드(2025.11).pdf
NEW_DOC = "service_sw_guide_2025"
OLD_DOC = "service_sw_guide_2024"
CHUNKS_OUT = ROOT / "etl" / "data" / "chunks" / f"chunks_{NEW_DOC}.jsonl"


def find_pdf() -> Path | None:
    if not SOURCE_DOCS_DIR.exists():
        return None
    matches = sorted(SOURCE_DOCS_DIR.glob(PDF_GLOB))
    return matches[0] if matches else None


def build_chunks(pdf: Path) -> list[dict]:
    raw = parse_pdf(pdf)
    print(f"parse_pdf: document_id={raw['document_id']} contract_type={raw['contract_type']} "
          f"sections={raw['total_sections']}")
    raw["contract_type"] = "service"  # 메타 태그 보장 (SW사업 = 용역 계열)
    chunks = chunk_document(raw)
    for c in chunks:
        c["document_id"] = NEW_DOC
        c["contract_type"] = "service"
    return chunks


def main() -> int:
    pdf = find_pdf()
    if pdf is None:
        print(f"공공SW사업 가이드 2025 PDF를 찾지 못했습니다: {SOURCE_DOCS_DIR}/{PDF_GLOB}")
        print("  공개 배포본 PDF를 내려받아 위 경로에 넣은 뒤 재실행하세요.")
        return 1

    dry = "--dry" in sys.argv
    chunks = build_chunks(pdf)
    lens = [len(c["content"]) for c in chunks]
    print(f"\n청크 {len(chunks)}개 | 길이 min={min(lens)} avg={sum(lens)//len(lens)} max={max(lens)}")
    print("--- 섹션 제목 샘플 ---")
    seen = []
    for c in chunks:
        t = c["section_title"]
        if t not in seen:
            seen.append(t)
    for t in seen[:25]:
        print(f"  · {t[:50]}")

    CHUNKS_OUT.parent.mkdir(parents=True, exist_ok=True)
    CHUNKS_OUT.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n", encoding="utf-8"
    )
    print(f"\n청크 저장: {CHUNKS_OUT.name}")

    if dry:
        print("\n[DRY-RUN] 인덱싱 생략. 검증 후 --dry 없이 재실행.")
        return 0

    # 실인덱싱: 기존 2024 삭제 → 2025 upsert
    client = get_client()
    col = client.get_or_create_collection(PUBLIC_GUIDES_COLLECTION)
    old = col.get(where={"document_id": OLD_DOC}, include=[])
    if old["ids"]:
        col.delete(ids=old["ids"])
        print(f"기존 {OLD_DOC} {len(old['ids'])}청크 삭제")
    upsert_chunks(client, CHUNKS_OUT)
    final = col.get(where={"document_id": NEW_DOC}, include=[])
    print(f"✅ {NEW_DOC} 인덱싱: {len(final['ids'])}청크 (전체 {len(chunks)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
