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


@dataclass
class GuardrailResult:
    intervened: bool
    # Guardrail's own message (e.g. blocked/masked text) when it intervened,
    # otherwise the original text unchanged.
    output_text: str


@traceable(name="check_content", run_type="tool")
def check_content(text: str, source: GuardrailSource) -> GuardrailResult:
    """Screen `text` with the configured Bedrock Guardrail.

    Fails open (reports no intervention) on any AWS error, since a guardrail
    outage should degrade to "unfiltered" rather than take the whole chat
    endpoint down — this is a defense-in-depth layer, not the only one.
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
        logger.error("Bedrock Guardrail check failed (%s), failing open: %s", source, exc)
        return GuardrailResult(intervened=False, output_text=text)

    intervened = response.get("action") == "GUARDRAIL_INTERVENED"
    outputs = response.get("outputs", [])
    output_text = outputs[0]["text"] if intervened and outputs else text
    return GuardrailResult(intervened=intervened, output_text=output_text)
