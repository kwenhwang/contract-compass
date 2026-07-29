"""다국어 임베딩 함수 — ChromaDB 커스텀 임베딩 (한국어 지원).

2026-07-10: 내부망 GPU 서빙 연동 —
- EMBEDDING_MODEL: 로컬 sentence-transformers 모델명 교체 (예: BAAI/bge-m3).
  ⚠️ 모델 교체 = 벡터 차원이 달라져 **전 컬렉션 재임베딩 필수** (tools/index_laws.py 등 재실행).
- EMBEDDING_ENDPOINT: TEI/Infinity 등 OpenAI 호환 임베딩 서버(예: http://gpu:8080/v1).
  지정 시 로컬 모델 로드 없이 HTTP 호출 — GPU 서버로 오프로드.
둘 다 미설정이면 현행(MiniLM 로컬) 그대로.
"""
import os
import warnings

# HF Hub 네트워크 체크 비활성화 — 모델은 로컬 캐시에 있으므로 cold start 시
# HF Hub로 가는 메타데이터 요청(수십 초 지연)을 건너뜀. (import 전에 설정해야 효과)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from chromadb import EmbeddingFunction, Documents, Embeddings

warnings.filterwarnings("ignore")

# 한국어 포함 다국어 임베딩 모델 (로컬, 무료, 384차원)
_DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
_model = None


def _cfg(env_key: str, settings_attr: str) -> str:
    """env 우선, pydantic settings(.env) 폴백 — reranker._get_key와 동일 규약."""
    if v := os.environ.get(env_key):
        return v
    try:
        from backend.config import get_settings
        return getattr(get_settings(), settings_attr, "") or ""
    except Exception:
        return ""


def _model_name() -> str:
    return _cfg("EMBEDDING_MODEL", "embedding_model") or _DEFAULT_MODEL


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        name = _model_name()
        try:
            # 로컬 캐시만 사용 — 네트워크 체크 생략
            _model = SentenceTransformer(name, local_files_only=True)
        except Exception:
            # 캐시 없으면 1회 다운로드 허용 (최초 배포 시)
            _model = SentenceTransformer(name)
    return _model


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec] if norm > 0 else vec


class GeminiEmbeddingFunction(EmbeddingFunction):
    """다국어 sentence-transformers 기반 ChromaDB 임베딩 함수 (한국어 지원).

    클래스명은 하위호환을 위해 유지.
    """

    def __init__(self, api_key: str | None = None):
        # api_key 파라미터는 하위호환을 위해 유지 (미사용)
        self._endpoint = _cfg("EMBEDDING_ENDPOINT", "embedding_endpoint").rstrip("/")
        self._model = None if self._endpoint else _get_model()

    def _embed_remote(self, texts: list[str]) -> Embeddings:
        import httpx
        resp = httpx.post(
            f"{self._endpoint}/embeddings",
            json={"model": _model_name(), "input": texts},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))
        # 서버별 정규화 여부가 달라 코사인 일관성 위해 항상 단위 벡터화
        return [_normalize(d["embedding"]) for d in data]

    def __call__(self, input: Documents) -> Embeddings:
        texts = list(input)
        if self._endpoint:
            return self._embed_remote(texts)
        # normalize_embeddings=True → 코사인 유사도에 최적화된 단위 벡터 반환
        vecs = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return [v.tolist() for v in vecs]
