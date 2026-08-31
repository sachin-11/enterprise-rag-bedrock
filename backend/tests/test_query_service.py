from unittest.mock import MagicMock

import pytest

from app.services import query_service


class _FakeResponse:
    def __init__(self, content):
        self.content = content


def _mock_llm(monkeypatch, *, return_value=None, side_effect=None):
    llm = MagicMock()
    if side_effect is not None:
        llm.invoke.side_effect = side_effect
    else:
        llm.invoke.return_value = _FakeResponse(return_value)
    monkeypatch.setattr(query_service, "_get_llm", lambda temperature=0.0: llm)
    return llm


def test_rewrite_query_resolves_pronoun_using_recent_history(monkeypatch):
    llm = _mock_llm(monkeypatch, return_value="What is the pricing for OpenSearch Serverless?")

    chat_history = [
        {"role": "user", "content": "We're using OpenSearch Serverless for our vector store."},
        {"role": "assistant", "content": "That's a good choice for a fully managed vector search backend."},
    ]

    result = query_service.rewrite_query("What about its pricing?", chat_history)

    assert result == "What is the pricing for OpenSearch Serverless?"
    llm.invoke.assert_called_once()


def test_rewrite_query_only_uses_last_three_turns(monkeypatch):
    llm = _mock_llm(monkeypatch, return_value="standalone question")

    # MAX_HISTORY_TURNS=3 means the last 3 *turns* (user+assistant pairs) —
    # 6 messages — not the last 3 messages, since a follow-up question must
    # never be split from its own answer.
    chat_history = [
        {"role": "user", "content": "TURN_1_SHOULD_BE_DROPPED"},
        {"role": "assistant", "content": "TURN_1_SHOULD_BE_DROPPED_TOO"},
        {"role": "user", "content": "TURN_2_KEPT"},
        {"role": "assistant", "content": "TURN_2_KEPT_TOO"},
        {"role": "user", "content": "TURN_3_KEPT"},
        {"role": "assistant", "content": "TURN_3_KEPT_TOO"},
        {"role": "user", "content": "TURN_4_KEPT"},
        {"role": "assistant", "content": "TURN_4_KEPT_TOO"},
    ]

    query_service.rewrite_query("follow up", chat_history)

    sent_messages = llm.invoke.call_args[0][0]
    human_prompt = sent_messages[-1].content
    assert "TURN_1_SHOULD_BE_DROPPED" not in human_prompt
    assert "TURN_1_SHOULD_BE_DROPPED_TOO" not in human_prompt
    assert "TURN_2_KEPT" in human_prompt
    assert "TURN_3_KEPT" in human_prompt
    assert "TURN_4_KEPT" in human_prompt


def test_rewrite_query_without_history_skips_llm_call(monkeypatch):
    llm = _mock_llm(monkeypatch, return_value="unused")

    result = query_service.rewrite_query("What is AWS Lambda?", [])

    assert result == "What is AWS Lambda?"
    llm.invoke.assert_not_called()


def test_rewrite_query_includes_history_summary_ahead_of_recent_turns(monkeypatch):
    llm = _mock_llm(monkeypatch, return_value="standalone question")

    chat_history = [
        {"role": "user", "content": "RECENT_TURN"},
        {"role": "assistant", "content": "RECENT_ANSWER"},
    ]

    query_service.rewrite_query("follow up", chat_history, history_summary="SUMMARY_OF_OLDER_TURNS")

    human_prompt = llm.invoke.call_args[0][0][-1].content
    assert "SUMMARY_OF_OLDER_TURNS" in human_prompt
    assert "RECENT_TURN" in human_prompt
    assert human_prompt.index("SUMMARY_OF_OLDER_TURNS") < human_prompt.index("RECENT_TURN")


def test_rewrite_query_falls_back_to_original_after_retry_exhausted(monkeypatch):
    llm = _mock_llm(monkeypatch, side_effect=[RuntimeError("throttled"), RuntimeError("throttled again")])

    chat_history = [{"role": "user", "content": "some earlier turn"}]
    result = query_service.rewrite_query("original follow-up question", chat_history)

    assert result == "original follow-up question"
    assert llm.invoke.call_count == 2  # one retry


def test_generate_hyde_passage_returns_passage_on_success(monkeypatch):
    passage = (
        "Bedrock Knowledge Bases connect your data sources to a vector store and handle "
        "chunking and embedding automatically. Retrieval can combine semantic and keyword "
        "search for better accuracy. They integrate directly with foundation models for RAG."
    )
    llm = _mock_llm(monkeypatch, return_value=passage)

    result = query_service.generate_hyde_passage("What is a Bedrock Knowledge Base?")

    assert result == passage
    llm.invoke.assert_called_once()


def test_generate_hyde_passage_returns_none_after_retry_exhausted(monkeypatch):
    llm = _mock_llm(monkeypatch, side_effect=[RuntimeError("timeout"), RuntimeError("timeout again")])

    result = query_service.generate_hyde_passage("What is a Bedrock Knowledge Base?")

    assert result is None
    assert llm.invoke.call_count == 2  # one retry


def test_summarize_history_folds_turns_into_previous_summary(monkeypatch):
    llm = _mock_llm(monkeypatch, return_value="Updated summary mentioning the vector store choice.")

    turns_to_fold = [
        {"role": "user", "content": "We're using OpenSearch Serverless for our vector store."},
        {"role": "assistant", "content": "That's a good choice for a fully managed vector search backend."},
    ]

    result = query_service.summarize_history("Previous summary text.", turns_to_fold)

    assert result == "Updated summary mentioning the vector store choice."
    human_prompt = llm.invoke.call_args[0][0][-1].content
    assert "Previous summary text." in human_prompt
    assert "OpenSearch Serverless" in human_prompt


def test_summarize_history_with_no_turns_skips_llm_call(monkeypatch):
    llm = _mock_llm(monkeypatch, return_value="unused")

    result = query_service.summarize_history("Existing summary.", [])

    assert result == "Existing summary."
    llm.invoke.assert_not_called()


def test_summarize_history_falls_back_to_existing_after_retry_exhausted(monkeypatch):
    llm = _mock_llm(monkeypatch, side_effect=[RuntimeError("throttled"), RuntimeError("throttled again")])

    turns_to_fold = [{"role": "user", "content": "some older turn"}]
    result = query_service.summarize_history("Existing summary.", turns_to_fold)

    assert result == "Existing summary."
    assert llm.invoke.call_count == 2  # one retry
