#!/usr/bin/env python3
"""지자체 룰셋 — 낙찰하한율 별표 추출 + '룰별 법령셋(LLM 전달용)' 데이터 구조 생성.

SaaS 지향: 각 룰(LOCAL_*)이 자신의 **법령셋**(지방계약법 시행령 조문 + 행안부 예규 별표
+ 낙찰하한율)을 들고 있어, 결정론 룰엔진이 매칭한 룰의 법령셋을 LLM 컨텍스트로 그대로
전달(=환각 없는 근거형). 기존 textbook_pack(국가) 패턴을 지방으로 확장.

산출:
  rules/local_award_criteria.json  — 행안부 예규 낙찰하한율(평점산식 %) 추출값(원문 기반)
  rules/local_law_pack.json        — 룰 case → 법령셋(조문 발췌+예규 ref+낙찰하한율)
사용: python3 tools/build_local_law_pack.py
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADMRUL = ROOT / "tools" / "laws" / "지방자치단체_입찰시_낙찰자_결정기준_전문.hwpx"
LAW_REG = ROOT / "rules" / "law_registry.json"


def _admrul_text() -> str:
    z = zipfile.ZipFile(io.BytesIO(ADMRUL.read_bytes()))
    xml = re.sub(r"<hp:fwSpace/>", " ", z.read("Contents/section0.xml").decode("utf-8", "ignore"))
    return re.sub(r"\s+", " ", " ".join(re.findall(r"<hp:t>(.*?)</hp:t>", xml, re.S)))


def extract_lower_limits(text: str) -> list[dict]:
    """예규의 '입찰가격 평점산식 (NN.NNN%)' = 낙찰하한율. 배점·공종 문맥과 함께 추출(원문 기반)."""
    out, seen = [], set()
    for m in re.finditer(r"평점산식\s*\(\s*([\d.]+)\s*%\s*\)", text):
        rate = float(m.group(1))
        if not (60 <= rate <= 95):
            continue
        pre = text[max(0, m.start() - 240): m.start()]
        pt = re.findall(r"입찰가격\s*평가\s*\(\s*(\d+)\s*점", pre)
        kind = next((k for k in ["종합공사", "전문공사", "시설공사", "기술용역", "용역", "물품",
                                  "전기공사", "정보통신공사", "소방시설공사"] if k in pre), "")
        band = re.findall(r"추정가격[^.]{0,55}?(?:미만|이상|이하)", pre)
        key = (rate, pt[-1] if pt else "", kind)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "낙찰하한율": rate,
            "입찰가격_배점": (int(pt[-1]) if pt else None),
            "공종": kind or "확인필요",
            "추정가격_구간단서": (band[-1].strip()[:50] if band else ""),
            "출처": "행정안전부 예규 「지방자치단체 입찰시 낙찰자 결정기준」(발령 344호, 시행 2025.12.1) 입찰가격 평점산식",
            "주의": "배점↔추정가격 구간 정밀 매핑은 동 예규 입찰등급 별표 대조 필요(구현단계)",
        })
    return sorted(out, key=lambda x: -x["낙찰하한율"])


def extract_facility_bands(text: str) -> list[dict]:
    """시설공사 적격심사 별표1~7: 금액구간(추정가격 미만) ↔ 입찰가격 배점 ↔ 낙찰하한율."""
    hdr = re.compile(r"시설공사\s*적격심사\s*세부기준\s*\[별표\s*(\d+)\]\s*추정가격이?\s*(\d+)\s*억")
    heads = [(m.group(1), int(m.group(2)), m.start()) for m in hdr.finditer(text)]
    pt2rate: dict[int, str] = {}
    for m in re.finditer(r"입찰가격\s*평가\s*\(\s*(\d+)\s*점\)\s*[가-힣]?\)?\s*입찰가격\s*평점산식\s*\(\s*([\d.]+)\s*%", text):
        pt2rate.setdefault(int(m.group(1)), float(m.group(2)))
    bands = []
    for i, (no, amt, pos) in enumerate(heads[:7]):  # 별표1~7 = 일반 적격심사
        end = heads[i + 1][2] if i + 1 < len(heads) else pos + 5000
        pt = re.search(r"입찰가격\s*평가\s*\(\s*(\d+)\s*점", text[pos:end])
        bp = int(pt.group(1)) if pt else None
        bands.append({"별표": int(no), "추정가격_미만_억": amt, "입찰가격_배점": bp,
                      "낙찰하한율": pt2rate.get(bp)})
    return bands


def build_law_pack(law_reg: dict, limits: list[dict], band_map: list[dict]) -> dict:
    """룰 case → 법령셋(조문 발췌 + 예규 ref + 낙찰하한율). LLM 컨텍스트 빌더가 이 팩을 전달."""
    reg = law_reg["registry"]

    def art(key: str) -> dict | None:
        e = reg.get(key)
        if not e:
            return None
        a = e["articles"][0]
        return {"source": f"{key} {a['title']}", "law_registry_key": key, "body": a["body"]}

    # 공종 태깅이 모호한 건은 공사계열로 간주(예규 시설공사 적격심사 별표 소속). 용역은 명시.
    cst_limits = [x for x in limits if x.get("공종") != "용역"]
    svc_limits = [x for x in limits if x.get("공종") == "용역"]
    rate_const = cst_limits[0]["낙찰하한율"] if cst_limits else None
    rate_svc = svc_limits[0]["낙찰하한율"] if svc_limits else None
    packs = {
        "LOCAL_SVC_PRD_소액수의_2천만": {
            "설명": "물품·용역 추정가격 2천만원 이하 소액수의",
            "적용": {"org_type": "local", "contract_type": ["service", "product"], "estimated_price_lte": 20_000_000},
            "result": {"method": "소액수의계약"},
            # 4단계 체인: 법 → 시행령 → 시행규칙
            "법령셋": [art("지방계약법 제9조"), art("지방계약법 시행령 제25조"), art("지방계약법 시행규칙 제33조")],
            "근거조문": "지방계약법 제9조①단서 → 시행령 제25조제1항제5호나목 → 시행규칙 제33조(견적 생략)",
            "낙찰하한율": None,
        },
        "LOCAL_CST_소액수의_공사": {
            "설명": "공사 소액수의(건설4억·전문2억·기타1.6억 이하)",
            "적용": {"org_type": "local", "contract_type": ["construction"]},
            "result": {"method": "소액수의계약"},
            "법령셋": [art("지방계약법 제9조"), art("지방계약법 시행령 제25조")],
            "근거조문": "지방계약법 제9조①단서 → 시행령 제25조제1항제5호가목",
            "낙찰하한율": None,
        },
        "LOCAL_CST_적격심사_시설공사": {
            "설명": "시설공사 적격심사(경쟁입찰) — 금액구간별 입찰가격 배점·낙찰하한율",
            "적용": {"org_type": "local", "contract_type": ["construction"], "bidder_selection": "적격심사"},
            "법령셋": [art("지방계약법 제9조"), art("지방계약법 시행령 제25조")],
            "예규_별표": {"source": "행안부 예규 「지방자치단체 입찰시 낙찰자 결정기준」 제2장의1 시설공사 적격심사 세부기준",
                        "금액구간_배점_낙찰하한율": band_map,
                        "낙찰하한율_후보": cst_limits},
            "근거조문": "지방계약법 제9조①(경쟁) → 시행령 제25조 → 행안부 예규 시설공사 적격심사 별표1~7",
            "낙찰하한율": rate_const,
        },
        "LOCAL_SVC_적격심사_용역": {
            "설명": "용역 적격심사 — 낙찰하한율 입찰가격 평점산식",
            "적용": {"org_type": "local", "contract_type": ["service"], "bidder_selection": "적격심사"},
            "법령셋": [art("지방계약법 시행령 제25조")],
            "예규_별표": {"source": "행안부 예규 제3장 기술용역/용역 적격심사 세부기준",
                        "낙찰하한율_후보": svc_limits},
            "낙찰하한율": rate_svc,
        },
    }
    return {
        "_meta": {
            "purpose": "지자체 룰별 법령셋 — 룰엔진 매칭 룰의 법령셋을 LLM 컨텍스트로 전달(결정론 근거형)",
            "기준": "지방계약법·시행령(law.go.kr 원문) + 행안부 예규(낙찰자 결정기준, 시행 2025.12.1)",
            "정직성": "낙찰하한율은 예규 평점산식 추출값. 배점↔금액구간 정밀 매핑·소액수의 공사 세부는 구현단계 확정.",
        },
        "packs": packs,
    }


def main() -> None:
    text = _admrul_text()
    limits = extract_lower_limits(text)
    band_map = extract_facility_bands(text)
    law_reg = json.loads(LAW_REG.read_text(encoding="utf-8"))

    (ROOT / "rules" / "local_award_criteria.json").write_text(
        json.dumps({"_source": "행안부 예규 지방자치단체 입찰시 낙찰자 결정기준(시행 2025.12.1)",
                    "낙찰하한율_평점산식": limits,
                    "시설공사_금액구간_배점_낙찰하한율": band_map}, ensure_ascii=False, indent=2), encoding="utf-8")
    pack = build_law_pack(law_reg, limits, band_map)
    (ROOT / "rules" / "local_law_pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"낙찰하한율 추출: {len(limits)}건 → rules/local_award_criteria.json")
    for x in limits:
        print(f"  {x['낙찰하한율']}%  배점{x['입찰가격_배점']}  {x['공종']}")
    print(f"법령셋 팩: {len(pack['packs'])}개 룰 → rules/local_law_pack.json")


if __name__ == "__main__":
    main()
