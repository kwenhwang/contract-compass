# 계약나침반 장애 전환 런북 (naru → quant 콜드 스탠바이)

작성 2026-07-30 (P3), 2단계 완료 반영. 데이터는 naru가 매일 05:30
`scripts/sync_standby_to_quant.sh`로 `quant:/home/ubuntu/standby/contract-compass`에
푸시(211MB, 세션·카운터 등 런타임 상태 제외).

## quant 쪽 준비 상태 (2026-07-30 완료)
- [x] venv `~/venvs/contract` + requirements 설치
- [x] systemd `contract-compass-standby.service`(:8402)·`contract-mcp-standby.service`(:8403) — **disabled, 평시 미기동**
- [x] Caddy 블록 스니펫 `/etc/caddy/contract-standby.caddy` — **Caddyfile에 미포함(비활성)**
- [x] 기동 스모크 테스트 통과

## 전환 절차 (naru 장애 확인 후, 수동 — 약 10분)
1. **판정**: Uptime Kuma(status.naru.build — quant에서 돌므로 naru 장애에도 생존)에서
   contract-compass 및 naru 전반 다운 확인. naru 부분 장애면 naru 복구가 우선.
2. **quant 앱 기동**:
   `sudo systemctl start contract-compass-standby contract-mcp-standby`
   → `curl localhost:8402/health` / `curl localhost:8403/health`
3. **Caddy 활성화**:
   `cat /etc/caddy/contract-standby.caddy | sudo tee -a /etc/caddy/Caddyfile && sudo systemctl reload caddy`
4. **CF DNS 전환**: contract.naru.build A 레코드를 naru(168.107.47.60) → quant(152.69.232.84),
   **proxied → DNS-only로 변경**(quant Caddy가 status.naru.build처럼 TLS-ALPN 자동 인증서 취득 —
   proxied 유지 시 인증서 발급 불가). 토큰=형제 앱(.env) CLOUDFLARE_API_TOKEN.
5. **확인**: `curl https://contract.naru.build/health` + Kuma 녹색 복귀(수 분 내 인증서 발급 대기).
6. **한계 고지**: 세션·이용 카운터는 초기 상태로 시작, 야간 QA·통계는 naru 복구 후 재개.
   quant는 RAM 3GB(가용 ~2GB)라 스탠바이(RSS ~1.3GB) 가동 중 여유가 작다 — 임시 운용임을 전제.

## 복귀 (naru 복구 후)
1. naru 서비스 정상 확인 → CF DNS를 naru + proxied로 원복.
2. quant: `sudo systemctl stop contract-compass-standby contract-mcp-standby` +
   Caddyfile에서 contract 블록 제거 + `sudo systemctl reload caddy`.
3. naru에서 sync 스크립트 1회 실행해 스탠바이 최신화.
