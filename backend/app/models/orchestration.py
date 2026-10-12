"""Channel-neutral contracts for the chat orchestrator."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.context import RequestContext


class RouteType(StrEnum):
    DIRECT = "direct"
    RAG = "rag"
    TOOL_READ = "tool_read"
    TOOL_WRITE = "tool_write"
    ESCALATE = "escalate"


class OrchestratorInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    context: RequestContext
    message: str = Field(min_length=1)
    conversation_id: str | None = None


class OrchestratorOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: RouteType
    response_text: str = ""
    conversation_id: str | None = None
    trace_id: str | None = None
