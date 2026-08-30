"""Automatic error watchdog: fires the moment a real chat query fails (see
app/api/chat.py's chat_query, the only place this gets triggered from),
retries it a bounded number of times, and — only if it's still failing —
emails every admin of the org a plain explanation. Logs every outcome
(fixed, escalated, or skipped) to the same audit trail admin actions use.

Deliberately event-driven, not a scheduled poller: it only ever does work
in response to an actual failure, so cost scales with real problems, not
with a check interval. Deliberately separate from copilot_service.py (the
on-demand admin co-pilot) — different trigger, different cost/safety
constraints — though both share run_pipeline_once/email_service/audit_service.
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache

from fastapi.concurrency import run_in_threadpool
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.services import audit_service, auth_service, email_service

logger = logging.getLogger(__name__)

# The user's own "3-4 baar try kare" — a hard cap, not a suggestion: this is
# what makes the investigation's cost and runtime bounded no matter what.
MAX_AUTO_RETRIES = 3
# Short, fixed backoff between attempts — long enough to ride out a brief
# throttle/transient blip, short enough that 3 attempts don't run for minutes
# in the background.
RETRY_BACKOFF_SECONDS = 2.0
# Per-tenant cooldown: if many users hit the same underlying outage at once,
# only the first failure starts an investigation — the rest are logged as
# skipped rather than each spawning their own redundant retry-storm.
COOLDOWN_SECONDS = 900.0

OPENAI_WATCHDOG_MODEL_ID = "gpt-5.4-mini"

_last_investigation_started_at: dict[str, float] = {}

_NOTIFICATION_SYSTEM_PROMPT = """\
You write short, plain-language incident notifications for a SaaS admin. \
Given a question that failed repeatedly and the error it kept hitting, explain \
in 2-4 sentences what happened and, if you can tell from the error text, its \
likely cause. No greeting, no sign-off — just the explanation."""


@lru_cache
def _get_watchdog_llm() -> ChatOpenAI:
    return ChatOpenAI(api_key=settings.openai_api_key, model=OPENAI_WATCHDOG_MODEL_ID, temperature=0.1)


def _in_cooldown(tenant_id: str) -> bool:
    started_at = _last_investigation_started_at.get(tenant_id)
    return started_at is not None and (time.monotonic() - started_at) < COOLDOWN_SECONDS


async def _compose_notification(query: str, error_detail: str, attempts: int) -> str:
    llm = _get_watchdog_llm()
    human_prompt = (
        f'The question "{query}" failed {attempts} time(s) in a row.\n'
        f"Final error: {error_detail}"
    )
    response = await llm.ainvoke([SystemMessage(content=_NOTIFICATION_SYSTEM_PROMPT), HumanMessage(content=human_prompt)])
    content = response.content
    if isinstance(content, list):
        content = "".join(str(part) for part in content)
    return content


async def investigate_error(
    tenant_id: str,
    run_id: str,
    query: str,
    chat_history: list[dict],
    user_id: str,
    is_admin: bool,
    error_detail: str,
) -> None:
    from app.api.chat import run_pipeline_once  # local import: chat.py imports this module's trigger site, not vice versa

    if _in_cooldown(tenant_id):
        await run_in_threadpool(
            audit_service.log_event,
            tenant_id,
            audit_service.SYSTEM_ACTOR_USER_ID,
            audit_service.SYSTEM_ACTOR_EMAIL,
            audit_service.ACTION_AUTO_INVESTIGATION_SKIPPED,
            run_id,
            "Skipped: another auto-investigation started recently for this organization.",
        )
        return
    _last_investigation_started_at[tenant_id] = time.monotonic()

    kb_id = settings.bedrock_kb_id
    if not kb_id:
        return

    # Retried with the ORIGINAL failing user's own user_id/is_admin, never
    # elevated — retrying as an admin could "succeed" by reading documents
    # that user can't actually see, which would be a false, misleading fix.
    attempts = 0
    succeeded = False
    last_error = error_detail
    while attempts < MAX_AUTO_RETRIES:
        attempts += 1
        await asyncio.sleep(RETRY_BACKOFF_SECONDS)
        succeeded, result = await run_pipeline_once(query, chat_history, tenant_id, kb_id, user_id, is_admin)
        if succeeded:
            break
        last_error = result

    if succeeded:
        await run_in_threadpool(
            audit_service.log_event,
            tenant_id,
            audit_service.SYSTEM_ACTOR_USER_ID,
            audit_service.SYSTEM_ACTOR_EMAIL,
            audit_service.ACTION_AUTO_RETRY_SUCCEEDED,
            run_id,
            f"Auto-retried {attempts} time(s); succeeded on attempt {attempts}. Query: {query[:200]}",
        )
        return

    explanation = await _compose_notification(query, last_error, attempts)

    members = await run_in_threadpool(auth_service.list_org_members, tenant_id)
    admin_emails = [m.email for m in members if m.is_admin and m.enabled]

    notified: list[str] = []
    for admin_email in admin_emails:
        try:
            await run_in_threadpool(
                email_service.send_admin_notification_email,
                admin_email,
                f"Recurring failure: {query[:60]}",
                explanation,
            )
            notified.append(admin_email)
        except email_service.EmailSendError as exc:
            logger.warning("Watchdog notification email to %s failed: %s", admin_email, exc)

    await run_in_threadpool(
        audit_service.log_event,
        tenant_id,
        audit_service.SYSTEM_ACTOR_USER_ID,
        audit_service.SYSTEM_ACTOR_EMAIL,
        audit_service.ACTION_AUTO_RETRY_EXHAUSTED,
        run_id,
        f"Auto-retried {attempts} time(s), still failing. Notified: {', '.join(notified) if notified else 'no admin (send failed)'}.",
        len(notified),
    )
