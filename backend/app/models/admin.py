from datetime import datetime

from pydantic import BaseModel, Field


class OrgStatsResponse(BaseModel):
    query_count: int
    error_count: int
    error_rate: float
    total_cost: float
    avg_latency_s: float
    p50_latency_s: float
    p95_latency_s: float
    feedback_count: int
    feedback_positive_rate: float
    cache_hit_count: int
    cache_hit_rate: float
    estimated_cost_saved: float


class ErrorRow(BaseModel):
    run_id: str
    error: str
    start_time: datetime
    langsmith_url: str


class ErrorsResponse(BaseModel):
    errors: list[ErrorRow]


class OrgMemberRow(BaseModel):
    sub: str
    email: str
    enabled: bool
    status: str
    is_self: bool
    is_admin: bool
    query_count: int
    total_cost: float
    avg_latency_s: float | None = None


class MembersResponse(BaseModel):
    members: list[OrgMemberRow]


class RetryResponse(BaseModel):
    succeeded: bool
    error: str | None = None
    answer_preview: str | None = None


class KnowledgeGapRow(BaseModel):
    query: str
    occurrence_count: int
    last_asked: datetime


class KnowledgeGapsResponse(BaseModel):
    gaps: list[KnowledgeGapRow]


class AuditEventRow(BaseModel):
    actor_email: str
    action: str
    target: str | None = None
    details: str | None = None
    created_at: datetime
    notified_count: int | None = None


class AuditLogResponse(BaseModel):
    events: list[AuditEventRow]


class WatchdogStatsResponse(BaseModel):
    total_investigations: int
    succeeded_count: int
    exhausted_count: int
    skipped_count: int
    success_rate: float
    emails_sent_count: int


class EvalRunSummary(BaseModel):
    run_id: str
    ran_at: datetime
    question_count: int
    avg_faithfulness: float
    avg_answer_relevancy: float
    avg_context_precision: float
    avg_context_recall: float


class EvalQuestionRow(BaseModel):
    question: str
    answer: str
    reference: str
    faithfulness: float
    answer_relevancy: float
    context_precision: float
    context_recall: float


class EvalResponse(BaseModel):
    # Oldest -> newest, one entry per run of scripts/run_eval.py, for a
    # quality-over-time trend. Empty until that script has been run at least
    # once — this data isn't produced by live traffic.
    history: list[EvalRunSummary]
    latest_rows: list[EvalQuestionRow]
