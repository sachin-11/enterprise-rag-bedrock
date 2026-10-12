from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.core import Conversation, Message, Tenant, User
from app.models.chat import SourceCitation
from app.models.context import RequestContext
from app.models.conversation import ConversationDetail, ConversationSummary, MessageRecord


class PostgresConversationRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions

    @staticmethod
    def _id() -> str:
        return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _scope(context, conversation_id):
        return (
            Conversation.tenant_id == context.tenant_id,
            Conversation.user_id == context.user_id,
            Conversation.conversation_id == conversation_id,
        )

    def _ensure_identity(self, db: Session, context: RequestContext) -> None:
        if db.get(Tenant, context.tenant_id) is None:
            db.add(Tenant(tenant_id=context.tenant_id, display_name=context.tenant_id))
            db.flush()
        if db.get(User, context.user_id) is None:
            db.add(User(user_id=context.user_id, tenant_id=context.tenant_id, email=str(context.email), role=context.role.value))
            db.flush()

    def create(self, context, title):
        now = datetime.now(timezone.utc)
        row = Conversation(conversation_id=self._id(), tenant_id=context.tenant_id, user_id=context.user_id, title=title, created_at=now, updated_at=now)
        with self._sessions.begin() as db:
            self._ensure_identity(db, context)
            db.add(row)
        return ConversationSummary(conversation_id=row.conversation_id, title=row.title, created_at=now, updated_at=now)

    def list(self, context):
        with self._sessions() as db:
            rows = db.scalars(select(Conversation).where(Conversation.tenant_id == context.tenant_id, Conversation.user_id == context.user_id).order_by(Conversation.updated_at.desc())).all()
            return [ConversationSummary(conversation_id=r.conversation_id, title=r.title, created_at=r.created_at, updated_at=r.updated_at) for r in rows]

    def get(self, context, conversation_id):
        with self._sessions() as db:
            row = db.scalar(select(Conversation).where(*self._scope(context, conversation_id)))
            if row is None:
                return None
            messages = db.scalars(select(Message).where(Message.tenant_id == context.tenant_id, Message.conversation_id == conversation_id).order_by(Message.created_at)).all()
            return ConversationDetail(conversation_id=row.conversation_id, title=row.title, created_at=row.created_at, updated_at=row.updated_at, messages=[MessageRecord(role=m.role, content=m.content, sources=[SourceCitation(**s) for s in m.sources], created_at=m.created_at, run_id=m.run_id) for m in messages])

    def append_message(self, context, conversation_id, role, content, sources, run_id=None):
        with self._sessions.begin() as db:
            if db.scalar(select(Conversation.conversation_id).where(*self._scope(context, conversation_id))) is None:
                raise LookupError("Conversation not found.")
            db.add(Message(message_id=self._id(), tenant_id=context.tenant_id, conversation_id=conversation_id, role=role, content=content, sources=[s.model_dump() for s in sources], run_id=run_id))

    def get_summary(self, context, conversation_id):
        with self._sessions() as db:
            row = db.execute(select(Conversation.history_summary, Conversation.summarized_through).where(*self._scope(context, conversation_id))).one_or_none()
            return (row[0], row[1]) if row else ("", 0)

    def update_summary(self, context, conversation_id, summary, summarized_through):
        with self._sessions.begin() as db:
            row = db.scalar(select(Conversation).where(*self._scope(context, conversation_id)))
            if row: row.history_summary, row.summarized_through = summary, summarized_through

    def touch(self, context, conversation_id):
        with self._sessions.begin() as db:
            row = db.scalar(select(Conversation).where(*self._scope(context, conversation_id)))
            if row: row.updated_at = datetime.now(timezone.utc)

    def delete(self, context, conversation_id):
        with self._sessions.begin() as db:
            row = db.scalar(select(Conversation).where(*self._scope(context, conversation_id)))
            if row is None: return False
            db.delete(row)
            return True
