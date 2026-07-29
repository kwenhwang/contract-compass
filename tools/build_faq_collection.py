"""public_guides Q&A 패턴 청크 추출 → faq 컬렉션 생성.

public_guides 컬렉션(공개 간행물 코퍼스)은 그대로 유지하고, Q&A 형식이 포함된 청크를
복제해 별도 faq 컬렉션에 저장한다. 검색 풀에 추가되어 자연스럽게 가중치 효과.

부스팅(인위적 점수 조작) 없이, 같은 청크가 두 컬렉션에 존재함으로써
BM25·Dense 양쪽에서 한 번 더 ranking 후보가 됨 (RRF가 자연스럽게 활용).

chunk_id는 'faq_' prefix로 구분 → 검색측 dedup이 별도 청크로 인식.

전제: tools/reindex_qa.py 등으로 public_guides 컬렉션을 먼저 구축.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chromadb

from backend.config import get_settings
from backend.services.embedding import GeminiEmbeddingFunction

_settings = get_settings()
CHROMA_PATH = _settings.chroma_path
SOURCE_COLLECTION = _settings.collection_public_guides
FAQ_COLLECTION = _settings.collection_faq
# Q&A 패턴: "Q1.", "Q :", "Q1 :", "Q: 한글" 등
QA_RE = re.compile(r"(?:^|\n)\s*Q\s*[\d.: ]|Q\s*:\s*[가-힣]")


def main() -> int:
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef = GeminiEmbeddingFunction()

    try:
        col = client.get_collection(SOURCE_COLLECTION, embedding_function=ef)
    except Exception:
        print(f"❌ {SOURCE_COLLECTION} 컬렉션 없음 — 먼저 tools/reindex_qa.py 등으로 "
              "공개 간행물 코퍼스를 인덱싱하세요.")
        return 1

    # 기존 faq 있으면 재생성 (정확히 동일 데이터)
    try:
        client.delete_collection(FAQ_COLLECTION)
        print(f"기존 {FAQ_COLLECTION} 삭제")
    except Exception:
        pass

    faq_col = client.create_collection(
        name=FAQ_COLLECTION,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    r = col.get(include=["documents", "metadatas"])
    ids = r["ids"]
    docs = r["documents"]
    metas = r["metadatas"]

    faq_ids, faq_docs, faq_metas = [], [], []
    for cid, doc, m in zip(ids, docs, metas):
        if not doc or not QA_RE.search(doc):
            continue
        new_meta = dict(m or {})
        new_meta["source_collection"] = SOURCE_COLLECTION
        new_meta["original_chunk_id"] = cid
        new_meta["is_faq"] = True
        faq_ids.append(f"faq_{cid}")
        faq_docs.append(doc)
        faq_metas.append(new_meta)

    # 배치 upsert (큰 컬렉션 한 번에 안 됨)
    BATCH = 100
    for i in range(0, len(faq_ids), BATCH):
        faq_col.add(
            ids=faq_ids[i:i + BATCH],
            documents=faq_docs[i:i + BATCH],
            metadatas=faq_metas[i:i + BATCH],
        )
    print(f"  [{SOURCE_COLLECTION}] Q&A 청크 {len(faq_ids)}건 → {FAQ_COLLECTION}")

    final_count = faq_col.count()
    print(f"\n✅ {FAQ_COLLECTION} 생성 완료: {final_count}건 (예상 {len(faq_ids)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
