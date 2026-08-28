from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.chat import SourceCitation


class MessageRecord(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    sources: list[SourceCitation] = Field(default_factory=list)
    created_at: datetime
    # The LangSmith trace id for this answer — set on assistant messages only,
    # lets the frontend attach 👍/👎 feedback to the right run even after a
    # reload (see app/services/feedback_service.py).
    run_id: str | None = None


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageRecord]


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]
