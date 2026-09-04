"""Regex-based redaction of credentials/secrets and financial/government IDs
from text before it leaves this service (a generated answer, or — via
chunking_service in the legacy OpenSearch ingestion path — an indexed chunk).

Deliberately narrow and pattern-based rather than full NLP-based PII
detection (e.g. Presidio's NER recognizers for PERSON/LOCATION): those would
also flag ordinary names/places that are frequently the actual answer to a
RAG question ("who is the project lead" -> a name is the correct output, not
something to redact). This module only targets categories that are never a
legitimate part of an answer — API keys, private keys, SSNs, credit card
numbers — so it can run unconditionally with no tuning and no risk of
redacting a correct answer.

Not a substitute for not having secrets in source documents in the first
place; this is a last-resort net for exactly the failure mode where one
slips through anyway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("PRIVATE_KEY_BLOCK", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")),
    ("OPENAI_API_KEY", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("BEARER_TOKEN", re.compile(r"\bBearer\s+[A-Za-z0-9\-_\.]{20,}")),
    # Catches "api_key: <value>", "password=<value>", "secret = <value>" etc.
    # — the label is what makes this a credential rather than an ordinary
    # word, so both the label and value are redacted together.
    (
        "LABELED_SECRET",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token)\b\s*[:=]\s*['\"]?[A-Za-z0-9\-_\.\/+=]{8,}['\"]?"
        ),
    ),
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CREDIT_CARD", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b")),
]


@dataclass
class RedactionResult:
    text: str
    categories_found: list[str] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return bool(self.categories_found)


def redact_pii(text: str) -> RedactionResult:
    """Replace any matches of the patterns above with a `[REDACTED_<CATEGORY>]`
    marker. Returns which categories fired (never the matched values
    themselves) so a caller can log/flag without re-logging the secret."""
    categories_found: list[str] = []
    result = text
    for category, pattern in _PATTERNS:
        result, count = pattern.subn(f"[REDACTED_{category}]", result)
        if count:
            categories_found.append(category)
    return RedactionResult(text=result, categories_found=categories_found)
