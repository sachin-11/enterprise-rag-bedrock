"""Self-service leave balance; identity comes only from RequestContext."""

from __future__ import annotations

from typing import Protocol

from app.models.context import RequestContext, Role
from app.models.tools import LeaveBalanceInput, LeaveBalanceOutput
from app.tools.base import RiskLevel, ToolDefinition, ToolKind


class LeaveBalanceConnector(Protocol):
    async def get_balance(self, tenant_id: str, user_id: str) -> LeaveBalanceOutput: ...


def definition(connector: LeaveBalanceConnector) -> ToolDefinition:
    async def handler(context: RequestContext, _arguments: LeaveBalanceInput) -> LeaveBalanceOutput:
        return await connector.get_balance(context.tenant_id, context.user_id)

    return ToolDefinition(
        name="get_leave_balance",
        purpose="Read the authenticated user's own leave balance",
        kind=ToolKind.READ,
        risk=RiskLevel.LOW,
        required_roles=frozenset({Role.MEMBER, Role.ADMIN}),
        input_model=LeaveBalanceInput,
        output_model=LeaveBalanceOutput,
        timeout_seconds=3.0,
        max_attempts=2,
        handler=handler,
    )
