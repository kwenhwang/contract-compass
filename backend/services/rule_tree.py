"""contract_rules.json 룰엔진 → 도메인 전문가 검증용 '동치 의사결정트리' 자동 도출.

이것은 **학습된 결정트리가 아니다.** 도메인을 직접 인코딩한 RuleEngine
(match: 조건 필터 → priority 최소 룰 선택)을, 사람이 읽을 수 있도록 동치 트리로 변환한 것이다.
트리의 각 잎은 `engine.match()` 최상위 결과와 1:1로 일치한다
(tests/unit/test_rule_tree_fidelity.py 가 모델 입력공간 전수로 검증).

모델링: 룰 조건을 의미 단위 '결정 차원'으로 묶는다(차원당 한 값, 상호배타 선택).
실무가 "어떤 수의사유/제한이 적용되나"를 하나씩 고르는 방식과 일치하며, 8개 독립 불리언을
전수 곱하는 것보다 트리가 훨씬 읽힌다. (다중 제한 동시설정은 priority로 해소되며 트리 범위 밖.)
"""
from __future__ import annotations

from typing import Any

from backend.services.rule_engine import RuleEngine

# ── 표시용 한글 라벨 ─────────────────────────────────────────────
NEG_LABELS = {
    "rebid_failure": "재공고 유찰", "rebid": "재공고", "urgent": "긴급",
    "technical_difficulty": "기술적 곤란", "patent_new_tech": "특허·신기술",
    "specific_person": "특정인", "small_repeat": "소액(경쟁 비효율)", "other_justified": "기타 정당사유",
}
NEG_ORDER = ["rebid_failure", "rebid", "urgent", "technical_difficulty",
             "patent_new_tech", "specific_person", "small_repeat", "other_justified"]
SERVICE_TYPE_LABELS = {"academic": "학술용역", "facility": "시설용역", "it_service": "정보화사업",
                       "technical": "기술용역", "other": "기타용역"}
SPECIALTY_LABELS = {"general": "일반(종합)공사", "electrical": "전기공사",
                    "ict": "정보통신공사", "fire_safety": "소방·법령공사", "cultural_heritage": "문화재공사",
                    "professional_generic": "전문공사(건산법 14종)"}
RESTRICTION_LABELS = {
    "is_simple_labor": "단순노무", "is_sme_competition_product": "중기간 경쟁제품",
    "is_sme_mandatory": "중소기업 의무구매", "is_social_enterprise": "사회적기업",
    "regional_restriction": "지역제한", "small_enterprise_restriction": "소기업·소상공인",
    "pq_required": "PQ(입찰참가자격 사전심사) 대상",
}
RESTRICTION_KEYS = list(RESTRICTION_LABELS)


def format_won(n: int) -> str:
    """원 단위 정수를 억/천만/만 한글 표기로."""
    if n % 100_000_000 == 0:
        return f"{n // 100_000_000}억"
    if n >= 100_000_000:
        return f"{n / 100_000_000:.2g}억".replace(".0억", "억")
    if n % 10_000_000 == 0:
        return f"{n // 10_000_000}천만"
    return f"{n // 10_000:,}만"


def _price_intervals(rules: list[dict]) -> list[tuple[int, int | None, int, str]]:
    """룰들의 gte/lt 경계로 금액 구간 도출 → (lo, hi, 대표가, 라벨) 목록."""
    bounds = set()
    for r in rules:
        c = r.get("conditions", {})
        for k in ("estimated_price_gte", "estimated_price_lt"):
            if k in c:
                bounds.add(int(c[k]))
        # lte X는 정수 금액에서 lt X+1과 동치 — 구간 절단점은 X+1
        if "estimated_price_lte" in c:
            bounds.add(int(c["estimated_price_lte"]) + 1)
    pts = sorted(bounds)
    intervals = []
    prev = 0
    for b in pts:
        if b > prev:
            intervals.append((prev, b))
            prev = b
    intervals.append((prev, None))
    out = []
    for lo, hi in intervals:
        rep = (lo + hi) // 2 if hi else lo + 1_000_000_000  # 구간 대표점
        if lo == 0:
            label = f"{format_won(hi)} 미만"
        elif hi is None:
            label = f"{format_won(lo)} 이상"
        else:
            label = f"{format_won(lo)}~{format_won(hi)}"
        out.append((lo, hi, rep, label))
    return out


