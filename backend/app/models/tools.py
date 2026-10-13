"""Strict business-tool argument and result contracts."""

from pydantic import BaseModel, ConfigDict, Field


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LeaveBalanceInput(ToolArguments):
    pass


class LeaveBalanceOutput(BaseModel):
    remaining_days: float = Field(ge=0)
    unit: str = "days"


class CreateTicketInput(ToolArguments):
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=1, max_length=4000)


class CreateTicketOutput(BaseModel):
    ticket_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
