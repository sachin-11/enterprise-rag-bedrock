import pytest
from pydantic import ValidationError

from app.models.context import ChannelType, CorrelationMetadata, RequestContext, Role
from app.models.orchestration import OrchestratorInput, RouteType
from app.services.orchestrator_service import plan_request


@pytest.fixture
def context():
    return RequestContext(
        user_id="user-1",
        email="user@example.com",
        tenant_id="tenant-a",
        role=Role.MEMBER,
        channel=ChannelType.WEB,
        correlation=CorrelationMetadata(correlation_id="correlation-1"),
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("hello", RouteType.DIRECT),
        ("What is our refund policy?", RouteType.RAG),
        ("Check my leave balance", RouteType.TOOL_READ),
        ("Create an IT ticket", RouteType.TOOL_WRITE),
        ("I need a human agent", RouteType.ESCALATE),
    ],
)
def test_route_contract(context, message, expected):
    output = plan_request(OrchestratorInput(context=context, message=message))
    assert output.route is expected
    assert (output.response_text == "") is (expected is RouteType.RAG)


def test_orchestrator_rejects_empty_message(context):
    with pytest.raises(ValidationError):
        OrchestratorInput(context=context, message="")
