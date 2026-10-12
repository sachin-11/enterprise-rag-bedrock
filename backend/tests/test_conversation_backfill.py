import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.conversation_backfill import backfill_conversations
from app.db.models.core import Conversation, Message


class ConversationTable:
    def scan(self, **kwargs):
        first = {
            "user_id": "user-a",
            "tenant_id": "tenant-a",
            "conversation_id": "conversation-a",
            "title": "First",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "history_summary": "Earlier chat",
            "summarized_through": 1,
        }
        second = {
            "user_id": "user-b",
            "tenant_id": "tenant-b",
            "conversation_id": "conversation-b",
            "title": "Second",
            "created_at": "2026-01-03T00:00:00+00:00",
            "updated_at": "2026-01-03T00:00:00+00:00",
        }
        if "ExclusiveStartKey" not in kwargs:
            return {"Items": [first], "LastEvaluatedKey": {"conversation_id": "conversation-a"}}
        return {"Items": [second]}


class MessageTable:
    def query(self, **kwargs):
        if kwargs["KeyConditionExpression"].get_expression()["values"][1] == "conversation-b":
            return {"Items": []}
        first = {
            "sort_key": "2026-01-01T00:01:00.000000#a",
            "role": "user",
            "content": "Hello",
            "sources": [],
            "created_at": "2026-01-01T00:01:00+00:00",
        }
        second = {
            "sort_key": "2026-01-01T00:02:00.000000#b",
            "role": "assistant",
            "content": "Policy",
            "sources": [{"chunk_id": "1", "doc_name": "policy.pdf", "excerpt": "Policy"}],
            "created_at": "2026-01-01T00:02:00+00:00",
            "run_id": "run-1",
        }
        if "ExclusiveStartKey" not in kwargs:
            return {"Items": [first], "LastEvaluatedKey": {"sort_key": first["sort_key"]}}
        return {"Items": [second]}


def _sessions():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_backfill_is_paginated_repeatable_and_verifiable():
    sessions = _sessions()
    conversations, messages = ConversationTable(), MessageTable()
    expected = {"conversations": 2, "messages": 2}

    assert backfill_conversations(conversations, messages, sessions) == expected
    assert backfill_conversations(conversations, messages, sessions) == expected
    assert backfill_conversations(conversations, messages, sessions, verify_only=True) == expected

    with sessions() as db:
        assert len(db.scalars(select(Conversation)).all()) == 2
        assert len(db.scalars(select(Message)).all()) == 2
        assert db.get(Conversation, "conversation-a").history_summary == "Earlier chat"


def test_verify_detects_missing_message_without_writing():
    sessions = _sessions()
    conversations, messages = ConversationTable(), MessageTable()
    backfill_conversations(conversations, messages, sessions)
    with sessions.begin() as db:
        db.delete(db.scalars(select(Message)).first())

    with pytest.raises(ValueError, match="Message count mismatch"):
        backfill_conversations(conversations, messages, sessions, verify_only=True)


def test_verify_detects_destination_conversation_absent_from_source():
    sessions = _sessions()
    conversations, messages = ConversationTable(), MessageTable()
    backfill_conversations(conversations, messages, sessions)
    with sessions.begin() as db:
        db.add(
            Conversation(
                conversation_id="extra",
                tenant_id="tenant-a",
                user_id="user-a",
                title="Extra",
            )
        )

    with pytest.raises(ValueError, match="Conversation set mismatch"):
        backfill_conversations(conversations, messages, sessions, verify_only=True)
