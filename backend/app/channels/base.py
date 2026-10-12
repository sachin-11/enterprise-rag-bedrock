from pydantic import BaseModel, ConfigDict, Field

from app.models.context import ChannelType


class ChannelMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: ChannelType
    event_id: str = Field(min_length=1)
    external_tenant_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ChannelResponse(BaseModel):
    text: str
    correlation_id: str
