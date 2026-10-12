from unittest.mock import MagicMock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.request_context import (
    CORRELATION_ID_HEADER,
    bind_correlation_id,
    build_web_request_context,
    reset_correlation_id,
)
from app.main import app
from app.models.chat import ChatQueryRequest
from app.models.context import ChannelType, CorrelationMetadata, RequestContext, Role
from app.models.user import CurrentUser
from app.services import audit_service


def _request(*, correlation_id: str = "server-correlation") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/chat/query",
            "headers": [
                (b"x-tenant-id", b"attacker-tenant"),
                (b"x-role", b"admin"),
            ],
            "query_string": b"tenant_id=attacker-tenant&role=admin",
        }
    )
    request.state.correlation_id = correlation_id
    return request


def test_context_identity_comes_only_from_verified_user():
    verified_user = CurrentUser(
        user_id="user-1",
        email="member@example.com",
        tenant_id="verified-tenant",
        is_admin=False,
    )

    context = build_web_request_context(_request(), verified_user)

    assert context.user_id == "user-1"
    assert context.tenant_id == "verified-tenant"
    assert context.role is Role.MEMBER
    assert context.channel is ChannelType.WEB
    assert context.correlation_id == "server-correlation"


def test_chat_payload_rejects_identity_override_fields():
    with pytest.raises(ValidationError):
        ChatQueryRequest.model_validate(
            {
                "query": "Show my policy",
                "tenant_id": "attacker-tenant",
                "role": "admin",
            }
        )


def test_request_context_is_immutable():
    context = RequestContext(
        user_id="user-1",
        email="member@example.com",
        tenant_id="tenant-a",
        role=Role.MEMBER,
        channel=ChannelType.WEB,
        correlation=CorrelationMetadata(correlation_id="correlation-1"),
    )

    with pytest.raises(ValidationError):
        context.tenant_id = "tenant-b"


def test_every_http_response_has_correlation_id():
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers[CORRELATION_ID_HEADER]


def test_safe_incoming_correlation_id_is_propagated():
    client = TestClient(app)
    response = client.get("/health", headers={CORRELATION_ID_HEADER: "client-trace-123"})

    assert response.headers[CORRELATION_ID_HEADER] == "client-trace-123"


def test_audit_event_includes_bound_correlation_id(monkeypatch):
    table = MagicMock()
    monkeypatch.setattr(audit_service, "_table", lambda: table)
    token = bind_correlation_id("audit-correlation-1")
    try:
        audit_service.log_event("tenant-a", "user-1", "member@example.com", "test_action")
    finally:
        reset_correlation_id(token)

    item = table.put_item.call_args.kwargs["Item"]
    assert item["correlation_id"] == "audit-correlation-1"