def _neg_patch(reason: str) -> dict:
    # 사유별 수의 룰은 negotiation_reason(+재공고는 prior_bid_count)만으로 매칭된다.
    # negotiation_contract(협상)는 별개 개념이라 여기서 설정하지 않는다.
    p = {"negotiation_reason": reason}
    if reason in ("rebid", "rebid_failure"):
        p["prior_bid_count"] = 1
    return p


def make_dimensions(engine: RuleEngine, contract_type: str, org_type: str) -> list[dict]:
    """계약유형별 결정 차원(순서대로). 각 차원: id·label·options[{label, patch}]."""
    rules = [r for r in engine._data.get("rules", [])
             if r.get("contract_type") in (contract_type, "public_procurement")]
    present = set()
    reasons = set()
    for r in rules:
        for k, v in r.get("conditions", {}).items():
            present.add(k)
            if k == "negotiation_reason":
                reasons.add(v)

    dims: list[dict] = []

    # 1) 수의계약 사유 — 해당 사유 전용 룰이 org_type에서 실제 매칭되는 것만
    #    (예: 'rebid'는 지자체 전용 → 공기업 트리에서 제외). 협상은 별도 옵션.
    neg_opts = [{"label": "해당 없음 (경쟁입찰)", "patch": {}}]
    for reason in NEG_ORDER:
        if reason not in reasons:
            continue
        patch = _neg_patch(reason)
        m = engine.match({"contract_type": contract_type, "estimated_price": 100_000_000, **patch}, org_type)
        if m and m[0].get("conditions", {}).get("negotiation_reason") == reason:
            neg_opts.append({"label": f"수의: {NEG_LABELS.get(reason, reason)}", "patch": patch})
    if "negotiation_contract" in present:
        neg_opts.append({"label": "협상에 의한 낙찰자결정", "patch": {"negotiation_contract": True}})
    if len(neg_opts) > 1:
        dims.append({"id": "negotiation", "label": "수의·협상 사유", "options": neg_opts})

    # 2) 공사 전문분야 (construction 전용) — 엔진 그룹매핑 대표값
    if contract_type == "construction" and "construction_specialty" in present:
        opts = [{"label": SPECIALTY_LABELS.get(s, s), "patch": {"construction_specialty": s}}
                for s in ["general", "professional_generic", "electrical", "ict",
                          "fire_safety", "cultural_heritage"]]
        dims.append({"id": "specialty", "label": "공사 종류", "options": opts})

    # 3) 추정가격 구간
    intervals = _price_intervals(rules)
    if len(intervals) > 1:
        dims.append({"id": "price", "label": "추정가격",
                     "options": [{"label": lab, "patch": {"estimated_price": rep}} for _, _, rep, lab in intervals]})

    # 4) 용역 종류 (service 전용)
    if contract_type == "service" and "service_type" in present:
        st_vals = sorted({v for r in rules for k, v in r.get("conditions", {}).items() if k == "service_type"})
        opts = [{"label": "일반 용역", "patch": {}}]
        opts += [{"label": SERVICE_TYPE_LABELS.get(s, s), "patch": {"service_type": s}} for s in st_vals]
        dims.append({"id": "service_type", "label": "용역 종류", "options": opts})

    # 5) 적용 제한·특례 (상호배타 모델)
    rkeys = [k for k in RESTRICTION_KEYS if k in present]
    if rkeys:
        opts = [{"label": "없음", "patch": {}}]
        opts += [{"label": RESTRICTION_LABELS[k], "patch": {k: True}} for k in rkeys]
        dims.append({"id": "restriction", "label": "적용 제한·특례", "options": opts})

    return dims


def _params(contract_type: str, assignment: dict) -> dict:
    p = {"contract_type": contract_type}
    for patch in assignment.values():
        p.update(patch)
    return p


def _top_rule(engine: RuleEngine, contract_type: str, org_type: str, assignment: dict):
    m = engine.match(_params(contract_type, assignment), org_type)
    return m[0] if m else None


def _rep_price(assignment: dict) -> int:
    for patch in assignment.values():
        if "estimated_price" in patch:
            return patch["estimated_price"]
    return 0


def _resolve_method(rule: dict, price: int) -> str:
    res = rule.get("result", {})
    mba = res.get("method_by_amount")
    if mba:
        tiers = sorted(((int(k.split("_", 1)[1]), v) for k, v in mba.items()), reverse=True)
        for thr, method in tiers:
            if price >= thr:
                return method
        return tiers[-1][1] if tiers else res.get("method", "")
    return res.get("method", "")


