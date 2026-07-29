"""FastAPI 의존성 주입 — 싱글톤 서비스 인스턴스."""
from functools import lru_cache
from fastapi import Header, HTTPException
from backend.config import get_settings
from backend.services.rule_engine import RuleEngine
from backend.services.rag_service import RAGService
from backend.services.session_store import SessionStore
from backend.services.llm import create_provider, LLMProvider
from backend.services.usage_logger import UsageLogger


@lru_cache
def get_rule_engine() -> RuleEngine:
    return RuleEngine(get_settings().rules_path)


@lru_cache
def get_rag_service() -> RAGService:
    return RAGService(get_settings().chroma_path)


@lru_cache
def get_session_store() -> SessionStore:
    s = get_settings()
    return SessionStore(ttl_seconds=s.session_ttl_seconds, db_path=s.session_db_path or None)


@lru_cache
def get_llm() -> LLMProvider:
    s = get_settings()
    # 키 선택은 라우팅(create_provider: "gemini"만 Gemini, 그 외 전부 OpenAI)과 대칭이어야 함.
    # 과거 == "openai"는 provider가 "" 등 비-gemini일 때 Gemini 키를 잘못 골랐음 → != "gemini"로 통일.
    api_key = s.gemini_api_key if s.llm_provider == "gemini" else s.openai_api_key
    return create_provider(
        s.llm_provider, api_key, gemini_model=s.gemini_model,
        # 내부망 vLLM 등 OpenAI 호환 서버 — 미설정 시 공식 OpenAI 그대로
        openai_base_url=s.openai_base_url, openai_model=s.openai_model,
        openai_timeout=s.openai_timeout, openai_max_retries=s.openai_max_retries,
    )


@lru_cache
def get_usage_logger() -> UsageLogger:
    return UsageLogger()


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Admin 엔드포인트용 인증 의존성 — fail-closed.

    .env의 ADMIN_TOKEN 값과 X-Admin-Token 헤더 비교.
    ADMIN_TOKEN이 비어있으면(미설정) 보호 엔드포인트를 **거부**한다(503).
    과거엔 미설정 시 통과(개발 모드)했으나, 운영 배포에서 토큰 누락 시
    인증이 통째로 무력화되는 fail-open 함정이 있어 fail-closed로 전환(B1 하드닝).
    """
    expected = get_settings().admin_token
    if not expected:
        # 토큰 미설정 = 잘못된 배포. 무인증 통과 대신 명시적 거부(fail-closed).
        raise HTTPException(
            status_code=503,
            detail="Admin 인증이 구성되지 않았습니다(ADMIN_TOKEN 미설정). 관리자에게 문의하세요.",
        )
    if x_admin_token != expected:
        raise HTTPException(status_code=401, detail="유효하지 않은 Admin 토큰입니다.")
