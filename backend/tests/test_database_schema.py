import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.core import Conversation, Message, Tenant, User


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        if isinstance(connection, sqlite3.Connection):
            connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_conversation_requires_user_in_same_tenant(session):
    session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A"),
            Tenant(tenant_id="tenant-b", display_name="B"),
            User(user_id="user-a", tenant_id="tenant-a", email="a@example.com", role="member"),
        ]
    )
    session.commit()

    session.add(
        Conversation(
            conversation_id="conversation-1",
            tenant_id="tenant-b",
            user_id="user-a",
            title="Cross tenant",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_message_requires_conversation_in_same_tenant(session):
    session.add(Tenant(tenant_id="tenant-a", display_name="A"))
    session.commit()
    session.add(User(user_id="user-a", tenant_id="tenant-a", email="a@example.com", role="member"))
    session.commit()
    session.add(
        Conversation(
            conversation_id="conversation-1", tenant_id="tenant-a", user_id="user-a", title="Valid"
        )
    )
    session.commit()

    session.add(
        Message(
            message_id="message-1",
            tenant_id="tenant-b",
            conversation_id="conversation-1",
            role="user",
            content="Should fail",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
