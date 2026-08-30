import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services import error_watchdog_service
from app.services.auth_service import OrgMember


def _reset_cooldown():
    error_watchdog_service._last_investigation_started_at.clear()


def _mock_notification_llm(monkeypatch, text="Something went wrong."):
    class _FakeResponse:
        content = text

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_FakeResponse())
    monkeypatch.setattr(error_watchdog_service, "_get_watchdog_llm", lambda: llm)
    return llm


def test_succeeds_on_a_later_attempt_sends_no_email(monkeypatch):
    _reset_cooldown()
    monkeypatch.setattr(error_watchdog_service.settings, "bedrock_kb_id", "kb-123")
    monkeypatch.setattr(error_watchdog_service, "RETRY_BACKOFF_SECONDS", 0)

    run_pipeline_mock = AsyncMock(side_effect=[(False, "still failing"), (True, "It works now.")])
    monkeypatch.setattr("app.api.chat.run_pipeline_once", run_pipeline_mock)

    send_mock = MagicMock()
    monkeypatch.setattr(error_watchdog_service.email_service, "send_admin_notification_email", send_mock)
    log_event_mock = MagicMock()
    monkeypatch.setattr(error_watchdog_service.audit_service, "log_event", log_event_mock)

    asyncio.run(
        error_watchdog_service.investigate_error(
            "org-a", "run-1", "what is our refund policy", [], "member-1", False, "boom"
        )
    )

    assert run_pipeline_mock.call_count == 2
    send_mock.assert_not_called()
    log_event_mock.assert_called_once()
    assert log_event_mock.call_args[0][3] == error_watchdog_service.audit_service.ACTION_AUTO_RETRY_SUCCEEDED
    # Retried scoped to the ORIGINAL failing member, never elevated to admin.
    for call in run_pipeline_mock.call_args_list:
        assert call.args[4] == "member-1"
        assert call.args[5] is False


def test_exhausts_retries_notifies_every_enabled_admin(monkeypatch):
    _reset_cooldown()
    monkeypatch.setattr(error_watchdog_service.settings, "bedrock_kb_id", "kb-123")
    monkeypatch.setattr(error_watchdog_service, "RETRY_BACKOFF_SECONDS", 0)

    run_pipeline_mock = AsyncMock(return_value=(False, "still failing"))
    monkeypatch.setattr("app.api.chat.run_pipeline_once", run_pipeline_mock)
    _mock_notification_llm(monkeypatch, text="The knowledge base has no matching documents.")

    members = [
        OrgMember(user_id="u1", email="admin1@org-a.com", enabled=True, status="CONFIRMED", is_admin=True),
        OrgMember(user_id="u2", email="admin2@org-a.com", enabled=True, status="CONFIRMED", is_admin=True),
        OrgMember(user_id="u3", email="disabled-admin@org-a.com", enabled=False, status="CONFIRMED", is_admin=True),
        OrgMember(user_id="u4", email="member@org-a.com", enabled=True, status="CONFIRMED", is_admin=False),
    ]
    monkeypatch.setattr(error_watchdog_service.auth_service, "list_org_members", MagicMock(return_value=members))
    send_mock = MagicMock()
    monkeypatch.setattr(error_watchdog_service.email_service, "send_admin_notification_email", send_mock)
    log_event_mock = MagicMock()
    monkeypatch.setattr(error_watchdog_service.audit_service, "log_event", log_event_mock)

    asyncio.run(
        error_watchdog_service.investigate_error(
            "org-a", "run-2", "what is our refund policy", [], "member-1", False, "boom"
        )
    )

    assert run_pipeline_mock.call_count == error_watchdog_service.MAX_AUTO_RETRIES
    # Only the two enabled admins get emailed — not the disabled admin, not the regular member.
    sent_to = {call.args[0] for call in send_mock.call_args_list}
    assert sent_to == {"admin1@org-a.com", "admin2@org-a.com"}
    log_event_mock.assert_called_once()
    assert log_event_mock.call_args[0][3] == error_watchdog_service.audit_service.ACTION_AUTO_RETRY_EXHAUSTED


def test_cooldown_skips_a_second_investigation_for_the_same_tenant(monkeypatch):
    _reset_cooldown()
    monkeypatch.setattr(error_watchdog_service.settings, "bedrock_kb_id", "kb-123")
    monkeypatch.setattr(error_watchdog_service, "RETRY_BACKOFF_SECONDS", 0)

    run_pipeline_mock = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr("app.api.chat.run_pipeline_once", run_pipeline_mock)
    log_event_mock = MagicMock()
    monkeypatch.setattr(error_watchdog_service.audit_service, "log_event", log_event_mock)

    asyncio.run(error_watchdog_service.investigate_error("org-a", "run-1", "q1", [], "u1", False, "boom"))
    asyncio.run(error_watchdog_service.investigate_error("org-a", "run-2", "q2", [], "u1", False, "boom"))

    # First investigation actually ran (1 retry, succeeded); second was skipped entirely.
    assert run_pipeline_mock.call_count == 1
    assert log_event_mock.call_count == 2
    assert log_event_mock.call_args_list[1][0][3] == error_watchdog_service.audit_service.ACTION_AUTO_INVESTIGATION_SKIPPED
