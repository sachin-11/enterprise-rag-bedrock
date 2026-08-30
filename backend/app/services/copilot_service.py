"""Admin co-pilot: a tool-calling agent that answers diagnostic questions
about one org by calling the same functions the admin dashboard's panels
already use, and can take two narrow actions — retry a failed query, or
email the admin — when it finds something worth acting on.

Every tool is dispatched here with the calling admin's own tenant_id/
user_id/email already fixed; the LLM only ever supplies a tool's real
parameters (days, run_id, subject, message, ...), never tenant_id, and
notify_admin's recipient is hardcoded to the caller's own email, never an
LLM-suppliable value. See the "Admin co-pilot agent" plan for the full
design rationale.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from fastapi.concurrency import run_in_threadpool
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from app.api.chat import run_pipeline_once
from app.core.config import settings
from app.models.copilot import CopilotAction, CopilotMessage, CopilotResponse, CopilotToolCall
from app.services import admin_service, audit_service, email_service

logger = logging.getLogger(__name__)

# Same tier already proven for rewrite/HyDE/the main answer generation —
# tool-selection reasoning doesn't need the flagship model.
OPENAI_COPILOT_MODEL_ID = "gpt-5.4-mini"
MAX_TOOL_ITERATIONS = 5

_SYSTEM_PROMPT = """\
You are a diagnostic assistant for one organization's admin on a RAG SaaS platform. \
You have read-only tools to inspect this org's query stats, recent failed queries, \
knowledge gaps (questions the knowledge base couldn't answer), per-member usage, and \
the admin audit log.

When investigating a specific failing query, call retry_failed_query on it first to \
check whether the failure was transient. If the retry now succeeds, say so plainly — \
that's resolved, no further action needed. If it still fails, or you find a real, \
specific, recurring issue that a retry can't fix (a persistent knowledge gap, a \
suspended member, a pattern across many failures), call notify_admin with a concrete \
explanation of the problem and, if you can tell, its likely cause.

Only call notify_admin when you've found something real and specific — never for a \
routine question, or the admin's inbox becomes noise. Ground every claim in what the \
tools actually returned; never invent numbers or errors that didn't come from a tool \
result."""

_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_org_stats",
            "description": "Query count, cost, latency, error rate, feedback, and cache-hit stats for this org.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "Lookback window in days. Default 7."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_errors",
            "description": "Recent failed queries for this org, each with a run_id you can pass to retry_failed_query.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "Max errors to return. Default 20."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_knowledge_gaps",
            "description": (
                "Questions that repeatedly got no relevant documents back — signals what's missing "
                "from the knowledge base."
            ),
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "Lookback window in days. Default 30."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_breakdown",
            "description": "Per-member query count/cost/latency breakdown for this org.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "Lookback window in days. Default 7."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_audit_log",
            "description": "Recent admin actions for this org (uploads, deletes, suspends, invites, etc).",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "Lookback window in days. Default 30."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retry_failed_query",
            "description": (
                "Re-runs a specific failed query (by its run_id from get_recent_errors) to check "
                "whether the failure was transient."
            ),
            "parameters": {
                "type": "object",
                "properties": {"run_id": {"type": "string", "description": "The run_id of the failed query to retry."}},
                "required": ["run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_admin",
            "description": (
                "Emails the current admin about a specific, real issue you found. Only call this for "
                "a genuine finding, not routine questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Short email subject line."},
                    "message": {
                        "type": "string",
                        "description": "A concrete explanation of the issue and its likely cause.",
                    },
                },
                "required": ["subject", "message"],
            },
        },
    },
]


@lru_cache
def _get_copilot_llm() -> ChatOpenAI:
    return ChatOpenAI(api_key=settings.openai_api_key, model=OPENAI_COPILOT_MODEL_ID, temperature=0.1)


