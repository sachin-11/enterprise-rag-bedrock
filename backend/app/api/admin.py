from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from app.api.chat import run_pipeline_once
from app.core.config import settings
from app.core.dependencies import require_admin
from app.models.admin import (
    AuditLogResponse,
    ErrorsResponse,
    EvalResponse,
    KnowledgeGapsResponse,
    MembersResponse,
    OrgMemberRow,
    OrgStatsResponse,
    RetryResponse,
    WatchdogStatsResponse,
)
from app.models.copilot import CopilotRequest, CopilotResponse
from app.models.user import CurrentUser, GenerateInviteRequest, GenerateInviteResponse, MessageResponse
from app.services import (
    admin_service,
    audit_service,
    auth_service,
    copilot_service,
    email_service,
    eval_service,
    invite_service,
)
from app.services.admin_service import AdminError
from app.services.auth_service import AuthError
from app.services.email_service import EmailSendError

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats", response_model=OrgStatsResponse)
async def get_stats(days: int = 7, current_user: CurrentUser = Depends(require_admin)) -> OrgStatsResponse:
    return await run_in_threadpool(admin_service.get_org_stats, current_user.tenant_id, days)


@router.get("/watchdog-stats", response_model=WatchdogStatsResponse)
async def get_watchdog_stats(days: int = 30, current_user: CurrentUser = Depends(require_admin)) -> WatchdogStatsResponse:
    return await run_in_threadpool(admin_service.get_watchdog_stats, current_user.tenant_id, days)


@router.get("/errors", response_model=ErrorsResponse)
async def get_errors(limit: int = 20, current_user: CurrentUser = Depends(require_admin)) -> ErrorsResponse:
    errors = await run_in_threadpool(admin_service.get_recent_errors, current_user.tenant_id, limit)
    return ErrorsResponse(errors=errors)


@router.get("/knowledge-gaps", response_model=KnowledgeGapsResponse)
async def get_knowledge_gaps(
    days: int = 30, limit: int = 20, current_user: CurrentUser = Depends(require_admin)
) -> KnowledgeGapsResponse:
    gaps = await run_in_threadpool(admin_service.get_knowledge_gaps, current_user.tenant_id, days, limit)
    return KnowledgeGapsResponse(gaps=gaps)


@router.get("/users", response_model=MembersResponse)
async def get_users(days: int = 7, current_user: CurrentUser = Depends(require_admin)) -> MembersResponse:
    def _load() -> MembersResponse:
        members = auth_service.list_org_members(current_user.tenant_id)
        breakdown = admin_service.get_user_breakdown(current_user.tenant_id, days)
        rows = []
        for member in members:
            stats = breakdown.get(member.user_id)
            rows.append(
                OrgMemberRow(
                    sub=member.user_id,
                    email=member.email,
                    enabled=member.enabled,
                    status=member.status,
                    is_self=member.user_id == current_user.user_id,
                    is_admin=member.is_admin,
                    query_count=stats.query_count if stats else 0,
                    total_cost=stats.total_cost if stats else 0.0,
                    avg_latency_s=stats.avg_latency_s if stats else None,
                )
            )
        return MembersResponse(members=rows)

    return await run_in_threadpool(_load)


