from pydantic import BaseModel, EmailStr, Field, field_validator

# Safe as a Cognito group name, S3 prefix, and DynamoDB tenant_id all at once.
ORG_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$"
# Cognito group names ending in this suffix are reserved as per-org
# admin-companion groups (see ADMIN_GROUP_SUFFIX in auth_service.py) — a real
# org slug can't end in it, or it would collide with another org's admin
# group. Pydantic's regex engine (Rust `regex` crate) has no look-around
# support, so this is enforced as a separate validator, not part of the
# pattern itself.
ORG_SLUG_RESERVED_SUFFIX = "-admins"


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    org_slug: str = Field(pattern=ORG_SLUG_PATTERN)

    @field_validator("org_slug")
    @classmethod
    def _reject_reserved_suffix(cls, value: str) -> str:
        if value.endswith(ORG_SLUG_RESERVED_SUFFIX):
            raise ValueError(f'Organization name cannot end in "{ORG_SLUG_RESERVED_SUFFIX}".')
        return value


class ConfirmRequest(BaseModel):
    email: EmailStr
    confirmation_code: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    confirmation_code: str
    new_password: str


class JoinRequest(BaseModel):
    token: str
    email: EmailStr
    password: str


class InvitePreviewResponse(BaseModel):
    org_slug: str


class GenerateInviteRequest(BaseModel):
    email: EmailStr


class GenerateInviteResponse(BaseModel):
    invite_url: str
    email_sent: bool
    email_error: str | None = None


class CurrentUser(BaseModel):
    user_id: str
    email: str
    tenant_id: str
    is_admin: bool = False


class AuthUserResponse(BaseModel):
    email: str
    tenant_id: str
    is_admin: bool = False


class MessageResponse(BaseModel):
    message: str
