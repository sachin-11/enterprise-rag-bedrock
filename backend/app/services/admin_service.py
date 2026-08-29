"""Org-scoped diagnostics for the per-organization admin dashboard.

Reads come from LangSmith (cost/latency/error data — see app/api/chat.py's
metadata tagging on every chat_query_pipeline run), never from a separate
store of our own; this module just queries and aggregates. Org-membership
actions (list/suspend/unsuspend) live in auth_service.py since they're
Cognito operations, not LangSmith ones — this module doesn't duplicate them.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from itertools import islice

from langsmith import Client

from app.core.config import settings
from app.models.admin import ErrorRow, KnowledgeGapRow, OrgStatsResponse
from app.services.feedback_service import FEEDBACK_KEY

PIPELINE_RUN_NAME = "chat_query_pipeline"
LANGSMITH_APP_BASE_URL = "https://smith.langchain.com"
# Safety cap against an org with unbounded query volume turning a stats
# request into an unbounded fetch — good enough for a v1 admin dashboard.
# Applied client-side via itertools.islice, NOT as list_runs(limit=...): the
# SDK forwards `limit` straight through as the /runs/query page size with no
# internal chunking, and the API rejects anything over LANGSMITH_PAGE_SIZE.
MAX_RUNS_PER_QUERY = 5000
LANGSMITH_PAGE_SIZE = 100


class AdminError(Exception):
    """Raised for expected admin-service failures (run not found, wrong org, etc)."""


@dataclass
class UserBreakdownStats:
    query_count: int = 0
    total_cost: float = 0.0
    latencies_s: list[float] = field(default_factory=list)

    @property
    def avg_latency_s(self) -> float | None:
        return statistics.mean(self.latencies_s) if self.latencies_s else None


@lru_cache
def _langsmith_client() -> Client:
    # Cached for the same reason the ChatOpenAI clients are elsewhere in this
    # codebase — reuse the HTTP connection pool instead of a fresh one per call.
    return Client(api_key=settings.langsmith_api_key)


def _tenant_filter(tenant_id: str) -> str:
    # tenant_id is always a Cognito group name already constrained by
    # ORG_SLUG_PATTERN at signup (app/models/user.py) — never raw,
    # unvalidated user input reaching this string.
    return (
        f'and(eq(name, "{PIPELINE_RUN_NAME}"), '
        f'eq(metadata_key, "tenant_id"), eq(metadata_value, "{tenant_id}"))'
    )


def _latency_stats(latencies_s: list[float]) -> tuple[float, float, float]:
    """Returns (avg, p50, p95). All zero if there's no latency data yet."""
    if not latencies_s:
        return 0.0, 0.0, 0.0
    if len(latencies_s) == 1:
        only = latencies_s[0]
        return only, only, only

    avg = statistics.mean(latencies_s)
    p50 = statistics.median(latencies_s)
    p95 = statistics.quantiles(latencies_s, n=100)[94]
    return avg, p50, p95


def _feedback_stats(client: Client, run_ids: list[str]) -> tuple[int, float]:
    """Returns (feedback_count, positive_rate) for the given run_ids' "user_score" feedback.

    A run can be rated more than once (see feedback_service.py's known v1
    gap — a reload doesn't know a run was already rated) — every submission
    counts individually here, same as LangSmith's own feedback tab does.
    """
    if not run_ids:
        return 0, 0.0

    entries = list(
        islice(
            client.list_feedback(run_ids=run_ids, feedback_key=[FEEDBACK_KEY]),
            MAX_RUNS_PER_QUERY,
        )
    )
    if not entries:
        return 0, 0.0

    positive = sum(1 for entry in entries if entry.score)
    return len(entries), positive / len(entries)


def get_org_stats(tenant_id: str, days: int = 7) -> OrgStatsResponse:
    client = _langsmith_client()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = list(
        islice(
            client.list_runs(
                project_name=settings.langsmith_project,
                filter=_tenant_filter(tenant_id),
                start_time=since,
                select=["id", "start_time", "end_time", "total_cost", "status"],
            ),
            MAX_RUNS_PER_QUERY,
        )
    )

    query_count = len(rows)
    error_count = sum(1 for row in rows if row.status == "error")
    total_cost = sum(float(row.total_cost or 0) for row in rows)
    latencies_s = [
        (row.end_time - row.start_time).total_seconds()
        for row in rows
        if row.start_time is not None and row.end_time is not None
    ]
    avg_latency_s, p50_latency_s, p95_latency_s = _latency_stats(latencies_s)
    feedback_count, feedback_positive_rate = _feedback_stats(client, [str(row.id) for row in rows])

    return OrgStatsResponse(
        query_count=query_count,
        error_count=error_count,
        error_rate=(error_count / query_count) if query_count else 0.0,
        total_cost=total_cost,
        avg_latency_s=avg_latency_s,
        p50_latency_s=p50_latency_s,
        p95_latency_s=p95_latency_s,
        feedback_count=feedback_count,
        feedback_positive_rate=feedback_positive_rate,
    )


