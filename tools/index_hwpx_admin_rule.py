"""HWPX 행정규칙 인덱서 — 조문 본문이 XML API로 제공되지 않고 HWPX 첨부로만
배포되는 행정규칙(예: 행안부 「지방자치단체 입찰시 낙찰자 결정기준」)을
admin_rules 컬렉션에 적재한다.

사용: python3 tools/index_hwpx_admin_rule.py "data/source_docs/<파일>.hwpx" --rule-name "지방자치단체 입찰시 낙찰자 결정기준"
"""
import argparse
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chromadb  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.services.embedding import GeminiEmbeddingFunction  # noqa: E402

CHUNK_MAX = 1200  # 항·별표 단위 분절 후 상한


def extract_hwpx_text(path: Path) -> str:
    """HWPX(zip)의 Contents/section*.xml에서 문단 텍스트 추출."""
    parts: list[str] = []
    with zipfile.ZipFile(path) as z:
        sections = sorted(n for n in z.namelist()
                          if n.startswith("Contents/section") and n.endswith(".xml"))
        for name in sections:
            root = ET.fromstring(z.read(name))
            # OWPML 네임스페이스가 버전마다 달라 로컬네임으로 매칭
            for p in root.iter():
                if p.tag.endswith("}p"):
                    texts = [t.text or "" for t in p.iter() if t.tag.endswith("}t")]
                    line = "".join(texts).strip()
                    if line:
                        parts.append(line)
    return "\n".join(parts)


def split_chunks(text: str, rule_name: str) -> list[dict]:
    """제N조·별표 경계로 분절, 길면 CHUNK_MAX 단위 재분할."""
    # 조문·별표 시작 지점 마커
    marker = re.compile(r"(?=^제\d+조(?:의\d+)?\s*\()|(?=^\[?별표\s*\d*)", re.M)
    segments = [s.strip() for s in marker.split(text) if s and s.strip()]
    if not segments:
        segments = [text]
    chunks: list[dict] = []
    for seg in segments:
        head = seg.split("\n", 1)[0][:60]
        art = re.match(r"제\d+조(?:의\d+)?", head)
        title = f"{rule_name} {art.group(0)}" if art else f"{rule_name} {head[:30]}"
        for i in range(0, len(seg), CHUNK_MAX):
            piece = seg[i:i + CHUNK_MAX]
            if len(piece.strip()) < 40:
                continue
            chunks.append({
                "title": title + (f" (계속{i // CHUNK_MAX})" if i else ""),
                "content": f"{title}\n{piece}",
                "article": art.group(0) if art else "",
            })
    return chunks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("hwpx", help="HWPX 파일 경로")
    ap.add_argument("--rule-name", required=True, help="행정규칙 정식 명칭")
    args = ap.parse_args()

    path = Path(args.hwpx)
    if not path.exists():
        print(f"파일 없음: {path}")
        sys.exit(1)

    text = extract_hwpx_text(path)
    if len(text) < 500:
        print(f"추출 텍스트가 비정상적으로 짧음({len(text)}자) — HWPX 구조 확인 필요")
        sys.exit(1)
    chunks = split_chunks(text, args.rule_name)
    print(f"추출 {len(text):,}자 → 청크 {len(chunks)}개")

    settings = get_settings()
    client = chromadb.PersistentClient(path=settings.chroma_path)
    col = client.get_or_create_collection(settings.collection_admin_rules,
                                          embedding_function=GeminiEmbeddingFunction())
    slug = re.sub(r"[^0-9A-Za-z가-힣]", "_", args.rule_name)
    # 동일 규칙 기존 청크 교체
    try:
        old = col.get(where={"rule_slug": slug})
        if old.get("ids"):
            col.delete(ids=old["ids"])
            print(f"기존 {len(old['ids'])}청크 삭제")
    except Exception:
        pass
    ids = [f"admrul_{slug}_{i:03d}" for i in range(len(chunks))]
    col.add(
        ids=ids,
        documents=[c["content"] for c in chunks],
        metadatas=[{
            "section_title": c["title"],
            "law_ref": c["title"],
            "law_name": args.rule_name,
            "article_titles": c["article"],
            "rule_slug": slug,
            "source": path.name,
        } for c in chunks],
    )
    print(f"admin_rules 적재 완료: {len(chunks)}청크 (총 {col.count()}건)")


if __name__ == "__main__":
    main()
