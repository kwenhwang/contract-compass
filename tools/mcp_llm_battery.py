#!/usr/bin/env python3
"""MCP LLM 배터리 — 질문은행(tests/question_bank.json)을 codex로 실행·채점.

mcp-tool-design §1-4의 'LLM 평가층'(합성 품질). 결정론 회귀(mcp_regression.py)와
2층을 이룬다. codex 업무계정(CODEX_HOME=~/.codex-work)이 stdio MCP로 붙어
실사용자와 같은 조건(도구만으로 답 구성)을 재현한다.

  전체:      python3 tools/mcp_llm_battery.py
  카테고리:  python3 tools/mcp_llm_battery.py --category 수의계약
  특정 id:   python3 tools/mcp_llm_battery.py --ids 수의-046,입찰-051
  미검증만:  python3 tools/mcp_llm_battery.py --unverified
  개수 제한: python3 tools/mcp_llm_battery.py --limit 3

출력: logs/llm_battery/<UTC시각>/q-<id>.txt (+ .err), 종료 시 must 채점 요약.
exit 0=전부 PASS(무기준 항목은 완료=PASS) / 1=FAIL 존재 / 2=실행 실패.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "tests" / "question_bank.json"
OUT_ROOT = ROOT / "logs" / "llm_battery"
CODEX_HOME = os.environ.get("CODEX_BATTERY_HOME", str(Path.home() / ".codex-work"))
TIMEOUT = int(os.environ.get("CODEX_BATTERY_TIMEOUT", "300"))

PROMPT = (
    "너는 contract_compass MCP 도구로 한국 공공계약 질문에 답하는 도우미다. "
    "필요한 도구를 스스로 판단해 법령·판례·해석례 근거를 확보하고 직접 답을 구성하라. "
    "도구가 {{'error':...}} 또는 hint를 반환하면 그 지침을 따르고, 도구 근거 없는 "
    "법령 수치는 '도구에서 확인 불가'라고 명시하라. "
    "도구 결과가 명백히 틀렸거나(오인용·모순된 판정·개정 미반영) 도구 자체가 오류를 "
    "내면, report_issue 도구로 제보한 뒤 답변에 '[제보: <category>]'를 남겨라 — 추측성 "
    "제보는 금지, 근거가 확실할 때만. "
    "답변 맨 끝에 '[도구사용: <도구명=성공/실패>]' 한 줄을 붙여라. 질문: {q}"
)


def run_one(item: dict, outdir: Path, mcp_cmd: str) -> tuple[bool, int]:
    """1문항 실행. (완료 여부, 소요초) 반환."""
    f = outdir / f"q-{item['id']}.txt"
    f.write_text(f"[{item['id']}] {item['q']}\n", encoding="utf-8")
    t0 = time.time()
    with open(f, "a", encoding="utf-8") as out, open(f.with_suffix(".err"), "w", encoding="utf-8") as err:
        rc = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check", "-s", "read-only",
             "-c", f'mcp_servers.contract_compass.command="{mcp_cmd}"',
             PROMPT.format(q=item["q"])],
            stdout=out, stderr=err, stdin=subprocess.DEVNULL,
            env={**os.environ, "CODEX_HOME": CODEX_HOME},
            timeout=TIMEOUT, cwd="/tmp", check=False,
        ).returncode
    dur = round(time.time() - t0)
    with open(f, "a", encoding="utf-8") as out:
        out.write(f"\nRC={rc} DUR={dur}s\n")
    return rc == 0, dur


def grade(item: dict, outdir: Path) -> tuple[str, str]:
    """must 그룹(OR) 전체(AND) 충족 검사. must 없으면 완료=PASS."""
    f = outdir / f"q-{item['id']}.txt"
    text = f.read_text(encoding="utf-8") if f.exists() else ""
    if "RC=0" not in text:
        return "FAIL", "실행 실패"
    for group in item.get("must") or []:
        if not any(alt in text for alt in group):
            return "FAIL", f"must 미충족 {group}"
    return "PASS", ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category")
    ap.add_argument("--ids")
    ap.add_argument("--unverified", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    items = json.loads(BANK.read_text(encoding="utf-8"))["items"]
    if a.category:
        items = [i for i in items if i["category"] == a.category]
    if a.ids:
        want = set(a.ids.split(","))
        items = [i for i in items if i["id"] in want]
    if a.unverified:
        items = [i for i in items if not i.get("verified")]
    if a.limit:
        items = items[: a.limit]
    if not items:
        print("대상 문항 없음"); return 2

    # stdio MCP 래퍼(.env 주입) — repo 밖 산출물이므로 실행 시 생성
    wrapper = OUT_ROOT / "mcp-wrapper.sh"
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(
        "#!/bin/bash\nset -a; source {r}/.env; set +a\n"
        "export CONTRACT_COMPASS_URL=http://127.0.0.1:8402\n"
        "exec /usr/bin/python3 {r}/mcp/server.py\n".format(r=ROOT), encoding="utf-8")
    wrapper.chmod(0o755)

    outdir = OUT_ROOT / time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    outdir.mkdir(parents=True)
    print(f"{len(items)}문항 → {outdir}")
    fails = 0
    for item in items:
        ok, dur = run_one(item, outdir, str(wrapper))
        verdict, why = grade(item, outdir)
        if verdict == "FAIL":
            fails += 1
        print(f"[{verdict}] {item['id']} ({dur}s){' — ' + why if why else ''}")
    print(f"\n결과: PASS {len(items) - fails} / FAIL {fails} (총 {len(items)})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
