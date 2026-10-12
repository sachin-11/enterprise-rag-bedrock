"""Idempotent DynamoDB conversation import with per-conversation verification."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from boto3.dynamodb.conditions import Key
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.core import Conversation, Message, Tenant, User


def _pages(table: Any, operation: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
    last_key = None
    while True:
        request = dict(kwargs)
        if last_key is not None:
            request["ExclusiveStartKey"] = last_key
        response = getattr(table, operation)(**request)
        yield from response.get("Items", [])
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_timestamp(value: str | datetime) -> str:
    parsed = _timestamp(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _message_id(conversation_id: str, sort_key: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"dynamodb-message:{conversation_id}:{sort_key}").hex


def _fingerprint(messages: list[dict[str, Any]]) -> str:
    normalized = [
        {
            "created_at": _canonical_timestamp(message["created_at"]),
            "role": message["role"],
            "content": message["content"],
            "sources": message.get("sources", []),
            "run_id": message.get("run_id"),
        }
        for message in messages
    ]
    payload = json.dumps(
        sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, default=str)),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _source_messages(table: Any, conversation_id: str) -> list[dict[str, Any]]:
    return list(
        _pages(
            table,
            "query",
            KeyConditionExpression=Key("conversation_id").eq(conversation_id),
            ScanIndexForward=True,
            ConsistentRead=True,
        )
    )


def _destination_messages(db: Session, tenant_id: str, conversation_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.message_id)
    ).all()
    return [
        {
            "created_at": row.created_at.isoformat(),
            "role": row.role,
            "content": row.content,
            "sources": row.sources,
            "run_id": row.run_id,
        }
        for row in rows
    ]


def _source_fingerprint(messages: list[dict[str, Any]]) -> str:
    return _fingerprint(sorted(messages, key=lambda row: row["sort_key"]))


def _destination_fingerprint(messages: list[dict[str, Any]]) -> str:
    return _fingerprint(messages)


def backfill_conversations(
    conversations_table: Any,
    messages_table: Any,
    sessions: sessionmaker[Session],
    *,
    verify_only: bool = False,
) -> dict[str, int]:
    """Copy all source rows; fail on ownership/content conflicts or incomplete copies.

    Re-running imports missing records and refreshes mutable conversation metadata.
    Verification never writes to PostgreSQL. The caller must stop live writes
    before the final pass because DynamoDB has no cross-table snapshot here.
    """
    result = {"conversations": 0, "messages": 0}
    source_ids: set[str] = set()
    for source in _pages(conversations_table, "scan", ConsistentRead=True):
        tenant_id = source["tenant_id"]
        user_id = source["user_id"]
        conversation_id = source["conversation_id"]
        if conversation_id in source_ids:
            raise ValueError(f"Duplicate source conversation {conversation_id}")
        source_ids.add(conversation_id)
        source_messages = _source_messages(messages_table, conversation_id)

        with sessions.begin() as db:
            row = db.get(Conversation, conversation_id)
            if row is not None and (row.tenant_id, row.user_id) != (tenant_id, user_id):
                raise ValueError(f"Ownership conflict for conversation {conversation_id}")
            if not verify_only:
                if db.get(Tenant, tenant_id) is None:
                    db.add(Tenant(tenant_id=tenant_id, display_name=tenant_id))
                    db.flush()
                user = db.get(User, user_id)
                if user is None:
                    email = f"{hashlib.sha256(user_id.encode()).hexdigest()}@migration.invalid"
                    db.add(User(user_id=user_id, tenant_id=tenant_id, email=email, role="member"))
                    db.flush()
                elif user.tenant_id != tenant_id:
                    raise ValueError(f"Ownership conflict for user {user_id}")

                if row is None:
                    row = Conversation(conversation_id=conversation_id, tenant_id=tenant_id, user_id=user_id)
                    db.add(row)
                row.title = source["title"]
                row.created_at = _timestamp(source["created_at"])
                row.updated_at = _timestamp(source["updated_at"])
                row.history_summary = str(source.get("history_summary", ""))
                row.summarized_through = int(source.get("summarized_through", 0))
                db.flush()

                for message in source_messages:
                    message_id = _message_id(conversation_id, message["sort_key"])
                    existing = db.get(Message, message_id)
                    if existing is None:
                        db.add(
                            Message(
                                message_id=message_id,
                                tenant_id=tenant_id,
                                conversation_id=conversation_id,
                                role=message["role"],
                                content=message["content"],
                                sources=message.get("sources", []),
                                run_id=message.get("run_id"),
                                created_at=_timestamp(message["created_at"]),
                            )
                        )
                    elif (existing.tenant_id, existing.conversation_id) != (tenant_id, conversation_id):
                        raise ValueError(f"Ownership conflict for message {message_id}")
                db.flush()

            if row is None:
                raise ValueError(f"Missing conversation {conversation_id}")
            if (
                row.title != source["title"]
                or row.history_summary != str(source.get("history_summary", ""))
                or row.summarized_through != int(source.get("summarized_through", 0))
                or _canonical_timestamp(row.created_at) != _canonical_timestamp(source["created_at"])
                or _canonical_timestamp(row.updated_at) != _canonical_timestamp(source["updated_at"])
            ):
                raise ValueError(f"Metadata mismatch for conversation {conversation_id}")
            destination_messages = _destination_messages(db, tenant_id, conversation_id)
            if len(destination_messages) != len(source_messages):
                raise ValueError(f"Message count mismatch for conversation {conversation_id}")
            if _destination_fingerprint(destination_messages) != _source_fingerprint(source_messages):
                raise ValueError(f"Message content mismatch for conversation {conversation_id}")

        result["conversations"] += 1
        result["messages"] += len(source_messages)
    if verify_only:
        with sessions() as db:
            destination_ids = set(db.scalars(select(Conversation.conversation_id)).all())
        if destination_ids != source_ids:
            raise ValueError(
                f"Conversation set mismatch: {len(source_ids)} source, {len(destination_ids)} destination"
            )
    return result
