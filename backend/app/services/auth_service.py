"""AWS Cognito auth: signup/confirm/login/refresh/logout + IdToken verification.

Mirrors bedrock_kb_service.py's role as the "external API wrapper" layer —
this module owns every Cognito boto3 call and JWT verification detail so
callers (app/api/auth.py, app/core/dependencies.py) never touch boto3 or
JWT internals directly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

import jwt
from botocore.exceptions import BotoCoreError, ClientError
from jwt import PyJWKClient

from app.core.aws_clients import get_cognito_client
from app.core.config import settings

TOKEN_USE_ID = "id"

# Cognito Groups have no "role" attribute, so per-org admin status is carried
# by a second, parallel group per org rather than an attribute on the tenant
# group itself. A user's org membership is still exactly one non-admin group
# (v1 constraint, unchanged); admin-ness is just whether they're ALSO in that
# group's "-admins" companion. ORG_SLUG_PATTERN rejects real org slugs ending
# in this suffix so the two namespaces can never collide.
ADMIN_GROUP_SUFFIX = "-admins"


class AuthError(Exception):
    """Raised for expected auth failures (bad credentials, unconfirmed user, invalid token)."""


class NoOrganizationError(AuthError):
    """Raised when a token is valid but the user belongs to no group — 403, not 401."""


@dataclass
class AuthTokens:
    id_token: str
    refresh_token: Optional[str]  # absent on refresh — Cognito doesn't reissue it


@dataclass
class IdTokenClaims:
    user_id: str  # Cognito `sub`
    email: str
    tenant_id: str  # the one `cognito:groups` entry not ending in ADMIN_GROUP_SUFFIX
    is_admin: bool  # whether they're also in `f"{tenant_id}{ADMIN_GROUP_SUFFIX}"`


@dataclass
class OrgMember:
    user_id: str  # Cognito `sub`
    email: str
    enabled: bool
    status: str
    is_admin: bool = False


def _secret_hash(username: str) -> str:
    message = (username + settings.cognito_app_client_id).encode("utf-8")
    digest = hmac.new(settings.cognito_app_client_secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _client_error_message(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Message", str(exc))


def _create_cognito_user(client: Any, email: str, password: str) -> None:
    try:
        client.sign_up(
            ClientId=settings.cognito_app_client_id,
            SecretHash=_secret_hash(email),
            Username=email,
            Password=password,
            UserAttributes=[{"Name": "email", "Value": email}],
        )
    except client.exceptions.UsernameExistsException as exc:
        raise AuthError("An account with this email already exists.") from exc
    except client.exceptions.InvalidPasswordException as exc:
        raise AuthError(f"Password does not meet requirements: {_client_error_message(exc)}") from exc
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Signup failed: {exc}") from exc


def signup(email: str, password: str, org_slug: str) -> None:
    """Create a Cognito user AND a brand-new org, with that user as its admin.

    Fails if org_slug is already taken (GroupExistsException) — joining an
    existing org is no longer possible this way; it requires a valid invite
    link (see invite_service.py + join_org() below). The founder is added to
    both org_slug and its f"{org_slug}-admins" companion group.

    Known v1 gap, left as-is deliberately: if group creation/membership
    fails after sign_up succeeds, the user is left unconfirmed with no
    group — that's an acceptable failure mode for a demo-to-production app
    (surfaces as a 400/500; recoverable via `aws cognito-idp admin-delete-user`),
    not worth building compensating-transaction logic for.
    """
    client = get_cognito_client()
    _create_cognito_user(client, email, password)

    try:
        client.create_group(
            UserPoolId=settings.cognito_user_pool_id,
            GroupName=org_slug,
            Description=f"Organization: {org_slug}",
        )
    except client.exceptions.GroupExistsException as exc:
        raise AuthError(
            f"An organization named '{org_slug}' already exists. Ask your admin for an invite link."
        ) from exc
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to set up organization '{org_slug}': {exc}") from exc

    try:
        client.admin_add_user_to_group(
            UserPoolId=settings.cognito_user_pool_id,
            Username=email,
            GroupName=org_slug,
        )
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to join organization '{org_slug}': {exc}") from exc

    admin_group = f"{org_slug}{ADMIN_GROUP_SUFFIX}"
    try:
        client.create_group(
            UserPoolId=settings.cognito_user_pool_id,
            GroupName=admin_group,
            Description=f"Admins of organization: {org_slug}",
        )
    except client.exceptions.GroupExistsException:
        pass  # shouldn't happen (org_slug was just freshly created), but harmless if it does
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to set up admin role for '{org_slug}': {exc}") from exc

    try:
        client.admin_add_user_to_group(
            UserPoolId=settings.cognito_user_pool_id,
            Username=email,
            GroupName=admin_group,
        )
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to grant admin role for '{org_slug}': {exc}") from exc


def join_org(email: str, password: str, org_slug: str) -> None:
    """Create a Cognito user and add them to an EXISTING org as a plain member.

    The only way to join an org someone else founded — org_slug here must
    already have come from a validated invite token (see
    invite_service.decode_invite_token), never raw user input. Never grants
    the f"{org_slug}-admins" companion group; there's no invite-as-admin
    flow, matching signup()'s "founder is the only admin" v1 constraint.
    """
    client = get_cognito_client()
    _create_cognito_user(client, email, password)

    try:
        client.admin_add_user_to_group(
            UserPoolId=settings.cognito_user_pool_id,
            Username=email,
            GroupName=org_slug,
        )
    except client.exceptions.ResourceNotFoundException as exc:
        # Defense in depth: the invite token's org_slug should already be
        # valid by construction, but the group could in principle have been
        # deleted between token issuance and redemption.
        raise AuthError(f"Organization '{org_slug}' no longer exists.") from exc
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to join organization '{org_slug}': {exc}") from exc


def confirm_signup(email: str, confirmation_code: str) -> None:
    client = get_cognito_client()
    try:
        client.confirm_sign_up(
            ClientId=settings.cognito_app_client_id,
            SecretHash=_secret_hash(email),
            Username=email,
            ConfirmationCode=confirmation_code,
        )
    except client.exceptions.CodeMismatchException as exc:
        raise AuthError("Incorrect confirmation code.") from exc
    except client.exceptions.ExpiredCodeException as exc:
        raise AuthError("Confirmation code has expired.") from exc
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Confirmation failed: {exc}") from exc


def forgot_password(email: str) -> None:
    """Triggers Cognito to email a password-reset code to `email`, if it exists.

    The App Client has PreventUserExistenceErrors enabled, so Cognito itself
    doesn't reveal whether the account exists — the exceptions caught here
    mirror that: callers should always show the same generic "check your
    email" message regardless of what actually happened, to avoid leaking
    which emails have accounts.
    """
    client = get_cognito_client()
    try:
        client.forgot_password(
            ClientId=settings.cognito_app_client_id,
            SecretHash=_secret_hash(email),
            Username=email,
        )
    except (client.exceptions.UserNotFoundException, client.exceptions.InvalidParameterException):
        pass
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to start password reset: {exc}") from exc


def confirm_forgot_password(email: str, confirmation_code: str, new_password: str) -> None:
    client = get_cognito_client()
    try:
        client.confirm_forgot_password(
            ClientId=settings.cognito_app_client_id,
            SecretHash=_secret_hash(email),
            Username=email,
            ConfirmationCode=confirmation_code,
            Password=new_password,
        )
    except client.exceptions.CodeMismatchException as exc:
        raise AuthError("Incorrect reset code.") from exc
    except client.exceptions.ExpiredCodeException as exc:
        raise AuthError("Reset code has expired.") from exc
    except client.exceptions.InvalidPasswordException as exc:
        raise AuthError(f"Password does not meet requirements: {_client_error_message(exc)}") from exc
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Password reset failed: {exc}") from exc


def login(email: str, password: str) -> AuthTokens:
    client = get_cognito_client()
    try:
        response = client.admin_initiate_auth(
            UserPoolId=settings.cognito_user_pool_id,
            ClientId=settings.cognito_app_client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password, "SECRET_HASH": _secret_hash(email)},
        )
    except client.exceptions.NotAuthorizedException as exc:
        raise AuthError("Incorrect email or password.") from exc
    except client.exceptions.UserNotConfirmedException as exc:
        raise AuthError("Please confirm your email before logging in.") from exc
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Login failed: {exc}") from exc

    result = response["AuthenticationResult"]
    return AuthTokens(id_token=result["IdToken"], refresh_token=result["RefreshToken"])


def refresh(username: str, refresh_token: str) -> AuthTokens:
    """`username` must be the Cognito `sub` (real Username), not the email alias.

    REFRESH_TOKEN_AUTH verifies SECRET_HASH against the real Username
    internally — passing the email alias here fails with "Unable to verify
    secret hash", even though the alias works fine for the login flow below.
    """
    client = get_cognito_client()
    try:
        response = client.admin_initiate_auth(
            UserPoolId=settings.cognito_user_pool_id,
            ClientId=settings.cognito_app_client_id,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_token, "SECRET_HASH": _secret_hash(username)},
        )
    except (client.exceptions.NotAuthorizedException, client.exceptions.UserNotFoundException) as exc:
        raise AuthError("Session expired, please log in again.") from exc
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Refresh failed: {exc}") from exc

    result = response["AuthenticationResult"]
    return AuthTokens(id_token=result["IdToken"], refresh_token=None)


def logout(username: str) -> None:
    client = get_cognito_client()
    try:
        client.admin_user_global_sign_out(UserPoolId=settings.cognito_user_pool_id, Username=username)
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Logout failed: {exc}") from exc


@lru_cache
def _jwks_client() -> PyJWKClient:
    jwks_url = (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.cognito_user_pool_id}/.well-known/jwks.json"
    )
    return PyJWKClient(jwks_url)


def verify_id_token(token: str) -> IdTokenClaims:
    """Verify a Cognito IdToken's signature, expiry, audience, and issuer.

    Explicitly checks `token_use == "id"` — PyJWT has no concept of this
    Cognito-specific claim, and without it a stolen AccessToken (which
    shares the same signing keys) could be replayed here as if it were an
    IdToken.
    """
    issuer = f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.cognito_app_client_id,
            issuer=issuer,
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid session: {exc}") from exc

    if claims.get("token_use") != TOKEN_USE_ID:
        raise AuthError("Invalid session: wrong token type.")

    groups = claims.get("cognito:groups") or []
    # Resolve tenant_id by content, not position — filtering out the
    # "-admins" companion group removes an assumption about Cognito's
    # group-claim ordering rather than adding one (AWS doesn't formally
    # guarantee an order for cognito:groups).
    tenant_groups = [g for g in groups if not g.endswith(ADMIN_GROUP_SUFFIX)]
    if not tenant_groups:
        raise NoOrganizationError("Account is not a member of any organization.")

    tenant_id = tenant_groups[0]
    is_admin = f"{tenant_id}{ADMIN_GROUP_SUFFIX}" in groups
    return IdTokenClaims(user_id=claims["sub"], email=claims["email"], tenant_id=tenant_id, is_admin=is_admin)


def _list_cognito_group_users(group_name: str) -> list[dict]:
    """Raw Cognito user dicts for a group, paginated. Empty (not an error) if
    the group doesn't exist yet — e.g. an org with no promoted admins beyond
    its founder still has its "-admins" companion group, but a caller
    shouldn't have to special-case a hypothetically-missing one.
    """
    client = get_cognito_client()
    users: list[dict] = []
    next_token = None
    try:
        while True:
            kwargs = {"UserPoolId": settings.cognito_user_pool_id, "GroupName": group_name}
            if next_token:
                kwargs["NextToken"] = next_token
            response = client.list_users_in_group(**kwargs)
            users.extend(response.get("Users", []))
            next_token = response.get("NextToken")
            if not next_token:
                break
    except client.exceptions.ResourceNotFoundException:
        return []
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to list members of group '{group_name}': {exc}") from exc
    return users


def list_org_members(tenant_id: str) -> list[OrgMember]:
    admin_subs = {
        attrs["sub"]
        for user in _list_cognito_group_users(f"{tenant_id}{ADMIN_GROUP_SUFFIX}")
        for attrs in [{a["Name"]: a["Value"] for a in user.get("Attributes", [])}]
        if "sub" in attrs
    }

    members: list[OrgMember] = []
    for user in _list_cognito_group_users(tenant_id):
        attrs = {a["Name"]: a["Value"] for a in user.get("Attributes", [])}
        sub = attrs.get("sub", "")
        members.append(
            OrgMember(
                user_id=sub,
                email=attrs.get("email", user.get("Username", "")),
                enabled=user.get("Enabled", True),
                status=user.get("UserStatus", ""),
                is_admin=sub in admin_subs,
            )
        )

    return members


def _require_org_member(tenant_id: str, target_sub: str) -> OrgMember:
    """Raises AuthError if target_sub isn't a member of tenant_id's org.

    This is the check that stops an admin from acting on a user outside
    their own org by guessing/enumerating a sub — list_users_in_group is
    scoped to tenant_id, so a sub belonging to a different org simply won't
    appear in it.
    """
    for member in list_org_members(tenant_id):
        if member.user_id == target_sub:
            return member
    raise AuthError("User not found in this organization.")


def suspend_user(tenant_id: str, target_sub: str, requesting_sub: str) -> OrgMember:
    if target_sub == requesting_sub:
        raise AuthError("You cannot suspend your own account.")

    member = _require_org_member(tenant_id, target_sub)

    client = get_cognito_client()
    try:
        client.admin_disable_user(UserPoolId=settings.cognito_user_pool_id, Username=member.email)
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to suspend user: {exc}") from exc

    return member


def unsuspend_user(tenant_id: str, target_sub: str) -> OrgMember:
    member = _require_org_member(tenant_id, target_sub)

    client = get_cognito_client()
    try:
        client.admin_enable_user(UserPoolId=settings.cognito_user_pool_id, Username=member.email)
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to unsuspend user: {exc}") from exc

    return member


def promote_to_admin(tenant_id: str, target_sub: str) -> OrgMember:
    """Grants target_sub the f"{tenant_id}-admins" companion group — the
    same mechanism that makes an org's founder its first admin (see
    signup()), just applied to an existing member instead of at signup time.
    """
    member = _require_org_member(tenant_id, target_sub)

    client = get_cognito_client()
    admin_group = f"{tenant_id}{ADMIN_GROUP_SUFFIX}"
    try:
        client.create_group(
            UserPoolId=settings.cognito_user_pool_id,
            GroupName=admin_group,
            Description=f"Admins of organization: {tenant_id}",
        )
    except client.exceptions.GroupExistsException:
        pass  # expected — every org's admin-companion group already exists from its founder
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to set up admin role for '{tenant_id}': {exc}") from exc

    try:
        client.admin_add_user_to_group(
            UserPoolId=settings.cognito_user_pool_id, Username=member.email, GroupName=admin_group
        )
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to grant admin role: {exc}") from exc

    return member


def demote_from_admin(tenant_id: str, target_sub: str, requesting_sub: str) -> OrgMember:
    """Revokes target_sub's admin role. Self-demotion is blocked outright
    (not just "if you're the last admin") — same simple, conservative shape
    as suspend_user's self-suspend block: an admin who wants to step down
    asks another admin to do it, rather than this needing to compute
    "am I the last one" via a second group-membership query.
    """
    if target_sub == requesting_sub:
        raise AuthError("You cannot remove your own admin access.")

    member = _require_org_member(tenant_id, target_sub)

    client = get_cognito_client()
    try:
        client.admin_remove_user_from_group(
            UserPoolId=settings.cognito_user_pool_id,
            Username=member.email,
            GroupName=f"{tenant_id}{ADMIN_GROUP_SUFFIX}",
        )
    except (ClientError, BotoCoreError) as exc:
        raise AuthError(f"Failed to remove admin role: {exc}") from exc

    return member
