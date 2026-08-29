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

from boto3.dynamodb.conditions import Key
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
) -> None:
    """Best-effort: a logging failure must never fail the real action it's
    recording. Callers invoke this after their action has already
    succeeded — a transient DynamoDB hiccup here should be a missing log
    line, not a failed document upload or a failed suspend.
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

    try:
        _table().put_item(Item=item)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("Failed to record audit event '%s' for tenant '%s': %s", action, tenant_id, exc)


def list_events(tenant_id: str, days: int = 30, limit: int = 100) -> list[AuditEventRow]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_event_id = f"{int(since.timestamp() * 1000):013d}"

    response = _table().query(
        KeyConditionExpression=Key("tenant_id").eq(tenant_id) & Key("event_id").gte(since_event_id),
        ScanIndexForward=False,
        Limit=limit,
        ConsistentRead=True,
    )
    return [
        AuditEventRow(
            actor_email=item["actor_email"],
            action=item["action"],
            target=item.get("target"),
            details=item.get("details"),
            created_at=item["created_at"],
        )
        for item in response.get("Items", [])
    ]
