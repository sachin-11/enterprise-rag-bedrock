"""Deterministic first-pass request routing.

The router intentionally does not trust an LLM to authorize actions. A later
classifier may suggest a route, but its output must still validate against
``RouteType`` and pass these server-side policy checks.
"""

import re

from app.models.orchestration import RouteType

_GREETING = re.compile(r"^\s*(hi|hello|hey|namaste|thanks|thank you)[!.\s]*$", re.IGNORECASE)
_ESCALATE = re.compile(r"\b(human|agent|representative|support person|insaan)\b", re.IGNORECASE)
_WRITE_TOOL = re.compile(
    r"\b(create|raise|submit|book|cancel|update)\b.*\b(ticket|leave|request|case)\b", re.IGNORECASE
)
_READ_TOOL = re.compile(r"\b(check|show|what is|how much)\b.*\b(balance|ticket status|leave balance)\b", re.IGNORECASE)


def route_request(message: str) -> RouteType:
    text = message.strip()
    if _GREETING.fullmatch(text):
        return RouteType.DIRECT
    if _ESCALATE.search(text):
        return RouteType.ESCALATE
    if _WRITE_TOOL.search(text):
        return RouteType.TOOL_WRITE
    if _READ_TOOL.search(text):
        return RouteType.TOOL_READ
    return RouteType.RAG
