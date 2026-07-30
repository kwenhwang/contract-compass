"""조문 내부 상호인용 정합성 검사 — LLM 미사용·결정론.

법률에는 **개정으로 항이 밀렸는데 그 항을 가리키던 인용은 정비되지 않은** 사례가
있다. 실측 사례(2026-07-31 제보 mcp-d25a36a8ea3c):

    지방자치단체 보조금 관리에 관한 법률 제21조 제5항
      "…승인 없이 중요재산에 대하여 **제2항 각 호의 행위**를 한 경우에는…"
    그러나 2023.4.11 개정으로 제2항은 '공시' 조항으로 신설되어 각 호가 없고,
    실제 행위 열거(목적 외 사용·양도·교환·대여·담보 제공)는 **제3항 각 호**다.

law.go.kr 현행 원문도 동일하므로 **우리 파싱 결함이 아니라 법률 자체의 미정비
인용**이다. 따라서 이 모듈은 원문을 절대 고치지 않는다 — 조회 결과에 주석을
붙여 표면화만 한다. 원문 변조는 오인용보다 훨씬 위험하다.

탐지 원칙은 보수적이다(무고 탐지 = 신뢰 손상):
  - 같은 조문 안의 인용만 본다. 앞에 다른 조(`제30조제2항 각 호`)가 붙으면 제외.
  - 가리키는 항이 **본문에 존재하는데 각 호가 하나도 없을 때**만 이상으로 본다.
  - 그 조문에 각 호를 가진 항이 하나도 없으면(= 호 구조를 안 쓰는 조문) 침묵한다.
"""
from __future__ import annotations

import re

# ① … ⑳ (U+2460 ~ U+2473) → 1 … 20
_CIRCLED_FIRST = 0x2460
_CIRCLED_LAST = 0x2473


def _circled_to_int(ch: str) -> int | None:
    cp = ord(ch)
    if _CIRCLED_FIRST <= cp <= _CIRCLED_LAST:
        return cp - _CIRCLED_FIRST + 1
    return None


_HANG_MARKER_RE = re.compile(r"([①-⑳])")
# 호 표지: 줄머리의 "1." "12." — 금액·날짜("2023.4.11")를 삼키지 않도록 줄머리로 한정
_HO_LINE_RE = re.compile(r"^\s*(\d{1,2})\.\s", re.MULTILINE)
# "제N항 각 호" (공백 변형 허용)
_XREF_RE = re.compile(r"제\s*(\d{1,2})\s*항\s*각\s*호")
# 인용 바로 앞이 다른 조 참조이면 외부 인용 — 같은 조 안의 정합성 판단 대상이 아니다
_EXTERNAL_PREFIX_RE = re.compile(r"제\s*\d+\s*조(?:의\s*\d+)?\s*$")


def _hang_blocks(content: str) -> dict[int, str]:
    """본문을 항 번호 → 항 본문으로 쪼갠다. 항 표지가 없으면 빈 dict."""
    parts = _HANG_MARKER_RE.split(content)
    if len(parts) < 3:  # 표지 없음
        return {}
    blocks: dict[int, str] = {}
    # split 결과: [머리말, 표지, 본문, 표지, 본문, …]
    for marker, body in zip(parts[1::2], parts[2::2]):
        n = _circled_to_int(marker)
        if n is None:
            continue
        # 같은 항 표지가 중복 등장하면(파싱 잔재) 첫 번째만 신뢰
        blocks.setdefault(n, "")
        blocks[n] += body
    return blocks


def _has_ho(block: str) -> bool:
    return bool(_HO_LINE_RE.search(block))


def detect_crossref_anomalies(content: str) -> list[dict]:
    """조문 본문에서 내부 상호인용 이상을 찾는다.

    Returns: [{"kind", "hang", "referenced", "candidates", "message"}, …]
             이상이 없으면 빈 리스트.
    """
    if not content:
        return []

    blocks = _hang_blocks(content)
    if not blocks:
        return []

    ho_hangs = sorted(n for n, b in blocks.items() if _has_ho(b))
    if not ho_hangs:
        # 호 구조를 쓰지 않는 조문 — 판단 근거가 없으므로 침묵
        return []

    anomalies: list[dict] = []
    seen: set[tuple[int, int]] = set()

    for hang_no, body in sorted(blocks.items()):
        for m in _XREF_RE.finditer(body):
            target = int(m.group(1))
            # 외부 조 인용 제외
            if _EXTERNAL_PREFIX_RE.search(body[max(0, m.start() - 24):m.start()]):
                continue
            if target not in blocks:
                continue  # 본문에 없는 항 — 조립 누락일 수 있어 판단하지 않는다
            if _has_ho(blocks[target]):
                continue  # 정상
            if (hang_no, target) in seen:
                continue
            seen.add((hang_no, target))

            cand = ", ".join(f"제{n}항" for n in ho_hangs)
            anomalies.append({
                "kind": "dangling_ho_reference",
                "hang": hang_no,
                "referenced": target,
                "candidates": ho_hangs,
                "message": (
                    f"제{hang_no}항이 '제{target}항 각 호'를 인용하지만 "
                    f"제{target}항에는 각 호가 없습니다. 이 조문에서 각 호를 "
                    f"가진 항은 {cand}입니다. 개정 과정에서 항 번호가 밀렸는데 "
                    f"인용이 정비되지 않은 것으로 보이며, 원문(law.go.kr 현행)이 "
                    f"그대로이므로 본문은 수정하지 않고 그대로 제공합니다. "
                    f"실제 적용 시 입법 연혁을 확인하십시오."
                ),
            })

    return anomalies
