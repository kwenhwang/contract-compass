#!/usr/bin/env bash
# 계약나침반 기동. workers=1 — 이 박스가 1 vCPU(Neoverse-N1)라 멀티워커는 RAM만 소모.
# rate limiter는 SQLite 공유(2026-07-30 P1)로 전환돼 있어 코어 증설·서버 이전 시
# --workers N만 올리면 된다(세션·캡·리미터 전부 파일/DB 공유 — 코드 제약 없음).
cd /data/apps/contract-compass
exec /usr/bin/python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8402 --workers 1 --log-level warning
