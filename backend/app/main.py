from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router

app = FastAPI(title="Enterprise RAG Bedrock API")

# Local Next.js dev server runs on a different origin (port 3000/3001).
# allow_credentials is required for the session cookies set by /auth/* to be
# sent back on cross-origin requests — note this only works with an explicit
# allow_origins list, never "*" (browsers reject wildcard + credentials).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
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
