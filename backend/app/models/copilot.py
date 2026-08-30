from typing import Literal

from pydantic import BaseModel


class CopilotMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CopilotRequest(BaseModel):
    message: str
    history: list[CopilotMessage] = []


class CopilotToolCall(BaseModel):
    tool_name: str
    summary: str


class CopilotAction(BaseModel):
    action_type: Literal["retry", "email"]
    summary: str


class CopilotResponse(BaseModel):
    answer: str
    tool_calls: list[CopilotToolCall]
    actions_taken: list[CopilotAction]
