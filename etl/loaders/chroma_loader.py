"""청크 JSONL → ChromaDB upsert. chunk_hash 기반 증분 업데이트.

공개 코퍼스 3원 체제:
  - public_guides : 공개 간행물(감사원 공공계약 실무가이드 등) 문서 청크 — 이 로더의 기본 대상
  - law_articles  : 법령 조문 (tools/index_laws.py가 XML 원문으로 정본 인덱싱, 여기서는
                    rules/law_registry.json 파생 upsert만 담당)
  - admin_rules   : 계약예규 등 행정규칙 (tools/index_admin_rules.py 담당)
컬렉션명은 backend.config.get_settings()의 collection_* 설정이 단일 출처.
"""
import json
import re
import sys
from pathlib import Path

import chromadb

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import BASE_DIR, get_settings  # noqa: E402

_settings = get_settings()

PUBLIC_GUIDES_COLLECTION = _settings.collection_public_guides
LAW_ARTICLES_COLLECTION = _settings.collection_law_articles
RULE_SUMMARIES_COLLECTION = "rule_summaries"  # rules/contract_rules.json 요약 검색용
CHROMA_PATH = _settings.chroma_path


def _get_ef():
    """다국어 임베딩 함수 반환 (sentence-transformers 로컬 모델)."""
    try:
        from backend.services.embedding import GeminiEmbeddingFunction
        return GeminiEmbeddingFunction()
    except Exception:
        return None


def get_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=CHROMA_PATH)


def init_collections(client: chromadb.ClientAPI):
    """모든 컬렉션 초기화 (없으면 생성)."""
    ef = _get_ef()
    kw = {"embedding_function": ef} if ef else {}
    all_names = [
        PUBLIC_GUIDES_COLLECTION,
        _settings.collection_admin_rules,
        _settings.collection_faq,
        RULE_SUMMARIES_COLLECTION,
    ]
    for name in all_names:
        client.get_or_create_collection(name=name, **kw)
    # law_articles는 기본 임베딩 유지 (tools/index_laws.py와 일관)
    client.get_or_create_collection(name=LAW_ARTICLES_COLLECTION)
    print(f"컬렉션 초기화 완료: {all_names + [LAW_ARTICLES_COLLECTION]}")


def _upsert_to_collection(client: chromadb.ClientAPI, collection_name: str, chunks: list[dict]):
    """청크 목록을 지정된 컬렉션에 upsert (hash 기반 증분)."""
    ef = _get_ef()
    kw = {"embedding_function": ef} if ef else {}
    collection = client.get_or_create_collection(name=collection_name, **kw)

    existing = collection.get(include=["metadatas"])
    existing_hashes = {
        m.get("chunk_hash") for m in existing["metadatas"]
    } if existing["metadatas"] else set()

    new_chunks = [c for c in chunks if c["chunk_hash"] not in existing_hashes]
    if not new_chunks:
        print(f"  [{collection_name}] 변경 없음 (전체 {len(chunks)}개)")
        return

    batch_size = 100
    for i in range(0, len(new_chunks), batch_size):
        batch = new_chunks[i:i + batch_size]
        collection.upsert(
            ids=[c["chunk_id"] for c in batch],
            documents=[c["content"] for c in batch],
            metadatas=[{
                "document_id": c["document_id"],
                "contract_type": c["contract_type"],
                "section_id": c["section_id"],
                "section_title": c["section_title"],
                "chunk_type": c["chunk_type"],
                "keywords": ",".join(c.get("keywords", [])),
                "law_refs": ",".join(c.get("law_refs", [])),
                "chunk_hash": c["chunk_hash"],
            } for c in batch],
        )

    print(f"  [{collection_name}] {len(new_chunks)}개 신규 upsert (전체 {len(chunks)}개)")


