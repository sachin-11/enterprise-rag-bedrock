import asyncio
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage

from app.models.admin import OrgStatsResponse
from app.services import copilot_service


def _fake_stats() -> OrgStatsResponse:
    return OrgStatsResponse(
        query_count=10,
        error_count=1,
        error_rate=0.1,
        total_cost=1.23,
        avg_latency_s=1.0,
        p50_latency_s=1.0,
        p95_latency_s=1.5,
        feedback_count=2,
        feedback_positive_rate=1.0,
        cache_hit_count=3,
        cache_hit_rate=0.3,
        estimated_cost_saved=0.5,
    )


def _mock_llm(monkeypatch, responses: list[AIMessage]) -> MagicMock:
    """Mocks _get_copilot_llm().bind_tools(...) to return successive AIMessages
    on each .ainvoke call, without needing a real OpenAI connection.
    """
    bound = MagicMock()
    bound.ainvoke = AsyncMock(side_effect=responses)
    llm = MagicMock()
    llm.bind_tools.return_value = bound
    monkeypatch.setattr(copilot_service, "_get_copilot_llm", lambda: llm)
    return bound


def test_get_org_stats_tool_is_scoped_to_caller_tenant(monkeypatch):
    get_stats_mock = MagicMock(return_value=_fake_stats())
    monkeypatch.setattr(copilot_service.admin_service, "get_org_stats", get_stats_mock)
    _mock_llm(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[{"name": "get_org_stats", "args": {"days": 14}, "id": "call_1"}]),
            AIMessage(content="Here are the stats.", tool_calls=[]),
        ],
    )

    result = asyncio.run(copilot_service.run_copilot("org-a", "user-1", "admin@org-a.com", "how are we doing?", []))

    # The tenant_id passed to the tool always comes from run_copilot's own
    # argument (the caller's verified session), never from the LLM's args —
    # this is the tenant-isolation boundary the plan calls out.
    get_stats_mock.assert_called_once_with("org-a", 14)
    assert result.answer == "Here are the stats."
    assert result.tool_calls[0].tool_name == "get_org_stats"
    assert result.actions_taken == []


def test_notify_admin_always_emails_the_calling_admin_regardless_of_args(monkeypatch):
    send_mock = MagicMock()
    monkeypatch.setattr(copilot_service.email_service, "send_admin_notification_email", send_mock)
    monkeypatch.setattr(copilot_service.audit_service, "log_event", MagicMock())
    _mock_llm(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "notify_admin",
                        # The tool schema has no recipient field at all, and
                        # _dispatch_tool never reads one — even a smuggled
                        # "to_email" arg must be ignored.
                        "args": {"subject": "Spike", "message": "Errors spiked.", "to_email": "attacker@evil.com"},
                        "id": "call_1",
                    }
                ],
            ),
            AIMessage(content="I emailed you about it.", tool_calls=[]),
        ],
    )

    result = asyncio.run(copilot_service.run_copilot("org-a", "user-1", "real-admin@org-a.com", "check for issues", []))

    send_mock.assert_called_once_with("real-admin@org-a.com", "Spike", "Errors spiked.")
    assert result.actions_taken[0].action_type == "email"


def test_retry_failed_query_reuses_pipeline_and_logs_audit(monkeypatch):
    monkeypatch.setattr(
        copilot_service.admin_service,
        "get_retry_inputs",
        MagicMock(return_value={"query": "what is our refund policy", "chat_history": []}),
    )
    monkeypatch.setattr(copilot_service.settings, "bedrock_kb_id", "kb-123")
    run_pipeline_mock = AsyncMock(return_value=(True, "We offer a 30-day refund."))
    monkeypatch.setattr(copilot_service, "run_pipeline_once", run_pipeline_mock)
    log_event_mock = MagicMock()
    monkeypatch.setattr(copilot_service.audit_service, "log_event", log_event_mock)
    _mock_llm(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[{"name": "retry_failed_query", "args": {"run_id": "abc-123"}, "id": "call_1"}]),
            AIMessage(content="That query now succeeds.", tool_calls=[]),
        ],
    )

    result = asyncio.run(copilot_service.run_copilot("org-a", "user-1", "admin@org-a.com", "retry run abc-123", []))

    run_pipeline_mock.assert_called_once_with("what is our refund policy", [], "org-a", "kb-123", "user-1", True)
    log_event_mock.assert_called_once()
    assert log_event_mock.call_args[0][3] == copilot_service.audit_service.ACTION_COPILOT_RETRY
    assert result.actions_taken[0].action_type == "retry"


def test_no_tool_calls_returns_answer_immediately(monkeypatch):
    _mock_llm(monkeypatch, [AIMessage(content="Everything looks healthy.", tool_calls=[])])

    result = asyncio.run(copilot_service.run_copilot("org-a", "user-1", "admin@org-a.com", "how's it going", []))

    assert result.answer == "Everything looks healthy."
    assert result.tool_calls == []
    assert result.actions_taken == []
