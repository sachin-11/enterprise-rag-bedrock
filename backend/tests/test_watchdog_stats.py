from unittest.mock import MagicMock

from app.models.admin import AuditEventRow
from app.services import admin_service, audit_service


def _row(action: str, notified_count: int | None = None) -> AuditEventRow:
    return AuditEventRow(
        actor_email=audit_service.SYSTEM_ACTOR_EMAIL,
        action=action,
        target="run-1",
        details="...",
        created_at="2026-08-30T00:00:00+00:00",
        notified_count=notified_count,
    )


def test_get_watchdog_stats_aggregates_counts_and_success_rate(monkeypatch):
    events = [
        _row(audit_service.ACTION_AUTO_RETRY_SUCCEEDED),
        _row(audit_service.ACTION_AUTO_RETRY_SUCCEEDED),
        _row(audit_service.ACTION_AUTO_RETRY_EXHAUSTED, notified_count=2),
        _row(audit_service.ACTION_AUTO_RETRY_EXHAUSTED, notified_count=0),
        _row(audit_service.ACTION_AUTO_INVESTIGATION_SKIPPED),
    ]
    list_events_mock = MagicMock(return_value=events)
    monkeypatch.setattr(audit_service, "list_events_for_actions", list_events_mock)

    stats = admin_service.get_watchdog_stats("org-a", days=14)

    list_events_mock.assert_called_once_with("org-a", 14, admin_service._WATCHDOG_ACTIONS)
    assert stats.succeeded_count == 2
    assert stats.exhausted_count == 2
    assert stats.skipped_count == 1
    assert stats.total_investigations == 4  # succeeded + exhausted, skipped isn't a real run
    assert stats.success_rate == 0.5
    assert stats.emails_sent_count == 2  # 2 + 0 across the two exhausted events


def test_get_watchdog_stats_handles_no_activity(monkeypatch):
    monkeypatch.setattr(audit_service, "list_events_for_actions", MagicMock(return_value=[]))

    stats = admin_service.get_watchdog_stats("org-a")

    assert stats.total_investigations == 0
    assert stats.success_rate == 0.0
    assert stats.emails_sent_count == 0
