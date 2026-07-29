"""LLM provider 선택·OpenAI 경로 단위 테스트 (2026-07-17).

Gemini free-tier RPM 캡 병목 → OpenAI 기본 경로 전환 검증.
- provider 선택(create_provider): 기본 openai, "gemini" 명시 시만 Gemini.
- api_key strip(앞 공백/끝 개행 손상 방어).
- OpenAIProvider.complete: 지정 모델·json_mode(response_format) 전달, content 반환.
- timeout·max_retries(SDK 지수 백오프) 주입.

실제 API 키 호출 없음 — AsyncOpenAI/genai.Client를 전부 mock. `-m "not integration"`에 포함.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.llm import create_provider  # noqa: E402
from backend.services.llm.openai_provider import OpenAIProvider  # noqa: E402
from backend.services.llm.gemini_provider import GeminiProvider  # noqa: E402

pytestmark = pytest.mark.unit


def _mock_openai_client():
    """chat.completions.create / embeddings.create 를 갖춘 가짜 AsyncOpenAI 클라이언트."""
    client = MagicMock()
    msg = MagicMock()
    msg.content = '{"contract_type":"service","confidence":0.9}'
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


# ── provider 선택 ────────────────────────────────────────────────────────────

def test_create_provider_defaults_to_openai():
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value = _mock_openai_client()
        prov = create_provider("openai", "sk-test-key")
        assert isinstance(prov, OpenAIProvider)


def test_create_provider_unknown_provider_falls_back_to_openai():
    # provider가 "gemini"가 아니면 무엇이든 OpenAI 경로(기본 안전).
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value = _mock_openai_client()
        prov = create_provider("", "sk-test-key")
        assert isinstance(prov, OpenAIProvider)


def test_create_provider_gemini_only_when_explicit():
    with patch("backend.services.llm.gemini_provider.genai") as mock_genai:
        mock_genai.Client.return_value = MagicMock()
        prov = create_provider("gemini", "AIza-test-key")
        assert isinstance(prov, GeminiProvider)


# ── api_key 정규화(손상 방어) ─────────────────────────────────────────────────

def test_create_provider_strips_key_whitespace_and_newline():
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value = _mock_openai_client()
        create_provider("openai", "  sk-test-key\n")
        _, kwargs = MockClient.call_args
        assert kwargs["api_key"] == "sk-test-key"


# ── OpenAIProvider 구성 파라미터 ───────────────────────────────────────────────

def test_openai_provider_passes_timeout_and_retries():
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value = _mock_openai_client()
        OpenAIProvider("sk-test-key", model="gpt-4o", timeout=42.0, max_retries=5)
        _, kwargs = MockClient.call_args
        assert kwargs["timeout"] == 42.0
        assert kwargs["max_retries"] == 5


def test_openai_provider_base_url_empty_key_placeholder():
    # vLLM 등 사내 서버(base_url 지정 + 빈 키) → SDK 빈 키 거부 회피용 EMPTY 플레이스홀더.
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value = _mock_openai_client()
        OpenAIProvider("", base_url="http://gpu:8000/v1", model="internal")
        _, kwargs = MockClient.call_args
        assert kwargs["api_key"] == "EMPTY"
        assert kwargs["base_url"] == "http://gpu:8000/v1"


# ── OpenAIProvider.complete 호출 계약 ─────────────────────────────────────────

def test_openai_complete_json_mode_sets_response_format():
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        client = _mock_openai_client()
        MockClient.return_value = client
        prov = OpenAIProvider("sk-test-key", model="gpt-4o-test")

        out = asyncio.run(prov.complete("system prompt", "user prompt", json_mode=True))

        assert out == '{"contract_type":"service","confidence":0.9}'
        _, kwargs = client.chat.completions.create.call_args
        assert kwargs["model"] == "gpt-4o-test"
        assert kwargs["response_format"] == {"type": "json_object"}
        # system·user 메시지가 순서대로 전달됐는지
        roles = [m["role"] for m in kwargs["messages"]]
        assert roles == ["system", "user"]


def test_openai_gpt4o_sends_temperature():
    # gpt-4o 계열은 결정성 위해 temperature=0.1 전달.
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        client = _mock_openai_client()
        MockClient.return_value = client
        prov = OpenAIProvider("sk-test-key", model="gpt-4o")
        asyncio.run(prov.complete("s", "u"))
        _, kwargs = client.chat.completions.create.call_args
        assert kwargs["temperature"] == 0.1


def test_openai_gpt5_omits_temperature():
    # gpt-5.x(reasoning 계열)는 temperature 커스텀 불가 → 파라미터 미전달(400 회피).
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        client = _mock_openai_client()
        MockClient.return_value = client
        prov = OpenAIProvider("sk-test-key", model="gpt-5.6-luna")
        asyncio.run(prov.complete("s", "u", json_mode=True))
        _, kwargs = client.chat.completions.create.call_args
        assert "temperature" not in kwargs
        # json_mode는 gpt-5.x에도 정상 전달
        assert kwargs["response_format"] == {"type": "json_object"}
        assert kwargs["model"] == "gpt-5.6-luna"


def test_openai_gpt5_stream_omits_temperature():
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        client = _mock_openai_client()

        async def _empty_aiter():
            if False:
                yield None  # pragma: no cover

        client.chat.completions.create = AsyncMock(return_value=_empty_aiter())
        MockClient.return_value = client
        prov = OpenAIProvider("sk-test-key", model="gpt-5.6-luna")

        async def _drain():
            return [c async for c in prov.stream("s", "u")]

        asyncio.run(_drain())
        _, kwargs = client.chat.completions.create.call_args
        assert "temperature" not in kwargs
        assert kwargs["stream"] is True


def test_openai_complete_plain_mode_no_response_format():
    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        client = _mock_openai_client()
        MockClient.return_value = client
        prov = OpenAIProvider("sk-test-key")

        asyncio.run(prov.complete("s", "u", json_mode=False))

        _, kwargs = client.chat.completions.create.call_args
        assert "response_format" not in kwargs


def test_openai_complete_retries_then_succeeds():
    """SDK 재시도를 provider 레벨에서 흉내 — 첫 호출 예외, 두 번째 성공."""
    from openai import APITimeoutError

    with patch("backend.services.llm.openai_provider.AsyncOpenAI") as MockClient:
        client = _mock_openai_client()
        good = client.chat.completions.create.return_value
        client.chat.completions.create = AsyncMock(
            side_effect=[APITimeoutError(request=MagicMock()), good]
        )
        MockClient.return_value = client
        prov = OpenAIProvider("sk-test-key")

        # 실제 재시도는 SDK max_retries가 담당하지만, 여기선 호출 측이 예외를
        # 그대로 받는지(=상위 fallback 트리거 가능)와 정상 경로 반환을 함께 확인.
        with pytest.raises(APITimeoutError):
            asyncio.run(prov.complete("s", "u"))

        # 두 번째 호출은 정상 content 반환
        out = asyncio.run(prov.complete("s", "u"))
        assert out == good.choices[0].message.content
