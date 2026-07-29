import asyncio
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, BackgroundTasks
from backend.models.request import IngestRequest
from backend.api.deps import get_rule_engine, get_rag_service, get_usage_logger, require_admin
from backend.config import BASE_DIR, get_settings
from backend.services.usage_logger import iter_all_log_records


def _gemini_quota_stats() -> dict:
    """Gemini quota guard 상태 (RPM·RPD 사용량). guard 미초기화 시 빈 dict."""
    try:
        from backend.services.llm.gemini_provider import quota_stats
        return quota_stats()
    except Exception:
        return {}

_RESULTS_DIR = BASE_DIR / "tests" / "results"

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

_ingest_status: dict[str, dict] = {}


async def _run_ingest(task_id: str, file_path: str, contract_type: str):
    import sys
    sys.path.insert(0, str(BASE_DIR))
    _ingest_status[task_id] = {"status": "running", "progress": 0.0, "message": "파싱 시작"}
    try:
        from etl.parsers.docx_parser import parse_docx
        from etl.chunkers.semantic_chunker import chunk_document
        from etl.loaders.chroma_loader import get_client, init_collections, upsert_chunks
        import json

        settings = get_settings()
        fp = Path(file_path)

        _ingest_status[task_id]["message"] = "DOCX 파싱 중"
        raw = parse_docx(fp)
        raw["contract_type"] = contract_type  # 강제 지정

        _ingest_status[task_id]["progress"] = 0.4
        _ingest_status[task_id]["message"] = "청킹 중"
        chunks = chunk_document(raw)

        chunks_path = BASE_DIR / "etl" / "data" / "chunks" / f"chunks_{contract_type}.jsonl"
        chunks_path.parent.mkdir(parents=True, exist_ok=True)
        with open(chunks_path, "w", encoding="utf-8") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

        _ingest_status[task_id]["progress"] = 0.7
        _ingest_status[task_id]["message"] = "ChromaDB upsert 중"
        client = get_client()
        init_collections(client)
        upsert_chunks(client, chunks_path)

        _ingest_status[task_id] = {"status": "completed", "progress": 1.0, "message": f"{len(chunks)}개 청크 인덱싱 완료"}
    except Exception as e:
        _ingest_status[task_id] = {"status": "failed", "progress": 0.0, "message": str(e)}


@router.post("/ingest")
async def ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    import uuid
    task_id = str(uuid.uuid4())[:8]
    _ingest_status[task_id] = {"status": "pending", "progress": 0.0, "message": "대기 중"}
    background_tasks.add_task(_run_ingest, task_id, req.file_path, req.contract_type)
    return {"task_id": task_id, "message": "ETL 파이프라인 시작됨"}


@router.get("/ingest/status/{task_id}")
async def ingest_status(task_id: str):
    return _ingest_status.get(task_id, {"status": "not_found"})


