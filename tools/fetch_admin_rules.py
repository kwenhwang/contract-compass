"""law.go.kr 행정규칙(target=admrul) 자동 다운로드.

사용법:
  LAW_API_KEY=발급키 python3 tools/fetch_admin_rules.py
"""
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

OC = os.environ.get("LAW_API_KEY", "test")
SERVICE_URL = "http://www.law.go.kr/DRF/lawService.do"
OUT_DIR = Path(__file__).parent / "admin_rules"
OUT_DIR.mkdir(exist_ok=True)

TARGETS = [
    {
        "id": "2100000273704",
        "name": "조달청 중소기업자간 경쟁물품에 대한 계약이행능력심사 세부기준",
        "key": "procurement_525",
    },
    {
        "id": "2100000257824",
        "name": "(중소벤처기업부) 중소기업자간 경쟁제품 중 물품의 구매에 관한 계약이행능력심사 세부기준",
        "key": "smes_basis",
    },
    {
        "id": "2100000263518",
        "name": "중소기업자간 경쟁제품 및 공사용자재 직접구매 대상 품목 지정 내역",
        "key": "sme_product_designation",
    },
    # 2026-05-24 확장: 핵심 계약예규 7건 (재정경제부·과기정통부)
    {"id": "2100000276688", "name": "정부 입찰·계약 집행기준 (계약예규)", "key": "govt_bid_contract_exec"},
    {"id": "2100000274732", "name": "적격심사기준 (계약예규)", "key": "qualification_review"},
    {"id": "2100000274730", "name": "공사계약 종합심사낙찰제 심사기준 (계약예규)", "key": "construction_comprehensive"},
    {"id": "2100000272436", "name": "협상에 의한 계약체결기준 (계약예규)", "key": "negotiation_contract"},
    {"id": "2100000276694", "name": "용역계약일반조건 (계약예규)", "key": "service_general_conditions"},
    {"id": "2100000276692", "name": "물품구매(제조)계약일반조건 (계약예규)", "key": "product_general_conditions"},
    {"id": "2100000223356", "name": "소프트웨어사업 계약 및 관리감독에 관한 지침", "key": "software_contract_guide"},
]

print(f"API 키: {OC}")
print(f"저장 위치: {OUT_DIR}\n")

success = 0
for t in TARGETS:
    fname = OUT_DIR / f"{t['key']}.xml"
    print(f"  {t['name']} ...", end=" ", flush=True)
    try:
        url = f"{SERVICE_URL}?OC={OC}&target=admrul&ID={t['id']}&type=XML"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if "일치하는 행정규칙이 없습니다".encode() in data or len(data) < 500:
            print(f"❌ 내용 없음 ({len(data)}B)")
            continue
        fname.write_bytes(data)
        kb = len(data) // 1024
        print(f"✅ {kb}KB  (ID={t['id']})")
        success += 1
    except Exception as e:
        print(f"❌ {e}")
    time.sleep(0.5)

print(f"\n{success}/{len(TARGETS)} 성공")
if success > 0:
    print("\n다음 단계:")
    print("  python3 tools/parse_admin_rules.py")
