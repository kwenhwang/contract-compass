"""raw JSON → JSONL 청크. 섹션 단위로 분할, 표는 마크다운 변환. 법령 참조 추출 포함."""
import json
import hashlib
import re
from pathlib import Path


MAX_TOKENS_PER_CHUNK = 800
AVG_CHARS_PER_TOKEN = 2.5  # 한국어 기준 근사값

# Q&A 문서 경계 패턴: □ / ■ / ◆ 로 시작하는 질문
_QA_BOUNDARY_RE = re.compile(r'^[□■◆]')
# 감사원 컨설팅 사례 경계: 【신청일: 로 끝나는 제목줄
_CONSULTING_RE = re.compile(r'【신청일:')

# 법령 참조 추출 패턴 (chunker에서 사용)
# 2026-05-20: 18~21% 추출률 → 40%+ 목표
# 확장: 시행규칙 단독, 지방계약법, 엔지니어링/판로지원/물품관리법, 환경/회계예규, 「」괄호 표기 등
_LAW_REF_RE = re.compile(
    r'(?:'
    # 국가계약법 계열
    r'국가계약법|국가를\s*당사자로\s*하는\s*계약에\s*관한\s*법률'
    # 지방계약법 계열
    r'|지방계약법|지방자치단체를\s*당사자로\s*하는\s*계약에\s*관한\s*법률'
    # 공공기관
    r'|공공기관\s*운영에?\s*관한\s*법률|공공기관운영법'
    # 규칙·규정
    r'|동법|회계예규|예정가격작성기준'
    r'|공기업[^\s,。.]{0,10}?규칙'
    # 산업·진흥법
    r'|중소기업[^\s,。.]{0,15}?법|판로지원법'
    r'|소프트웨어[^\s,。.]{0,10}?법'
    r'|건설기술[^\s,。.]{0,10}?법'
    r'|엔지니어링[^\s,。.]{0,10}?법'
    r'|조달사업[^\s,。.]{0,10}?법'
    # 기타
    r'|물품관리법|문화재수리법|환경영향평가법'
    r')'
    r'\s*(?:시행령\s*|시행규칙\s*)?제\s*\d+\s*조(?:의\s*\d+)?'
    # 단독 시행령·시행규칙
    r'|시행령\s*제\s*\d+\s*조(?:의\s*\d+)?'
    r'|시행규칙\s*제\s*\d+\s*조(?:의\s*\d+)?'
    # 단독 약칭: 「영 제N조」(시행령 약칭), 「영 제N조 각 호」 — 같은 문서 내 자체 참조
    r'|(?<![가-힣A-Za-z])영\s*제\s*\d+\s*조(?:의\s*\d+)?'
    # 「같은 법」, 「동법」, 「본법」 + 제N조 (앞 문장의 법령을 가리키는 self-ref)
    r'|(?:같은\s*법|동법|본법)\s*제\s*\d+\s*조(?:의\s*\d+)?'
    # 「법령명」 괄호 표기 (예: 「국가계약법」 제7조)
    # 끝 키워드 확장: 법률·시행령·조건·유의서·기본조건 등 포함
    r'|「[^」]{2,50}?(?:법률|법|시행령|시행규칙|규칙|규정|예규|지침|고시|기준|기준서|요령|약관|령|조건|유의서|입찰서|규약)」\s*제\s*\d+\s*조(?:의\s*\d+)?',
    re.UNICODE,
)


def _extract_law_refs(text: str) -> list[str]:
    """텍스트에서 법령 참조 목록 추출 (정규화)."""
    refs = set()
    for m in _LAW_REF_RE.finditer(text):
        raw = re.sub(r'\s+', ' ', m.group()).strip()
        # 붙어있는 "국가계약법시행령" → "국가계약법 시행령"
        raw = re.sub(r'(국가계약법)(시행령)', r'\1 \2', raw)
        raw = re.sub(r'(시행령|규정|법률|진흥법)(제)', r'\1 \2', raw)
        raw = re.sub(r'제\s*(\d+)\s*조', r'제\1조', raw)
        refs.add(raw)
    return sorted(refs)


def _table_to_markdown(table: dict) -> str:
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if not headers:
        return ""

    md = "| " + " | ".join(str(h) for h in headers) + " |\n"
    md += "| " + " | ".join("---" for _ in headers) + " |\n"
    for row in rows[:30]:  # 최대 30행
        if isinstance(row, dict):
            vals = [str(row.get(h, "")) for h in headers]
        else:
            vals = [str(v) for v in row]
        md += "| " + " | ".join(v.replace("\n", " ") for v in vals) + " |\n"
    return md


def _chunk_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()[:16]


def _extract_keywords(text: str) -> list[str]:
    """금액 기준, 법령, 계약방법 관련 키워드 추출."""
    keywords = []
    # 금액 패턴
    keywords += re.findall(r"\d+(?:\.\d+)?(?:억|천만|백만|만)원?", text)
    # 법령 조항
    keywords += re.findall(r"(?:국가계약법|시행령|시행규칙|중소기업[^\s]*법)\s*[제]?\d+조", text)
    # 계약방법
    for kw in ["수의계약", "일반경쟁", "제한경쟁", "지명경쟁", "적격심사", "협상에 의한", "소액수의"]:
        if kw in text:
            keywords.append(kw)
    return list(set(keywords))[:10]


