from __future__ import annotations

from functools import lru_cache

import jwt

from app.channels.base import ChannelMessage
from app.models.context import ChannelType

BOT_CONNECTOR_ISSUER = "https://api.botframework.com"
BOT_CONNECTOR_JWKS = "https://login.botframework.com/v1/.well-known/keys"


class TeamsVerificationError(ValueError):
    pass


@lru_cache
def _jwk_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(BOT_CONNECTOR_JWKS, cache_keys=True)


def verify_teams_token(token: str, app_id: str, service_url: str) -> dict:
    if not token or not app_id:
        raise TeamsVerificationError("Missing Teams request credentials.")
    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=app_id,
            issuer=BOT_CONNECTOR_ISSUER,
            leeway=300,
        )
    except jwt.PyJWTError as exc:
        raise TeamsVerificationError("Invalid Teams bearer token.") from exc
    if claims.get("serviceUrl") != service_url:
        raise TeamsVerificationError("Teams serviceUrl claim does not match the activity.")
    return claims


def normalize_teams_activity(payload: dict) -> ChannelMessage | None:
    if payload.get("type") != "message":
        return None
    conversation = payload.get("conversation") or {}
    sender = payload.get("from") or {}
    channel_data = payload.get("channelData") or {}
    tenant = channel_data.get("tenant") or {}
    return ChannelMessage(
        channel=ChannelType.TEAMS,
        event_id=str(payload["id"]),
        external_tenant_id=str(tenant["id"]),
        external_user_id=str(sender["id"]),
        conversation_id=str(conversation["id"]),
        text=str(payload.get("text") or "").strip(),
    )