@router.get("/stats")
async def get_stats(usage_logger=Depends(get_usage_logger)):
    """전체 로그 파일(일별 회전 + 레거시) 기반 집계 통계 + P99 응답시간 + LLM 실패율."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    records = list(iter_all_log_records())

    step1_records = [r for r in records if r.get("event") == "step1"]
    step2_records = [r for r in records if r.get("event") == "step2"]
    total = len(step1_records)

    def _dt(ts: str):
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    today_count = sum(1 for r in step1_records if _dt(r.get("ts", "")) >= today_start)
    week_count  = sum(1 for r in step1_records if _dt(r.get("ts", "")) >= week_start)

    by_contract_type = dict(Counter(r.get("contract_type", "") for r in step1_records))
    by_rule_id = dict(Counter(r.get("rule_id", "") for r in step1_records).most_common(10))
    by_service_type = dict(Counter(
        r.get("service_type", "") for r in step1_records if r.get("service_type")
    ))

    # P99 응답시간 (step1 + step2)
    durations_step1 = sorted(r["duration_ms"] for r in step1_records if "duration_ms" in r)
    durations_step2 = sorted(r["duration_ms"] for r in step2_records if "duration_ms" in r)

    def _p99(lst: list) -> int | None:
        if not lst:
            return None
        idx = max(0, int(len(lst) * 0.99) - 1)
        return lst[idx]

    def _avg(lst: list) -> int | None:
        return int(sum(lst) / len(lst)) if lst else None

    perf = {
        "step1_p99_ms": _p99(durations_step1),
        "step1_avg_ms": _avg(durations_step1),
        "step2_p99_ms": _p99(durations_step2),
        "step2_avg_ms": _avg(durations_step2),
    }

    recent = sorted(records, key=lambda r: r.get("ts", ""), reverse=True)[:10]

    # 피드백 통계
    feedback_log = BASE_DIR / "logs" / "feedback.jsonl"
    feedback_good = feedback_bad = 0
    recent_feedback: list[dict] = []
    if feedback_log.exists():
        fb_records: list[dict] = []
        with open(feedback_log, encoding="utf-8") as f:
            for line in f:
                try:
                    fb_records.append(json.loads(line))
                except Exception:
                    pass
        feedback_good = sum(1 for r in fb_records if r.get("rating") == 1)
        feedback_bad  = sum(1 for r in fb_records if r.get("rating") == -1)
        recent_feedback = sorted(fb_records, key=lambda r: r.get("ts", ""), reverse=True)[:5]

    return {
        "total": total,
        "today": today_count,
        "this_week": week_count,
        "by_contract_type": by_contract_type,
        "by_rule_id": by_rule_id,
        "by_service_type": by_service_type,
        "performance": perf,
        "llm_failure_rate": usage_logger.get_llm_failure_rate(),
        "gemini_quota": _gemini_quota_stats(),
        "recent": recent,
        "feedback": {
            "good": feedback_good,
            "bad": feedback_bad,
            "total": feedback_good + feedback_bad,
            "recent": recent_feedback,
        },
    }


@router.get("/test-results")
async def get_test_results():
    """가장 최근 테스트 결과 JSON 반환."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(_RESULTS_DIR.glob("*.json"), reverse=True)
    if not files:
        return {"message": "테스트 결과 없음. POST /admin/run-tests 로 실행하세요."}
    with open(files[0], encoding="utf-8") as f:
        return json.load(f)


_test_run_status: dict = {"status": "idle"}


def _run_tests_bg():
    global _test_run_status
    _test_run_status = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        result = subprocess.run(
            ["python3", "tests/run_all_tests.py"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=300,
        )
        _test_run_status = {
            "status": "completed",
            "returncode": result.returncode,
            "stdout": result.stdout[-3000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        _test_run_status = {"status": "failed", "error": str(e)}


@router.post("/run-tests")
async def run_tests(background_tasks: BackgroundTasks):
    """백그라운드로 자동화 테스트 실행."""
    if _test_run_status.get("status") == "running":
        return {"message": "이미 실행 중입니다.", "status": _test_run_status}
    background_tasks.add_task(_run_tests_bg)
    return {"message": "테스트 시작됨. GET /admin/test-results 로 결과를 확인하세요."}


@router.get("/test-run-status")
async def test_run_status():
    return _test_run_status


@router.post("/reload-rules")
async def reload_rules(
    rule_engine=Depends(get_rule_engine),
    rag=Depends(get_rag_service),
):
    from etl.loaders.chroma_loader import get_client, upsert_rules
    rule_engine.reload()
    client = get_client()
    rules_file = Path(get_settings().rules_path)
    upsert_rules(client, rules_file)
    return {"message": "규칙 핫-리로드 완료"}


@router.get("/rules")
async def get_rules():
    """전체 규칙 목록 반환 (요약 필드만)."""
    rules_file = Path(get_settings().rules_path)
    with open(rules_file, encoding="utf-8") as f:
        data = json.load(f)
    rules = data.get("rules", [])
    summary = [
        {
            "rule_id": r.get("rule_id"),
            "method": r.get("result", {}).get("method") or r.get("method"),
            "contract_type": r.get("contract_type"),
            "legal_basis": r.get("legal_basis", [])[:1],
            "source": r.get("source", {}),
            "last_updated": r.get("last_updated"),
        }
        for r in rules
    ]
    return {"total": len(summary), "rules": summary}
