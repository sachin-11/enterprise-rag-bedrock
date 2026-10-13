"""Tool definitions keep validation and execution policy in backend code."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Awaitable, Callable

from pydantic import BaseModel

from app.models.context import RequestContext, Role
from app.models.tools import ToolArguments


class ToolKind(StrEnum):
    READ = "read"
    WRITE = "write"


class RiskLevel(StrEnum):
    LOW = "low"
    HIGH = "high"


ToolHandler = Callable[[RequestContext, ToolArguments], Awaitable[BaseModel]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    purpose: str
    kind: ToolKind
    risk: RiskLevel
    required_roles: frozenset[Role]
    input_model: type[ToolArguments]
    output_model: type[BaseModel]
    timeout_seconds: float
    max_attempts: int
    handler: ToolHandler

    def __post_init__(self) -> None:
        if not self.name or self.timeout_seconds <= 0 or self.max_attempts < 1 or not self.required_roles:
            raise ValueError("Tool definition has invalid execution policy")
