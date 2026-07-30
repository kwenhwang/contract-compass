#!/usr/bin/env python3
"""매니페스트(LAW_MANIFEST) 기준으로 tools/laws/ 법령 스냅샷을 취득한다.

지금까지 법령 추가는 매번 임시 코드로 받아왔다(3회). 취득 경로를 한 곳으로
모아 `find_exact` 정확 일치 규약을 우회할 수 없게 한다.

  python3 tools/fetch_laws.py              # 매니페스트 중 없는 파일만 취득
  python3 tools/fetch_laws.py --force      # 전부 재취득(개정 반영)
  python3 tools/fetch_laws.py 장애인복지법  # 이름 부분일치 대상만

취득 후에는 색인이 따로 필요하다: `python3 tools/index_laws.py`
신선도 감시는 `tools/check_law_freshness.py`가 같은 매니페스트를 본다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.lib.lawgo import (  # noqa: E402
    LAW_MANIFEST,
    fetch_xml,
    find_exact,
    load_oc,
)

LAWS_DIR = ROOT / "tools" / "laws"
RETRIES = 3
BACKOFF_SEC = 2.0


def fetch_one(spec, oc: str, dest: Path) -> tuple[bool, str]:
    """(성공여부, 메시지). 정확 일치 실패는 폴백 없이 실패로 보고한다."""
    entry = None
    for attempt in range(1, RETRIES + 1):
        try:
            entry = find_exact(spec.name, oc, target=spec.target)
            break
        except Exception as exc:  # 네트워크·일시 오류만 재시도
            if attempt == RETRIES:
                return False, f"검색 실패({exc})"
            time.sleep(BACKOFF_SEC * attempt)

    if entry is None:
        # 폴백 금지 — 과거 '검색 첫 결과' 폴백이 무관 법령을 코퍼스에 넣은 사고 있음
        return False, "law.go.kr에 정확 일치 법령명 없음 (폴백하지 않음)"

    for attempt in range(1, RETRIES + 1):
        try:
            raw = fetch_xml(entry.mst, oc, target=spec.target)
            break
        except Exception as exc:
            if attempt == RETRIES:
                return False, f"본문 취득 실패({exc})"
            time.sleep(BACKOFF_SEC * attempt)

    dest.write_bytes(raw)
    return True, f"시행 {entry.ef_date} · 공포 {entry.mst} · {len(raw):,}B"


def main() -> int:
    ap = argparse.ArgumentParser(description="LAW_MANIFEST 기준 법령 스냅샷 취득")
    ap.add_argument("filter", nargs="?", default="",
                    help="법령명 부분일치 필터 (생략 시 매니페스트 전체)")
    ap.add_argument("--force", action="store_true",
                    help="이미 있는 파일도 재취득 (개정 반영)")
    args = ap.parse_args()

    oc = load_oc()
    LAWS_DIR.mkdir(parents=True, exist_ok=True)

    targets = [s for s in LAW_MANIFEST if args.filter in s.name]
    if not targets:
        print(f"매니페스트에 '{args.filter}' 와 일치하는 법령이 없습니다.")
        return 1

    fetched = skipped = failed = 0
    for spec in targets:
        dest = LAWS_DIR / spec.filename
        if dest.exists() and not args.force:
            skipped += 1
            continue
        ok, msg = fetch_one(spec, oc, dest)
        if ok:
            fetched += 1
            print(f"  ✓ {spec.name} — {msg}")
        else:
            failed += 1
            print(f"  ✗ {spec.name} — {msg}", file=sys.stderr)

    print(f"\n취득 {fetched} · 건너뜀(이미 있음) {skipped} · 실패 {failed}")
    if fetched:
        print("→ 색인 반영: python3 tools/index_laws.py")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
