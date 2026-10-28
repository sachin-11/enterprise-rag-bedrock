"""Create the Bedrock Guardrail this app screens queries and answers with.

Run once per account/region, then put the printed id/version in .env:

    python -m scripts.create_guardrail

Needs bedrock:CreateGuardrail and bedrock:CreateGuardrailVersion. Nothing is
created unless you run it. The version is numbered (not DRAFT) so a later edit
of the guardrail can't change production behavior unannounced.
"""

from __future__ import annotations

from app.core.aws_clients import get_boto3_session

BLOCKED_MESSAGE = "Sorry, I can't help with that request."

# Prompt-attack screening only applies to INPUT; the API requires OUTPUT
# strength to be NONE for it.
CONTENT_FILTERS = [
    {"type": "HATE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
    {"type": "INSULTS", "inputStrength": "HIGH", "outputStrength": "HIGH"},
    {"type": "SEXUAL", "inputStrength": "HIGH", "outputStrength": "HIGH"},
    {"type": "VIOLENCE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
    {"type": "MISCONDUCT", "inputStrength": "HIGH", "outputStrength": "HIGH"},
    {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"},
]

# BLOCK, not ANONYMIZE, for credentials/ID numbers: a retrieved document that
# contains one should not be echoed back even in masked form.
PII_ENTITIES = [
    {"type": "AWS_ACCESS_KEY", "action": "BLOCK"},
    {"type": "AWS_SECRET_KEY", "action": "BLOCK"},
    {"type": "PASSWORD", "action": "BLOCK"},
    {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "BLOCK"},
    {"type": "IN_AADHAAR", "action": "BLOCK"},
    {"type": "IN_PERMANENT_ACCOUNT_NUMBER", "action": "BLOCK"},
    {"type": "EMAIL", "action": "ANONYMIZE"},
    {"type": "PHONE", "action": "ANONYMIZE"},
]


def main() -> None:
    client = get_boto3_session().client("bedrock")
    created = client.create_guardrail(
        name="enterprise-rag-guardrail",
        description="Screens RAG user queries and generated answers.",
        contentPolicyConfig={"filtersConfig": CONTENT_FILTERS},
        sensitiveInformationPolicyConfig={"piiEntitiesConfig": PII_ENTITIES},
        blockedInputMessaging=BLOCKED_MESSAGE,
        blockedOutputsMessaging=BLOCKED_MESSAGE,
    )
    guardrail_id = created["guardrailId"]
    version = client.create_guardrail_version(guardrailIdentifier=guardrail_id)["version"]
    print(f"BEDROCK_GUARDRAIL_ID={guardrail_id}")
    print(f"BEDROCK_GUARDRAIL_VERSION={version}")


if __name__ == "__main__":
    main()
