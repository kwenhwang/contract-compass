"""tools/lib/lawgo.py 순수 단위테스트 — 네트워크 불필요.

핵심 회귀 대상은 두 가지다:
  1. `find_exact`의 **폴백 부재** — 오취득(「물품관리법」→「공유재산 및 물품 관리법」)
     재발 방지. 검색 결과에 정확 일치가 없으면 None이어야 한다.
  2. 매니페스트가 `tools/laws/` 실제 파일 집합과 어긋나지 않는 것.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.lib import lawgo  # noqa: E402

# 실제 law.go.kr이 "물품관리법" 질의에 주는 순서(2026-07-28 관측) — 1순위가 다른 법이다.
MULPUM_SEARCH = [
    lawgo.LawEntry(name="공유재산 및 물품 관리법", mst="258477", ef_date="20240710",
                   promul_no="19990", promul_date="20240109", revision="타법개정",
                   abbr="공유재산법"),
    lawgo.LawEntry(name="공유재산 및 물품 관리법 시행령", mst="288163", ef_date="20260721",
                   promul_no="36515", promul_date="20260721", revision="일부개정"),
    lawgo.LawEntry(name="물품관리법", mst="276107", ef_date="20260102",
                   promul_no="21065", promul_date="20251001", revision="타법개정"),
]


@pytest.fixture
def stub_search(monkeypatch):
    def _install(rows):
        monkeypatch.setattr(lawgo, "search", lambda *a, **k: rows)
    return _install


def test_find_exact_skips_higher_ranked_wrong_law(stub_search):
    """검색 1순위가 유사명 다른 법이어도 정확 일치를 골라야 한다 (오취득 방지)."""
    stub_search(MULPUM_SEARCH)
    got = lawgo.find_exact("물품관리법", "oc")
    assert got is not None and got.name == "물품관리법" and got.mst == "276107"


def test_find_exact_returns_none_without_exact_match(stub_search):
    """폴백 금지 — 첫 결과를 대신 내놓으면 조용한 오취득이 재발한다."""
    stub_search(MULPUM_SEARCH[:2])
    assert lawgo.find_exact("물품관리법", "oc") is None


def test_find_exact_returns_none_on_empty(stub_search):
    stub_search([])
    assert lawgo.find_exact("없는법률", "oc") is None


def test_find_exact_tolerates_middle_dot_variants(stub_search):
    """law.go.kr은 'ㆍ', 우리 코드·문서는 '·' — 표기 차이로 취득이 실패하면 안 된다."""
    stub_search([lawgo.LawEntry(name="공기업ㆍ준정부기관 계약사무규칙", mst="285569",
                                ef_date="20260420", promul_no="00028",
                                promul_date="20260420", revision="일부개정")])
    assert lawgo.find_exact("공기업·준정부기관 계약사무규칙", "oc") is not None


@pytest.mark.parametrize(("raw", "want"), [
    ("제00028호", "28"), ("00028", "28"), ("28", "28"), ("21418", "21418"), ("", ""),
])
def test_norm_no(raw, want):
    assert lawgo.norm_no(raw) == want


def test_norm_name_collapses_middle_dots_and_spaces():
    assert lawgo.norm_name("공기업ㆍ준정부기관 계약사무규칙") == lawgo.norm_name(
        "공기업·준정부기관계약사무규칙")


def test_manifest_filenames_unique():
    names = [s.filename for s in lawgo.LAW_MANIFEST]
    assert len(names) == len(set(names))


# 법령 XML 스냅샷은 repo에 포함되지 않는다 — 운영자가 law.go.kr에서 받아
# tools/laws/에 넣은 뒤에만 아래 정합 검사가 의미를 가진다.
_needs_snapshot = pytest.mark.skipif(
    not lawgo.LAWS_DIR.exists(),
    reason="tools/laws/ 스냅샷 없음 — 운영자 반입 후 검사",
)


@_needs_snapshot
def test_manifest_covers_every_snapshot_file():
    """tools/laws/의 모든 파일은 매니페스트나 수동반입 명단에 속해야 한다.

    명단 밖 파일은 취득·감시 어디에도 걸리지 않고 조용히 늙는다(침묵 확장 방지).
    """
    known = {s.filename for s in lawgo.LAW_MANIFEST} | set(lawgo.MANUAL_FILES)
    actual = {p.name for p in lawgo.LAWS_DIR.iterdir() if p.is_file()}
    assert not (actual - known), f"매니페스트 밖 파일: {sorted(actual - known)}"


@_needs_snapshot
def test_manifest_files_exist():
    missing = [s.filename for s in lawgo.LAW_MANIFEST
               if not (lawgo.LAWS_DIR / s.filename).exists()]
    assert not missing, f"매니페스트에 있는데 없는 파일: {missing}"


@_needs_snapshot
def test_local_snapshot_contents_match_manifest():
    """각 XML의 법령명이 매니페스트 기대값과 같아야 한다 — 오취득 상시 감시.

    (물품관리법.xml에 「공유재산 및 물품 관리법」이 들어 있던 결함의 회귀 방지.
     네트워크 없이도 잡히는 검사라 CI에 둔다.)
    """
    bad = []
    for spec in lawgo.LAW_MANIFEST:
        meta = lawgo.read_local_meta(lawgo.LAWS_DIR / spec.filename)
        if meta is None:
            bad.append(f"{spec.filename}: 헤더 파싱 실패")
        elif lawgo.norm_name(meta.name) != lawgo.norm_name(spec.name):
            bad.append(f"{spec.filename}: 내용 「{meta.name}」 ≠ 기대 「{spec.name}」")
    assert not bad, "오취득 의심: " + "; ".join(bad)