def _is_qa_boundary(text: str) -> bool:
    """단락이 Q&A 새 질문 시작점인지 판단."""
    return bool(_QA_BOUNDARY_RE.match(text))


def _is_consulting_boundary(text: str) -> bool:
    """감사원 사전컨설팅 사례 제목 경계 ('XX 관련 【신청일:')."""
    return bool(_CONSULTING_RE.search(text)) and len(text) < 100


def chunk_document(raw_json: dict) -> list[dict]:
    """문서 raw JSON을 청크 리스트로 변환.

    Q&A 패턴(□/■/◆)과 감사원 컨설팅 사례(【신청일:)는
    각 항목을 독립 청크로 분리하여 검색 정밀도를 높임.
    """
    contract_type = raw_json["contract_type"]
    document_id = raw_json["document_id"]
    chunks = []

    for section in raw_json.get("sections", []):
        section_id = section["section_id"]
        section_title = section["title"]

        # 단락 청크 (텍스트 누적)
        para_buffer: list[str] = []
        buffer_len = 0
        effective_title = section_title  # Q&A 경계마다 갱신

        # □ 경계 청크가 부모 섹션과 다른 제목을 가질 때 맥락 보존용
        in_subsection = False  # 현재 □ 하위 청크 진행 중 여부

        def flush_para_buffer(force: bool = False):
            nonlocal para_buffer, buffer_len
            if not para_buffer:
                return
            content = "\n".join(para_buffer)
            # □ 하위 청크에 부모 섹션 제목을 접두사로 삽입 (벡터·키워드 검색 정확도 향상)
            # 예: "[14. 계약금액의 조정]\n□ 조정요건\n◦ 90일..."
            if in_subsection and effective_title != section_title:
                enriched = f"[{section_title}]\n{content}"
            else:
                enriched = content
            # 2026-05-20: 50자 미만 부실 청크 자동 병합
            # flush 호출 시 짧으면 청크 추가 X, buffer 유지 → 다음 paragraph와 자연 합쳐짐
            # force=True (섹션 종료)일 때만 강제 flush
            if not force and len(enriched.strip()) < 50:
                return
            chunk = {
                "chunk_id": f"{section_id}_p{len(chunks):04d}",
                "document_id": document_id,
                "contract_type": contract_type,
                "section_id": section_id,
                "section_title": effective_title,
                "chunk_type": "paragraph",
                "content": enriched,
                "keywords": _extract_keywords(enriched),
                "law_refs": _extract_law_refs(enriched),
                "chunk_hash": _chunk_hash(enriched),
            }
            chunks.append(chunk)
            para_buffer = []
            buffer_len = 0

        for para in section.get("paragraphs", []):
            text = para["text"]
            text_len = len(text) / AVG_CHARS_PER_TOKEN

            # Q&A / 컨설팅 경계: 무조건 새 청크
            if _is_qa_boundary(text) or _is_consulting_boundary(text):
                flush_para_buffer()
                # Q&A 질문 텍스트를 제목으로 사용 (앞 □■◆ 제거)
                effective_title = re.sub(r'^[□■◆]\s*', '', text).strip()
                if len(effective_title) > 80:
                    effective_title = effective_title[:77] + "…"
                in_subsection = True

            elif buffer_len + text_len > MAX_TOKENS_PER_CHUNK:
                flush_para_buffer()
                # 일반 섹션은 원래 제목 유지
                effective_title = section_title
                in_subsection = False

            para_buffer.append(text)
            buffer_len += text_len

        flush_para_buffer(force=True)  # 섹션 종료 — 짧아도 강제 flush

        # 표 청크 (표마다 1개 청크)
        for table in section.get("tables", []):
            md = _table_to_markdown(table)
            if not md:
                continue
            content = f"[표] {section_title}\n\n{md}"
            chunk = {
                "chunk_id": f"{section_id}_t{table['table_index']:04d}",
                "document_id": document_id,
                "contract_type": contract_type,
                "section_id": section_id,
                "section_title": section_title,
                "chunk_type": "table",
                "table_index": table["table_index"],
                "content": content,
                "keywords": _extract_keywords(content),
                "law_refs": _extract_law_refs(content),
                "chunk_hash": _chunk_hash(content),
            }
            chunks.append(chunk)

    return chunks


if __name__ == "__main__":
    _root = Path(__file__).resolve().parents[2]
    raw_dir = _root / "etl" / "data" / "raw"
    chunks_dir = _root / "etl" / "data" / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists():
        raise SystemExit(f"raw 디렉터리 없음: {raw_dir} — 파서(etl/parsers)를 먼저 실행")

    for raw_file in sorted(raw_dir.glob("raw_*.json")):
        with open(raw_file, encoding="utf-8") as f:
            raw = json.load(f)
        chunks = chunk_document(raw)
        out_path = chunks_dir / f"chunks_{raw['document_id']}.jsonl"
        with open(out_path, "w", encoding="utf-8") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        print(f"{raw_file.name} → {len(chunks)} 청크")
