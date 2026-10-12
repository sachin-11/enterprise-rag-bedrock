import hashlib
import hmac
import json

import pytest

from app.channels.slack import SlackVerificationError, normalize_slack_event, verify_slack_request
from app.channels.teams import TeamsVerificationError, normalize_teams_activity, verify_teams_token
from app.models.context import ChannelType


def _slack_signature(secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_slack_signature_and_normalization():
    body = json.dumps({"event_id": "Ev1"}).encode()
    signature = _slack_signature("secret", "1000", body)
    verify_slack_request(body, "1000", signature, "secret", now=1100)

    message = normalize_slack_event(
        {
            "event_id": "Ev1",
            "team_id": "T1",
            "event": {"type": "message", "user": "U1", "channel": "C1", "text": "hello"},
        }
    )
    assert message.channel is ChannelType.SLACK
    assert message.external_tenant_id == "T1"
    assert message.text == "hello"


def test_slack_rejects_replay_and_bad_signature():
    with pytest.raises(SlackVerificationError):
        verify_slack_request(b"{}", "1000", "v0=bad", "secret", now=1400)
    with pytest.raises(SlackVerificationError):
        verify_slack_request(b"{}", "1000", "v0=bad", "secret", now=1000)


def test_slack_ignores_bot_messages():
    assert normalize_slack_event(
        {
            "event_id": "Ev1",
            "team_id": "T1",
            "event": {"type": "message", "user": "U1", "channel": "C1", "text": "hello", "bot_id": "B1"},
        }
    ) is None


def test_teams_normalization():
    message = normalize_teams_activity(
        {
            "type": "message",
            "id": "activity-1",
            "serviceUrl": "https://example.invalid",
            "from": {"id": "user-1"},
            "conversation": {"id": "conversation-1"},
            "channelData": {"tenant": {"id": "tenant-1"}},
            "text": "hello",
        }
    )
    assert message.channel is ChannelType.TEAMS
    assert message.external_user_id == "user-1"


def test_teams_token_requires_credentials():
    with pytest.raises(TeamsVerificationError):
        verify_teams_token("", "app-id", "https://service.example.com")
