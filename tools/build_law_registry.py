"""rules/law_registry.json 의 조문 본문·공포정보를 tools/laws/*.xml 원문에서 재생성.

레지스트리를 수기 관리하면 법령이 개정될 때 XML만 갱신되고 레지스트리는 방치돼
의견서에 구 공포번호가 인쇄된다. 이 스크립트가 그 갱신을 기계화한다.

레지스트리 본문은 의견서 DOCX "관계 법규 조문" 부록에 **원문 그대로 인쇄**되므로
`tools/index_laws.py`(RAG 코퍼스)와 정규화 규칙이 다르다:
  - RAG는 항·호·목 번호 태그를 그대로 이어 붙인다(검색 재현율 우선, 중복 무해).
  - 레지스트리는 **중복 마커를 지운다** — law.go.kr XML은 `<항번호>①</항번호>` 다음
    `<항내용>① …</항내용>`처럼 마커를 내용에도 담아, 그대로 붙이면 사람이 읽는 문서에
    "① ① 각 중앙관서의 장은…"이 찍힌다(2026-07-28 발견).

사용법:
  python3 tools/build_law_registry.py --check   # 차이만 출력(쓰지 않음)
  python3 tools/build_law_registry.py           # rules/law_registry.json 갱신
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.lib.lawgo import norm_name  # noqa: E402

LAWS = ROOT / "tools" / "laws"
REGISTRY_PATH = ROOT / "rules" / "law_registry.json"

_ORDERED_TAGS = ("조문내용", "항번호", "항내용", "호번호", "호내용", "목번호", "목내용")
_NUM_TAGS = {"항번호", "호번호", "목번호"}

_L = "지방자치단체를_당사자로_하는_계약에_관한_법률"

# 레지스트리 키 → (XML 파일, 조번호, 조문가지번호). XML 원문이 있는 항목 전수.
# tests/unit/test_law_registry_integrity.py 가 이 표를 import 해 정합을 검사한다.
REGISTRY_SOURCES: dict[str, tuple[str, int, int | None]] = {
    # 국제입찰 근거 (2026-07-28 추가) — SVC_INTL_001·PRD_006이 인용하는데 레지스트리에
    # 없어서, method_law_keys 폴백이 대신 일반경쟁 조문(제7조·제42조)을 의견서에 붙이고
    # 있었다. 비어 보이지 않으니 드러나지 않던 오첨부.
    "국가계약법 제4조": ("국가를_당사자로_하는_계약에_관한_법률.xml", 4, None),
    "시행령 제2조": ("국가를_당사자로_하는_계약에_관한_법률_시행령.xml", 2, None),
    "국가계약법 제7조": ("국가를_당사자로_하는_계약에_관한_법률.xml", 7, None),
    "시행령 제21조": ("국가를_당사자로_하는_계약에_관한_법률_시행령.xml", 21, None),
    "시행령 제26조": ("국가를_당사자로_하는_계약에_관한_법률_시행령.xml", 26, None),
    "시행령 제27조": ("국가를_당사자로_하는_계약에_관한_법률_시행령.xml", 27, None),
    "시행령 제42조": ("국가를_당사자로_하는_계약에_관한_법률_시행령.xml", 42, None),
    "시행령 제43조": ("국가를_당사자로_하는_계약에_관한_법률_시행령.xml", 43, None),
    "시행규칙 제33조": ("국가를_당사자로_하는_계약에_관한_법률_시행규칙.xml", 33, None),
    "공기업계약사무규칙 제7조의2": ("공기업_준정부기관_계약사무규칙.xml", 7, 2),
    "계약사무규칙 제6조": ("공기업_준정부기관_계약사무규칙.xml", 6, None),
    "판로지원법 시행령 제2조의2": ("중소기업제품_구매촉진_및_판로지원에_관한_법률_시행령.xml", 2, 2),
    "중소기업제품구매촉진법 제6조": ("중소기업제품_구매촉진_및_판로지원에_관한_법률.xml", 6, None),
    "공공기관운영법 제44조": ("공공기관의_운영에_관한_법률.xml", 44, None),
    "소프트웨어 진흥법 제48조": ("소프트웨어_진흥법.xml", 48, None),
    "지방계약법 제9조": (f"{_L}.xml", 9, None),
    "지방계약법 시행령 제22조": (f"{_L}_시행령.xml", 22, None),
    "지방계약법 시행령 제25조": (f"{_L}_시행령.xml", 25, None),
    "지방계약법 시행령 제26조": (f"{_L}_시행령.xml", 26, None),
    "지방계약법 시행령 제27조": (f"{_L}_시행령.xml", 27, None),
    "지방계약법 시행규칙 제27조": (f"{_L}_시행규칙.xml", 27, None),
    "지방계약법 시행규칙 제30조": (f"{_L}_시행규칙.xml", 30, None),
    "지방계약법 시행규칙 제32조": (f"{_L}_시행규칙.xml", 32, None),
    "지방계약법 시행규칙 제33조": (f"{_L}_시행규칙.xml", 33, None),
}

# XML 원문이 없어 수기 관리하는 항목 — 재생성 대상에서 제외한다.
# 하드코딩 대신 레지스트리 데이터에서 판별한다: 이 생성기가 만드는 promulgation은
# 항상 "[시행 …]"으로 시작하므로, 그 형식이 아닌 항목(예규 수기 항목·기관 내부 규정 등)은
# XML 비보유로 간주한다. XML 형식인데 REGISTRY_SOURCES에 없는 항목은 여전히 차단된다
# (침묵 확장 방지 — tests/unit/test_law_registry_integrity.py가 검증).
def _load_non_xml_keys() -> frozenset[str]:
    try:
        reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["registry"]
    except (OSError, json.JSONDecodeError, KeyError):
        return frozenset()
    return frozenset(
        k for k, e in reg.items()
        if not re.match(r"^\[시행\s", (e or {}).get("promulgation", ""))
    )


NON_XML_KEYS = _load_non_xml_keys()


def ordered_parts(node: ET.Element, dedupe_markers: bool = True) -> list[str]:
    """조문 하위를 문서 순서로 평탄화. dedupe_markers면 중복 마커 태그를 버린다.

    `<항번호>①</항번호><항내용>① 각 …</항내용>` → ["① 각 …"]
    마커가 내용에 안 들어 있는 법령(표기 흔들림)에서는 번호 태그를 살린다.
    """
    parts: list[str] = []
    pending_num: str | None = None
    for el in node.iter():
        if el.tag not in _ORDERED_TAGS or not el.text or not el.text.strip():
            continue
        text = re.sub(r"<[^>]+>", "", el.text.strip())
        if not text:
            continue
        if dedupe_markers and el.tag in _NUM_TAGS:
            pending_num = text
            continue
        if pending_num:
            # 내용이 마커로 시작하지 않으면(표기 누락) 마커를 앞에 살려 붙인다.
            if not text.startswith(pending_num):
                text = f"{pending_num} {text}"
            pending_num = None
        parts.append(text)
    if pending_num:  # 내용 없는 마커(삭제 조항 등)는 버리지 않고 남긴다
        parts.append(pending_num)
    return parts


def article(path: Path, jonum: int, branch: int | None,
            dedupe_markers: bool = True) -> tuple[str, str]:
    """(조문 제목, 본문) 반환. 본문은 조문내용 + 항·호·목을 줄바꿈으로 이은 원문."""
    root = ET.parse(path).getroot()
    for jo in root.iter("조문단위"):
        if (jo.findtext("조문여부") or "") != "조문":
            continue
        if (jo.findtext("조문번호") or "") != str(jonum):
            continue
        b = jo.findtext("조문가지번호") or ""
        if branch and b != str(branch):
            continue
        if not branch and b not in ("", None, "0"):
            continue
        parts = ordered_parts(jo, dedupe_markers)
        title = re.sub(r"<[^>]+>", "", (jo.findtext("조문내용") or "").strip())
        return title, "\n".join(parts)
    raise SystemExit(f"{path.name} 제{jonum}조{f'의{branch}' if branch else ''} 없음")


def promulgation(path: Path) -> str:
    """XML 기본정보 → "[시행 2026.6.11.] [법률 제21418호, 2026.3.10., 일부개정]"."""
    root = ET.parse(path).getroot()
    g = lambda t: (root.findtext(f"기본정보/{t}") or "").strip()  # noqa: E731
    def dot(d: str) -> str:
        return f"{d[:4]}.{int(d[4:6])}.{int(d[6:8])}." if len(d) == 8 else d
    no = g("공포번호").lstrip("0") or g("공포번호")
    return (f"[시행 {dot(g('시행일자'))}] "
            f"[{g('법종구분')} 제{no}호, {dot(g('공포일자'))}, {g('제개정구분')}]")


def law_name(path: Path) -> str:
    return (ET.parse(path).getroot().findtext("기본정보/법령명_한글") or "").strip()


def main() -> int:
    check_only = "--check" in sys.argv
    doc = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    reg = doc["registry"]

    missing = set(reg) - set(REGISTRY_SOURCES) - NON_XML_KEYS
    if missing:
        print(f"⚠️ 관리 명단에 없는 레지스트리 항목: {missing}")
        print("   REGISTRY_SOURCES 또는 NON_XML_KEYS에 등록할 것 (침묵 확장 방지)")
        return 2

    changed = []
    for key, (fname, jo, br) in REGISTRY_SOURCES.items():
        path = LAWS / fname
        if not path.exists():
            print(f"⚠️ {key}: 원문 XML 없음 — {fname}")
            return 2
        entry = reg.setdefault(key, {})
        new_name, new_promul = law_name(path), promulgation(path)
        # 중점 표기(·/ㆍ)만 다르면 기존 표기를 유지한다 — law_name은 의견서·UI에 찍히는
        # 표시 문자열이고, 코드 곳곳(classify.py·Step3Page)이 '·' 형을 쓴다. 같은 법령을
        # 두 표기로 갈라 놓으면 얻는 것 없이 불일치만 생긴다.
        old_name = entry.get("law_name") or ""
        if old_name and norm_name(old_name) == norm_name(new_name):
            new_name = old_name
        title, body = article(path, jo, br)

        old_bodies = [a.get("body", "") for a in entry.get("articles", [])]
        diffs = []
        if entry.get("law_name") != new_name:
            diffs.append(f"law_name: {entry.get('law_name')!r} → {new_name!r}")
        if entry.get("promulgation") != new_promul:
            diffs.append(f"promulgation: {entry.get('promulgation')!r} → {new_promul!r}")
        if old_bodies != [body]:
            o = old_bodies[0] if old_bodies else ""
            diffs.append(f"body: {len(o)}자 → {len(body)}자")
        if not diffs:
            continue
        changed.append((key, diffs))
        if not check_only:
            entry["law_name"] = new_name
            entry["promulgation"] = new_promul
            entry["articles"] = [{"title": title, "body": body}]

    for key, diffs in changed:
        print(f"\n● {key}")
        for d in diffs:
            print(f"    {d}")

    if not changed:
        print("변경 없음 — 레지스트리가 원문과 일치.")
        return 0
    if check_only:
        print(f"\n{len(changed)}개 항목 변경 예정 (--check 이므로 쓰지 않음)")
        return 1

    doc["_meta"]["note_20260728"] = (
        "생성기 도입(tools/build_law_registry.py) — 본문·promulgation을 tools/laws XML에서 "
        "재생성. 항·호·목 중복 마커 제거(기존 '① ① …' 아티팩트 정정). 수기 편집 금지."
    )
    REGISTRY_PATH.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n✅ {len(changed)}개 항목 갱신 → {REGISTRY_PATH}")
    print("   다음: pytest tests/unit/test_law_registry_integrity.py -q")
    return 0


if __name__ == "__main__":
    sys.exit(main())
