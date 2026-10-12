"""Trusted identity and request metadata shared by every application layer."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ChannelType(StrEnum):
    WEB = "web"
    SLACK = "slack"
    TEAMS = "teams"


class Role(StrEnum):
    MEMBER = "member"
    ADMIN = "admin"


class CorrelationMetadata(BaseModel):
    """Server-issued identifiers used to connect traces, logs and audits."""

    model_config = ConfigDict(frozen=True)

    correlation_id: str = Field(min_length=1, max_length=64)
    channel_event_id: str | None = Field(default=None, max_length=256)


class RequestContext(BaseModel):
    """Authenticated request identity.

    This model must only be constructed by ``core.request_context`` after a
    channel credential has been verified. It is deliberately absent from all
    public request-body models.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    email: EmailStr
    tenant_id: str = Field(min_length=1)
    role: Role
    channel: ChannelType
    correlation: CorrelationMetadata

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def correlation_id(self) -> str:
        return self.correlation.correlation_id
