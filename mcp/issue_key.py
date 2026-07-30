#!/usr/bin/env python3
"""contract-mcp 유료 API 키 발급·회수 CLI (2026-07-30 — realty-mcp issue_key 이식).

결제 확인 후 운영자가 1줄로 발급하고, 출력된 평문 키와 설정 스니펫을 구매자에게
그대로 전달한다. 평문은 이 순간 1회만 출력된다 — 저장소(data/mcp_keys.json)에는
sha256 해시만 남는다.

  발급:  python3 mcp/issue_key.py --name "주문#1234" [--days 30] [--daily 2000]
  회수:  python3 mcp/issue_key.py --revoke cc_live_ab12   (prefix 일부로도 매칭)
  목록:  python3 mcp/issue_key.py --list
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEYS_PATH = Path(__file__).resolve().parents[1] / "data" / "mcp_keys.json"
MCP_URL = "https://contract.naru.build/mcp"


def _load(f) -> dict:
    f.seek(0)
    raw = f.read()
    return json.loads(raw) if raw.strip() else {"keys": []}


def _save(f, data: dict) -> None:
    f.seek(0)
    f.truncate()
    f.write(json.dumps(data, ensure_ascii=False, indent=1))


def issue(name: str, days: int, daily: int) -> None:
    key = f"cc_live_{secrets.token_hex(32)}"
    now = datetime.now(timezone.utc)
    rec = {
        "key_hash": hashlib.sha256(key.encode()).hexdigest(),
        "key_prefix": key[:16],
        "name": name,
        "is_active": True,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=days)).isoformat(timespec="seconds"),
        "daily_limit": daily,
    }
    KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(KEYS_PATH, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        data = _load(f)
        data["keys"].append(rec)
        _save(f, data)
    KEYS_PATH.chmod(0o600)

    expires_kst = (now + timedelta(days=days)).astimezone(timezone(timedelta(hours=9)))
    print(f"""\
발급 완료 — 아래 평문 키는 지금 1회만 표시됩니다.

  키:     {key}
  이름:   {name}
  만료:   {expires_kst:%Y-%m-%d %H:%M} KST ({days}일)
  한도:   하루 {daily}콜

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


def revoke(prefix: str) -> None:
    with open(KEYS_PATH, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        data = _load(f)
        hit = [r for r in data["keys"] if r.get("key_prefix", "").startswith(prefix) and r.get("is_active")]
        if not hit:
            print(f"활성 키 중 prefix '{prefix}' 매칭 없음"); sys.exit(1)
        if len(hit) > 1:
            print(f"매칭 {len(hit)}건 — prefix를 더 길게: " + ", ".join(r["key_prefix"] for r in hit)); sys.exit(1)
        hit[0]["is_active"] = False
        hit[0]["revoked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _save(f, data)
    print(f"회수 완료: {hit[0]['key_prefix']} ({hit[0].get('name','')})")


def list_keys() -> None:
    try:
        data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    except OSError:
        print("발급된 키 없음"); return
    for r in data.get("keys", []):
        state = "활성" if r.get("is_active") else "회수"
        print(f"{r.get('key_prefix')}  {state}  ~{str(r.get('expires_at',''))[:10]}  "
              f"{r.get('daily_limit')}콜/일  {r.get('name','')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--name")
    g.add_argument("--revoke")
    g.add_argument("--list", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--daily", type=int, default=2000)
    a = ap.parse_args()
    if a.list:
        list_keys()
    elif a.revoke:
        revoke(a.revoke)
    else:
        issue(a.name, a.days, a.daily)
