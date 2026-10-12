"""Run from backend/: python -m scripts.backfill_conversations [--verify-only]."""

import argparse

from app.core.aws_clients import get_dynamodb_resource
from app.core.config import settings
from app.db.conversation_backfill import backfill_conversations
from app.db.session import get_session_factory


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill DynamoDB chat history into PostgreSQL")
    parser.add_argument("--verify-only", action="store_true", help="Compare without writing")
    args = parser.parse_args()
    dynamodb = get_dynamodb_resource()
    counts = backfill_conversations(
        dynamodb.Table(settings.dynamodb_conversations_table),
        dynamodb.Table(settings.dynamodb_messages_table),
        get_session_factory(),
        verify_only=args.verify_only,
    )
    print(f"Verified {counts['conversations']} conversations and {counts['messages']} messages")


if __name__ == "__main__":
    main()
