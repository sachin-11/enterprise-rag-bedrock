import os

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    aws_region: str = os.getenv("AWS_REGION", "")
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    bedrock_kb_id: str = os.getenv("BEDROCK_KB_ID", "")
    s3_bucket_name: str = os.getenv("S3_BUCKET_NAME", "")
    opensearch_endpoint: str = os.getenv("OPENSEARCH_ENDPOINT", "")
    cohere_api_key: str = os.getenv("COHERE_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    bedrock_guardrail_id: str = os.getenv("BEDROCK_GUARDRAIL_ID", "")
    bedrock_guardrail_version: str = os.getenv("BEDROCK_GUARDRAIL_VERSION", "DRAFT")
    cognito_user_pool_id: str = os.getenv("COGNITO_USER_POOL_ID", "")
    cognito_app_client_id: str = os.getenv("COGNITO_APP_CLIENT_ID", "")
    cognito_app_client_secret: str = os.getenv("COGNITO_APP_CLIENT_SECRET", "")
    cognito_region: str = os.getenv("COGNITO_REGION", "") or os.getenv("AWS_REGION", "")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"
    dynamodb_conversations_table: str = os.getenv("DYNAMODB_CONVERSATIONS_TABLE", "")
    dynamodb_messages_table: str = os.getenv("DYNAMODB_MESSAGES_TABLE", "")
    audit_log_table: str = os.getenv("DYNAMODB_AUDIT_LOG_TABLE", "")
    # Reuses the same env vars LangChain's own tracing machinery already
    # reads directly from os.environ — the admin dashboard needs its own
    # read access to the same key to call the LangSmith API directly.
    langsmith_api_key: str = os.getenv("LANGCHAIN_API_KEY", "")
    langsmith_project: str = os.getenv("LANGCHAIN_PROJECT", "enterprise-rag-bedrock")
    invite_signing_key: str = os.getenv("INVITE_SIGNING_KEY", "")
    ses_sender_email: str = os.getenv("SES_SENDER_EMAIL", "")
    frontend_base_url: str = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000")
    # CORS allow-list for app/main.py — comma-separated so a deployed
    # frontend's real origin can be added via env var alone, no redeploy of
    # this service needed. frontend_base_url is folded in automatically so
    # it never has to be set twice.
    cors_allowed_origins: list[str] = list(
        {
            *[o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()],
            "http://localhost:3000",
            "http://localhost:3001",
            os.getenv("FRONTEND_BASE_URL", "http://localhost:3000"),
        }
    )
    # Kill switch for app/services/error_watchdog_service.py — lets the
    # automatic retry-or-notify behavior be turned off via .env alone (e.g.
    # if it proves too costly/noisy in practice) without touching the
    # on-demand admin co-pilot, which is a separate code path.
    error_watchdog_enabled: bool = os.getenv("ERROR_WATCHDOG_ENABLED", "true").lower() == "true"
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_connect_timeout_seconds: float = float(os.getenv("REDIS_CONNECT_TIMEOUT_SECONDS", "1"))
    redis_socket_timeout_seconds: float = float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "1"))
    rate_limit_enabled: bool = os.getenv("RATE_LIMIT_ENABLED", "false").lower() == "true"
    rate_limit_window_seconds: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    tenant_requests_per_window: int = int(os.getenv("TENANT_REQUESTS_PER_WINDOW", "300"))
    user_requests_per_window: int = int(os.getenv("USER_REQUESTS_PER_WINDOW", "60"))
    auth_ip_requests_per_window: int = int(os.getenv("AUTH_IP_REQUESTS_PER_WINDOW", "10"))
    trusted_proxy_cidrs: list[str] = [
        cidr.strip() for cidr in os.getenv("TRUSTED_PROXY_CIDRS", "").split(",") if cidr.strip()
    ]
    slack_signing_secret: str = os.getenv("SLACK_SIGNING_SECRET", "")
    teams_app_id: str = os.getenv("TEAMS_APP_ID", "")
    channel_identity_mappings_json: str = os.getenv("CHANNEL_IDENTITY_MAPPINGS_JSON", "{}")


settings = Settings()
