from __future__ import annotations

import hashlib
import hmac
import time

from app.channels.base import ChannelMessage
from app.models.context import ChannelType


class SlackVerificationError(ValueError):
    pass


def verify_slack_request(
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    signing_secret: str,
    *,
    now: int | None = None,
    replay_window_seconds: int = 300,
) -> None:
    if not signing_secret or not timestamp or not signature:
        raise SlackVerificationError("Missing Slack request credentials.")
    try:
        request_time = int(timestamp)
    except ValueError as exc:
        raise SlackVerificationError("Invalid Slack timestamp.") from exc
    if abs((now or int(time.time())) - request_time) > replay_window_seconds:
        raise SlackVerificationError("Slack request timestamp is outside the replay window.")
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SlackVerificationError("Invalid Slack request signature.")


def normalize_slack_event(payload: dict) -> ChannelMessage | None:
    event = payload.get("event") or {}
    if event.get("type") not in {"message", "app_mention"} or event.get("bot_id") or event.get("subtype"):
        return None
    return ChannelMessage(
        channel=ChannelType.SLACK,
        event_id=str(payload["event_id"]),
        external_tenant_id=str(payload["team_id"]),
        external_user_id=str(event["user"]),
        conversation_id=str(event.get("thread_ts") or event["channel"]),
        text=str(event.get("text") or "").strip(),
    )
