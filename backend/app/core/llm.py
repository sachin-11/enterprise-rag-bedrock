"""Single place that decides which chat model backs every LLM call.

Callers ask for a model by *role* (rewrite, answer, copilot, ...) instead of a
vendor model ID, so switching OpenAI <-> Bedrock is one env var
(LLM_PROVIDER) and the two can be A/B compared with the same eval set.
"""

from __future__ import annotations

from typing import Literal, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

from app.core.config import settings

LLMRole = Literal["fast", "answer"]

OPENAI_MODEL_IDS: dict[LLMRole, str] = {"fast": "gpt-5.4-mini", "answer": "gpt-5.4-mini"}


def _model_id(role: LLMRole) -> str:
    if settings.llm_provider == "bedrock":
        return settings.bedrock_answer_model_id if role == "answer" else settings.bedrock_fast_model_id
    return OPENAI_MODEL_IDS[role]


def get_chat_llm(role: LLMRole, *, temperature: float, max_tokens: Optional[int] = None) -> BaseChatModel:
    """Build a chat model for `role`. Callers are expected to cache the result."""
    if settings.llm_provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        from app.core.aws_clients import get_bedrock_runtime_client

        # Reuses the shared bedrock-runtime client so credentials, region and
        # connection pooling match the guardrail calls.
        return ChatBedrockConverse(
            client=get_bedrock_runtime_client(),
            model_id=_model_id(role),
            region_name=settings.aws_region or None,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=settings.openai_api_key,
        model=_model_id(role),
        temperature=temperature,
        max_tokens=max_tokens,
    )


def message_text(message: BaseMessage) -> str:
    """Plain text of a model response, whatever the provider's content shape.

    OpenAI returns a str; Bedrock Converse returns a list of content blocks
    ({"type": "text", "text": ...}, plus non-text blocks such as tool_use) that
    must not be str()-ified into the answer.
    """
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)
