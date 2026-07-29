from .base import LLMProvider
from .openai_provider import OpenAIProvider
from .gemini_provider import GeminiProvider


class NullProvider(LLMProvider):
    """API 키 미설정 시 provider — 생성은 성공, 호출 시점에만 실패.

    키 없이 기동해도 위저드는 rule-only 폴백으로 동작해야 한다(공개판 약속).
    호출부(step1/step2/ask)는 complete 예외를 잡아 llm_fallback 경로를 탄다.
    """

    async def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        raise RuntimeError("LLM API 키가 설정되지 않았습니다 (rule-only 폴백)")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("LLM API 키가 설정되지 않았습니다")


def create_provider(
    provider: str,
    api_key: str,
    gemini_model: str = "gemini-3.1-flash-lite",
    openai_base_url: str = "",
    openai_model: str = "",
    openai_timeout: float = 60.0,
    openai_max_retries: int = 3,
) -> LLMProvider:
    """provider 선택 팩토리.

    provider="gemini"만 Gemini 경로. 그 외(기본 "openai")는 OpenAI/OpenAI 호환 경로.
    api_key는 여기서 strip — env·.env의 앞뒤 공백/개행 손상으로 인증 실패하던 이력 방어.
    (키 값은 로깅·출력하지 않는다.)
    """
    api_key = (api_key or "").strip()
    if not api_key and not openai_base_url:
        return NullProvider()
    if provider == "gemini":
        return GeminiProvider(api_key, model=gemini_model)
    return OpenAIProvider(
        api_key,
        base_url=openai_base_url,
        model=openai_model,
        timeout=openai_timeout,
        max_retries=openai_max_retries,
    )
