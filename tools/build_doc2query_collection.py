"""doc2query 컬렉션 적재 — 청크별 가상질문 jsonl을 doc2query 컬렉션에 넣는다.

입력 jsonl 형식(줄당): {"question": "...", "original_chunk_id": "...", "original_collection": "law_articles"}
사용: python3 tools/build_doc2query_collection.py <questions.jsonl> [--reset]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chromadb  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.services.embedding import GeminiEmbeddingFunction  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", help="가상질문 jsonl 경로")
    ap.add_argument("--reset", action="store_true", help="기존 컬렉션 삭제 후 재적재")
    args = ap.parse_args()

    rows = []
    for line in Path(args.jsonl).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("question") and d.get("original_chunk_id"):
            rows.append(d)
    if not rows:
        print("적재할 가상질문 없음")
        sys.exit(1)

    settings = get_settings()
    client = chromadb.PersistentClient(path=settings.chroma_path)
    if args.reset:
        try:
            client.delete_collection(settings.collection_doc2query)
            print("기존 doc2query 삭제")
        except Exception:
            pass
    col = client.get_or_create_collection(settings.collection_doc2query,
                                          embedding_function=GeminiEmbeddingFunction())

    BATCH = 500
    for i in range(0, len(rows), BATCH):
        part = rows[i:i + BATCH]
        col.add(
            ids=[f"d2q_{i + j:06d}" for j in range(len(part))],
            documents=[r["question"] for r in part],
            metadatas=[{
                "original_chunk_id": r["original_chunk_id"],
                "original_collection": r.get("original_collection", settings.collection_law_articles),
            } for r in part],
        )
        print(f"적재 {min(i + BATCH, len(rows))}/{len(rows)}")
    print(f"doc2query 완료: {col.count()}건")


if __name__ == "__main__":
    main()