def build_tree(engine: RuleEngine, contract_type: str, org_type: str = "public_corp") -> dict[str, Any]:
    """엔진을 모델 입력공간에 대해 재귀 분할 → 엔진과 동치인 최소 트리.

    반환: {contract_type, org_type, dimensions, nodes, edges, mermaid, coverage}
    """
    dims = make_dimensions(engine, contract_type, org_type)
    law_reg = {a.get("key"): a for a in engine._data.get("law_registry", [])} if isinstance(
        engine._data.get("law_registry"), list) else {}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    counter = [0]
    leaf_memo: dict[tuple, str] = {}  # 동일 결과 잎 공유 → 트리 간결화

    def new_id() -> str:
        counter[0] += 1
        return f"n{counter[0]}"

    def outcomes_list(assignment: dict, dim_idx: int) -> tuple:
        """assignment 고정 + dims[dim_idx:] 전수(고정 순서) → 최상위 rule_id 튜플.
        길이1 집합이면 잎, 동일 튜플이면 동일 서브트리(DAG 공유)."""
        if dim_idx >= len(dims):
            r = _top_rule(engine, contract_type, org_type, assignment)
            return (r["rule_id"] if r else None,)
        out: list = []
        for opt in dims[dim_idx]["options"]:
            a2 = dict(assignment); a2[dims[dim_idx]["id"]] = opt["patch"]
            out.extend(outcomes_list(a2, dim_idx + 1))
        return tuple(out)

    def make_leaf(assignment: dict) -> str:
        rule = _top_rule(engine, contract_type, org_type, assignment)
        if not rule:
            key = ("__none__",)
            if key in leaf_memo:
                return leaf_memo[key]
            nid = new_id()
            nodes[nid] = {"type": "leaf", "rule_id": None, "method": "(매칭 룰 없음 — 검토 필요)"}
            leaf_memo[key] = nid
            return nid
        price = _rep_price(assignment)
        score = engine.get_pass_score(rule, price)
        method = _resolve_method(rule, price)
        # 동일 결과(룰+금액맥락)면 잎 공유
        key = (rule["rule_id"], method, score.get("pass_score"), score.get("lower_limit_rate"))
        if key in leaf_memo:
            return leaf_memo[key]
        nid = new_id()
        nodes[nid] = {
            "type": "leaf", "rule_id": rule["rule_id"], "name": rule.get("name", ""),
            "method": method,
            "bidder_selection": rule.get("result", {}).get("bidder_selection"),
            "pass_score": score.get("pass_score"), "lower_limit_rate": score.get("lower_limit_rate"),
            "legal_basis": rule.get("legal_basis", []),
            "alternatives": [a.get("method") for a in rule.get("result", {}).get("alternatives", [])],
        }
        leaf_memo[key] = nid
        return nid

    subtree_memo: dict[tuple, str] = {}  # (dim_idx, 결과시그니처) → 노드 (동일 서브트리 공유=DAG)

    def rec(assignment: dict, dim_idx: int) -> str:
        # 결과를 가르는 첫 차원으로 전진(분기 안 만드는 차원은 건너뜀)
        while dim_idx < len(dims):
            if len(set(outcomes_list(assignment, dim_idx))) == 1:
                return make_leaf(assignment)  # 남은 공간 단일 결과 → 잎
            dim = dims[dim_idx]
            sigs = set()
            for opt in dim["options"]:
                a2 = dict(assignment); a2[dim["id"]] = opt["patch"]
                sigs.add(outcomes_list(a2, dim_idx + 1))
            if len(sigs) > 1:
                break  # 이 차원이 분기를 만든다
            dim_idx += 1
        if dim_idx >= len(dims):
            return make_leaf(assignment)
        sig = (dim_idx, outcomes_list(assignment, dim_idx))
        if sig in subtree_memo:
            return subtree_memo[sig]
        nid = new_id()
        dim = dims[dim_idx]
        nodes[nid] = {"type": "decision", "dim": dim["id"], "question": dim["label"]}
        for opt in dim["options"]:
            a2 = dict(assignment); a2[dim["id"]] = opt["patch"]
            child = rec(a2, dim_idx + 1)
            edges.append({"from": nid, "to": child, "label": opt["label"]})
        subtree_memo[sig] = nid
        return nid

    root = rec({}, 0) if dims else make_leaf({})

    # 충실도 커버리지: 모델 공간 전수에서 트리 경로 == 엔진 결과
    cells, ok = _coverage(engine, contract_type, org_type, dims, nodes, edges, root)

    mermaid = _to_mermaid(nodes, edges, root)
    return {
        "contract_type": contract_type, "org_type": org_type,
        "dimensions": [{"id": d["id"], "label": d["label"]} for d in dims],
        "root": root, "nodes": nodes, "edges": edges, "mermaid": mermaid,
        "coverage": {"cells": cells, "reproduced": ok},
    }


