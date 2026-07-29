"""Ask 상시 회귀 — 질문뱅크를 라이브 API에 던져 결정론 문자열 규칙으로 판정.

exit 0 = 전부 PASS / 1 = 규칙 위반 존재 / 2 = 수집 실패(API 미도달 등)
크론·pulse cross-check 규약과 호환. LLM 판정 없음(비용·비결정성 배제).

사용: python3 tools/qa_regression.py [--base http://localhost:8402] [--pace 7]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8402")
    ap.add_argument("--pace", type=float, default=7.0, help="질문 간격(초) — per-IP 레이트리밋 준수")
    args = ap.parse_args()

    bank = json.loads((ROOT / "tests" / "qa_bank.json").read_text(encoding="utf-8"))["items"]
    fails: list[str] = []
    errors = 0
    with httpx.Client(timeout=90.0) as c:
        for i, item in enumerate(bank):
            try:
                r = c.post(f"{args.base}/api/v1/ask", json={"question": item["q"]})
                r.raise_for_status()
                answer = r.json().get("answer", "")
            except Exception as e:
                errors += 1
                print(f"[ERR ] {item['id']}: {type(e).__name__}: {e}")
                continue
            ok = True
            for group in item.get("must", []):
                if not any(alt in answer for alt in group):
                    ok = False
                    fails.append(f"{item['id']}: must 미충족 {group}")
                    break
            for bad in item.get("forbid", []):
                if bad in answer:
                    ok = False
                    fails.append(f"{item['id']}: forbid 등장 '{bad}'")
                    break
            print(f"[{'PASS' if ok else 'FAIL'}] {item['id']}")
            if not ok:
                print(f"       └ 답변: {answer[:160]}")
            if i < len(bank) - 1:
                time.sleep(args.pace)

    total = len(bank)
    print(f"\n결과: PASS {total - len(fails) - errors} / FAIL {len(fails)} / ERR {errors} (총 {total})")
    if errors >= total // 2:
        return 2  # 수집 실패
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
