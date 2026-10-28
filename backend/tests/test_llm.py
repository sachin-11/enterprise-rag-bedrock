from langchain_core.messages import AIMessage

from app.core import llm
from app.core.config import settings


def test_message_text_handles_plain_string():
    assert llm.message_text(AIMessage(content="hello")) == "hello"


def test_message_text_keeps_only_text_blocks_from_bedrock_converse():
    message = AIMessage(
        content=[
            {"type": "text", "text": "Answer "},
            {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
            {"type": "text", "text": "[1]"},
        ]
    )
    assert llm.message_text(message) == "Answer [1]"


def test_get_chat_llm_builds_bedrock_converse(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "bedrock")
    monkeypatch.setattr(settings, "bedrock_answer_model_id", "test.answer-model")
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    monkeypatch.setattr(
        "app.core.aws_clients.get_bedrock_runtime_client", lambda: _FakeClient()
    )

    model = llm.get_chat_llm("answer", temperature=0.2, max_tokens=100)

    assert type(model).__name__ == "ChatBedrockConverse"
    assert model.model_id == "test.answer-model"
    assert model.max_tokens == 100


class _FakeClient:
    meta = type("Meta", (), {"region_name": "us-east-1"})()
