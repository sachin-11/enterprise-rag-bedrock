from __future__ import annotations

from app.models.chat import SourceCitation
from app.models.context import RequestContext
from app.services import conversation_store


class DynamoDBConversationRepository:
    def create(self, context: RequestContext, title: str):
        return conversation_store.create_conversation(context.user_id, context.tenant_id, title)

    def list(self, context: RequestContext):
        return conversation_store.list_conversations(context.user_id)

    def get(self, context: RequestContext, conversation_id: str):
        return conversation_store.get_conversation(context.user_id, conversation_id)

    def append_message(self, context, conversation_id, role, content, sources: list[SourceCitation], run_id=None):
        conversation = conversation_store.get_conversation(context.user_id, conversation_id)
        if conversation is None:
            raise LookupError("Conversation not found.")
        conversation_store.append_message(conversation_id, role, content, sources, run_id)

    def get_summary(self, context, conversation_id):
        return conversation_store.get_history_summary(context.user_id, conversation_id)

    def update_summary(self, context, conversation_id, summary, summarized_through):
        conversation_store.update_history_summary(context.user_id, conversation_id, summary, summarized_through)

    def touch(self, context, conversation_id):
        conversation_store.touch_conversation(context.user_id, conversation_id)

    def delete(self, context, conversation_id):
        return conversation_store.delete_conversation(context.user_id, conversation_id)
