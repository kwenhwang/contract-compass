"""시점(as-of) 법령 조회 — 특정 날짜에 **시행 중이던** 조문을 되살린다.

왜 필요한가: 공공계약 실무의 판단 기준은 '지금 법'이 아니라 **그 계약 당시 법**이다.
2023년에 체결한 계약의 적법성·감사 대응·분쟁은 2023년 시행 조문으로 따진다.
코퍼스(chroma)는 현행 스냅샷만 갖고 있고, `tools/lib/lawgo.find_exact`도 '오늘 기준
시행 중 최신'을 고르므로 과거 시점을 물어볼 경로가 없었다.

law.go.kr DRF 실측(2026-07-31):
  - `lawSearch.do?target=eflaw&query=<정식명>` → 시행일자별 연혁 목록(각 법령일련번호=MST)
  - `lawService.do?target=law&MST=<연혁MST>`   → 그 시점 본문 XML
  검증: 국가계약법 제27조가 시행 20180320판 1,810자 / 20260611판 2,111자로 실제 상이.

이 모듈은 **순수 함수만** 둔다(파싱·선택). HTTP는 호출자(backend/api/v1/law.py)가
기존 `_drf_get`으로 수행한다 — 네트워크 없이 단위테스트가 가능하도록.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATE_RE = re.compile(r"^\d{8}$")


def norm_name(s: str) -> str:
    """법령명 비교용 정규화 — 공백·중점 제거(‘공기업·준정부기관’ 표기 흔들림 흡수)."""
    return re.sub(r"[\s·ㆍ]", "", s or "")


@dataclass(frozen=True)
class LawVersion:
    ef_date: str      # 시행일자 YYYYMMDD
    mst: str          # 법령일련번호(연혁마다 다름)
    name: str         # 법령명한글
    revision: str     # 제개정구분명
    promul_no: str    # 공포번호
    promul_date: str  # 공포일자
    is_current: bool  # 현행연혁코드 == '현행'


# ── 법령명 해석 ──────────────────────────────────────────────────────
# 코퍼스 law_name은 XML 헤더의 '법령명약칭'(예: 국가계약법 시행령)에서 온다
# (tools/index_laws.py). 반면 eflaw 검색은 **정식명**으로만 걸린다(약칭 검색 미지원,
# 2026-07-31 실측). 그래서 로컬 스냅샷 헤더에서 약칭→정식명 표를 만든다.
# 별도 매핑표를 새로 두지 않는 이유: 같은 사실을 두 곳에 적으면 갈라진다.
def build_name_map(laws_dir: Path) -> dict[str, str]:
    """tools/laws/*.xml 헤더에서 {정규화된 약칭·정식명 → 정식명} 표를 만든다."""
    out: dict[str, str] = {}
    if not laws_dir.is_dir():
        return out
    for path in sorted(laws_dir.glob("*.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        official = (root.findtext("기본정보/법령명_한글")
                    or root.findtext("기본정보/법령명한글") or "").strip()
        if not official:
            continue
        abbr = (root.findtext("기본정보/법령명약칭") or "").strip()
        out[norm_name(official)] = official
        if abbr:
            out.setdefault(norm_name(abbr), official)
    return out


@lru_cache(maxsize=1)
def _cached_name_map(laws_dir_str: str) -> dict[str, str]:
    return build_name_map(Path(laws_dir_str))


def resolve_official_name(name: str, laws_dir: Path) -> str:
    """약칭·정식명 무엇이 오든 정식명으로. 모르는 법령이면 입력을 그대로 돌려준다
    (코퍼스 밖 법령도 정식명으로 부르면 시점 조회가 되도록)."""
    if not name:
        return ""
    return _cached_name_map(str(laws_dir)).get(norm_name(name), name.strip())


# ── 연혁 파싱·선택 ───────────────────────────────────────────────────
def parse_versions(xml_text: str, official_name: str) -> list[LawVersion]:
    """eflaw 검색 XML → 정확 일치 법령의 연혁 목록(시행일 오름차순, 중복 제거).

    정확 일치만 채택한다 — 부분일치를 허용하면 '국가계약법'에 시행령·시행규칙이
    섞여 엉뚱한 시점 본문을 집는다(오취득 사고 이력과 같은 계열의 함정).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    target = norm_name(official_name)
    seen: dict[tuple[str, str], LawVersion] = {}
    for law in root.iter("law"):
        nm = (law.findtext("법령명한글") or "").strip()
        if norm_name(nm) != target:
            continue
        ef = (law.findtext("시행일자") or "").strip()
        mst = (law.findtext("법령일련번호") or "").strip()
        if not _DATE_RE.match(ef) or not mst:
            continue
        seen[(ef, mst)] = LawVersion(
            ef_date=ef,
            mst=mst,
            name=nm,
            revision=(law.findtext("제개정구분명") or "").strip(),
            promul_no=(law.findtext("공포번호") or "").strip(),
            promul_date=(law.findtext("공포일자") or "").strip(),
            is_current=(law.findtext("현행연혁코드") or "").strip() == "현행",
        )
    return sorted(seen.values(), key=lambda v: (v.ef_date, v.mst))


def pick_asof(versions: list[LawVersion], date: str) -> LawVersion | None:
    """`date`에 시행 중이던 판 = 시행일자 ≤ date 중 가장 늦은 것."""
    eligible = [v for v in versions if v.ef_date <= date]
    return eligible[-1] if eligible else None


def neighbors(versions: list[LawVersion], chosen: LawVersion) -> tuple[str | None, str | None]:
    """선택된 판의 직전·직후 시행일 — 경계(개정 직전/직후) 인지용."""
    prev_d = next_d = None
    for v in versions:
        if v.ef_date < chosen.ef_date:
            prev_d = v.ef_date
        elif v.ef_date > chosen.ef_date and next_d is None:
            next_d = v.ef_date
    return prev_d, next_d


# ── 본문 추출 ────────────────────────────────────────────────────────
_ARTICLE_RE = re.compile(r"제(\d+)조(?:의(\d+))?")


def split_article_ref(article: str) -> tuple[str, str] | None:
    """'제7조의2' → ('7', '2'), '제27조' → ('27', '')."""
    m = _ARTICLE_RE.fullmatch(article.strip())
    if not m:
        return None
    return m.group(1), (m.group(2) or "")


def extract_article(xml_text: str, article: str) -> str | None:
    """법령 본문 XML에서 해당 조문 전문을 조립한다(조문내용 + 항·호·목).

    장(章) 표제 노드는 조문여부='전문'으로 그 장 첫 조문번호를 달고 있어
    실조문을 가릴 수 있다 — 명시적으로 제외한다(2026-07-30 스텁 청크 사고와 동일 함정).
    """
    parsed = split_article_ref(article)
    if parsed is None:
        return None
    want_no, want_sub = parsed

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    for jo in root.iter("조문단위"):
        if (jo.findtext("조문여부") or "").strip() == "전문":
            continue
        if (jo.findtext("조문번호") or "").strip() != want_no:
            continue
        if (jo.findtext("조문가지번호") or "").strip() != want_sub:
            continue

        lines: list[str] = []
        head = (jo.findtext("조문내용") or "").strip()
        if head:
            lines.append(head)
        for hang in jo.iter("항"):
            h = (hang.findtext("항내용") or "").strip()
            if h:
                lines.append(h)
            for ho in hang.iter("호"):
                ho_t = (ho.findtext("호내용") or "").strip()
                if ho_t:
                    lines.append(ho_t)
                for mok in ho.iter("목"):
                    for mt in mok.itertext():
                        mt = (mt or "").strip()
                        if mt:
                            lines.append(mt)
        return "\n".join(lines).strip() or None
    return None
