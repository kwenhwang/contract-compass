#!/usr/bin/env bash
# 유료 키 만료 D-3 텔레그램 알림 (2026-07-30, 크론 09:30). 만료 임박 고객 갱신 유도용.
set -u
cd /data/apps/contract-compass
MSG=$(python3 - <<'PY'
import sys; sys.path.insert(0, "mcp")
import keystore
rows = keystore.expiring_within(3)
if rows:
    lines = ["🔑 계약나침반 MCP 키 만료 임박(D-3):"]
    for r in rows:
        lines.append(f"- {r['key_prefix']} ~{r['expires_at'][:10]} [{r.get('channel','?')}] "
                     f"{r.get('name','')} {r.get('contact','')}")
    lines.append("갱신 유도: contract@sallim.app 회신 또는 LS 재구매 안내")
    print("\n".join(lines))
PY
)
[ -z "$MSG" ] && exit 0
set -a; . /data/secrets/telegram.env; set +a
curl -s -m 10 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${TELEGRAM_CHAT_ID}" --data-urlencode text="$MSG" >/dev/null
