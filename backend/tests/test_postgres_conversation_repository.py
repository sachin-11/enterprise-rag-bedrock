from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.context import ChannelType, CorrelationMetadata, RequestContext, Role
from app.repositories.postgres_conversation import PostgresConversationRepository


def _context(tenant, user):
    return RequestContext(user_id=user, email=f"{user}@example.com", tenant_id=tenant, role=Role.MEMBER, channel=ChannelType.WEB, correlation=CorrelationMetadata(correlation_id="c1"))


def _repository():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record): dbapi_connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return PostgresConversationRepository(sessionmaker(bind=engine, expire_on_commit=False))


def test_repository_never_returns_other_tenant_conversation():
    repository = _repository()
    owner = _context("tenant-a", "user-a")
    outsider = _context("tenant-b", "user-b")
    created = repository.create(owner, "Private conversation")

    assert repository.get(owner, created.conversation_id) is not None
    assert repository.get(outsider, created.conversation_id) is None
    assert repository.delete(outsider, created.conversation_id) is False


def test_repository_round_trip_and_cascade_delete():
    repository = _repository()
    context = _context("tenant-a", "user-a")
    created = repository.create(context, "Conversation")
    repository.append_message(context, created.conversation_id, "user", "hello", [])
    repository.update_summary(context, created.conversation_id, "summary", 1)

    detail = repository.get(context, created.conversation_id)
    assert [message.content for message in detail.messages] == ["hello"]
    assert repository.get_summary(context, created.conversation_id) == ("summary", 1)
    assert repository.delete(context, created.conversation_id) is True
    assert repository.get(context, created.conversation_id) is None
