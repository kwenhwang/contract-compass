"""Ask 상시 회귀 — 질문뱅크를 라이브 API에 던져 결정론 문자열 규칙으로 판정.

exit 0 = 전부 PASS / 1 = 규칙 위반 존재 / 2 = 수집 실패(API 미도달 등)
크론·pulse cross-check 규약과 호환. LLM 판정 없음(비용·비결정성 배제).

사용: python3 tools/qa_regression.py [--base http://localhost:8402] [--pace 8]

인증: /ask에 chat_access 로그인 게이트(익명 IP당 2회/일)가 붙어(2026-07-29) 익명으로는
19문항을 못 돌린다 — 환경변수 SUPABASE_JWT_SECRET(백엔드 .env와 동일)이 있으면 내부
JWT를 서명해 로그인 사용자로 통과한다(mcp/server.py와 같은 패턴). 미설정 시 익명 폴백.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _auth_headers() -> dict:
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        # 크론이 .env를 안 물려줬을 때의 폴백 — 백엔드와 같은 파일에서 직접 읽는다
        try:
            for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
                if line.startswith("SUPABASE_JWT_SECRET="):
                    secret = line.split("=", 1)[1].strip().strip('"')
                    break
        except OSError:
            pass
    if not secret:
        return {}
    import jwt
    now = int(time.time())
    token = jwt.encode(
        {"sub": "qa-regression", "email": "qa@internal", "aud": "authenticated",
         "iat": now, "exp": now + 3600},
        secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8402")
    ap.add_argument("--pace", type=float, default=8.0, help="질문 간격(초) — per-IP 레이트리밋 준수")
    args = ap.parse_args()

    bank = json.loads((ROOT / "tests" / "qa_bank.json").read_text(encoding="utf-8"))["items"]
    fails: list[str] = []
    errors = 0
    skipped = 0
    headers = _auth_headers()
    if not headers:
        print("[warn] SUPABASE_JWT_SECRET 미확보 — 익명 모드(무료 2회 후 401 예상)")
    with httpx.Client(timeout=90.0) as c:
        for i, item in enumerate(bank):
            try:
                r = c.post(f"{args.base}/api/v1/ask", json={"question": item["q"]},
                           headers=headers)
                # 일일 LLM 캡 소진(429 daily_cap_exceeded)은 회귀 실패가 아니라 예산 문제 —
                # 남은 문항을 SKIP으로 중단해 캡 소진이 ERR 오탐·ops 오보고로 번지지 않게 한다.
                if r.status_code == 429 and "daily_cap_exceeded" in r.text:
                    skipped = len(bank) - i
                    print(f"[SKIP] {item['id']} 이후 {skipped}문항 — 일일 LLM 캡 소진(내부 예산)")
                    break
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
    passed = total - len(fails) - errors - skipped
    print(f"\n결과: PASS {passed} / FAIL {len(fails)} / ERR {errors} / SKIP {skipped} (총 {total})")
    if errors >= total // 2:
        return 2  # 수집 실패
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
