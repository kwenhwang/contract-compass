"""서빙 RAG 코퍼스 신선도 기록·판독 (2026-07-24).

배경: /ready 신선도 게이트가 `chroma.sqlite3` mtime을 봤으나, 이 mtime은 재색인
없이 **컬렉션을 열기만 해도** 갱신된다(실측). 그래서 게이트가 사실상 영구 "fresh"라
'유지보수 정체 canary'로 무효였다(c8e4e43의 결함).

정정: 재색인 스크립트(예: index_laws.py)가 완료 시 명시적으로
`indexed_at`을 이 JSON에 기록하고, /ready는 그 값으로 신선도를 판정한다. chroma에
쓰지 않아 폐쇄망·읽기전용 점검에 안전하다.

역순 적용 안전장치: 판독부가 쓰기부보다 먼저 배포돼 파일이 아직 없어도 /ready가
경고를 쏟지 않도록, 파일 부재는 warning이 아니라 `corpus_indexed_at=None`(신호 없음)
으로 다룬다.
"""
import json
from datetime import datetime
from pathlib import Path

# backend/services/index_status.py → repo 루트/data/index_status.json
STATUS_PATH = Path(__file__).resolve().parents[2] / "data" / "index_status.json"


def record_index_status(collection: str, chunks: int, by: str, path: Path | None = None) -> None:
    """재색인 완료 컬렉션의 신선도 1건 기록 (기존 다른 컬렉션 항목은 보존·병합).

    같은 파일을 여러 인덱싱 스크립트가 갱신하므로 read-merge-write로 서로 덮어쓰지 않는다.
    """
    p = path or STATUS_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    cols = data.get("collections")
    if not isinstance(cols, dict):
        cols = {}
    cols[collection] = {
        "indexed_at": datetime.now().isoformat(timespec="seconds"),
        "chunks": int(chunks),
        "by": by,
    }
    data["collections"] = cols
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_index_status(path: Path | None = None) -> dict:
    """index_status.json 안전 판독 — 부재·손상 시 {} (예외 전파 없음)."""
    p = path or STATUS_PATH
    try:
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def evaluate_corpus_freshness(status: dict, now_ts: float, max_age_d: int = 90) -> tuple[dict, list[str]]:
    """서빙 코퍼스 신선도 판정 (순수 함수 — /ready에서 호출, 단위테스트 대상).

    반환: (info, warnings)
    - 기록 부재 → info["corpus_indexed_at"]=None, warnings=[] (역순 배포 안전장치)
    - 컬렉션별 indexed_at 경과일 > max_age_d → warning 1건씩, info["corpus"]에 경과일·청크수
    """
    info: dict = {}
    warnings: list[str] = []
    cols = (status or {}).get("collections")
    if not isinstance(cols, dict) or not cols:
        info["corpus_indexed_at"] = None
        return info, warnings
    per: dict = {}
    for name, rec in cols.items():
        if not isinstance(rec, dict):
            continue
        iso = rec.get("indexed_at")
        try:
            age_d = round((now_ts - datetime.fromisoformat(iso).timestamp()) / 86400, 1)
        except Exception:
            continue
        per[name] = {"age_d": age_d, "chunks": rec.get("chunks")}
        if age_d > max_age_d:
            warnings.append(f"RAG 코퍼스 '{name}' 재색인 {age_d:.0f}일 경과 (신선도 점검 필요)")
    if per:
        info["corpus"] = per
    else:
        info["corpus_indexed_at"] = None
    return info, warnings