def upsert_chunks(client: chromadb.ClientAPI, chunks_file: Path):
    """JSONL 청크 파일을 public_guides 컬렉션에 upsert.

    문서 유형(contract_type)은 메타데이터로만 남긴다 — 컬렉션 분기는 하지 않는다.
    """
    with open(chunks_file, encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f if line.strip()]

    if not chunks:
        return

    _upsert_to_collection(client, PUBLIC_GUIDES_COLLECTION, chunks)


def upsert_rules(client: chromadb.ClientAPI, rules_file: Path):
    """contract_rules.json을 rule_summaries 컬렉션에 upsert."""
    with open(rules_file, encoding="utf-8") as f:
        rules_data = json.load(f)

    collection = client.get_or_create_collection(name=RULE_SUMMARIES_COLLECTION)
    collection.delete(where={"document_id": {"$ne": ""}})

    rules = rules_data.get("rules", [])
    if not rules:
        return

    documents, metadatas, ids = [], [], []

    for rule in rules:
        content = (
            f"규칙ID: {rule['rule_id']}\n"
            f"계약유형: {rule['contract_type']}\n"
            f"규칙명: {rule.get('name', '')}\n"
            f"계약방법: {rule['result'].get('method', '')}\n"
            f"법적근거: {', '.join(rule.get('legal_basis', []))}\n"
            f"조건: {json.dumps(rule.get('conditions', {}), ensure_ascii=False)}\n"
            f"결과: {json.dumps(rule.get('result', {}), ensure_ascii=False)}"
        )
        documents.append(content)
        metadatas.append({
            "rule_id": rule["rule_id"],
            "contract_type": rule["contract_type"],
            "document_id": "contract_rules",
            "chunk_type": "rule",
            "method": rule["result"].get("method", ""),
            "chunk_hash": f"rule_{rule['rule_id']}",
        })
        ids.append(f"rule_{rule['rule_id']}")

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    print(f"  [{RULE_SUMMARIES_COLLECTION}] {len(rules)}개 규칙 upsert")


def upsert_law_articles(client: chromadb.ClientAPI, law_registry: dict):
    """법령 조문 레지스트리를 law_articles 컬렉션에 upsert.

    law_registry 형식:
      {canonical_ref: {law_name, promulgation, articles: [{title, body}]}}
    """
    collection = client.get_or_create_collection(name=LAW_ARTICLES_COLLECTION)

    documents, metadatas, ids = [], [], []

    for law_ref, entry in law_registry.items():
        law_name = entry.get("law_name", "")
        promulgation = entry.get("promulgation", "")
        articles = entry.get("articles", [])

        parts = [law_name]
        if promulgation:
            parts.append(promulgation)
        for art in articles:
            parts.append(f"\n{art['title']}")
            parts.append(art["body"])
        content = "\n".join(parts)

        safe_id = re.sub(r"[^a-zA-Z0-9가-힣]", "_", law_ref)
        documents.append(content)
        metadatas.append({
            "law_ref": law_ref,
            "law_name": law_name,
            "chunk_type": "law_article",
            "article_titles": ",".join(a["title"] for a in articles),
        })
        ids.append(f"law_{safe_id}")

    if not documents:
        return

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    print(f"  [{LAW_ARTICLES_COLLECTION}] {len(documents)}개 법령 조문 upsert")


if __name__ == "__main__":
    client = get_client()
    init_collections(client)

    chunks_dir = BASE_DIR / "etl" / "data" / "chunks"
    if chunks_dir.exists():
        for chunks_file in sorted(chunks_dir.glob("chunks_*.jsonl")):
            print(f"로딩: {chunks_file.name}")
            upsert_chunks(client, chunks_file)
    else:
        print(f"청크 디렉터리 없음: {chunks_dir} — etl/run_etl.py를 먼저 실행")

    rules_file = Path(_settings.rules_path)
    if rules_file.exists():
        print("규칙 JSON 로딩")
        upsert_rules(client, rules_file)

    registry_file = BASE_DIR / "rules" / "law_registry.json"
    if registry_file.exists():
        print("법령 조문 로딩")
        registry = json.loads(registry_file.read_text(encoding="utf-8"))["registry"]
        upsert_law_articles(client, registry)
