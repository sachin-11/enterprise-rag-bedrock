"""Deterministic actor checks independent of model output."""

from app.models.context import RequestContext
from app.tools.base import ToolDefinition


def can_execute(context: RequestContext, tool: ToolDefinition) -> bool:
    return context.role in tool.required_roles
