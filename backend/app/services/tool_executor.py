"""Validated, audited execution boundary for read-only business tools."""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError

from app.models.context import RequestContext
from app.services import audit_service
from app.tools.base import ToolKind
from app.tools.policy import can_execute
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolDeniedError(Exception):
    pass


class ToolInputError(Exception):
    pass


class ToolUnavailableError(Exception):
    pass


class ToolTransientError(Exception):
    """Connector failure that can be safely retried for a read tool."""


def _audit(context: RequestContext, name: str, outcome: str) -> None:
    logger.info(
        "tool_event tenant=%s actor=%s correlation=%s tool=%s outcome=%s",
        context.tenant_id,
        context.user_id,
        context.correlation_id,
        name,
        outcome,
    )
    audit_service.log_event(
        context.tenant_id,
        context.user_id,
        str(context.email),
        "business_tool_" + outcome,
        target=name,
        correlation_id=context.correlation_id,
    )


async def execute_read_tool(
    registry: ToolRegistry,
    context: RequestContext,
    name: str,
    arguments: dict,
):
    tool = registry.get(name)
    if tool is None or tool.kind is not ToolKind.READ or not can_execute(context, tool):
        _audit(context, name, "denied")
        raise ToolDeniedError("Tool is not available for this request")
    try:
        parsed = tool.input_model.model_validate(arguments)
    except ValidationError as exc:
        _audit(context, name, "invalid_input")
        raise ToolInputError("Tool arguments are invalid") from exc

    for attempt in range(tool.max_attempts):
        try:
            raw = await asyncio.wait_for(tool.handler(context, parsed), timeout=tool.timeout_seconds)
            result = tool.output_model.model_validate(raw)
            _audit(context, name, "succeeded")
            return result
        except (ToolTransientError, TimeoutError):
            if attempt + 1 == tool.max_attempts:
                break
        except Exception:
            logger.exception("Business tool failed: %s", name)
            break
    _audit(context, name, "failed")
    raise ToolUnavailableError("Business service is temporarily unavailable")
