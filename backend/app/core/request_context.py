"""Construction and propagation of trusted per-request context."""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

from fastapi import Request

from app.models.context import ChannelType, CorrelationMetadata, RequestContext, Role
from app.models.user import CurrentUser

CORRELATION_ID_HEADER = "X-Correlation-ID"
_SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def normalize_correlation_id(candidate: str | None) -> str:
    """Accept a safe caller trace ID or issue one without trusting identity data."""

    if candidate and _SAFE_CORRELATION_ID.fullmatch(candidate):
        return candidate
    return new_correlation_id()


def bind_correlation_id(correlation_id: str) -> Token:
    return _correlation_id.set(correlation_id)


def reset_correlation_id(token: Token) -> None:
    _correlation_id.reset(token)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def build_web_request_context(request: Request, user: CurrentUser) -> RequestContext:
    """Build context from verified Cognito identity plus server request state.

    Tenant and role are copied exclusively from ``CurrentUser``, which is
    produced by token verification. Body, query and identity-like headers are
    intentionally never inspected here.
    """

    correlation_id = getattr(request.state, "correlation_id", None) or new_correlation_id()
    return RequestContext(
        user_id=user.user_id,
        email=user.email,
        tenant_id=user.tenant_id,
        role=Role.ADMIN if user.is_admin else Role.MEMBER,
        channel=ChannelType.WEB,
        correlation=CorrelationMetadata(correlation_id=correlation_id),
    )


def build_internal_request_context(
    *,
    user_id: str,
    email: str,
    tenant_id: str,
    is_admin: bool,
    correlation_id: str | None = None,
) -> RequestContext:
    """Build context for authenticated background work derived from a request."""

    return RequestContext(
        user_id=user_id,
        email=email,
        tenant_id=tenant_id,
        role=Role.ADMIN if is_admin else Role.MEMBER,
        channel=ChannelType.WEB,
        correlation=CorrelationMetadata(
            correlation_id=correlation_id or get_correlation_id() or new_correlation_id()
        ),
    )
