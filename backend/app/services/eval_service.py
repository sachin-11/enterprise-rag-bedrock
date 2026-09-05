"""Reads offline RAG-quality eval runs produced by scripts/run_eval.py.

Those runs are a developer-triggered ragas evaluation (faithfulness, answer
relevancy, context precision/recall) against a fixed test set
(eval/test_qa.json) under a synthetic "eval-tenant" — not live production
traffic for any real org. So, unlike the rest of admin_service.py, this data
isn't tenant-scoped: every admin sees the same eval history, the same way
every developer sees the same CI results regardless of which org they
administer. This module only reads the JSON files scripts/run_eval.py
already writes — it doesn't run evaluations itself (ragas' judge-LLM calls
are too slow/costly to trigger from an admin page load).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.models.admin import EvalQuestionRow, EvalRunSummary

EVAL_RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "eval"
_RESULT_FILENAME_RE = re.compile(r"^results_(\d{8}T\d{6}Z)\.json$")
_METRIC_FIELDS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def _parse_run_timestamp(filename: str) -> datetime | None:
    match = _RESULT_FILENAME_RE.match(filename)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _list_result_files() -> list[tuple[datetime, Path]]:
    """(ran_at, path) for every results_*.json file, oldest first. Anything
    not matching the timestamp naming scripts/run_eval.py uses is ignored
    rather than raising — this reads a directory a human/script can add
    stray files to."""
    if not EVAL_RESULTS_DIR.is_dir():
        return []
    dated = [
        (ran_at, path)
        for path in EVAL_RESULTS_DIR.glob("results_*.json")
        if (ran_at := _parse_run_timestamp(path.name)) is not None
    ]
    dated.sort(key=lambda pair: pair[0])
    return dated


def _load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _average(rows: list[dict], field: str) -> float:
    values = [row.get(field) or 0.0 for row in rows]
    return sum(values) / len(values) if values else 0.0


def _summarize(ran_at: datetime, path: Path, rows: list[dict]) -> EvalRunSummary:
    return EvalRunSummary(
        run_id=path.stem.removeprefix("results_"),
        ran_at=ran_at,
        question_count=len(rows),
        avg_faithfulness=_average(rows, "faithfulness"),
        avg_answer_relevancy=_average(rows, "answer_relevancy"),
        avg_context_precision=_average(rows, "context_precision"),
        avg_context_recall=_average(rows, "context_recall"),
    )


def get_eval_history() -> list[EvalRunSummary]:
    """One summary per eval run, oldest first — for a quality-over-time trend."""
    return [_summarize(ran_at, path, _load_rows(path)) for ran_at, path in _list_result_files()]


def get_latest_eval_rows() -> list[EvalQuestionRow]:
    """Per-question detail from the most recent eval run, or [] if none exist yet."""
    files = _list_result_files()
    if not files:
        return []
    _, latest_path = files[-1]
    return [
        EvalQuestionRow(
            question=row.get("user_input", ""),
            answer=row.get("response", ""),
            reference=row.get("reference", ""),
            faithfulness=row.get("faithfulness") or 0.0,
            answer_relevancy=row.get("answer_relevancy") or 0.0,
            context_precision=row.get("context_precision") or 0.0,
            context_recall=row.get("context_recall") or 0.0,
        )
        for row in _load_rows(latest_path)
    ]
