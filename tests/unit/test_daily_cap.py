"""전역 일일 LLM 호출 상한(서킷브레이커) 단위 테스트 (2026-07-17).

비용 폭주 가드 — filter step1/step2·rfp validate 포함 전 LLM 경로에 적용되는 전역 상한.
검증:
- record() 카운터 증가 + 파일 영속(재시작 시뮬레이션: 새 인스턴스가 같은 파일 읽음).
- current()가 cap 도달 시 check()가 429(과금 대신 우아한 차단).
- 날짜(UTC)가 바뀌면 카운터 자동 리셋.
- cap<=0이면 비활성(무제한).
- 카운터 조회 실패(파일 손상)가 정상 요청을 막지 않음(fail-open on read).

파일 IO만 사용 — 서버·LLM·네트워크 불필요. `-m "not integration"` 포함.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.rate_limiter import DailyCallCap  # noqa: E402

pytestmark = pytest.mark.unit


def _cap(tmp_path, cap=3) -> DailyCallCap:
    return DailyCallCap(cap, str(tmp_path / "cap.json"))


def test_record_increments_and_persists(tmp_path):
    cap = _cap(tmp_path)
    assert cap.current() == 0          # 파일 없음 → 0
    assert cap.record() == 1
    assert cap.record() == 2
    assert cap.current() == 2
    # 재시작 시뮬레이션: 같은 파일을 읽는 새 인스턴스가 이어서 셈
    cap2 = _cap(tmp_path)
    assert cap2.current() == 2
    assert cap2.record() == 3


def test_check_blocks_at_cap_with_429(tmp_path):
    cap = _cap(tmp_path, cap=2)
    cap.record()
    cap.check()          # 1 < 2 → 통과
    cap.record()         # count=2
    with pytest.raises(HTTPException) as ei:
        cap.check()      # 2 >= 2 → 차단
    assert ei.value.status_code == 429
    assert ei.value.detail["error"] == "daily_cap_exceeded"
    assert "이용량을 모두 사용" in ei.value.detail["message"]
    assert int(ei.value.headers["Retry-After"]) >= 60  # UTC 자정까지 실시간 계산


def test_date_rollover_resets_counter(tmp_path):
    cap = _cap(tmp_path, cap=3)
    cap.record(); cap.record()
    assert cap.current() == 2
    # 어제 날짜로 저장된 카운터는 오늘 기준 0으로 간주되고, record 시 리셋됨
    Path(tmp_path / "cap.json").write_text(json.dumps({"date": "2000-01-01", "count": 99}))
    assert cap.current() == 0           # 날짜 불일치 → 0
    cap.check()                          # 차단 안 됨
    assert cap.record() == 1             # 리셋 후 1부터


def test_cap_zero_disables_limit(tmp_path):
    cap = DailyCallCap(0, str(tmp_path / "cap.json"))
    for _ in range(50):
        cap.record()
    cap.check()   # cap<=0 → 무제한, 예외 없음


def test_corrupt_counter_file_does_not_block(tmp_path):
    p = tmp_path / "cap.json"
    p.write_text("{ this is not valid json ]]]")
    cap = DailyCallCap(2, str(p))
    assert cap.current() == 0    # 손상 → 0 (fail-open)
    cap.check()                  # 요청 막지 않음
    assert cap.record() == 1     # record는 손상 파일을 오늘 0부터 새로 씀


def test_check_read_failure_is_fail_open(tmp_path):
    cap = _cap(tmp_path, cap=1)
    cap.record()   # count=1 → 원래는 차단
    # current() 자체가 예외를 던지는 극단 상황에도 check는 통과(방어)
    with patch.object(cap, "current", side_effect=OSError("disk error")):
        cap.check()  # 예외 삼키고 통과 — 카운터 조회 실패가 정상요청을 막지 않음
