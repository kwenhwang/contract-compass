"""fail-closed 가드 테스트 (2026-07-20 dataloss-audit).

핵심 불변식: 검색 근거 0건이면 LLM을 호출하지 않고 결정론 안내를 반환하고,
LLM 빈 응답은 유효 답변으로 승격되지 않는다.
네트워크/LLM 불필요 — 라우트 함수 직접 호출(test_rfp_api.py 관례).
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.api.v1 import ask as ask_mod  # noqa: E402


class _EmptyRAG:
    def search_all(self, q, top_k=5):
        return []


class _BoomLLM:
    async def complete(self, *a, **k):
        raise AssertionError("근거 0건이면 LLM이 호출되면 안 됨")


def test_ask_zero_hit_skips_llm_and_returns_no_evidence():
    req = ask_mod.AskRequest(question="근거가 전혀 없는 테스트 질문 XYZZY-0720")
    resp = asyncio.run(ask_mod.ask_question(
        req, rag=_EmptyRAG(), llm=_BoomLLM(), client_ip="127.0.0.1"))
    assert resp.answer == ask_mod._NO_EVIDENCE_ANSWER
    assert resp.sources == []
    assert resp.avg_relevance == 0.0
    assert resp.timing["llm_ms"] == 0.0


def test_ask_zero_hit_not_cached():
    q = "캐시 오염 방지 확인 XYZZY-0720-B"
    asyncio.run(ask_mod.ask_question(
        ask_mod.AskRequest(question=q), rag=_EmptyRAG(), llm=_BoomLLM(), client_ip="127.0.0.1"))
    assert ask_mod._cache_get(q) is None, "0-hit 응답이 캐시되면 일시 장애가 6h 고착됨"


# ── 2026-07-24: 0-hit 비용가드 후속(396b824) 동작 검증 ───────────────────────────
#   기존 test_llm_cost_guard 는 소스 문자열 카운트라 순서·경로를 검증 못 함.
#   실제 불변식: RAG 0-hit(LLM 미호출)는 전역 일일 캡은 미차감하되 IP 스로틀엔 계상.

def _ip_hits(rl, ip: str) -> int:
    # 2026-07-30: 리미터 SQLite(WAL) 전환(ba9181a)에 맞춰 인메모리 _ips 대신 DB를 직접 센다
    return rl._connect().execute(
        "SELECT COUNT(*) FROM llm_hits WHERE ip = ?", (ip,)).fetchone()[0]


def test_zero_hit_counted_in_ip_window():
    """0-hit 요청도 IP별 sliding window에는 계상돼야 무한 재시도 증폭이 막힌다."""
    from backend.services.rate_limiter import get_rate_limiter
    ip = "203.0.113.7"  # 비화이트리스트 (테스트 격리용 TEST-NET-3)
    rl = get_rate_limiter()
    before = _ip_hits(rl, ip)
    asyncio.run(ask_mod.ask_question(
        ask_mod.AskRequest(question="IP 스로틀 계상 확인 XYZZY-0724"),
        rag=_EmptyRAG(), llm=_BoomLLM(), client_ip=ip))
    assert _ip_hits(rl, ip) == before + 1


def test_zero_hit_does_not_charge_daily_cap(monkeypatch):
    """0-hit는 과금이 없으므로 전역 일일 캡(예산)은 차감하지 않는다."""
    import backend.services.rate_limiter as rl_mod
    charged = {"n": 0}
    monkeypatch.setattr(rl_mod.DailyCallCap, "record", lambda self: charged.__setitem__("n", charged["n"] + 1))
    asyncio.run(ask_mod.ask_question(
        ask_mod.AskRequest(question="일일 캡 미차감 확인 XYZZY-0724-B"),
        rag=_EmptyRAG(), llm=_BoomLLM(), client_ip="198.51.100.9"))
    assert charged["n"] == 0, "0-hit가 일일 과금 캡을 차감하면 안 됨"


def test_cache_hit_charges_nothing(monkeypatch):
    """캐시 히트는 LLM·임베딩 비용이 없으므로 IP 스로틀·일일 캡 어느 쪽도 차감 안 함."""
    from backend.services.rate_limiter import get_rate_limiter
    import backend.services.rate_limiter as rl_mod
    ip = "198.51.100.42"
    q = "캐시 히트 무차감 확인 XYZZY-0724-C"
    ask_mod._cache_set(q, {"answer": "미리 넣은 답", "sources": [], "timing": None,
                           "unverified_citations": [], "avg_relevance": 0.7})
    rl = get_rate_limiter()
    before = _ip_hits(rl, ip)
    charged = {"n": 0}
    monkeypatch.setattr(rl_mod.DailyCallCap, "record", lambda self: charged.__setitem__("n", charged["n"] + 1))
    resp = asyncio.run(ask_mod.ask_question(
        ask_mod.AskRequest(question=q), rag=_EmptyRAG(), llm=_BoomLLM(), client_ip=ip))
    assert resp.answer == "미리 넣은 답"
    assert _ip_hits(rl, ip) == before
    assert charged["n"] == 0


def test_openai_empty_response_raises():
    from backend.services.llm import openai_provider as op

    class _Msg:
        content = "   "

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        async def create(self, **kwargs):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    prov = object.__new__(op.OpenAIProvider)
    prov._model = "test-model"
    prov._client = _Client()
    with pytest.raises(RuntimeError, match="빈 응답"):
        asyncio.run(prov.complete("system", "user"))
