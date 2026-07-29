"""비용 폭주 가드 — rate_limit_llm 전 경로 적용 + 전역 일일 상한 배선 테스트.

검증관 지적(rate-limit 일부 경로 미적용, 전역 상한 없음) 해소를 확인한다:
- rate_limit_llm 의존성이 IP별 한도 + 전역 일일 상한을 둘 다 검사.
- record_llm_call이 IP별 카운터와 전역 일일 카운터를 동시에 증가.
- filter step1/step2·ask·classify 전 LLM 경로가 rate_limit_llm + record_llm_call 배선.

무거운 임포트(chromadb 등) 회피 위해 전 경로 적용은 소스 수준으로 확인(빠른 단위 테스트).
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import backend.services.rate_limiter as rl  # noqa: E402

pytestmark = pytest.mark.unit
V1 = ROOT / "backend" / "api" / "v1"


# ── rate_limit_llm: IP별 + 전역 일일 상한 둘 다 ──────────────────────────────

def test_rate_limit_llm_checks_both_iplimit_and_daily_cap():
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="203.0.113.9")
    checked = {"ip": False, "daily": False}

    fake_ipl = MagicMock()
    fake_ipl.check = lambda r, limits: (checked.__setitem__("ip", True), "203.0.113.9")[1]
    fake_cap = MagicMock()
    fake_cap.check = lambda: checked.__setitem__("daily", True)

    with patch.object(rl, "get_rate_limiter", return_value=fake_ipl), \
         patch.object(rl, "get_daily_cap", return_value=fake_cap):
        ip = rl.rate_limit_llm(req)

    assert ip == "203.0.113.9"
    assert checked["ip"] and checked["daily"], "IP별·일일 상한 모두 검사해야 함"


def test_rate_limit_llm_raises_when_daily_cap_exceeded():
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="203.0.113.9")
    fake_ipl = MagicMock()
    fake_ipl.check = MagicMock(return_value="203.0.113.9")  # IP 한도 통과
    fake_cap = MagicMock()
    fake_cap.check = MagicMock(side_effect=HTTPException(status_code=429, detail={"error": "daily_cap_exceeded"}))

    with patch.object(rl, "get_rate_limiter", return_value=fake_ipl), \
         patch.object(rl, "get_daily_cap", return_value=fake_cap):
        with pytest.raises(HTTPException) as ei:
            rl.rate_limit_llm(req)
    assert ei.value.status_code == 429


def test_record_llm_call_increments_both_counters():
    fake_ipl = MagicMock()
    fake_cap = MagicMock()
    with patch.object(rl, "get_rate_limiter", return_value=fake_ipl), \
         patch.object(rl, "get_daily_cap", return_value=fake_cap):
        rl.record_llm_call("203.0.113.9")
    fake_ipl.record.assert_called_once_with("203.0.113.9")
    fake_cap.record.assert_called_once_with()


def test_get_daily_cap_reads_env_override(monkeypatch, tmp_path):
    monkeypatch.setattr(rl, "_daily_cap", None)
    monkeypatch.setenv("OPENAI_DAILY_CALL_CAP", "7")
    monkeypatch.setenv("OPENAI_DAILY_CAP_FILE", str(tmp_path / "c.json"))
    cap = rl.get_daily_cap()
    assert cap._cap == 7
    # 실제 증가·차단이 파일 경유로 동작
    for _ in range(7):
        cap.record()
    with pytest.raises(HTTPException):
        cap.check()
    monkeypatch.setattr(rl, "_daily_cap", None)  # 싱글톤 원복


# ── 전 LLM 경로 배선 (소스 수준) ─────────────────────────────────────────────

def _src(name: str) -> str:
    return (V1 / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("fname", ["filter.py", "ask.py", "classify.py"])
def test_all_llm_routes_import_guard(fname):
    src = _src(fname)
    assert "rate_limit_llm" in src, f"{fname}: rate_limit_llm 미적용"
    assert "record_llm_call" in src, f"{fname}: record_llm_call 미배선"


def test_filter_step1_step2_have_rate_limit_dependency():
    src = _src("filter.py")
    # step1·step2 각 함수 시그니처에 Depends(rate_limit_llm)
    assert len(re.findall(r"Depends\(rate_limit_llm\)", src)) >= 2
    # 캐시 미스 경로에서만 record (캐시 히트는 과금 없음 → 미차감)
    assert src.count("record_llm_call(client_ip)") >= 2
