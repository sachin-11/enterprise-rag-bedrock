from fastapi import APIRouter, Depends, HTTPException, status

from app.core.dependencies import get_request_context
from app.models.context import RequestContext
from app.models.conversation import ConversationDetail, ConversationListResponse
from app.services.conversation_repository import delete_conversation, get_conversation, list_conversations

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=ConversationListResponse, status_code=status.HTTP_200_OK)
async def list_conversations_endpoint(
    current_user: RequestContext = Depends(get_request_context),
) -> ConversationListResponse:
    return ConversationListResponse(conversations=list_conversations(current_user))


@router.get("/{conversation_id}", response_model=ConversationDetail, status_code=status.HTTP_200_OK)
async def get_conversation_endpoint(
    conversation_id: str,
    current_user: RequestContext = Depends(get_request_context),
) -> ConversationDetail:
    conversation = get_conversation(current_user, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No conversation found with id '{conversation_id}'",
        )
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation_endpoint(
    conversation_id: str,
    current_user: RequestContext = Depends(get_request_context),
) -> None:
    deleted = delete_conversation(current_user, conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No conversation found with id '{conversation_id}'",
        )
