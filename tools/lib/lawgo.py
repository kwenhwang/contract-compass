"""law.go.kr(국가법령정보센터) 오픈API 공통 헬퍼 + 법령 스냅샷 매니페스트.

`tools/check_law_freshness.py`·`tools/build_law_registry.py`가 공유하는 부분만 모은다
(HTTP 재시도·에러정책은 각 스크립트 고유).

> 추출 배경: MST 조회 코드가 여러 스크립트에 중복돼 있었고, 사본마다 "법령명 정확 일치
> 실패 시 검색 첫 결과를 사용"하는 폴백을 갖고 있었다. 그 폴백 때문에 `"물품관리법"`
> 질의가 「공유재산 및 물품 관리법」을 가져와 무관 법령이 RAG 코퍼스에 들어갔다.
> 여기서는 `find_exact()`가 **폴백 없이** None을 반환한다 — 조용한 오취득 재발 방지.
"""
from __future__ import annotations

import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SEARCH_URL = "http://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "http://www.law.go.kr/DRF/lawService.do"
LAWS_DIR = ROOT / "tools" / "laws"
UA = {"User-Agent": "Mozilla/5.0"}


def load_api_key(env_var: str = "LAW_API_KEY") -> str:
    """API 키 로딩 — 환경변수 우선, 없으면 repo 루트 `.env`. 둘 다 없으면 SystemExit."""
    if v := os.environ.get(env_var, "").strip():
        return v
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{env_var}="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    raise SystemExit(f"{env_var} 미설정 — 환경변수 또는 {env_path}에 설정할 것")


def load_oc() -> str:
    """law.go.kr OC 키(=LAW_API_KEY). 환경변수 우선, 없으면 .env."""
    return load_api_key("LAW_API_KEY")


def norm_name(s: str) -> str:
    """법령명 비교용 정규화 — 중점(·/ㆍ) 통일 + 공백 제거.

    law.go.kr은 「공기업ㆍ준정부기관 계약사무규칙」처럼 중점을 'ㆍ'로 쓰고,
    우리 코드·문서는 '·'로 쓴다. 공백도 표기가 흔들린다(건설기술 진흥법).
    """
    return (s or "").replace("ㆍ", "·").replace("‧", "·").replace("・", "·").replace(" ", "")


def norm_no(s: str) -> str:
    """공포번호 비교용 정규화 — '제00028호'·'28'·'00028'을 모두 '28'로."""
    import re
    m = re.search(r"\d+", s or "")
    return str(int(m.group())) if m else ""


# ── 스냅샷 매니페스트 ────────────────────────────────────────────────
# tools/laws/ 에 어떤 법령이 어떤 파일명으로 있어야 하는가의 **단일 출처**.
# fetch(취득)·check_law_freshness(감시)가 같은 표를 본다.
#   name   : law.go.kr 공식 법령명(정확 일치 대상)
#   target : "law" | "admrul"
# MST는 개정마다 바뀌는 연혁 식별자라 고정하지 않는다 —
# 오취득 방어는 MST 핀이 아니라 find_exact()의 정확 일치가 담당한다.
@dataclass(frozen=True)
class LawSpec:
    filename: str
    name: str
    target: str = "law"
    note: str = ""


LAW_MANIFEST: tuple[LawSpec, ...] = (
    # 국가계약 3종 — 룰엔진 결정론의 뼈대
    LawSpec("국가를_당사자로_하는_계약에_관한_법률.xml", "국가를 당사자로 하는 계약에 관한 법률"),
    LawSpec("국가를_당사자로_하는_계약에_관한_법률_시행령.xml", "국가를 당사자로 하는 계약에 관한 법률 시행령"),
    LawSpec("국가를_당사자로_하는_계약에_관한_법률_시행규칙.xml", "국가를 당사자로 하는 계약에 관한 법률 시행규칙"),
    # 지방계약 3종 (2026-05-26 추가)
    LawSpec("지방자치단체를_당사자로_하는_계약에_관한_법률.xml", "지방자치단체를 당사자로 하는 계약에 관한 법률"),
    LawSpec("지방자치단체를_당사자로_하는_계약에_관한_법률_시행령.xml", "지방자치단체를 당사자로 하는 계약에 관한 법률 시행령"),
    LawSpec("지방자치단체를_당사자로_하는_계약에_관한_법률_시행규칙.xml", "지방자치단체를 당사자로 하는 계약에 관한 법률 시행규칙"),
    # 공공기관·공기업 근거
    LawSpec("공공기관의_운영에_관한_법률.xml", "공공기관의 운영에 관한 법률"),
    LawSpec("공기업_준정부기관_계약사무규칙.xml", "공기업·준정부기관 계약사무규칙"),
    # 중소기업제품 판로지원(중기간 제한경쟁)
    LawSpec("중소기업제품_구매촉진_및_판로지원에_관한_법률.xml", "중소기업제품 구매촉진 및 판로지원에 관한 법률"),
    LawSpec("중소기업제품_구매촉진_및_판로지원에_관한_법률_시행령.xml", "중소기업제품 구매촉진 및 판로지원에 관한 법률 시행령"),
    # 공사·용역 종류 판정 근거
    LawSpec("건설기술_진흥법.xml", "건설기술 진흥법"),
    LawSpec("건설기술_진흥법_시행령.xml", "건설기술 진흥법 시행령"),
    LawSpec("엔지니어링산업_진흥법_시행령.xml", "엔지니어링산업 진흥법 시행령"),
    LawSpec("소프트웨어_진흥법.xml", "소프트웨어 진흥법"),
    # 물품 — lawNm 검색 1순위가 「공유재산 및 물품 관리법」이므로 정확 일치 필수
    LawSpec("물품관리법.xml", "물품관리법",
            note="검색 1순위가 「공유재산 및 물품 관리법」 — 폴백 취득 시 오취득됨"),
    LawSpec("물품관리법_시행령.xml", "물품관리법 시행령"),
    # 행정규칙(예규) — 조문내용이 비어 있고 실체는 별표/hwpx에 있음(index는 stub 1청크)
    LawSpec("지방자치단체_입찰시_낙찰자_결정기준.xml", "지방자치단체 입찰시 낙찰자 결정기준",
            target="admrul", note="조문내용 공란 — 별표 실체는 동명 hwpx(수동 반입)"),
    # 나라장터 투찰 정정·취소 등 빈출 질문 근거 (2026-07-30 배터리 업체-059 404 발견)
    LawSpec("전자조달의_이용_및_촉진에_관한_법률.xml", "전자조달의 이용 및 촉진에 관한 법률"),
    LawSpec("전자조달의_이용_및_촉진에_관한_법률_시행령.xml", "전자조달의 이용 및 촉진에 관한 법률 시행령"),
    LawSpec("전자조달의_이용_및_촉진에_관한_법률_시행규칙.xml", "전자조달의 이용 및 촉진에 관한 법률 시행규칙"),
    # 공백 주제 반입분 (2026-07-29 매니페스트 등재 — 반입 커밋 b048a51에서 누락)
    LawSpec("건설산업기본법.xml", "건설산업기본법"),
    LawSpec("건설산업기본법_시행령.xml", "건설산업기본법 시행령"),
    LawSpec("국가재정법.xml", "국가재정법"),
    LawSpec("지방재정법.xml", "지방재정법"),
)