def _traverse(nodes, edges, root, assignment, dims) -> str | None:
    """모델 입력(assignment)으로 트리를 타고 내려가 도달한 leaf의 rule_id."""
    by_from: dict[str, list[dict]] = {}
    for e in edges:
        by_from.setdefault(e["from"], []).append(e)
    dim_label_to_opt = {}
    for d in dims:
        for opt in d["options"]:
            dim_label_to_opt[(d["id"], opt["label"])] = opt
    cur = root
    while nodes[cur]["type"] == "decision":
        dim_id = nodes[cur]["dim"]
        chosen_patch = assignment.get(dim_id, {})
        # assignment의 해당 차원 옵션 라벨로 간선 선택
        target = None
        for e in by_from.get(cur, []):
            opt = dim_label_to_opt.get((dim_id, e["label"]))
            if opt is not None and opt["patch"] == chosen_patch:
                target = e["to"]; break
        if target is None:
            return "__NO_EDGE__"
        cur = target
    return nodes[cur].get("rule_id")


def _coverage(engine, contract_type, org_type, dims, nodes, edges, root):
    """모델 공간 전수: 트리 경로 결과 == 엔진 결과 인지 카운트."""
    cells = [0]; ok = [0]

    def walk(assignment, idx):
        if idx >= len(dims):
            cells[0] += 1
            r = _top_rule(engine, contract_type, org_type, assignment)
            engine_rid = r["rule_id"] if r else None
            tree_rid = _traverse(nodes, edges, root, assignment, dims)
            if tree_rid == engine_rid:
                ok[0] += 1
            return
        for opt in dims[idx]["options"]:
            a2 = dict(assignment); a2[dims[idx]["id"]] = opt["patch"]
            walk(a2, idx + 1)

    walk({}, 0)
    return cells[0], ok[0]


def _esc(s: str) -> str:
    return str(s).replace('"', "'").replace("\n", " ")


def _group_label(labels: list[str]) -> str:
    """같은 대상으로 가는 간선 라벨 묶음 — 선택지를 전부 나열(2026-07-13: '그 외 N종' 축약이
    실무 검토에서 '이게 뭐냐'는 질문을 유발해 폐지). 폭 제한을 위해 ~24자 단위로 줄바꿈."""
    if len(labels) == 1:
        return labels[0]
    rows: list[list[str]] = [[]]
    for lb in labels:
        if rows[-1] and len(" · ".join(rows[-1] + [lb])) > 24:
            rows.append([])
        rows[-1].append(lb)
    return "<br/>".join(" · ".join(r) for r in rows)


def _to_mermaid(nodes: dict, edges: list, root: str) -> str:
    """표시용 Mermaid. 같은 대상으로 가는 간선은 라벨을 묶어 전부 나열."""
    lines = ["graph TD"]
    for nid, n in nodes.items():
        if n["type"] == "decision":
            lines.append(f'  {nid}{{"{_esc(n["question"])}"}}')
        else:
            rid = n.get("rule_id") or "—"
            method = _esc(n.get("method", ""))
            extra = ""
            if n.get("pass_score"):
                extra = f"<br/>적격 {n['pass_score']}점·하한 {n['lower_limit_rate']}"
            elif n.get("lower_limit_rate"):
                extra = f"<br/>하한율 {n['lower_limit_rate']}"
            lines.append(f'  {nid}["<b>{method}</b><br/>{_esc(n.get("name",""))}<br/><i>{rid}</i>{extra}"]')
            lines.append(f"  class {nid} leaf")
    # 간선 그룹: source별 target→[labels]
    grouped: dict[str, dict[str, list[str]]] = {}
    order: dict[str, list[str]] = {}
    for e in edges:
        g = grouped.setdefault(e["from"], {})
        if e["to"] not in g:
            g[e["to"]] = []
            order.setdefault(e["from"], []).append(e["to"])
        g[e["to"]].append(e["label"])
    for src in order:
        for tgt in order[src]:
            label = _group_label(grouped[src][tgt])
            # 간선 라벨은 따옴표로 감싼다 — 괄호·슬래시가 mermaid 노드문법으로 오인되지 않도록
            lines.append(f'  {src} -->|"{_esc(label)}"| {tgt}')
    lines.append("  classDef leaf fill:#eef6ff,stroke:#2563eb,stroke-width:1px;")
    return "\n".join(lines)
