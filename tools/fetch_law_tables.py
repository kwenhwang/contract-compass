"""법령 별표·행정규칙 전문 PDF 수집 → admin_rules 컬렉션 적재.

law.go.kr API의 두 경로로 '별표' 공백(2026-07-30 MCP 실질문 QA에서 확인)을 메운다:
  1) target=licbyl(별표서식 검색) — 법령 별표 PDF (부정당 제재기준, 하자담보책임기간 등)
  2) 행정규칙 XML의 첨부파일 링크 — 조달청 적격심사 세부기준 '개정전문' PDF(별표 포함)

전문 PDF는 본문이 이미 admin_rules에 인덱스돼 있으므로 첫 '별표' 표지부터만 취한다.
청크는 chunk_id 접두사 `byl_`로 업서트(재실행 시 갱신, idempotent).

사용: LAW_API_KEY=... python3 tools/fetch_law_tables.py            # 수집+적재
     python3 tools/fetch_law_tables.py --fetch-only               # 다운로드까지만
이후 BM25 재구축: python3 tools/build_bm25_index.py
"""
from __future__ import annotations

import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OC = os.environ.get("LAW_API_KEY", "test")
OUT_DIR = Path(__file__).parent / "law_tables"
OUT_DIR.mkdir(exist_ok=True)
RULES_DIR = Path(__file__).parent / "admin_rules"
# 700자 창 + 200자 중첩(2026-07-30): rag_service의 모든 검색 경로가 content를
# 800자로 절단한다 — 1,200자 청크는 꼬리 400자가 회수돼도 안 보였다(배터리
# 업체-062). 800 미만 창이면 절단 자체가 불가능하고, 중첩이 행 단위 정보의
# 경계 분단을 흡수한다.
CHUNK_MAX = 700
CHUNK_STEP = 500

# ── 1) 법령 별표 (licbyl 검색: 쿼리 → 관련법령명·별표명으로 정확 선별) ─────────
LICBYL_TARGETS = [
    {"query": "부정당업자 입찰참가자격", "law": "국가를 당사자로 하는 계약에 관한 법률 시행규칙",
     "label": "국가계약법 시행규칙 별표2 부정당업자 제한기준", "key": "byl_nat_debarment"},
    {"query": "부정당업자 입찰참가자격", "law": "지방자치단체를 당사자로 하는 계약에 관한 법률 시행규칙",
     "label": "지방계약법 시행규칙 별표 부정당업자 제한기준", "key": "byl_local_debarment"},
    {"query": "하자담보책임기간", "law": "건설산업기본법 시행령",
     "label": "건설산업기본법 시행령 별표4 하자담보책임기간", "key": "byl_defect_period"},
]

# ── 2) 행정규칙 전문 첨부(별표 포함) — 이미 받아 둔 XML에서 PDF 링크 추출 ───────
ADMRUL_FULLTEXT = [
    {"xml": "ppa_construction_qual.xml", "label": "조달청 시설공사 적격심사세부기준 별표", "key": "byl_ppa_cst_qual"},
    {"xml": "ppa_product_qual.xml", "label": "조달청 물품구매적격심사 세부기준 별표", "key": "byl_ppa_prd_qual"},
    {"xml": "ppa_service_qual.xml", "label": "조달청 일반용역 적격심사 세부기준 별표", "key": "byl_ppa_svc_qual"},
    {"xml": "ppa_tech_service_qual.xml", "label": "조달청 기술용역 적격심사 세부기준 별표", "key": "byl_ppa_tech_qual"},
]

_UA = {"User-Agent": "contract-compass-etl/1.0"}


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _licbyl_pdf_url(query: str, law: str) -> str | None:
    """별표서식 검색에서 관련법령명이 일치하는 첫 항목의 PDF 링크."""
    url = ("http://www.law.go.kr/DRF/lawSearch.do?"
           f"OC={OC}&target=licbyl&type=XML&display=20&query={urllib.parse.quote(query)}")
    xml = _http_get(url).decode("utf-8", errors="ignore")
    for block in re.findall(r"<licbyl .*?</licbyl>", xml, re.S):
        law_name = re.search(r"<관련법령명><!\[CDATA\[(.*?)\]\]>", block)
        pdf = re.search(r"<별표서식PDF파일링크>(.*?)</별표서식PDF파일링크>", block)
        if law_name and pdf and law_name.group(1).strip() == law:
            return "http://www.law.go.kr" + pdf.group(1).strip()
    return None


def _admrul_fulltext_pdf_url(xml_path: Path) -> str | None:
    """행정규칙 XML의 첨부파일 중 '전문' PDF 링크."""
    s = xml_path.read_text(encoding="utf-8", errors="ignore")
    pairs = re.findall(
        r"<첨부파일명><!\[CDATA\[(.*?)\]\]>\s*</첨부파일명>\s*<첨부파일링크>(.*?)\s*</첨부파일링크>",
        s, re.S)
    for name, link in pairs:
        if name.lower().endswith(".pdf") and ("전문" in name or "별표" in name):
            return link.strip()
    return None


