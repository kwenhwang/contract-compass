"""다운로드된 행정규칙 XML 파싱:
  1) 525호 / 중기부 심사기준 → 조문·별표 청크 JSONL (RAG 인덱싱용)
  2) 중기부 지정 고시의 첨부 PDF → 분류번호+품명 JSON (data/sme_competition_products.json)

전제: `python3 tools/fetch_admin_rules.py` 선행.
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

RULES_DIR = Path(__file__).parent / "admin_rules"
CHUNKS_OUT = RULES_DIR / "admin_rules_chunks.jsonl"
DATA_DIR = Path(__file__).parent.parent / "data"
SME_JSON = DATA_DIR / "sme_competition_products.json"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def parse_rule_chunks(xml_path: Path) -> list[dict]:
    """행정규칙 XML → 조문/별표 청크."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    rule_name = _norm(root.findtext(".//행정규칙명", ""))
    rule_no = _norm(root.findtext(".//발령번호", ""))

    chunks: list[dict] = []
    key = xml_path.stem

    # 조문
    for i, 조문 in enumerate(root.findall("조문내용")):
        content = (조문.text or "").strip()
        if not content:
            continue
        chunks.append({
            "chunk_id": f"{key}_art_{i:03d}",
            "source_type": "admin_rule",
            "law_name": rule_name,
            "law_ref": f"{rule_name} 제{i+1}조" if rule_no else rule_name,
            "section_title": content.split("\n")[0][:60],
            "content": content,
        })

    # 별표/별지
    for 별표 in root.findall(".//별표단위"):
        bnum = _norm(별표.findtext("별표번호", ""))
        bsub = _norm(별표.findtext("별표가지번호", "00"))
        btype = _norm(별표.findtext("별표구분", ""))
        title = _norm(별표.findtext("별표제목", ""))
        content = (별표.findtext("별표내용", "") or "").strip()
        if not content or len(content) < 50:
            continue
        sub_label = f"의{int(bsub)}" if bsub and int(bsub) > 0 else ""
        chunks.append({
            "chunk_id": f"{key}_{btype}_{bnum}_{bsub}",
            "source_type": "admin_rule",
            "law_name": rule_name,
            "law_ref": f"{rule_name} [{btype} {int(bnum)}{sub_label}] {title}",
            "section_title": f"[{btype} {int(bnum)}{sub_label}] {title}",
            "content": content,
        })
    return chunks


