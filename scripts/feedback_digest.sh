#!/usr/bin/env bash
# 피드백 다이제스트 — 파일에만 쌓이면 아무도 안 읽는다(mcp-tool-design §1-3, realty 3주 방치 실사고).
# 하루 1회 신규 제보(웹+MCP)를 모아 ops 인박스로. 오프셋 상태파일로 재시작 생존.
set -u
cd /data/apps/contract-compass
LOG=logs/feedback.jsonl
STATE=logs/.feedback_digest_offset
[ -f "$LOG" ] || exit 0
total=$(wc -l < "$LOG")
last=$(cat "$STATE" 2>/dev/null || echo 0)
[ "$total" -le "$last" ] && exit 0
NEW=$(tail -n +"$((last+1))" "$LOG" | python3 -c "
import json, sys
rows=[]
for line in sys.stdin:
    try: d=json.loads(line)
    except: continue
    c=(d.get('comment') or '').strip()
    if not c: continue
    src='MCP' if (d.get('context') or {}).get('page')=='MCP' else '웹'
    rows.append(f\"[{src}] {c[:160]}\")
print('\n'.join(rows[:20]))
if len(rows)>20: print(f'…외 {len(rows)-20}건')
")
echo "$total" > "$STATE"
[ -z "$NEW" ] && exit 0
/data/ops/ops-report.sh "계약나침반 피드백 다이제스트($((total-last))건 신규) — $NEW"
