"""Durable audit trail of admin actions — who did what, when, to what.

Separate from admin_service.py's LangSmith-backed diagnostics: those cover
events that happen *inside* the traced chat pipeline (a query, its cost,
its latency). Admin actions (upload/delete/share a document, suspend/
promote a teammate, generate an invite) happen on plain CRUD routes with no
trace to tag metadata onto — this is a small, append-only DynamoDB table
instead. PK tenant_id, SK event_id (a millis-timestamp-prefixed sortable
id, same shape as conversation_store.py's conversation ids), no GSI needed.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import BotoCoreError, ClientError

from app.core.aws_clients import get_dynamodb_resource
from app.core.config import settings
from app.models.admin import AuditEventRow

logger = logging.getLogger(__name__)

ACTION_DOCUMENT_UPLOADED = "document_uploaded"
ACTION_DOCUMENT_DELETED = "document_deleted"
ACTION_DOCUMENT_SHARED = "document_shared"
ACTION_USER_SUSPENDED = "user_suspended"
ACTION_USER_UNSUSPENDED = "user_unsuspended"
ACTION_USER_PROMOTED = "user_promoted"
ACTION_USER_DEMOTED = "user_demoted"
ACTION_INVITE_GENERATED = "invite_generated"
ACTION_COPILOT_RETRY = "copilot_retry"
ACTION_COPILOT_NOTIFIED = "copilot_notified"
ACTION_AUTO_RETRY_SUCCEEDED = "auto_retry_succeeded"
ACTION_AUTO_RETRY_EXHAUSTED = "auto_retry_exhausted"
ACTION_AUTO_INVESTIGATION_SKIPPED = "auto_investigation_skipped"

# Actor identity used for audit entries the error watchdog writes on its
# own, with no human admin involved (app/services/error_watchdog_service.py).
SYSTEM_ACTOR_USER_ID = "system"
SYSTEM_ACTOR_EMAIL = "Automatic error watchdog"


def _table():
    return get_dynamodb_resource().Table(settings.audit_log_table)


def _new_event_id() -> str:
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"


def log_event(
    tenant_id: str,
    actor_user_id: str,
    actor_email: str,
    action: str,
    target: Optional[str] = None,
    details: Optional[str] = None,
    notified_count: Optional[int] = None,
) -> None:
    """Best-effort: a logging failure must never fail the real action it's
    recording. Callers invoke this after their action has already
    succeeded — a transient DynamoDB hiccup here should be a missing log
    line, not a failed document upload or a failed suspend.

    notified_count is a narrow structured field (not just prose in
    `details`) specifically so admin_service.get_watchdog_stats can sum
    "how many admins were actually emailed" without parsing free text —
    only the error watchdog's exhausted-retry event sets it today.
    """
    item = {
        "tenant_id": tenant_id,
        "event_id": _new_event_id(),
        "actor_user_id": actor_user_id,
        "actor_email": actor_email,
        "action": action,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if target is not None:
        item["target"] = target
    if details is not None:
        item["details"] = details
    if notified_count is not None:
        item["notified_count"] = notified_count

    try:
        _table().put_item(Item=item)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("Failed to record audit event '%s' for tenant '%s': %s", action, tenant_id, exc)


def _row_from_item(item: dict) -> AuditEventRow:
    return AuditEventRow(
        actor_email=item["actor_email"],
        action=item["action"],
        target=item.get("target"),
        details=item.get("details"),
        created_at=item["created_at"],
        notified_count=item.get("notified_count"),
    )


def list_events(tenant_id: str, days: int = 30, limit: int = 100) -> list[AuditEventRow]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_event_id = f"{int(since.timestamp() * 1000):013d}"

    response = _table().query(
        KeyConditionExpression=Key("tenant_id").eq(tenant_id) & Key("event_id").gte(since_event_id),
        ScanIndexForward=False,
        Limit=limit,
        ConsistentRead=True,
    )
    return [_row_from_item(item) for item in response.get("Items", [])]


def list_events_for_actions(tenant_id: str, days: int, actions: set[str]) -> list[AuditEventRow]:
    """Like list_events, but paginated to completion (not capped at a
    display-oriented page size) and filtered server-side to specific action
    types — for aggregating stats (admin_service.get_watchdog_stats) rather
    than rendering a feed, where under-counting from a silent page cap would
    make the numbers wrong rather than just short.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_event_id = f"{int(since.timestamp() * 1000):013d}"

    items: list[dict] = []
    query_kwargs = {
        "KeyConditionExpression": Key("tenant_id").eq(tenant_id) & Key("event_id").gte(since_event_id),
        "FilterExpression": Attr("action").is_in(list(actions)),
        "ConsistentRead": True,
    }
    while True:
        response = _table().query(**query_kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        query_kwargs["ExclusiveStartKey"] = last_key

    return [_row_from_item(item) for item in items]
