import asyncio

import pytest

from app.models.context import ChannelType, CorrelationMetadata, RequestContext, Role
from app.models.tools import CreateTicketInput, CreateTicketOutput, LeaveBalanceOutput
from app.services import tool_executor
from app.tools.base import RiskLevel, ToolDefinition, ToolKind
from app.tools.hr.get_leave_balance import definition as leave_balance_definition
from app.tools.registry import ToolRegistry


def _context(role=Role.MEMBER):
    return RequestContext(
        tenant_id="tenant-a",
        user_id="alice",
        email="alice@example.com",
        role=role,
        channel=ChannelType.WEB,
        correlation=CorrelationMetadata(correlation_id="trace-a"),
    )


class FakeHR:
    def __init__(self):
        self.calls = []

    async def get_balance(self, tenant_id, user_id):
        self.calls.append((tenant_id, user_id))
        return LeaveBalanceOutput(remaining_days=7)


@pytest.fixture
def audit(monkeypatch):
    events = []
    monkeypatch.setattr(tool_executor.audit_service, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    return events


def test_read_tool_uses_verified_actor_and_audits(audit):
    connector = FakeHR()
    registry = ToolRegistry()
    registry.register(leave_balance_definition(connector))

    result = asyncio.run(tool_executor.execute_read_tool(registry, _context(), "get_leave_balance", {}))

    assert result.remaining_days == 7
    assert connector.calls == [("tenant-a", "alice")]
    assert audit[0][0][3] == "business_tool_succeeded"
    assert audit[0][1]["correlation_id"] == "trace-a"


def test_unknown_tool_and_identity_override_are_denied(audit):
    connector = FakeHR()
    registry = ToolRegistry()
    registry.register(leave_balance_definition(connector))

    with pytest.raises(tool_executor.ToolDeniedError):
        asyncio.run(tool_executor.execute_read_tool(registry, _context(), "unregistered", {}))
    with pytest.raises(tool_executor.ToolInputError):
        asyncio.run(
            tool_executor.execute_read_tool(
                registry, _context(), "get_leave_balance", {"tenant_id": "tenant-b", "user_id": "bob"}
            )
        )

    assert connector.calls == []
    assert [item[0][3] for item in audit] == ["business_tool_denied", "business_tool_invalid_input"]


def test_write_definition_cannot_run_without_confirmation(audit):
    called = False

    async def handler(_context, _arguments):
        nonlocal called
        called = True
        return CreateTicketOutput(ticket_id="T-1", status="open")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="create_ticket",
            purpose="Create IT ticket",
            kind=ToolKind.WRITE,
            risk=RiskLevel.HIGH,
            required_roles=frozenset({Role.MEMBER}),
            input_model=CreateTicketInput,
            output_model=CreateTicketOutput,
            timeout_seconds=3,
            max_attempts=1,
            handler=handler,
        )
    )

    with pytest.raises(tool_executor.ToolDeniedError):
        asyncio.run(
            tool_executor.execute_read_tool(
                registry, _context(), "create_ticket", {"title": "Laptop issue", "description": "Won't boot"}
            )
        )
    assert called is False


def test_role_policy_denies_member_before_connector_call(audit):
    connector = FakeHR()
    original = leave_balance_definition(connector)
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=original.name,
            purpose=original.purpose,
            kind=original.kind,
            risk=original.risk,
            required_roles=frozenset({Role.ADMIN}),
            input_model=original.input_model,
            output_model=original.output_model,
            timeout_seconds=original.timeout_seconds,
            max_attempts=original.max_attempts,
            handler=original.handler,
        )
    )

    with pytest.raises(tool_executor.ToolDeniedError):
        asyncio.run(tool_executor.execute_read_tool(registry, _context(Role.MEMBER), "get_leave_balance", {}))

    assert connector.calls == []
    assert audit[-1][0][3] == "business_tool_denied"


def test_read_tool_retries_timeout_and_sanitizes_failure(audit):
    attempts = 0

    class SlowHR:
        async def get_balance(self, tenant_id, user_id):
            nonlocal attempts
            attempts += 1
            await asyncio.sleep(0.02)
            return LeaveBalanceOutput(remaining_days=7)

    original = leave_balance_definition(SlowHR())
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=original.name,
            purpose=original.purpose,
            kind=original.kind,
            risk=original.risk,
            required_roles=original.required_roles,
            input_model=original.input_model,
            output_model=original.output_model,
            timeout_seconds=0.001,
            max_attempts=2,
            handler=original.handler,
        )
    )

    with pytest.raises(tool_executor.ToolUnavailableError, match="temporarily unavailable"):
        asyncio.run(tool_executor.execute_read_tool(registry, _context(), "get_leave_balance", {}))

    assert attempts == 2
    assert audit[-1][0][3] == "business_tool_failed"
