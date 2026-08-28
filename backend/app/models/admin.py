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
    query_count: int
    total_cost: float
    avg_latency_s: float | None = None


class MembersResponse(BaseModel):
    members: list[OrgMemberRow]


class RetryResponse(BaseModel):
    succeeded: bool
    error: str | None = None
    answer_preview: str | None = None