# 감시·취득 대상이 아닌 수동 반입 파일(전문 hwpx 등)
MANUAL_FILES: frozenset[str] = frozenset({
    "지방자치단체_입찰시_낙찰자_결정기준_전문.hwpx",
})


# ── 원격 조회 ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LawEntry:
    """lawSearch 결과 1건 (법령·행정규칙 공통 정규화형)."""
    name: str
    mst: str            # 법령일련번호 / 행정규칙일련번호
    ef_date: str        # 시행일자 YYYYMMDD
    promul_no: str      # 공포번호 / 발령번호
    promul_date: str    # 공포일자 / 발령일자
    revision: str       # 제개정구분명
    abbr: str = ""      # 법령약칭명


def _get(url: str, timeout: int) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def search(query: str, oc: str, target: str = "law", display: int = 20,
           timeout: int = 15) -> list[LawEntry]:
    """lawSearch.do 결과를 LawEntry 리스트로. 정렬·필터 없음(원순서 유지)."""
    url = (f"{SEARCH_URL}?OC={oc}&target={target}&type=XML"
           f"&query={urllib.parse.quote(query)}&display={display}")
    root = ET.fromstring(_get(url, timeout))
    node, keys = ("law", ("법령명한글", "법령일련번호", "공포번호", "공포일자")) if target == "law" \
        else ("admrul", ("행정규칙명", "행정규칙일련번호", "발령번호", "발령일자"))
    out = []
    for el in root.findall(node):
        g = lambda t: (el.findtext(t) or "").strip()  # noqa: E731
        out.append(LawEntry(
            name=g(keys[0]), mst=g(keys[1]), ef_date=g("시행일자"),
            promul_no=g(keys[2]), promul_date=g(keys[3]),
            revision=g("제개정구분명"), abbr=g("법령약칭명"),
        ))
    return out


def find_exact(name: str, oc: str, target: str = "law", timeout: int = 15) -> LawEntry | None:
    """법령명이 **정확히 일치**하는 현행 법령만 반환. 없으면 None.

    폴백 금지 — 이 함수의 존재 이유다(모듈 docstring 참조).
    """
    want = norm_name(name)
    for e in search(name, oc, target=target, timeout=timeout):
        if norm_name(e.name) == want:
            return e
    return None


def fetch_xml(mst: str, oc: str, target: str = "law", timeout: int = 30) -> bytes:
    """lawService.do 로 원문 XML을 받는다. 인증 실패·빈 응답은 RuntimeError."""
    param = "MST" if target == "law" else "ID"
    data = _get(f"{SERVICE_URL}?OC={oc}&target={target}&{param}={mst}&type=XML", timeout)
    if "검증에 실패".encode() in data:
        raise RuntimeError("API 키 검증 실패 — law.go.kr에 서버 IP 등록 여부 확인")
    if "없습니다".encode() in data[:400] or len(data) < 500:
        raise RuntimeError(f"내용 없음 ({len(data)}B)")
    return data


# ── 로컬 스냅샷 파싱 ─────────────────────────────────────────────────
def read_local_meta(path: Path) -> LawEntry | None:
    """tools/laws/*.xml 헤더에서 법령명·시행일자·공포번호를 뽑는다.

    법령(<기본정보>)과 행정규칙(<행정규칙기본정보>) 태그명이 달라 둘 다 본다.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    import re

    def tag(*names: str) -> str:
        for n in names:
            m = re.search(rf"<{n}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{n}>", raw[:6000], re.S)
            if m and m.group(1).strip():
                return m.group(1).strip()
        return ""

    name = tag("법령명_한글", "법령명한글", "행정규칙명")
    if not name:
        return None
    return LawEntry(
        name=name, mst=tag("법령일련번호", "행정규칙일련번호"), ef_date=tag("시행일자"),
        promul_no=tag("공포번호", "발령번호"), promul_date=tag("공포일자", "발령일자"),
        revision=tag("제개정구분명"), abbr=tag("법령명약칭", "법령약칭명"),
    )
