"""IP-based abuse limits for unauthenticated authentication endpoints."""

from __future__ import annotations

from ipaddress import ip_address, ip_network

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import settings
from app.core.request_context import CORRELATION_ID_HEADER, normalize_correlation_id
from app.services.rate_limit_service import (
    RateLimitBackendError,
    RateLimitScope,
    get_rate_limit_service,
)

_SENSITIVE_AUTH_PATHS = {
    "/auth/signup",
    "/auth/join",
    "/auth/confirm",
    "/auth/login",
    "/auth/forgot-password",
    "/auth/reset-password",
    "/auth/refresh",
}


def _is_trusted_proxy(host: str) -> bool:
    try:
        address = ip_address(host)
        return any(address in ip_network(network, strict=False) for network in settings.trusted_proxy_cidrs)
    except ValueError:
        return False


def client_ip(request: Request) -> str:
    direct_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded and _is_trusted_proxy(direct_ip):
        candidate = forwarded.split(",", 1)[0].strip()
        try:
            return str(ip_address(candidate))
        except ValueError:
            pass
    return direct_ip


def _error_response(status_code: int, detail: str, correlation_id: str, retry_after: int | None = None) -> JSONResponse:
    headers = {CORRELATION_ID_HEADER: correlation_id}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not settings.rate_limit_enabled or request.url.path not in _SENSITIVE_AUTH_PATHS:
            return await call_next(request)

        correlation_id = getattr(request.state, "correlation_id", None) or normalize_correlation_id(
            request.headers.get(CORRELATION_ID_HEADER)
        )
        try:
            decision = get_rate_limit_service().check(
                [RateLimitScope("auth-ip", client_ip(request), settings.auth_ip_requests_per_window)]
            )
        except RateLimitBackendError:
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Request protection is temporarily unavailable.",
                correlation_id,
            )

        if not decision.allowed:
            return _error_response(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many authentication attempts.",
                correlation_id,
                decision.retry_after_seconds,
            )
        return await call_next(request)
