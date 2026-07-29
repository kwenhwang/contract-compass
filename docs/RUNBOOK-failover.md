# 계약나침반 장애 전환 런북 (naru → quant 콜드 스탠바이)

작성 2026-07-30 (P3). 데이터는 naru가 매일 05:30 `scripts/sync_standby_to_quant.sh`로
`quant:/home/ubuntu/standby/contract-compass`에 푸시(211MB, 런타임 상태 제외).

## 전제 (quant 쪽 1회 준비 — 아직 미완, 장 마감 후 작업 예정)
- [ ] venv: `python3 -m venv ~/venvs/contract && ~/venvs/contract/bin/pip install -r backend/requirements.txt`
- [ ] systemd 유닛 `contract-compass-standby.service`(disabled): standby 경로에서 :8402 기동
- [ ] quant nginx에 contract.naru.build server 블록(비활성 conf로 준비)
- [ ] 위 완료 시 이 체크박스 갱신 + FLEET.md 변경로그 기록

## 전환 절차 (naru 장애 확인 후, 수동 — 약 10분)
1. 판정: Uptime Kuma(status.naru.build)에서 contract-compass·naru 전반 다운 확인.
   naru만 부분 장애면 naru 복구가 우선(이 런북은 서버 전체 불능일 때).
2. quant에서: `sudo systemctl start contract-compass-standby` → `curl localhost:8402/health`
3. quant nginx: contract 블록 활성화(`ln -s` + `nginx -t && reload`)
4. CF DNS: contract.naru.build A 레코드를 naru(168.107.47.60) → quant(152.69.232.84)로
   변경(proxied 유지). 토큰은 형제 앱 .env의 CLOUDFLARE_API_TOKEN.
5. 확인: `curl https://contract.naru.build/health` + Kuma 모니터 녹색 복귀.
6. 한계 고지: 스탠바이는 세션·카운터가 초기화된 상태로 뜨고, MCP(:8403)는 미기동
   (필요 시 같은 방식으로 contract-mcp도 준비). 야간 QA·통계는 naru 복구 후 재개.

## 복귀 (naru 복구 후)
1. naru 서비스 정상 확인 → CF DNS를 naru로 원복 → quant 스탠바이 stop + nginx 블록 비활성.
2. naru에서 sync 스크립트 1회 실행해 스탠바이를 최신화.
