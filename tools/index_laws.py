"""law.go.kr XML 법령 파일 → law_articles ChromaDB 인덱싱.

사용법:
  python3 tools/index_laws.py --dir tools/laws/
  python3 tools/index_laws.py --dir tools/laws/ --reset  # 기존 삭제 후 재인덱싱
"""
import sys, argparse, re
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chromadb

from backend.config import get_settings

_settings = get_settings()
CHROMA_PATH = _settings.chroma_path
LAW_COLLECTION = _settings.collection_law_articles


def parse_law_xml(xml_path: Path) -> list[dict]:
    """법령 XML → 조문별 청크 리스트.

    법령 XML 구조:
      <법령> → <조문단위> → <조문번호> + <조문내용>
    조가 많으면 조별로 분리, 짧으면 묶어서 청크.
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"  XML 파싱 오류 ({xml_path.name}): {e}")
        return []

    law_name = (
        root.findtext("기본정보/법령명약칭")    # 약칭 우선 (예: 국가계약법 시행령)
        or root.findtext("기본정보/법령명_한글")
        or root.findtext("기본정보/법령명한글")
        or root.findtext("법령명한글")
        or xml_path.stem.replace("_", " ")
    )
    promulgation = (
        root.findtext("기본정보/시행일자")
        or root.findtext("시행일자")
        or ""
    )

    chunks = []
    articles = root.findall(".//조문단위")

    if not articles:
        text = " ".join(root.itertext()).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > 50:
            chunks.append({
                "law_ref":        law_name,
                "law_name":       law_name,
                "content":        f"{law_name}\n{text[:2000]}",
                "article_titles": "",
            })
        return chunks

    # 조문별 1:1 청크 (정확한 조문 번호 검색 보장)
    for art in articles:
        art_num  = art.findtext("조문번호") or ""
        art_sub  = art.findtext("조문가지번호") or ""
        art_cont = art.findtext("조문내용") or ""

        # 항·호·목 내용 수집 — 문서 순서 워커 (2026-07-17 정정: 기존엔 목(目)내용과
        # 항·호·목 번호가 통째로 누락 — 소액수의 금액 한도(4억/2천만/1억 등)가 전부
        # 목에 있어 법령 RAG가 금액 질의에 답을 못 찾던 근본 원인)
        def _hang_full_text(hang) -> str:
            parts = []
            for el in hang.iter():
                if el.tag in ("항번호", "항내용", "호번호", "호내용", "목번호", "목내용") \
                        and el.text and el.text.strip():
                    parts.append(el.text.strip())
            return " ".join(parts)

        hang_texts = [_hang_full_text(h) for h in art.findall(".//항")]

        full_text = art_cont + "\n" + "\n".join(hang_texts)
        full_text = re.sub(r"\s+", " ", full_text).strip()
        if not full_text or len(full_text) < 5:
            continue

        # 조문 번호 문자열 (예: "제21조", "제4조의2")
        art_id = f"제{art_num}조"
        if art_sub:
            art_id += f"의{art_sub}"

        ref = f"{law_name} {art_id}"
        hang_list = art.findall(".//항")
        is_long = len(full_text) >= 800 or len(hang_list) >= 3

        # 부모 청크 — 조문 전체 (계층화 시 maxlen 확대해 맥락 보존)
        chunks.append({
            "law_ref":        ref,
            "law_name":       law_name,
            "content":        f"{law_name} {art_id}\n{full_text[:2000]}",
            "article_titles": art_id,
            "chunk_level":    "parent" if is_long else "single",
            "parent_ref":     "",
        })

        # 자식 청크 — 긴 조문의 항(項) 단위. 정밀 검색용, 부모 조문으로 맥락 보강(auto-merge)
        if is_long:
            for hi, hang in enumerate(hang_list, 1):
                h_content = re.sub(r"\s+", " ", _hang_full_text(hang)).strip()
                if len(h_content) < 10:
                    continue
                child_id = f"{art_id} 제{hi}항"
                # 2026-07-17: 1200자 절단이 긴 항(지방령 25조① 5,000자+)의 호·목을
                # 잘라먹지 않도록 3000자 단위 분할 저장
                for pi in range(0, len(h_content), 3000):
                    seg = h_content[pi:pi + 3000]
                    seg_id = child_id if pi == 0 else f"{child_id} (계속{pi // 3000})"
                    chunks.append({
                        "law_ref":        f"{law_name} {seg_id}",
                        "law_name":       law_name,
                        "content":        f"{law_name} {child_id}\n{seg}",
                        "article_titles": child_id,
                        "chunk_level":    "child",
                        "parent_ref":     ref,
                    })

    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="tools/laws", help="XML 파일 디렉터리")
    parser.add_argument("--reset", action="store_true", help="기존 law_articles 컬렉션 삭제 후 재생성")
    args = parser.parse_args()

    xml_dir = Path(args.dir)
    if not xml_dir.exists():
        print(f"디렉터리 없음: {xml_dir}")
        sys.exit(1)

    xml_files = sorted(xml_dir.glob("*.xml"))
    if not xml_files:
        print(f"XML 파일 없음: {xml_dir}")
        sys.exit(1)

    client = chromadb.PersistentClient(path=CHROMA_PATH)

    if args.reset:
        try:
            client.delete_collection(LAW_COLLECTION)
            print(f"기존 컬렉션 삭제: {LAW_COLLECTION}")
        except Exception:
            pass

    # 임베딩은 기존(default) 유지 — rag_service·law.py가 ef 없이 검색하므로 일관성 유지.
    # hierarchy(부모 조문 + 자식 항)의 정밀 검색은 BM25 키워드 매칭이 담당(임베딩 교체 불필요).
    collection = client.get_or_create_collection(name=LAW_COLLECTION)

    all_ids, all_docs, all_metas = [], [], []

    for xml_path in xml_files:
        print(f"파싱: {xml_path.name} ...", end=" ", flush=True)
        chunks = parse_law_xml(xml_path)
        print(f"{len(chunks)}개 청크")

        for i, c in enumerate(chunks):
            safe_id = re.sub(r"[^a-zA-Z0-9가-힣_-]", "_", c["law_ref"])
            chunk_id = f"law_{safe_id}_{i:03d}"
            all_ids.append(chunk_id)
            all_docs.append(c["content"])
            all_metas.append({
                "law_ref":        c["law_ref"],
                "law_name":       c["law_name"],
                "chunk_type":     "law_article",
                "article_titles": c["article_titles"],
                "chunk_level":    c.get("chunk_level", "single"),
                "parent_ref":     c.get("parent_ref", ""),
            })

    if not all_ids:
        print("인덱싱할 데이터 없음")
        return

    # 배치 upsert
    batch = 50
    for i in range(0, len(all_ids), batch):
        collection.upsert(
            ids=all_ids[i:i+batch],
            documents=all_docs[i:i+batch],
            metadatas=all_metas[i:i+batch],
        )

    print(f"\n완료: {len(all_ids)}개 조문 청크 → {LAW_COLLECTION}")
    _final = collection.count()
    print(f"현재 컬렉션 총 청크: {_final}")
    # 2026-07-24: 서빙 코퍼스 신선도 기록 — /ready가 이 값으로 재색인 정체를 감시(chroma mtime 무효 대체)
    from backend.services.index_status import record_index_status
    record_index_status(LAW_COLLECTION, _final, "index_laws.py")


if __name__ == "__main__":
    main()
