"""법령 스냅샷 신선도 점검 — 로컬 XML · law_registry.json · law.go.kr 현행 3자 대조.

"법령 개정 자동 갱신 부재" 한계의 감시 장치.
우리 코퍼스는 **오프라인 스냅샷**이라 개정을 자동 추종할 수 없다.
그래서 "자동 갱신" 대신 **자동 감지**를 둔다 — 갱신 판단·검수는 사람이 한다.

점검 항목
  WRONG_LAW      로컬 XML의 법령명이 매니페스트 기대값과 다름 (오취득)
  STALE          로컬 공포번호·시행일자가 law.go.kr 현행과 다름 (개정 미반영)
  MISSING        매니페스트에 있는데 파일이 없음
  LOOKUP_FAIL    law.go.kr에서 정확 일치 검색 실패 (법령명 변경·폐지 의심)
  REGISTRY_DRIFT rules/law_registry.json의 promulgation이 현행과 다름
                 (= 의견서에 인쇄되는 공포번호가 실제와 어긋남)

사용법:
  python3 tools/check_law_freshness.py            # 표 출력 + data/law_freshness.json
  python3 tools/check_law_freshness.py --quiet    # 문제만 출력 (크론용)
드리프트가 하나라도 있으면 종료코드 1.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.lib.lawgo import (  # noqa: E402
    LAW_MANIFEST, LAWS_DIR, MANUAL_FILES, find_exact, load_oc, norm_name, norm_no,
    read_local_meta,
)

REGISTRY_PATH = ROOT / "rules" / "law_registry.json"
OUT_PATH = ROOT / "data" / "law_freshness.json"

# registry에만 있고 tools/laws/ 스냅샷이 없는 항목의 원문 소재.
# 계약예규 등 행정규칙은 law.go.kr 법령 검색 대상이 아니라 별도(admrul) 조회한다.
# law.go.kr 등록 명칭은 "(계약예규) " 접두가 붙는다 — 정확 일치라서 접두 포함 필수.
REGISTRY_ADMRUL = {
    "공동계약운용요령 (기획재정부 계약예규)": "(계약예규) 공동계약운용요령",
}


def _is_external_checkable(promulgation: str) -> bool:
    """law.go.kr 대조 가능 여부 — promulgation이 법령([시행 …])·예규([발령 …]) 형식일 때만.

    그 외 형식(운영기관 내부 규정 등 수기 관리 항목)은 외부 API 대상이 아니므로 SKIP.
    """
    return bool(re.match(r"^\[(시행|발령)\s", promulgation or ""))


def _parse_promulgation(text: str) -> tuple[str, str]:
    """promulgation 문자열에서 (시행일 YYYYMMDD, 공포번호) 추출.

    예: "[시행 2026.1.2.] [법률 제21065호, 2025.10.1., 타법개정]" → ("20260102", "21065")
        "[발령 2026.1.2. 기획재정부 계약예규 제29호]"              → ("", "29")
    """
    ef = ""
    m = re.search(r"시행\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", text or "")
    if m:
        ef = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    m2 = re.search(r"제\s*([\d]+)\s*호", text or "")
    return ef, (str(int(m2.group(1))) if m2 else "")


def check_snapshots(oc: str, sleep: float = 0.4) -> list[dict]:
    """tools/laws/ 스냅샷 vs law.go.kr 현행."""
    rows = []
    for spec in LAW_MANIFEST:
        path = LAWS_DIR / spec.filename
        row: dict = {"file": spec.filename, "expected": spec.name, "target": spec.target}
        if not path.exists():
            rows.append({**row, "status": "MISSING"})
            continue
        local = read_local_meta(path)
        if local is None:
            rows.append({**row, "status": "MISSING", "detail": "헤더 파싱 실패"})
            continue
        row |= {"local_name": local.name, "local_ef": local.ef_date, "local_no": local.promul_no}

        if norm_name(local.name) != norm_name(spec.name):
            rows.append({**row, "status": "WRONG_LAW",
                         "detail": f"파일 내용이 「{local.name}」 — 기대 「{spec.name}」"})
            continue
        try:
            cur = find_exact(spec.name, oc, target=spec.target)
        except Exception as e:  # 네트워크·XML 오류는 감지 실패로 남긴다(조용한 통과 금지)
            rows.append({**row, "status": "LOOKUP_FAIL", "detail": f"{type(e).__name__}: {e}"})
            continue
        finally:
            time.sleep(sleep)
        if cur is None:
            rows.append({**row, "status": "LOOKUP_FAIL",
                         "detail": "law.go.kr 정확 일치 없음 — 법령명 변경·폐지 확인"})
            continue
        row |= {"cur_ef": cur.ef_date, "cur_no": cur.promul_no, "cur_mst": cur.mst,
                "cur_revision": cur.revision}
        # 시행일자 단독 불일치는 공포번호가 같고 로컬이 더 미래면 STALE 아님 —
        # 분리시행 법령(2026-07 국유재산법: 검색 API 시행 20260219, 본문 XML 헤더는
        # 후행 시행분 20260820)은 같은 문서를 재취득해도 영원히 불일치한다.
        stale = (norm_no(local.promul_no) != norm_no(cur.promul_no)
                 or (local.ef_date != cur.ef_date and local.ef_date < cur.ef_date))
        row["status"] = "STALE" if stale else "OK"
        if stale:
            row["detail"] = (f"로컬 제{norm_no(local.promul_no)}호(시행 {local.ef_date}) → "
                             f"현행 제{norm_no(cur.promul_no)}호(시행 {cur.ef_date}, {cur.revision})")
        rows.append(row)
    return rows


def check_registry(oc: str, snapshot_rows: list[dict], sleep: float = 0.4) -> list[dict]:
    """law_registry.json promulgation vs law.go.kr 현행.

    스냅샷 점검에서 이미 조회한 결과를 재사용해 API 호출을 줄인다.
    """
    reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["registry"]
    live_by_name = {norm_name(r["expected"]): r for r in snapshot_rows if r.get("cur_no")}
    cache: dict[str, object] = {}
    rows = []
    for key, entry in reg.items():
        law_name = entry.get("law_name", "")
        promul = entry.get("promulgation", "")
        if not _is_external_checkable(promul):
            rows.append({"key": key, "law_name": law_name, "status": "SKIP",
                         "detail": "수기 관리 항목(내부 규정 등) — 외부 API 대상 아님"})
            continue
        reg_ef, reg_no = _parse_promulgation(promul)
        snap = live_by_name.get(norm_name(law_name))
        if snap:
            cur_no, cur_ef, rev = snap["cur_no"], snap["cur_ef"], snap.get("cur_revision", "")
        else:
            official = REGISTRY_ADMRUL.get(law_name, law_name)
            target = "admrul" if law_name in REGISTRY_ADMRUL else "law"
            if official not in cache:
                try:
                    cache[official] = find_exact(official, oc, target=target)
                except Exception:
                    cache[official] = None
                time.sleep(sleep)
            cur = cache[official]
            if cur is None:
                rows.append({"key": key, "law_name": law_name, "status": "LOOKUP_FAIL",
                             "detail": f"현행 조회 실패 — 「{official}」"})
                continue
            cur_no, cur_ef, rev = cur.promul_no, cur.ef_date, cur.revision
        drift = norm_no(reg_no) != norm_no(cur_no)
        row = {"key": key, "law_name": law_name, "promulgation": promul,
               "reg_no": reg_no, "reg_ef": reg_ef, "cur_no": norm_no(cur_no), "cur_ef": cur_ef,
               "status": "REGISTRY_DRIFT" if drift else "OK"}
        if drift:
            row["detail"] = (f"registry 제{reg_no}호 → 현행 제{norm_no(cur_no)}호"
                             f"(시행 {cur_ef}, {rev}) — 의견서 인쇄값이 어긋남")
        rows.append(row)
    return rows


BAD = {"WRONG_LAW", "STALE", "MISSING", "LOOKUP_FAIL", "REGISTRY_DRIFT"}
ICON = {"OK": "✅", "SKIP": "－", "STALE": "🟠", "WRONG_LAW": "🔴",
        "MISSING": "🔴", "LOOKUP_FAIL": "⚠️", "REGISTRY_DRIFT": "🟠"}


def main() -> int:
    quiet = "--quiet" in sys.argv
    oc = load_oc()
    snaps = check_snapshots(oc)
    regs = check_registry(oc, snaps)

    problems = [r for r in snaps + regs if r["status"] in BAD]

    print(f"\n[스냅샷] tools/laws/ — {len(snaps)}건")
    for r in snaps:
        if quiet and r["status"] == "OK":
            continue
        print(f"  {ICON[r['status']]} {r['file'][:52]:54s} {r['status']}")
        if r.get("detail"):
            print(f"       └ {r['detail']}")

    print(f"\n[레지스트리] rules/law_registry.json — {len(regs)}건")
    for r in regs:
        if quiet and r["status"] in ("OK", "SKIP"):
            continue
        print(f"  {ICON[r['status']]} {r['key'][:40]:42s} {r['status']}")
        if r.get("detail"):
            print(f"       └ {r['detail']}")

    manual = sorted(MANUAL_FILES)
    extra = sorted(p.name for p in LAWS_DIR.iterdir()
                   if p.name not in {s.filename for s in LAW_MANIFEST} and p.name not in MANUAL_FILES) \
        if LAWS_DIR.exists() else []
    if extra:
        print(f"\n[매니페스트 밖 파일] {extra} — LAW_MANIFEST에 등록하거나 삭제할 것")
    print(f"[수동 반입(감시 제외)] {manual}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "problem_count": len(problems),
        "snapshots": snaps, "registry": regs,
        "unmanaged_files": extra, "manual_files": manual,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'🔴' if problems else '✅'} 문제 {len(problems)}건 · 리포트 {OUT_PATH}")
    if problems:
        print("   갱신 절차: law.go.kr에서 최신 XML 재취득(tools/lib/lawgo.find_exact·fetch_xml,"
              " 폴백 없는 정확 일치) → tools/laws/ 갱신 → tools/index_laws.py"
              " → pytest tests/unit/test_law_registry_integrity.py (본문 대조)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
