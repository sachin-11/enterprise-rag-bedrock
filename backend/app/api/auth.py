from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.config import settings
from app.core.dependencies import ID_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE, USERNAME_COOKIE, get_current_user
from app.models.user import (
    AuthUserResponse,
    ConfirmRequest,
    CurrentUser,
    ForgotPasswordRequest,
    InvitePreviewResponse,
    JoinRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SignupRequest,
)
from app.services import auth_service, invite_service
from app.services.auth_service import AuthError
from app.services.invite_service import InviteTokenError

router = APIRouter(prefix="/auth", tags=["auth"])

ID_TOKEN_MAX_AGE_SECONDS = 60 * 60
REFRESH_TOKEN_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


# Browsers only send a SameSite=Lax cookie on a top-level navigation, never
# on a cross-site fetch/XHR — fine for local dev (localhost:3000 -> :8000 is
# cross-origin but same-site), but the frontend and backend sit on entirely
# different registrable domains once deployed (e.g. a Vercel app calling a
# Render API), which is genuinely cross-site. SameSite=None is required for
# that, and the spec requires None to be paired with Secure — so this rides
# on the same cookie_secure flag rather than being a second env var to keep
# in sync with it.
_COOKIE_SAMESITE = "none" if settings.cookie_secure else "lax"


def _set_session_cookies(response: Response, *, id_token: str, refresh_token: str | None, username: str) -> None:
    response.set_cookie(
        ID_TOKEN_COOKIE,
        id_token,
        max_age=ID_TOKEN_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=_COOKIE_SAMESITE,
    )
    if refresh_token is not None:
        response.set_cookie(
            REFRESH_TOKEN_COOKIE,
            refresh_token,
            max_age=REFRESH_TOKEN_MAX_AGE_SECONDS,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=_COOKIE_SAMESITE,
        )
        # Needed to recompute SECRET_HASH on /auth/refresh and /auth/logout
        # without decoding a (possibly expired) JWT. This is the Cognito
        # `sub` (real Username), not the email alias — not a secret itself.
        response.set_cookie(
            USERNAME_COOKIE,
            username,
            max_age=REFRESH_TOKEN_MAX_AGE_SECONDS,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=_COOKIE_SAMESITE,
        )


def _clear_session_cookies(response: Response) -> None:
    for cookie_name in (ID_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE, USERNAME_COOKIE):
        response.delete_cookie(cookie_name)


@router.post("/signup", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest) -> MessageResponse:
    try:
        auth_service.signup(payload.email, payload.password, payload.org_slug)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MessageResponse(message="Check your email for a confirmation code.")


@router.get("/invite-preview", response_model=InvitePreviewResponse, status_code=status.HTTP_200_OK)
async def invite_preview(token: str) -> InvitePreviewResponse:
    try:
        payload = invite_service.decode_invite_token(token)
    except InviteTokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return InvitePreviewResponse(org_slug=payload.org_slug)


@router.post("/join", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def join(payload: JoinRequest) -> MessageResponse:
    try:
        invite = invite_service.decode_invite_token(payload.token)
    except InviteTokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        auth_service.join_org(payload.email, payload.password, invite.org_slug)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MessageResponse(message="Check your email for a confirmation code.")


@router.post("/confirm", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def confirm(payload: ConfirmRequest) -> MessageResponse:
    try:
        auth_service.confirm_signup(payload.email, payload.confirmation_code)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MessageResponse(message="Email confirmed. You can now log in.")


@router.post("/forgot-password", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def forgot_password(payload: ForgotPasswordRequest) -> MessageResponse:
    try:
        auth_service.forgot_password(payload.email)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # Always the same message regardless of whether the email has an
    # account — matches PreventUserExistenceErrors on the App Client, so a
    # response-timing/content difference can't be used to enumerate emails.
    return MessageResponse(message="If an account with this email exists, a reset code has been sent.")


@router.post("/reset-password", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def reset_password(payload: ResetPasswordRequest) -> MessageResponse:
    try:
        auth_service.confirm_forgot_password(payload.email, payload.confirmation_code, payload.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MessageResponse(message="Password reset. You can now log in.")


@router.post("/login", response_model=AuthUserResponse, status_code=status.HTTP_200_OK)
async def login(payload: LoginRequest, response: Response) -> AuthUserResponse:
    try:
        tokens = auth_service.login(payload.email, payload.password)
        claims = auth_service.verify_id_token(tokens.id_token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    _set_session_cookies(
        response, id_token=tokens.id_token, refresh_token=tokens.refresh_token, username=claims.user_id
    )
    return AuthUserResponse(email=claims.email, tenant_id=claims.tenant_id, is_admin=claims.is_admin)


@router.post("/refresh", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def refresh(request: Request, response: Response) -> MessageResponse:
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    username = request.cookies.get(USERNAME_COOKIE)
    if not refresh_token or not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        tokens = auth_service.refresh(username, refresh_token)
    except AuthError as exc:
        _clear_session_cookies(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    _set_session_cookies(response, id_token=tokens.id_token, refresh_token=None, username=username)
    return MessageResponse(message="Session refreshed.")


@router.post("/logout", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def logout(request: Request, response: Response) -> MessageResponse:
    username = request.cookies.get(USERNAME_COOKIE)
    if username:
        try:
            auth_service.logout(username)
        except AuthError:
            pass  # best-effort — cookies are cleared regardless so the client is logged out either way

    _clear_session_cookies(response)
    return MessageResponse(message="Logged out.")


@router.get("/me", response_model=AuthUserResponse, status_code=status.HTTP_200_OK)
async def me(current_user: CurrentUser = Depends(get_current_user)) -> AuthUserResponse:
    return AuthUserResponse(email=current_user.email, tenant_id=current_user.tenant_id, is_admin=current_user.is_admin)