@router.post("/users/{sub}/suspend", response_model=MessageResponse)
async def suspend(sub: str, current_user: CurrentUser = Depends(require_admin)) -> MessageResponse:
    try:
        member = await run_in_threadpool(
            auth_service.suspend_user, current_user.tenant_id, sub, current_user.user_id
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await run_in_threadpool(
        audit_service.log_event,
        current_user.tenant_id,
        current_user.user_id,
        current_user.email,
        audit_service.ACTION_USER_SUSPENDED,
        member.email,
    )
    return MessageResponse(message="User suspended.")


@router.post("/users/{sub}/unsuspend", response_model=MessageResponse)
async def unsuspend(sub: str, current_user: CurrentUser = Depends(require_admin)) -> MessageResponse:
    try:
        member = await run_in_threadpool(auth_service.unsuspend_user, current_user.tenant_id, sub)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await run_in_threadpool(
        audit_service.log_event,
        current_user.tenant_id,
        current_user.user_id,
        current_user.email,
        audit_service.ACTION_USER_UNSUSPENDED,
        member.email,
    )
    return MessageResponse(message="User unsuspended.")


@router.post("/users/{sub}/promote", response_model=MessageResponse)
async def promote(sub: str, current_user: CurrentUser = Depends(require_admin)) -> MessageResponse:
    try:
        member = await run_in_threadpool(auth_service.promote_to_admin, current_user.tenant_id, sub)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await run_in_threadpool(
        audit_service.log_event,
        current_user.tenant_id,
        current_user.user_id,
        current_user.email,
        audit_service.ACTION_USER_PROMOTED,
        member.email,
    )
    return MessageResponse(message="User promoted to admin.")


@router.post("/users/{sub}/demote", response_model=MessageResponse)
async def demote(sub: str, current_user: CurrentUser = Depends(require_admin)) -> MessageResponse:
    try:
        member = await run_in_threadpool(
            auth_service.demote_from_admin, current_user.tenant_id, sub, current_user.user_id
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await run_in_threadpool(
        audit_service.log_event,
        current_user.tenant_id,
        current_user.user_id,
        current_user.email,
        audit_service.ACTION_USER_DEMOTED,
        member.email,
    )
    return MessageResponse(message="Admin access removed.")


@router.post("/invites", response_model=GenerateInviteResponse)
async def create_invite(
    payload: GenerateInviteRequest, current_user: CurrentUser = Depends(require_admin)
) -> GenerateInviteResponse:
    token = invite_service.generate_invite_token(current_user.tenant_id)
    invite_url = f"{settings.frontend_base_url}/join?token={token}"

    try:
        await run_in_threadpool(
            email_service.send_invite_email, payload.email, current_user.tenant_id, invite_url
        )
    except EmailSendError as exc:
        # Never a hard failure — the admin still has the link on-screen to
        # send manually (e.g. SES sandbox mode rejects unverified
        # recipients until production access is granted).
        await run_in_threadpool(
            audit_service.log_event,
            current_user.tenant_id,
            current_user.user_id,
            current_user.email,
            audit_service.ACTION_INVITE_GENERATED,
            payload.email,
            "email delivery failed",
        )
        return GenerateInviteResponse(invite_url=invite_url, email_sent=False, email_error=str(exc))

    await run_in_threadpool(
        audit_service.log_event,
        current_user.tenant_id,
        current_user.user_id,
        current_user.email,
        audit_service.ACTION_INVITE_GENERATED,
        payload.email,
    )
    return GenerateInviteResponse(invite_url=invite_url, email_sent=True)


@router.get("/eval", response_model=EvalResponse)
async def get_eval(current_user: CurrentUser = Depends(require_admin)) -> EvalResponse:
    """RAG-quality eval history from scripts/run_eval.py's saved runs.

    Not tenant-scoped — see eval_service.py's module docstring for why — but
    still admin-gated, same as every other panel here.
    """
    history = await run_in_threadpool(eval_service.get_eval_history)
    latest_rows = await run_in_threadpool(eval_service.get_latest_eval_rows)
    return EvalResponse(history=history, latest_rows=latest_rows)


@router.get("/audit-log", response_model=AuditLogResponse)
async def get_audit_log(
    days: int = 30, limit: int = 100, current_user: CurrentUser = Depends(require_admin)
) -> AuditLogResponse:
    events = await run_in_threadpool(audit_service.list_events, current_user.tenant_id, days, limit)
    return AuditLogResponse(events=events)


@router.post("/errors/{run_id}/retry", response_model=RetryResponse)
async def retry(run_id: str, current_user: CurrentUser = Depends(require_admin)) -> RetryResponse:
    """Re-runs a previously-failed query as a smoke test only — nothing is
    written to DynamoDB or any user's conversation. See admin_service's
    get_retry_inputs docstring for why this shape was chosen over replaying
    into the original user's chat history.
    """
    try:
        inputs = await run_in_threadpool(admin_service.get_retry_inputs, current_user.tenant_id, run_id)
    except AdminError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    kb_id = settings.bedrock_kb_id
    if not kb_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="BEDROCK_KB_ID is not configured."
        )

    succeeded, text = await run_pipeline_once(
        inputs["query"], inputs["chat_history"], current_user.tenant_id, kb_id, current_user.user_id, current_user.is_admin
    )
    if not succeeded:
        return RetryResponse(succeeded=False, error=text)

    return RetryResponse(succeeded=True, answer_preview=text[:300])


@router.post("/copilot", response_model=CopilotResponse)
async def copilot(
    request: CopilotRequest, current_user: CurrentUser = Depends(require_admin)
) -> CopilotResponse:
    """Tool-calling agent for diagnostic questions about this org. Stateless
    — the caller resends the full conversation history each turn (see
    copilot_service.py's module docstring / the "Admin co-pilot agent" plan
    for why: no new persistence layer for a v1 on-demand feature).
    """
    return await copilot_service.run_copilot(
        current_user.tenant_id, current_user.user_id, current_user.email, request.message, request.history
    )
