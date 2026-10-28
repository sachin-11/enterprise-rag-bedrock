from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from app.services import guardrail_service


def _failing_client():
    client = MagicMock()
    client.apply_guardrail.side_effect = ClientError({"Error": {"Code": "ThrottlingException"}}, "ApplyGuardrail")
    return client


def _configure(monkeypatch, client, fail_mode):
    monkeypatch.setattr(guardrail_service.settings, "bedrock_guardrail_id", "gr-123")
    monkeypatch.setattr(guardrail_service.settings, "guardrail_fail_mode", fail_mode)
    monkeypatch.setattr(guardrail_service, "get_bedrock_runtime_client", lambda: client)


def test_fails_open_by_default_on_aws_error(monkeypatch):
    _configure(monkeypatch, _failing_client(), "open")

    result = guardrail_service.check_content("hello", "INPUT")

    assert result.intervened is False
    assert result.output_text == "hello"


def test_fails_closed_when_configured(monkeypatch):
    _configure(monkeypatch, _failing_client(), "closed")

    result = guardrail_service.check_content("hello", "OUTPUT")

    assert result.intervened is True
    assert result.output_text == guardrail_service.FAIL_CLOSED_MESSAGE


def test_reports_intervention_from_guardrail(monkeypatch):
    client = MagicMock()
    client.apply_guardrail.return_value = {"action": "GUARDRAIL_INTERVENED", "outputs": [{"text": "blocked"}]}
    _configure(monkeypatch, client, "open")

    result = guardrail_service.check_content("bad", "INPUT")

    assert result.intervened is True
    assert result.output_text == "blocked"


def test_noop_without_guardrail_id(monkeypatch):
    monkeypatch.setattr(guardrail_service.settings, "bedrock_guardrail_id", "")

    result = guardrail_service.check_content("hello", "INPUT")

    assert result.intervened is False