async def _dispatch_tool(
    name: str, args: dict[str, Any], tenant_id: str, admin_user_id: str, admin_email: str
) -> tuple[str, Any]:
    """Runs one tool call and returns (summary_for_the_transcript, data_for_the_llm)."""
    if name == "get_org_stats":
        days = int(args.get("days") or 7)
        stats = await run_in_threadpool(admin_service.get_org_stats, tenant_id, days)
        return f"Checked org stats for the last {days} day(s).", stats.model_dump()

    if name == "get_recent_errors":
        limit = int(args.get("limit") or 20)
        errors = await run_in_threadpool(admin_service.get_recent_errors, tenant_id, limit)
        return f"Checked the last {len(errors)} error(s).", [e.model_dump() for e in errors]

    if name == "get_knowledge_gaps":
        days = int(args.get("days") or 30)
        gaps = await run_in_threadpool(admin_service.get_knowledge_gaps, tenant_id, days)
        return f"Checked knowledge gaps for the last {days} day(s) ({len(gaps)} found).", [g.model_dump() for g in gaps]

    if name == "get_user_breakdown":
        days = int(args.get("days") or 7)
        breakdown = await run_in_threadpool(admin_service.get_user_breakdown, tenant_id, days)
        return (
            f"Checked per-member usage for the last {days} day(s).",
            {
                user_id: {
                    "query_count": stats.query_count,
                    "total_cost": stats.total_cost,
                    "avg_latency_s": stats.avg_latency_s,
                }
                for user_id, stats in breakdown.items()
            },
        )

    if name == "get_audit_log":
        days = int(args.get("days") or 30)
        events = await run_in_threadpool(audit_service.list_events, tenant_id, days)
        return f"Checked the audit log for the last {days} day(s) ({len(events)} event(s)).", [
            e.model_dump(mode="json") for e in events
        ]

    if name == "retry_failed_query":
        run_id = str(args.get("run_id") or "")
        try:
            inputs = await run_in_threadpool(admin_service.get_retry_inputs, tenant_id, run_id)
        except admin_service.AdminError as exc:
            return f"Could not retry run {run_id}: {exc}", {"error": str(exc)}

        kb_id = settings.bedrock_kb_id
        if not kb_id:
            return "Could not retry: BEDROCK_KB_ID is not configured.", {"error": "not configured"}

        # The co-pilot is only reachable via require_admin, so the caller is
        # always an admin — matches the dashboard retry button's use of the
        # requesting user's own is_admin.
        succeeded, text = await run_pipeline_once(inputs["query"], inputs["chat_history"], tenant_id, kb_id, admin_user_id, True)
        summary = f"Retried run {run_id}: {'now succeeds' if succeeded else 'still fails'}."
        await run_in_threadpool(
            audit_service.log_event,
            tenant_id,
            admin_user_id,
            admin_email,
            audit_service.ACTION_COPILOT_RETRY,
            run_id,
            summary,
        )
        return summary, {"succeeded": succeeded, "result": text[:500]}

    if name == "notify_admin":
        subject = str(args.get("subject") or "Admin co-pilot notification")
        message = str(args.get("message") or "")
        try:
            await run_in_threadpool(email_service.send_admin_notification_email, admin_email, subject, message)
            summary = f'Emailed you: "{subject}".'
            sent = True
        except email_service.EmailSendError as exc:
            summary = f"Tried to email you but it failed: {exc}"
            sent = False
        await run_in_threadpool(
            audit_service.log_event,
            tenant_id,
            admin_user_id,
            admin_email,
            audit_service.ACTION_COPILOT_NOTIFIED,
            subject,
            summary,
        )
        return summary, {"sent": sent}

    return f"Unknown tool '{name}'.", {"error": "unknown tool"}


async def run_copilot(
    tenant_id: str, admin_user_id: str, admin_email: str, message: str, history: list[CopilotMessage]
) -> CopilotResponse:
    llm = _get_copilot_llm().bind_tools(_TOOL_SCHEMAS)

    messages: list[BaseMessage] = [SystemMessage(content=_SYSTEM_PROMPT)]
    for turn in history:
        messages.append(HumanMessage(content=turn.content) if turn.role == "user" else AIMessage(content=turn.content))
    messages.append(HumanMessage(content=message))

    tool_calls_made: list[CopilotToolCall] = []
    actions_taken: list[CopilotAction] = []

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await llm.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            content = response.content
            if isinstance(content, list):
                content = "".join(str(part) for part in content)
            return CopilotResponse(answer=content, tool_calls=tool_calls_made, actions_taken=actions_taken)

        for call in response.tool_calls:
            name = call["name"]
            args = call.get("args") or {}
            summary, data = await _dispatch_tool(name, args, tenant_id, admin_user_id, admin_email)

            tool_calls_made.append(CopilotToolCall(tool_name=name, summary=summary))
            if name == "retry_failed_query":
                actions_taken.append(CopilotAction(action_type="retry", summary=summary))
            elif name == "notify_admin":
                actions_taken.append(CopilotAction(action_type="email", summary=summary))

            messages.append(ToolMessage(content=json.dumps(data, default=str), tool_call_id=call["id"]))

    return CopilotResponse(
        answer="I looked into this but couldn't finish within my tool-call budget — try a narrower question.",
        tool_calls=tool_calls_made,
        actions_taken=actions_taken,
    )
