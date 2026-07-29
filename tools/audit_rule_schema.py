#!/usr/bin/env python3
"""F33 (2026-06-10) — 룰 schema 가드 도구.

신규 룰 작성자가 priority 충돌·alternatives 누락·필수 필드 미설정을 즉시 발견 가능.
회귀 도구가 아닌 작성 가드. 룰엔진 동작 변경 X.

실행: python3 tools/audit_rule_schema.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "rules" / "contract_rules.json"


def main() -> int:
    with open(RULES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    rules = data.get("rules", [])
    issues: list[str] = []
    warnings: list[str] = []

    # 1. 필수 필드
    for r in rules:
        rid = r.get("rule_id", "?")
        if not r.get("contract_type"):
            issues.append(f"{rid}: contract_type 누락")
        if "priority" not in r:
            issues.append(f"{rid}: priority 누락")
        if not r.get("name"):
            warnings.append(f"{rid}: name 비어있음")
        # legal_basis는 최소 1개 권장
        if not r.get("legal_basis"):
            warnings.append(f"{rid}: legal_basis 비어있음 — 환각 위험")
        # result.method 또는 method_by_amount 중 하나 필수 (금액별 분기 룰은 method_by_amount 사용)
        res = r.get("result", {}) or {}
        if not res.get("method") and not res.get("method_by_amount"):
            issues.append(f"{rid}: result.method 또는 method_by_amount 누락")

    # 2. priority 충돌 (같은 priority + 같은 contract_type + 조건 차원 동일)
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for r in rules:
        key = (
            r.get("priority"),
            r.get("contract_type"),
            tuple(sorted(r.get("conditions", {}).keys())),
        )
        buckets[key].append(r)
    for key, group in buckets.items():
        if len(group) > 1:
            # specialty 또는 negotiation_reason로 분기되면 OK
            specialties = set((r.get("conditions", {}) or {}).get("construction_specialty") for r in group)
            reasons = set((r.get("conditions", {}) or {}).get("negotiation_reason") for r in group)
            if len(specialties) == len(group) and None not in specialties:
                continue  # specialty로 분기 → OK
            if len(reasons) == len(group) and None not in reasons:
                continue  # reason으로 분기 → OK
            rids = [r["rule_id"] for r in group]
            warnings.append(f"priority/ct/conds 동일 ({key[0]}·{key[1]}·{key[2]}): {rids} — 자연 분기 검증 필요")

    # 3. alternatives 명시성
    for r in rules:
        rid = r.get("rule_id", "?")
        alts = (r.get("result", {}) or {}).get("alternatives", []) or []
        for i, a in enumerate(alts):
            if not isinstance(a, dict):
                issues.append(f"{rid}.alternatives[{i}]: dict 아님")
                continue
            if not a.get("method"):
                issues.append(f"{rid}.alternatives[{i}]: method 비어있음")
            if not a.get("kind"):
                warnings.append(f"{rid}.alternatives[{i}]: kind 미명시 — 매핑 시 모호")
            if not a.get("reason"):
                warnings.append(f"{rid}.alternatives[{i}]: reason 비어있음 — 사용자 이해 어려움")

    # 4. priority 대역 검사 (_meta.priority_layers와 일치)
    layers = data.get("_meta", {}).get("priority_layers", {})
    if layers:
        valid_bands: list[tuple[int, int]] = []
        for k in layers:
            try:
                lo, hi = k.split("-") if "-" in k else (k.rstrip("+"), k.rstrip("+"))
                lo_int = int(lo)
                hi_int = int(hi) if hi.isdigit() else 9999
                valid_bands.append((lo_int, hi_int))
            except Exception:
                continue
        if valid_bands:
            for r in rules:
                p = r.get("priority", 0)
                if not any(lo <= p <= hi for lo, hi in valid_bands):
                    warnings.append(f"{r.get('rule_id')}: priority {p}가 _meta.priority_layers에 정의된 대역 밖")

    # 6. thresholds ↔ 룰 conditions 일관성 (2026-06-13 E1)
    #    backend/services/thresholds.py가 단일 소스로 참조하는 핵심 임계값이
    #    실제 룰 conditions의 금액 경계에 등장하는지 검증 → JSON 내부 드리프트 차단.
    thresholds = data.get("thresholds", {})
    cond_amounts: set[int] = set()
    for r in rules:
        for k, v in (r.get("conditions", {}) or {}).items():
            if k in ("estimated_price_gte", "estimated_price_lt", "estimated_price_lte") and isinstance(v, int):
                cond_amounts.add(v)
    core_keys = (
        "announcement_limit",
        "sme_small_enterprise_upper",
        "international_bid_product",
        "international_bid_service",
    )
    for tk in core_keys:
        tv = thresholds.get(tk)
        if tv is None:
            warnings.append(f"thresholds.{tk} 미정의 — thresholds.py fallback 사용 중")
        elif tv not in cond_amounts:
            warnings.append(
                f"thresholds.{tk}={tv}가 어떤 룰 conditions 금액 경계에도 없음 "
                f"— 코드/룰 드리프트 의심"
            )

    # 5. 통계 (정보)
    print("=" * 60)
    print(f"룰 schema audit — {RULES_PATH.relative_to(ROOT)}")
    print("=" * 60)
    print(f"총 룰: {len(rules)}")
    print(f"메타: priority_layers {len(layers)}개 정의")
    print()
    if issues:
        print(f"❌ 차단 이슈 {len(issues)}건:")
        for x in issues:
            print(f"  {x}")
        print()
    if warnings:
        print(f"⚠️  경고 {len(warnings)}건 (자연 분기·선택 필드):")
        for x in warnings[:20]:
            print(f"  {x}")
        if len(warnings) > 20:
            print(f"  ... 외 {len(warnings)-20}건")
        print()
    if not issues and not warnings:
        print("✅ schema audit PASS — 차단 이슈·경고 0건")
    elif not issues:
        print(f"✅ schema audit PASS (차단 0, 경고 {len(warnings)}건)")
    else:
        print(f"❌ schema audit FAIL — 차단 {len(issues)}건")

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