def find_designation_pdf_url(xml_path: Path) -> str | None:
    """지정 고시 XML에서 '개정 전문 ... .pdf' 첨부파일 URL 찾기."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    pairs: list[tuple[str, str]] = []
    attach = root.find("첨부파일")
    if attach is None:
        return None
    cur_name = None
    for c in attach:
        if c.tag == "첨부파일명":
            cur_name = (c.text or "").strip()
        elif c.tag == "첨부파일링크" and cur_name:
            pairs.append((cur_name, (c.text or "").strip()))
            cur_name = None

    # 우선순위: "지정 내역" + ".pdf" + "개정 전문"
    for name, url in pairs:
        if "지정 내역" in name and name.lower().endswith(".pdf") and "개정 전문" in name:
            return url
    for name, url in pairs:
        if "지정" in name and name.lower().endswith(".pdf"):
            return url
    return None


# PDF 표 구조 특이 케이스(코드 뒤가 숫자, 또는 [품명][코드] 역순)에 대한 수동 보정.
# 다음 fetch+parse 재실행 시에도 자동 반영됨.
_MANUAL_FALLBACK = {
    "2326150701": "3차원프린터",
    "2611170401": "산업용충전장치",
    "5046700501": "깍두기",
}


def extract_codes_from_pdf(pdf_path: Path) -> list[dict]:
    """PDF에서 (10자리 분류번호, 품명) 추출.

    전략: 모든 10자리 위치를 찾고, 각 코드 다음~다음 10자리 직전까지의 텍스트에서
    첫 한글/영문 시퀀스만 품명으로 사용. PDF가 공백 없이 코드를 붙여 출력하더라도 안전.
    매칭 실패한 누락 코드는 _MANUAL_FALLBACK으로 보정.
    """
    from pypdf import PdfReader

    r = PdfReader(str(pdf_path))
    text = ""
    for p in r.pages:
        text += (p.extract_text() or "") + "\n"

    positions = [(m.start(), m.group()) for m in re.finditer(r"\d{10}", text)]
    items: list[dict] = []
    seen_codes: set[str] = set()

    name_re = re.compile(r"[가-힣A-Za-z·,()\s\-]+")

    for i, (pos, code) in enumerate(positions):
        if code in seen_codes:
            continue
        next_pos = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        chunk = text[pos + 10 : next_pos]
        m = name_re.match(chunk)
        if not m:
            continue
        name = m.group().strip()
        # 끝의 잡문자(공백·전각공백·소문자 o) 정리
        name = re.sub(r"[\s　]+[oO]?\s*$", "", name).strip()
        # 'PDF', '뉴홈' 같은 인접 노이즈 제거: 연속된 공백 정규화
        name = re.sub(r"\s+", " ", name)
        if not (1 <= len(name) <= 80):
            continue
        seen_codes.add(code)
        items.append({"code": code, "name": name})

    # 누락 보정 — PDF에 존재하지만 정규식이 못 잡은 코드를 수동 매핑으로 추가
    all_codes_in_pdf = set(re.findall(r"\d{10}", text))
    for code, name in _MANUAL_FALLBACK.items():
        if code in all_codes_in_pdf and code not in seen_codes:
            items.append({"code": code, "name": name})
            seen_codes.add(code)
    return items


def main() -> int:
    # 1) 청크 JSONL — 모든 행정규칙 (sme_product_designation 제외 — 본문 거의 없음)
    all_chunks: list[dict] = []
    parse_keys = [
        "procurement_525",
        "smes_basis",
        "govt_bid_contract_exec",
        "qualification_review",
        "construction_comprehensive",
        "negotiation_contract",
        "service_general_conditions",
        "product_general_conditions",
        "software_contract_guide",
    ]
    for key in parse_keys:
        xml_path = RULES_DIR / f"{key}.xml"
        if not xml_path.exists():
            print(f"  ⚠️  {xml_path.name} 없음 — 건너뜀")
            continue
        chunks = parse_rule_chunks(xml_path)
        print(f"  ✅ {xml_path.name}: {len(chunks)}개 청크")
        all_chunks.extend(chunks)

    CHUNKS_OUT.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in all_chunks),
        encoding="utf-8",
    )
    print(f"\n청크 JSONL 저장: {CHUNKS_OUT} ({len(all_chunks)}건)")

    # 2) 분류번호 JSON (중기부 지정 고시 PDF에서)
    designation_xml = RULES_DIR / "sme_product_designation.xml"
    if not designation_xml.exists():
        print(f"\n⚠️  {designation_xml.name} 없음 — 분류번호 추출 건너뜀")
        return 0

    pdf_url = find_designation_pdf_url(designation_xml)
    if not pdf_url:
        print("\n⚠️  지정 PDF 첨부 못 찾음")
        return 0

    pdf_path = RULES_DIR / "sme_product_designation.pdf"
    if not pdf_path.exists():
        print(f"\nPDF 다운로드: {pdf_url}")
        req = urllib.request.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            pdf_path.write_bytes(resp.read())
        print(f"  ✅ {pdf_path.name} ({pdf_path.stat().st_size // 1024}KB)")

    items = extract_codes_from_pdf(pdf_path)
    print(f"\n추출된 분류번호: {len(items)}개")
    print("샘플 5개:")
    for it in items[:5]:
        print(f"  {it['code']} | {it['name'][:50]}")

    DATA_DIR.mkdir(exist_ok=True)
    SME_JSON.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {SME_JSON} ({len(items)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