def _pdf_text(path: Path) -> str:
    """PDF 텍스트 추출 — 글리프 좌표로 어절 공백 복원.

    법제처 별표 PDF는 어절 경계가 공백 문자가 아니라 글리프 간격으로만 표현돼
    get_text()가 '계약을체결또는이행하지않은자'식 무공백 텍스트를 뱉는다
    (2026-07-30 배터리 업체-062 — BM25 토큰·substring 검색 전멸). 문자 bbox
    간격이 글자 크기 대비 크게 벌어진 지점을 공백으로 복원한다(실측: 어절 간
    4.8~6.0pt vs 자내 ±0.1pt).
    """
    import fitz  # pymupdf
    doc = fitz.open(path)
    pages = []
    for page in doc:
        lines = []
        for block in page.get_text("rawdict").get("blocks", []):
            for line in block.get("lines", []):
                buf: list[str] = []
                prev_x1: float | None = None
                for span in line.get("spans", []):
                    size = span.get("size") or 10.0
                    for ch in span.get("chars", []):
                        if prev_x1 is not None and ch["bbox"][0] - prev_x1 > size * 0.15:
                            buf.append(" ")
                        buf.append(ch["c"])
                        prev_x1 = ch["bbox"][2]
                lines.append(re.sub(r" {2,}", " ", "".join(buf)).strip())
        pages.append("\n".join(l for l in lines if l))
    return "\n".join(pages)


def _chunks(text: str, label: str, key: str) -> list[dict]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    out = []
    for n, i in enumerate(range(0, max(len(text) - (CHUNK_MAX - CHUNK_STEP), 1), CHUNK_STEP)):
        out.append({
            "chunk_id": f"{key}_{n:03d}",
            "content": f"[{label}]\n{text[i:i + CHUNK_MAX]}",
            "law_name": label,
            "law_ref": label,
            "section_title": label,
            "source_type": "law_table",
        })
    return out


def main() -> int:
    fetch_only = "--fetch-only" in sys.argv
    all_chunks: list[dict] = []

    for t in LICBYL_TARGETS:
        url = _licbyl_pdf_url(t["query"], t["law"])
        if not url:
            print(f"  ⚠️  {t['label']}: licbyl 검색 실패")
            continue
        pdf = OUT_DIR / f"{t['key']}.pdf"
        pdf.write_bytes(_http_get(url))
        text = _pdf_text(pdf)
        cs = _chunks(text, t["label"], t["key"])
        all_chunks += cs
        print(f"  ✅ {t['label']}: {len(pdf.read_bytes())//1024}KB → {len(cs)}청크")

    for t in ADMRUL_FULLTEXT:
        xml_path = RULES_DIR / t["xml"]
        if not xml_path.exists():
            print(f"  ⚠️  {t['xml']} 없음 — fetch_admin_rules.py 먼저")
            continue
        url = _admrul_fulltext_pdf_url(xml_path)
        if not url:
            print(f"  ⚠️  {t['label']}: 전문 PDF 첨부 미발견")
            continue
        pdf = OUT_DIR / f"{t['key']}.pdf"
        pdf.write_bytes(_http_get(url))
        text = _pdf_text(pdf)
        # 본문은 이미 인덱스됨 — 첫 별표 표지부터만 취해 중복 최소화
        m = re.search(r"\[?별\s*표\s*1?\]?", text)
        if m:
            text = text[m.start():]
        cs = _chunks(text, t["label"], t["key"])
        all_chunks += cs
        print(f"  ✅ {t['label']}: {len(pdf.read_bytes())//1024}KB → {len(cs)}청크(별표부)")

    print(f"\n총 {len(all_chunks)}청크")
    if fetch_only or not all_chunks:
        return 0

    import chromadb
    from backend.config import get_settings
    from backend.services.embedding import GeminiEmbeddingFunction
    col = chromadb.PersistentClient(get_settings().chroma_path).get_collection(
        get_settings().collection_admin_rules, embedding_function=GeminiEmbeddingFunction())
    before = col.count()
    # 공백 복원으로 청크 수가 달라질 수 있음 — 이번에 재생성된 라벨의 기존 청크를
    # 먼저 지워 잔재(구 무공백 꼬리 청크)가 남지 않게 한다. upsert만으론 못 지움.
    for label in {c["law_ref"] for c in all_chunks}:
        col.delete(where={"law_ref": label})
    col.upsert(
        ids=[c["chunk_id"] for c in all_chunks],
        documents=[c["content"] for c in all_chunks],
        metadatas=[{k: c[k] for k in ("law_name", "law_ref", "section_title", "source_type")}
                   for c in all_chunks])
    print(f"admin_rules 컬렉션: {before} → {col.count()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