def get_recent_errors(tenant_id: str, limit: int = 20) -> list[ErrorRow]:
    client = _langsmith_client()

    rows = list(
        client.list_runs(
            project_name=settings.langsmith_project,
            filter=_tenant_filter(tenant_id),
            error=True,
            select=["id", "error", "start_time", "app_path"],
            limit=min(limit, LANGSMITH_PAGE_SIZE),
        )
    )
    rows.sort(key=lambda row: row.start_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return [
        ErrorRow(
            run_id=str(row.id),
            error=row.error or "Unknown error",
            start_time=row.start_time,
            langsmith_url=f"{LANGSMITH_APP_BASE_URL}{row.app_path}" if row.app_path else LANGSMITH_APP_BASE_URL,
        )
        for row in rows
    ]


def get_user_breakdown(tenant_id: str, days: int = 7) -> dict[str, UserBreakdownStats]:
    """Per-user_id cost/latency breakdown within one org.

    LangSmith's filter grammar only supports one metadata_key/metadata_value
    pair per query, so this can't also filter by user_id server-side — it
    fetches every tenant-scoped run once (same base filter as
    get_org_stats) and groups by the user_id metadata client-side instead.
    """
    client = _langsmith_client()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = list(
        islice(
            client.list_runs(
                project_name=settings.langsmith_project,
                filter=_tenant_filter(tenant_id),
                start_time=since,
                select=["start_time", "end_time", "total_cost", "extra"],
            ),
            MAX_RUNS_PER_QUERY,
        )
    )

    breakdown: dict[str, UserBreakdownStats] = {}
    for row in rows:
        metadata = (row.extra or {}).get("metadata", {}) or {}
        user_id = metadata.get("user_id") or "unknown"
        stats = breakdown.setdefault(user_id, UserBreakdownStats())
        stats.query_count += 1
        stats.total_cost += float(row.total_cost or 0)
        if row.start_time is not None and row.end_time is not None:
            stats.latencies_s.append((row.end_time - row.start_time).total_seconds())

    return breakdown


def get_knowledge_gaps(tenant_id: str, days: int = 30, limit: int = 20) -> list[KnowledgeGapRow]:
    """Questions that repeatedly got zero retrieved sources back — the
    strongest signal an admin has for "what's missing from our docs."

    Uses the source_count metadata tagged in chat.py's _run_rag_pipeline
    right after reranking (0 = nothing relevant was found), not a text
    match against the generated answer — that stays correct even if the
    answer's exact wording changes. Same client-side-grouping constraint as
    get_user_breakdown: the filter grammar only supports one
    metadata_key/metadata_value pair per query, so this can't filter by
    source_count server-side alongside the tenant filter.

    Grouping is by exact (lowercased, stripped) query text — a real-world
    limitation: "What's our refund policy?" and "refund policy?" count as
    two different gaps rather than one. Good enough to point an admin at
    the right area; not a semantic-similarity clustering pass.
    """
    client = _langsmith_client()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = list(
        islice(
            client.list_runs(
                project_name=settings.langsmith_project,
                filter=_tenant_filter(tenant_id),
                start_time=since,
                select=["start_time", "extra", "inputs"],
            ),
            MAX_RUNS_PER_QUERY,
        )
    )

    gaps: dict[str, dict] = {}
    for row in rows:
        metadata = (row.extra or {}).get("metadata", {}) or {}
        if metadata.get("source_count") != 0:
            continue  # not tagged (e.g. guardrail-blocked) or found something — not a gap

        query_text = (row.inputs or {}).get("query", "").strip()
        if not query_text:
            continue

        key = query_text.lower()
        entry = gaps.setdefault(key, {"query": query_text, "count": 0, "last_asked": row.start_time})
        entry["count"] += 1
        if row.start_time is not None and (entry["last_asked"] is None or row.start_time > entry["last_asked"]):
            entry["last_asked"] = row.start_time

    ranked = sorted(gaps.values(), key=lambda entry: (entry["count"], entry["last_asked"]), reverse=True)
    return [
        KnowledgeGapRow(query=entry["query"], occurrence_count=entry["count"], last_asked=entry["last_asked"])
        for entry in ranked[:limit]
    ]


def get_retry_inputs(tenant_id: str, run_id: str) -> dict:
    """Fetches a failed run's original (query, chat_history) so it can be
    re-run as a smoke test — see app/api/admin.py's retry endpoint.

    Re-verifying the run's own tenant_id metadata against the requesting
    admin's tenant_id (not just trusting the run_id) is the actual
    authorization check here: it stops an admin from retrying a run_id
    belonging to a different organization even if they guess/enumerate one.
    """
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        # LangSmith's API 422s on a non-UUID id filter rather than returning
        # an empty result — treat it the same as "not found" rather than
        # letting a malformed run_id from a client surface as a 500.
        raise AdminError("Run not found.") from exc

    client = _langsmith_client()
    rows = list(client.list_runs(run_ids=[run_id], select=["inputs", "extra"]))
    if not rows:
        raise AdminError("Run not found.")

    row = rows[0]
    metadata = (row.extra or {}).get("metadata", {}) or {}
    if metadata.get("tenant_id") != tenant_id:
        raise AdminError("Run not found.")  # deliberately the same message as "not found" — no cross-org existence leak

    inputs = row.inputs or {}
    return {"query": inputs.get("query", ""), "chat_history": inputs.get("chat_history") or []}
