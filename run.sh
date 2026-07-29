#!/usr/bin/env bash
# 계약나침반 기동 — rate limiter 인메모리라 workers=1 고정
cd /data/apps/contract-compass
exec /usr/bin/python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8402 --log-level warning
