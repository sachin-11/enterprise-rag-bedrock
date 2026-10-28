"""Optional content moderation via AWS Bedrock Guardrails.

Uses the standalone `apply_guardrail` API, which screens arbitrary text
independent of which model generated it — so it works for both the user's
raw query (source="INPUT") and the OpenAI-generated answer (source="OUTPUT").

No-op when BEDROCK_GUARDRAIL_ID is unset: `check_content` always reports no
intervention, so deployments without a provisioned guardrail behave exactly
as before this module existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from botocore.exceptions import BotoCoreError, ClientError
from langsmith import traceable

from app.core.aws_clients import get_bedrock_runtime_client
from app.core.config import settings

logger = logging.getLogger(__name__)

GuardrailSource = Literal["INPUT", "OUTPUT"]

FAIL_CLOSED_MESSAGE = "Content screening is temporarily unavailable, so this request could not be processed."


@dataclass
class GuardrailResult:
    intervened: bool
    # Guardrail's own message (e.g. blocked/masked text) when it intervened,
    # otherwise the original text unchanged.
    output_text: str


@traceable(name="check_content", run_type="tool")
def check_content(text: str, source: GuardrailSource) -> GuardrailResult:
    """Screen `text` with the configured Bedrock Guardrail.

    On any AWS error, behavior follows GUARDRAIL_FAIL_MODE: "open" (default)
    reports no intervention, so a guardrail outage degrades to "unfiltered"
    rather than taking the chat endpoint down; "closed" blocks the text.
    """
    if not settings.bedrock_guardrail_id:
        return GuardrailResult(intervened=False, output_text=text)

    client = get_bedrock_runtime_client()
    try:
        response = client.apply_guardrail(
            guardrailIdentifier=settings.bedrock_guardrail_id,
            guardrailVersion=settings.bedrock_guardrail_version,
            source=source,
            content=[{"text": {"text": text}}],
        )
    except (ClientError, BotoCoreError) as exc:
        if settings.guardrail_fail_mode == "closed":
            logger.error("Bedrock Guardrail check failed (%s), failing closed: %s", source, exc)
            return GuardrailResult(intervened=True, output_text=FAIL_CLOSED_MESSAGE)
        logger.error("Bedrock Guardrail check failed (%s), failing open: %s", source, exc)
        return GuardrailResult(intervened=False, output_text=text)

    intervened = response.get("action") == "GUARDRAIL_INTERVENED"
    outputs = response.get("outputs", [])
    output_text = outputs[0]["text"] if intervened and outputs else text
    return GuardrailResult(intervened=intervened, output_text=output_text)
