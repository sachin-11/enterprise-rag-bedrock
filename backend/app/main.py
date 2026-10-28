import logging
from contextvars import Token
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.channels import router as channels_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.core.config import settings
from app.core.request_context import (
    CORRELATION_ID_HEADER,
    bind_correlation_id,
    normalize_correlation_id,
    reset_correlation_id,
)
from app.middleware.rate_limit import RateLimitMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Not a hard failure — this deployment may be a local/dev environment
    # where provisioning a Bedrock Guardrail isn't worth the setup cost. But
    # guardrail_service.check_content() silently no-ops without it (see its
    # docstring), so a production deploy that forgot to set this would run
    # with the INPUT/OUTPUT guardrail checks quietly doing nothing — loud at
    # startup, not silent, is the point here.
    if settings.guardrail_required and not settings.bedrock_guardrail_id:
        raise RuntimeError("GUARDRAIL_REQUIRED is true but BEDROCK_GUARDRAIL_ID is not set.")
    if not settings.bedrock_guardrail_id:
        logger.warning(
            "BEDROCK_GUARDRAIL_ID is not set — chat requests will run without "
            "AWS Bedrock Guardrail content screening (guardrail_service.check_content "
            "no-ops). Set BEDROCK_GUARDRAIL_ID before deploying to production."
        )
    if not settings.rate_limit_enabled:
        logger.warning(
            "RATE_LIMIT_ENABLED is false — distributed API rate limiting is disabled. "
            "Enable it and configure REDIS_URL before production deployment."
        )
    yield


app = FastAPI(title="Enterprise RAG Bedrock API", lifespan=_lifespan)


@app.middleware("http")
async def correlation_context(request, call_next):
    correlation_id = normalize_correlation_id(request.headers.get(CORRELATION_ID_HEADER))
    request.state.correlation_id = correlation_id
    token: Token = bind_correlation_id(correlation_id)
    try:
        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
    finally:
        reset_correlation_id(token)

# allow_credentials is required for the session cookies set by /auth/* to be
# sent back on cross-origin requests — note this only works with an explicit
# allow_origins list, never "*" (browsers reject wildcard + credentials).
# The allow-list itself comes from settings.cors_allowed_origins (env-driven,
# see app/core/config.py) so a deployed frontend's real origin can be added
# without touching this file.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(channels_router)
app.include_router(conversations_router)
app.include_router(admin_router)
