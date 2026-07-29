"""계약업무 용어사전 — 정적 JSON 기반."""
import json
from functools import lru_cache
from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.config import BASE_DIR

router = APIRouter(prefix="/glossary", tags=["glossary"])
_GLOSSARY_PATH = BASE_DIR / "data" / "glossary.json"


class Term(BaseModel):
    term: str
    definition: str
    related: list[str] = []


@lru_cache(maxsize=1)
def _load_terms() -> list[Term]:
    with open(_GLOSSARY_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [Term(**t) for t in data]


@router.get("", response_model=list[Term])
def list_terms() -> list[Term]:
    return _load_terms()


@router.get("/search", response_model=list[Term])
def search_terms(q: str = Query(..., min_length=1, max_length=50)) -> list[Term]:
    q_lower = q.lower().strip()
    terms = _load_terms()
    return [t for t in terms if q_lower in t.term.lower() or q_lower in t.definition.lower()]
