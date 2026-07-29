"""PDF → raw JSON 변환. PyMuPDF 기반 섹션/단락 구조 추출 + 법령 참조 메타데이터."""
import json
import re
from pathlib import Path
import fitz  # PyMuPDF


# 파일명 → 문서 유형(메타데이터) 힌트. 컬렉션 분기가 아니라 검색 필터·표시용 태그다.
# 매칭 실패 시 "general"로 둔다 — 어떤 공개 간행물이든 파이프라인에 넣을 수 있다.
CONTRACT_TYPE_MAP = {
    "공공계약 실무가이드": "general",
    "공공계약실무가이드": "general",
    "공공SW사업": "service",
    "공공sw사업": "service",
    "건설엔지니어링": "construction",
    "엔지니어링사업발주": "construction",
}

# 법령 참조 추출: 다양한 띄어쓰기/줄바꿈 허용
LAW_REF_PATTERN = re.compile(
    r'(?:국가계약법|국가를\s*당사자로\s*하는\s*계약에\s*관한\s*법률'
    r'|동법'
    r'|중소기업[^\s,。.]*?법'
    r'|공공기관\s*운영에\s*관한\s*법률|공공기관운영법'
    r'|공기업[^\s,。.]*?규칙'
    r'|소프트웨어\s*진흥법|소프트웨어산업\s*진흥법'
    r'|건설기술\s*진흥법'
    r'|조달사업[^\s,。.]*?법)'
    r'\s*(?:시행령\s*|시행규칙\s*)?제\s*\d+\s*조(?:의\s*\d+)?',
    re.UNICODE,
)

# 단독 "시행령 제N조" (앞에 법률명 없는 경우도 캡처 – 문맥에서 추론)
SIMPLE_LAW_REF = re.compile(r'시행령\s*제\s*\d+\s*조(?:의\s*\d+)?', re.UNICODE)


def _normalize_law_ref(raw: str) -> str:
    """법령 참조를 공백 정규화 후 표준 형식으로."""
    s = re.sub(r'\s+', ' ', raw).strip()
    s = re.sub(r'(국가계약법)(시행령)', r'\1 \2', s)
    s = re.sub(r'(시행령|규정|법률|진흥법)(제)', r'\1 \2', s)
    s = re.sub(r'(제\s*)(\d+)(\s*조)', lambda m: f"제{m.group(2)}조", s)
    return s


def extract_law_refs(text: str) -> list[str]:
    """텍스트에서 법령 참조 목록 추출 (정규화된 형식)."""
    refs = set()
    for m in LAW_REF_PATTERN.finditer(text):
        refs.add(_normalize_law_ref(m.group()))
    for m in SIMPLE_LAW_REF.finditer(text):
        refs.add(_normalize_law_ref(m.group()))
    return sorted(refs)


def _infer_contract_type(stem: str) -> tuple[str, str]:
    for key, val in CONTRACT_TYPE_MAP.items():
        if key.lower() in stem.lower():
            year_match = re.search(r'(20\d\d)', stem)
            if year_match:
                return val, f"{val}_{year_match.group(1)}"
            # 연도 없는 파일: 파일명 앞 6자 슬러그로 고유 ID 생성
            slug = re.sub(r'[^가-힣a-zA-Z0-9]', '', stem)[:6]
            return val, f"{val}_{slug}" if slug else f"{val}_etc"
    return "general", re.sub(r'[^가-힣a-zA-Z0-9_]', '_', stem)[:30]


def _is_junk(text: str) -> bool:
    """페이지 번호·특수기호만으로 된 줄 걸러내기."""
    stripped = text.strip()
    if not stripped:
        return True
    if re.match(r'^-?\s*\d+\s*-?\s*$', stripped):  # - 1 -
        return True
    # 의미없는 기호 (⦊ 등 단독)
    if len(stripped) <= 2 and not stripped.isalnum():
        return True
    return False


def _heading_level(size: float, h1_threshold: float) -> int:
    """폰트 크기 → 헤딩 레벨 (0=본문)."""
    if size >= h1_threshold:
        return 1
    if size >= 15.5:
        return 2
    return 0


def parse_pdf(file_path: Path) -> dict:
    """PDF 파일 → 섹션/단락 구조 dict. 법령 참조는 단락 메타로 포함."""
    doc = fitz.open(str(file_path))
    stem = file_path.stem
    contract_type, document_id = _infer_contract_type(stem)

    # 표지 제외 후 본문의 최대 폰트 크기로 h1 기준 설정
    max_body_size = 0.0
    for pg_num in range(2, min(12, len(doc))):
        page = doc[pg_num]
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    t = s["text"].strip()
                    if len(t) > 4 and not _is_junk(t):
                        max_body_size = max(max_body_size, s["size"])
    h1_threshold = max(max_body_size * 0.88, 17.0)

    sections: list[dict] = []
    current_section: dict | None = None

    def ensure_section():
        nonlocal current_section
        if current_section is None:
            current_section = {
                "section_id": f"{document_id}_s000",
                "title": "서론",
                "level": 1,
                "paragraphs": [],
                "tables": [],
            }
            sections.append(current_section)

    for pg_num in range(1, len(doc)):   # 표지(0페이지) 건너뜀
        page = doc[pg_num]
        blocks = page.get_text("dict")["blocks"]

        # 세로 위치 순 정렬
        sorted_blocks = sorted(blocks, key=lambda b: b.get("bbox", [0, 0])[1])

        for b in sorted_blocks:
            if b.get("type", 0) != 0:
                continue

            for line in b.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                line_text = " ".join(s["text"] for s in spans).strip()
                if _is_junk(line_text):
                    continue

                max_size = max(s["size"] for s in spans)
                level = _heading_level(max_size, h1_threshold)

                # 헤딩 조건: 짧고 번호/특수문자로 시작하거나 명백히 큰 글씨
                is_heading = (
                    level > 0
                    and len(line_text) < 80
                    and not line_text.startswith("◦")
                    and not line_text.startswith("·")
                    and not line_text.startswith("•")
                )

                if is_heading:
                    sec_id = f"{document_id}_s{len(sections):03d}"
                    current_section = {
                        "section_id": sec_id,
                        "title": line_text,
                        "level": level,
                        "paragraphs": [],
                        "tables": [],
                    }
                    sections.append(current_section)
                else:
                    ensure_section()
                    current_section["paragraphs"].append({"text": line_text})

    doc.close()

    year_m = re.search(r'(20\d\d)', stem)
    return {
        "document_id": document_id,
        "source_file": file_path.name,
        "contract_type": contract_type,
        "version": year_m.group(1) if year_m else "",
        "total_sections": len(sections),
        "total_tables": 0,
        "sections": sections,
    }


if __name__ == "__main__":
    _root = Path(__file__).resolve().parents[2]
    src_dir = _root / "data" / "source_docs"
    out_dir = _root / "etl" / "data" / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not src_dir.exists():
        raise SystemExit(f"소스 문서 디렉터리 없음: {src_dir} — 공개 간행물 PDF를 넣은 뒤 재실행")

    pdf_files = sorted(src_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"PDF 없음: {src_dir}")
    for fp in pdf_files:
        print(f"파싱 중: {fp.name}")
        result = parse_pdf(fp)
        out_path = out_dir / f"raw_{result['document_id']}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  → {out_path.name}  섹션:{result['total_sections']}")
