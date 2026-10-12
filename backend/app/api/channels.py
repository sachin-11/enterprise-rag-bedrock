import json

from fastapi import APIRouter, HTTPException, Request, status

from app.channels.slack import SlackVerificationError, normalize_slack_event, verify_slack_request
from app.channels.teams import TeamsVerificationError, normalize_teams_activity, verify_teams_token
from app.core.config import settings
from app.services.identity_mapping_service import IdentityMappingError, resolve_channel_identity
from app.models.orchestration import OrchestratorInput
from app.services.orchestrator_service import plan_request

router = APIRouter(prefix="/channels", tags=["channels"])


@router.post("/slack/events")
async def slack_events(request: Request) -> dict:
    body = await request.body()
    try:
        verify_slack_request(
            body,
            request.headers.get("X-Slack-Request-Timestamp"),
            request.headers.get("X-Slack-Signature"),
            settings.slack_signing_secret,
        )
    except SlackVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    payload = json.loads(body)
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}
    message = normalize_slack_event(payload)
    if message is None:
        return {"accepted": True, "ignored": True}
    try:
        context = resolve_channel_identity(
            message.channel, message.external_tenant_id, message.external_user_id, request.state.correlation_id
        )
    except IdentityMappingError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    plan = plan_request(OrchestratorInput(context=context, message=message.text, conversation_id=message.conversation_id))
    return {"accepted": True, "event_id": message.event_id, "route": plan.route.value, "response": plan.response_text}


@router.post("/teams/messages")
async def teams_messages(request: Request) -> dict:
    payload = await request.json()
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    try:
        verify_teams_token(token, settings.teams_app_id, str(payload.get("serviceUrl") or ""))
    except TeamsVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    message = normalize_teams_activity(payload)
    if message is None:
        return {"accepted": True, "ignored": True}
    try:
        context = resolve_channel_identity(
            message.channel, message.external_tenant_id, message.external_user_id, request.state.correlation_id
        )
    except IdentityMappingError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    plan = plan_request(OrchestratorInput(context=context, message=message.text, conversation_id=message.conversation_id))
    return {"accepted": True, "event_id": message.event_id, "route": plan.route.value, "response": plan.response_text}
