"""DynamoDB-backed chat history: conversations + their messages.

Two tables, no GSI:
- conversations: PK user_id, SK conversation_id (a millis-timestamp-prefixed
  ID, so it sorts chronologically as-is — ScanIndexForward=False gives
  most-recent-first for list_conversations without needing a GSI).
- messages: PK conversation_id, SK sort_key (an explicit
  "YYYY-MM-DDTHH:MM:SS.ffffff#<8-hex>" string — not bare .isoformat(), which
  drops the microseconds field entirely when it's 0 and would silently break
  sort order for some messages).

SECURITY: conversations is the only table keyed by user_id. Every read of a
conversation's messages must go through get_conversation() first to confirm
the caller owns conversation_id — messages has no user_id/tenant_id in its
own key, so nothing else stops a caller from reading another user's messages
by guessing/sniffing a conversation_id.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from boto3.dynamodb.conditions import Key

from app.core.aws_clients import get_dynamodb_resource
from app.core.config import settings
from app.models.chat import SourceCitation
from app.models.conversation import ConversationDetail, ConversationSummary, MessageRecord


def _new_conversation_id() -> str:
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"


def _new_message_sort_key() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
    return f"{timestamp}#{uuid.uuid4().hex[:8]}"


def _conversations_table():
    return get_dynamodb_resource().Table(settings.dynamodb_conversations_table)


def _messages_table():
    return get_dynamodb_resource().Table(settings.dynamodb_messages_table)


def create_conversation(user_id: str, tenant_id: str, title: str) -> ConversationSummary:
    now = datetime.now(timezone.utc)
    conversation_id = _new_conversation_id()
    _conversations_table().put_item(
        Item={
            "user_id": user_id,
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "title": title,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    )
    return ConversationSummary(conversation_id=conversation_id, title=title, created_at=now, updated_at=now)


def list_conversations(user_id: str) -> list[ConversationSummary]:
    # ConsistentRead=True: this list is fetched right after create/delete
    # calls (new chat, sidebar delete) — an eventually-consistent read could
    # briefly show a just-deleted conversation or omit a just-created one.
    # Low-volume, single-partition reads, so the consistency guarantee is
    # cheap here.
    response = _conversations_table().query(
        KeyConditionExpression=Key("user_id").eq(user_id),
        ScanIndexForward=False,
        ConsistentRead=True,
    )
    return [
        ConversationSummary(
            conversation_id=item["conversation_id"],
            title=item["title"],
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        )
        for item in response.get("Items", [])
    ]


def get_conversation(user_id: str, conversation_id: str) -> Optional[ConversationDetail]:
    """Returns None if the conversation doesn't exist OR isn't owned by user_id."""
    # ConsistentRead=True: this is fetched right after create_conversation
    # (navigating to a brand-new conversation's URL) and right after
    # append_message (reloading a conversation just messaged) — same
    # read-after-write staleness risk as list_conversations above.
    response = _conversations_table().get_item(
        Key={"user_id": user_id, "conversation_id": conversation_id}, ConsistentRead=True
    )
    item = response.get("Item")
    if item is None:
        return None

    messages_response = _messages_table().query(
        KeyConditionExpression=Key("conversation_id").eq(conversation_id),
        ScanIndexForward=True,
        ConsistentRead=True,
    )
    messages = [
        MessageRecord(
            role=message["role"],
            content=message["content"],
            sources=[SourceCitation(**source) for source in message.get("sources", [])],
            created_at=message["created_at"],
            run_id=message.get("run_id"),
        )
        for message in messages_response.get("Items", [])
    ]

    return ConversationDetail(
        conversation_id=item["conversation_id"],
        title=item["title"],
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        messages=messages,
    )


def append_message(
    conversation_id: str, role: str, content: str, sources: list[SourceCitation], run_id: Optional[str] = None
) -> None:
    item = {
        "conversation_id": conversation_id,
        "sort_key": _new_message_sort_key(),
        "role": role,
        "content": content,
        "sources": [source.model_dump() for source in sources],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if run_id is not None:
        item["run_id"] = run_id
    _messages_table().put_item(Item=item)


def get_history_summary(user_id: str, conversation_id: str) -> tuple[str, int]:
    """Returns (history_summary, summarized_through) for conversation_id, or
    ("", 0) if the conversation has never been summarized yet.

    summarized_through is a count of this conversation's messages (from the
    start) already folded into history_summary — see
    query_service.summarize_history and its call site in app/api/chat.py.
    """
    response = _conversations_table().get_item(
        Key={"user_id": user_id, "conversation_id": conversation_id},
        ProjectionExpression="history_summary, summarized_through",
        ConsistentRead=True,
    )
    item = response.get("Item") or {}
    return str(item.get("history_summary", "")), int(item.get("summarized_through", 0))


def update_history_summary(user_id: str, conversation_id: str, history_summary: str, summarized_through: int) -> None:
    _conversations_table().update_item(
        Key={"user_id": user_id, "conversation_id": conversation_id},
        UpdateExpression="SET history_summary = :s, summarized_through = :t",
        ExpressionAttributeValues={":s": history_summary, ":t": summarized_through},
    )


def touch_conversation(user_id: str, conversation_id: str) -> None:
    """Bumps updated_at after a new message, so list_conversations sorts it to the top."""
    _conversations_table().update_item(
        Key={"user_id": user_id, "conversation_id": conversation_id},
        UpdateExpression="SET updated_at = :now",
        ExpressionAttributeValues={":now": datetime.now(timezone.utc).isoformat()},
    )


def delete_conversation(user_id: str, conversation_id: str) -> bool:
    """Deletes the conversation and all its messages. Returns False if not found/not owned."""
    existing = _conversations_table().get_item(
        Key={"user_id": user_id, "conversation_id": conversation_id}, ConsistentRead=True
    )
    if "Item" not in existing:
        return False

    messages_response = _messages_table().query(
        KeyConditionExpression=Key("conversation_id").eq(conversation_id),
        ProjectionExpression="sort_key",
        ConsistentRead=True,
    )
    items = messages_response.get("Items", [])
    if items:
        # DynamoDB has no cascade delete — messages must be cleared explicitly,
        # or they'd be orphaned (unreachable, since only the deleted
        # conversation's owner could ever have queried for them anyway, but
        # they'd still sit in the table forever).
        with _messages_table().batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"conversation_id": conversation_id, "sort_key": item["sort_key"]})

    _conversations_table().delete_item(Key={"user_id": user_id, "conversation_id": conversation_id})
    return True
