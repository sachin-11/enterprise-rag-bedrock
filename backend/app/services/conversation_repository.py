from functools import lru_cache

from app.core.config import settings
from app.db.session import get_session_factory
from app.repositories.dynamodb_conversation import DynamoDBConversationRepository
from app.repositories.postgres_conversation import PostgresConversationRepository


@lru_cache
def get_conversation_repository():
    if settings.conversation_store_backend == "postgres":
        return PostgresConversationRepository(get_session_factory())
    return DynamoDBConversationRepository()


def create_conversation(context, title): return get_conversation_repository().create(context, title)
def list_conversations(context): return get_conversation_repository().list(context)
def get_conversation(context, conversation_id): return get_conversation_repository().get(context, conversation_id)
def append_message(context, conversation_id, role, content, sources, run_id=None): return get_conversation_repository().append_message(context, conversation_id, role, content, sources, run_id)
def get_history_summary(context, conversation_id): return get_conversation_repository().get_summary(context, conversation_id)
def update_history_summary(context, conversation_id, summary, summarized_through): return get_conversation_repository().update_summary(context, conversation_id, summary, summarized_through)
def touch_conversation(context, conversation_id): return get_conversation_repository().touch(context, conversation_id)
def delete_conversation(context, conversation_id): return get_conversation_repository().delete(context, conversation_id)
