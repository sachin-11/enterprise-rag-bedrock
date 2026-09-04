import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.core.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Not a hard failure — this deployment may be a local/dev environment
    # where provisioning a Bedrock Guardrail isn't worth the setup cost. But
    # guardrail_service.check_content() silently no-ops without it (see its
    # docstring), so a production deploy that forgot to set this would run
    # with the INPUT/OUTPUT guardrail checks quietly doing nothing — loud at
    # startup, not silent, is the point here.
    if not settings.bedrock_guardrail_id:
        logger.warning(
            "BEDROCK_GUARDRAIL_ID is not set — chat requests will run without "
            "AWS Bedrock Guardrail content screening (guardrail_service.check_content "
            "no-ops). Set BEDROCK_GUARDRAIL_ID before deploying to production."
        )
    yield


app = FastAPI(title="Enterprise RAG Bedrock API", lifespan=_lifespan)

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

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(admin_router)
