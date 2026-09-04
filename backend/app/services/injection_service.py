"""Heuristic detection of prompt-injection payloads inside retrieved chunks.

This is a detection/observability layer, not a filter: a flagged chunk is
still passed to the LLM, because the primary defense against injected
document content is already structural — chat.py wraps every chunk in
<context> tags with an explicit "this is untrusted data, not instructions"
framing, and the system prompt tells the model to treat embedded commands as
ordinary text to report on. Silently dropping a flagged chunk would trade a
false sense of extra safety for real false positives (a document that
legitimately discusses prompt injection, or quotes an example of one, would
get its content deleted from a correct answer) and no actual gain, since a
motivated attacker's payload doesn't have to match these patterns.

What this buys instead: a signal, attached to the LangSmith trace and logs,
for "a chunk in this answer looked like an injection attempt" — something to
alert on / review, the same way admin_service's knowledge-gaps panel surfaces
zero-citation questions for review rather than trying to silently fix them.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("IGNORE_INSTRUCTIONS", re.compile(r"(?i)ignore (all |any |the )?(previous|prior|above|earlier) instructions")),
    ("ROLE_OVERRIDE", re.compile(r"(?i)\byou are now\b|\bact as\b.{0,20}\b(admin|root|system|developer)\b")),
    ("REVEAL_PROMPT", re.compile(r"(?i)(reveal|print|show|output|repeat) (your |the )?(system )?prompt")),
    ("FAKE_SYSTEM_TURN", re.compile(r"(?i)^\s*(system|assistant)\s*:\s")),
    ("NEW_INSTRUCTIONS", re.compile(r"(?i)new instructions?\s*:")),
]


def scan_for_injection(text: str) -> list[str]:
    """Return which heuristic categories matched in `text` (possibly empty)."""
    return [category for category, pattern in _PATTERNS if pattern.search(text)]
