from typing import AsyncIterator

from openai import AsyncOpenAI
from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI 및 OpenAI 호환 서버(vLLM 등) 공용 프로바이더.

    2026-07-10: 자체 호스팅 LLM(vLLM, OpenAI 호환 API) 연결을 위해 base_url·model 주입 지원.
    base_url 미지정 시 공식 OpenAI(gpt-4o) — 기존 동작 그대로.

    2026-07-17: Gemini free-tier RPM 캡 병목 해소를 위해 생성 LLM 기본 경로로 승격.
      - timeout·max_retries 주입 (SDK 내장 지수 백오프: 429/5xx/네트워크 오류 자동 재시도).
      - api_key는 호출 측에서 strip 후 전달(과거 '끝 개행/앞 공백' 손상 이력 대응). 키 값은
        어디에도 로깅하지 않는다.

    2026-07-17(2): gpt-5.6 채택 — gpt-5.x와 gpt-4o 계열을 한 코드로 안전 호출.
      gpt-5.x(reasoning 계열)는 sampling 파라미터를 기본값으로 고정 → temperature 등을
      명시 전달하면 400(unsupported_value). 따라서 모델 prefix로 조건 분기해 gpt-5.x에는
      temperature를 보내지 않는다. json_mode(response_format)·streaming은 양쪽 공통.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        model: str = "",
        timeout: float = 60.0,
        max_retries: int = 3,
    ):
        # 방어적 정규화: 앞뒤 공백·개행 제거(env·.env 손상 대비). 값 자체는 절대 로깅 금지.
        api_key = (api_key or "").strip()
        base_url = (base_url or "").strip()
        # vLLM 등 자체 서버는 키 검증을 안 하지만 SDK가 빈 키를 거부 → 플레이스홀더
        if base_url and not api_key:
            api_key = "EMPTY"
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._model = model or "gpt-4o"
        self._embed_model = "text-embedding-3-small"

    def _sampling_kwargs(self) -> dict:
        """gpt-5.x(reasoning 계열)는 temperature 등 sampling 파라미터 커스텀 불가(기본값 고정).
        명시 전달 시 400(unsupported_value). gpt-4o 계열만 결정성 위해 temperature=0.1 전달.
        """
        if self._model.startswith("gpt-5"):
            return {}
        return {"temperature": 0.1}

    async def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        kwargs = self._sampling_kwargs()
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )
        content = resp.choices[0].message.content
        if not content or not content.strip():
            # fail-closed(2026-07-20): 빈 응답이 유효 답변으로 캐시·반환되는 것을 차단
            raise RuntimeError(f"LLM 빈 응답 (model={self._model}, finish_reason={resp.choices[0].finish_reason})")
        return content

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            stream=True,
            **self._sampling_kwargs(),
        )
        async for chunk in resp:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self._embed_model, input=texts)
        return [item.embedding for item in resp.data]
