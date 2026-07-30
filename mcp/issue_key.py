#!/usr/bin/env python3
"""contract-mcp 유료 API 키 발급·회수·대장 리포트 CLI (로직은 keystore.py).

  발급:  python3 mcp/issue_key.py --name "크몽 주문#1234" [--days 30] [--daily 2000]
                                  [--channel kmong] [--amount 9900] [--contact buyer@x.com] [--order K1234]
  회수:  python3 mcp/issue_key.py --revoke cc_live_ab12   (prefix 일부로도 매칭)
  목록:  python3 mcp/issue_key.py --list
  대장:  python3 mcp/issue_key.py --report   (매출·활성·만료임박·사용량)

결제 확인 후 발급하면 평문 키+클라이언트 설정 스니펫이 1회 출력된다(저장은 해시만).
Lemon Squeezy 판매분은 웹훅이 자동 미러하므로 이 CLI를 쓸 일이 없다.
"""
from __future__ import annotations

import argparse
import sys

import keystore

MCP_URL = "https://contract.sallim.app/mcp"


def cmd_issue(a) -> None:
    key, rec = keystore.issue(a.name, a.days, a.daily, channel=a.channel,
                              amount_krw=a.amount, contact=a.contact, order_id=a.order)
    if key is None:
        print(f"이미 발급된 주문입니다(멱등): {rec['key_prefix']} ({rec.get('name','')})")
        return
    print(f"""\
발급 완료 — 아래 평문 키는 지금 1회만 표시됩니다.

  키:     {key}
  이름:   {a.name}  ·  채널 {a.channel}  ·  {a.amount:,}원
  만료:   {rec['expires_at'][:10]} ({a.days}일)  ·  하루 {a.daily}콜

구매자 전달용 스니펫 ──────────────────────────────

# Claude Code
claude mcp add --transport http contract-compass {MCP_URL} \\
  --header "Authorization: Bearer {key}"

# Cursor (.cursor/mcp.json)
{{ "mcpServers": {{ "contract-compass": {{
    "url": "{MCP_URL}",
    "headers": {{ "Authorization": "Bearer {key}" }} }} }} }}

# Claude Desktop (mcp-remote)
{{ "mcpServers": {{ "contract-compass": {{
    "command": "npx",
    "args": ["-y", "mcp-remote", "{MCP_URL}",
             "--header", "Authorization: Bearer {key}"] }} }} }}

# ChatGPT 커넥터 (커스텀 헤더 불가 — 쿼리 폴백)
{MCP_URL}?key={key}
──────────────────────────────────────────────────""")


def cmd_list() -> None:
    keys = keystore.list_keys()
    if not keys:
        print("발급된 키 없음"); return
    for r in keys:
        state = "활성" if r.get("is_active") else "회수"
        print(f"{r.get('key_prefix')}  {state}  ~{str(r.get('expires_at',''))[:10]}  "
              f"{r.get('daily_limit')}콜/일  [{r.get('channel','manual')}]  {r.get('name','')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--name")
    g.add_argument("--revoke")
    g.add_argument("--list", action="store_true")
    g.add_argument("--report", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--daily", type=int, default=2000)
    ap.add_argument("--channel", default="manual")
    ap.add_argument("--amount", type=int, default=0)
    ap.add_argument("--contact", default="")
    ap.add_argument("--order", default="")
    a = ap.parse_args()
    if a.list:
        cmd_list()
    elif a.report:
        print(keystore.report())
    elif a.revoke:
        try:
            rec = keystore.revoke(a.revoke)
            print(f"회수 완료: {rec['key_prefix']} ({rec.get('name','')})")
        except ValueError as e:
            print(e); sys.exit(1)
    else:
        cmd_issue(a)
