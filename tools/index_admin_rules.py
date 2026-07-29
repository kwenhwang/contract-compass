"""행정규칙 청크 JSONL → ChromaDB `admin_rules` 컬렉션 upsert."""
import json
import sys
from pathlib import Path

import chromadb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings
from backend.services.embedding import GeminiEmbeddingFunction

_settings = get_settings()
CHROMA_PATH = _settings.chroma_path
CHUNKS_PATH = Path(__file__).parent / "admin_rules" / "admin_rules_chunks.jsonl"
COLLECTION = _settings.collection_admin_rules


def main() -> int:
    if not CHUNKS_PATH.exists():
        print(f"❌ {CHUNKS_PATH} 없음 — 먼저 parse_admin_rules.py 실행")
        return 1

    chunks: list[dict] = []
    for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            chunks.append(json.loads(line))
    print(f"청크 {len(chunks)}개 로드")

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef = GeminiEmbeddingFunction()
    col = client.get_or_create_collection(name=COLLECTION, embedding_function=ef)
    print(f"컬렉션: {COLLECTION} (현재 {col.count()}개)")

    ids = [c["chunk_id"] for c in chunks]
    docs = [c["content"] for c in chunks]
    metas: list[dict] = []
    for c in chunks:
        metas.append({
            "source_type": c.get("source_type", "admin_rule"),
            "law_name": c.get("law_name", ""),
            "law_ref": c.get("law_ref", ""),
            "section_title": c.get("section_title", ""),
        })

    col.upsert(ids=ids, documents=docs, metadatas=metas)
    print(f"✅ upsert 완료 — 현재 컬렉션 청크: {col.count()}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
