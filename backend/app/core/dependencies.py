from fastapi import Depends, HTTPException, Request, status

from app.models.user import CurrentUser
from app.services.auth_service import AuthError, NoOrganizationError, verify_id_token

ID_TOKEN_COOKIE = "id_token"
REFRESH_TOKEN_COOKIE = "refresh_token"
# Cognito's real Username is the `sub` UUID, not the email — email is only a
# login *alias*. AdminInitiateAuth's REFRESH_TOKEN_AUTH flow verifies
# SECRET_HASH against the real Username, so this cookie must hold the sub,
# not the email, or refresh fails with "Unable to verify secret hash".
USERNAME_COOKIE = "cognito_username"


def get_current_user(request: Request) -> CurrentUser:
    """Verify the session cookie and resolve it to a user + their org (tenant).

    This is the sole source of tenant_id for every tenant-scoped endpoint —
    replaces the old client-supplied, unverified X-Tenant-ID header. A
    request can no longer claim to belong to a tenant; it must prove it via
    a Cognito-signed token.
    """
    token = request.cookies.get(ID_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        claims = verify_id_token(token)
    except NoOrganizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return CurrentUser(user_id=claims.user_id, email=claims.email, tenant_id=claims.tenant_id, is_admin=claims.is_admin)


def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Gate for org-admin-only endpoints — see app/api/admin.py.

    Scoped to the requester's own tenant_id implicitly: current_user.tenant_id
    is what every admin_service call filters by, so this never grants
    visibility into another organization.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return current_user
