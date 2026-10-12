from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app.core import dependencies
from app.main import app
from app.middleware import rate_limit as rate_limit_middleware
from app.models.user import CurrentUser
from app.services.rate_limit_service import (
    RateLimitBackendError,
    RateLimitDecision,
    RateLimitScope,
    RateLimitService,
)


class _Backend:
    def __init__(self, result=(1, 0), error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = []

    def eval(self, script, numkeys, *keys_and_args):
        self.calls.append((script, numkeys, keys_and_args))
        if self.error:
            raise self.error
        return self.result


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/auth/me",
            "headers": [],
            "query_string": b"",
        }
    )
    request.state.correlation_id = "correlation-1"
    return request


def test_multiple_scopes_are_checked_in_one_atomic_backend_call():
    backend = _Backend()
    service = RateLimitService(backend, window_seconds=60)

    decision = service.check(
        [RateLimitScope("tenant", "tenant-a", 100), RateLimitScope("tenant-user", "tenant-a:user-1", 20)]
    )

    assert decision.allowed is True
    assert len(backend.calls) == 1
    _, key_count, args = backend.calls[0]
    assert key_count == 2
    assert args[-3:] == (60000, 100, 20)
    assert args[0] != args[1]
    assert "tenant-a" not in args[0]  # identities are hashed in Redis keys


def test_denied_decision_rounds_retry_after_up():
    service = RateLimitService(_Backend(result=(0, 1501)))

    decision = service.check([RateLimitScope("tenant", "tenant-a", 1)])

    assert decision == RateLimitDecision(allowed=False, retry_after_seconds=2)


def test_backend_failure_is_explicit():
    service = RateLimitService(_Backend(error=ConnectionError("redis down")))

    with pytest.raises(RateLimitBackendError):
        service.check([RateLimitScope("tenant", "tenant-a", 1)])


def test_authenticated_limit_uses_verified_tenant_and_user(monkeypatch):
    limiter = MagicMock()
    limiter.check.return_value = RateLimitDecision(allowed=True)
    monkeypatch.setattr(dependencies.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(dependencies, "get_rate_limit_service", lambda: limiter)

    context = dependencies.get_request_context(
        _request(),
        CurrentUser(user_id="user-1", email="user@example.com", tenant_id="tenant-a", is_admin=False),
    )

    scopes = limiter.check.call_args.args[0]
    assert context.tenant_id == "tenant-a"
    assert [(scope.namespace, scope.identity) for scope in scopes] == [
        ("tenant", "tenant-a"),
        ("tenant-user", "tenant-a:user-1"),
    ]


def test_authenticated_limit_returns_429_with_retry_after(monkeypatch):
    limiter = MagicMock()
    limiter.check.return_value = RateLimitDecision(allowed=False, retry_after_seconds=17)
    monkeypatch.setattr(dependencies.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(dependencies, "get_rate_limit_service", lambda: limiter)

    with pytest.raises(HTTPException) as exc_info:
        dependencies.get_request_context(
            _request(),
            CurrentUser(user_id="user-1", email="user@example.com", tenant_id="tenant-a", is_admin=False),
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "17"}


def test_auth_ip_limit_returns_429_and_correlation_header(monkeypatch):
    limiter = SimpleNamespace(check=lambda scopes: RateLimitDecision(False, 9))
    monkeypatch.setattr(rate_limit_middleware.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(rate_limit_middleware, "get_rate_limit_service", lambda: limiter)

    response = TestClient(app).post(
        "/auth/login",
        json={"email": "user@example.com", "password": "irrelevant"},
        headers={"X-Correlation-ID": "login-attempt-1"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "9"
    assert response.headers["X-Correlation-ID"] == "login-attempt-1"


def test_untrusted_peer_cannot_spoof_forwarded_ip(monkeypatch):
    monkeypatch.setattr(rate_limit_middleware.settings, "trusted_proxy_cidrs", [])
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [(b"x-forwarded-for", b"203.0.113.99")],
            "client": ("198.51.100.10", 1234),
            "query_string": b"",
        }
    )

    assert rate_limit_middleware.client_ip(request) == "198.51.100.10"
