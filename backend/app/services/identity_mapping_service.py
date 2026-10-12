"""Resolve verified channel identities to immutable internal identities."""

import json
from functools import lru_cache

from app.core.config import settings
from app.core.request_context import new_correlation_id
from app.models.context import ChannelType, CorrelationMetadata, RequestContext, Role


class IdentityMappingError(LookupError):
    pass


@lru_cache
def _mappings() -> dict:
    return json.loads(settings.channel_identity_mappings_json or "{}")


def resolve_channel_identity(
    channel: ChannelType,
    external_tenant_id: str,
    external_user_id: str,
    correlation_id: str | None = None,
) -> RequestContext:
    key = f"{channel.value}:{external_tenant_id}:{external_user_id}"
    record = _mappings().get(key)
    if not record:
        raise IdentityMappingError("Channel identity is not linked to an enterprise account.")
    return RequestContext(
        user_id=record["user_id"],
        email=record["email"],
        tenant_id=record["tenant_id"],
        role=Role(record.get("role", "member")),
        channel=channel,
        correlation=CorrelationMetadata(correlation_id=correlation_id or new_correlation_id()),
    )
