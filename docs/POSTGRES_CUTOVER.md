# DynamoDB conversation cutover

The PostgreSQL conversation repository is optional and defaults to DynamoDB.
The backfill reads the configured DynamoDB conversation and message tables and
writes only to PostgreSQL. It preserves conversation IDs, message contents,
citations, timestamps and history summaries. It can be rerun after an
interrupted import. It does not delete DynamoDB records.

## Preparation

1. Take a PostgreSQL backup and confirm that the database is dedicated to this
   application. Keep `CONVERSATION_STORE_BACKEND=dynamodb` on every API instance.
2. Set `DATABASE_URL` and both `DYNAMODB_*_TABLE` settings in the backend
   environment. Run `alembic upgrade head` from `backend/` to create the schema.
3. Run `python -m scripts.backfill_conversations` from `backend/`. A successful
   run prints verified conversation and message counts. Any conflict or count
   mismatch exits nonzero. Investigate it before switching stores.

## Final sync and cutover

1. Pause conversation create, append, summary update and delete writes on all
   API instances and background workers. DynamoDB scans cannot provide one
   cross-table snapshot while writes continue.
2. Rerun `python -m scripts.backfill_conversations`, then run
   `python -m scripts.backfill_conversations --verify-only`. The verification
   checks conversation ownership and metadata, each conversation's message
   count and content digest, and the complete set of conversation IDs.
3. Change `CONVERSATION_STORE_BACKEND=postgres` on all API instances in one
   controlled deployment. Resume writes only after every instance uses the
   new setting. Smoke test create, list, reply and delete for separate tenants.

## Rollback

Before resuming writes, return all instances to
`CONVERSATION_STORE_BACKEND=dynamodb`; the original DynamoDB data remains
intact. Once writes have resumed on PostgreSQL, this switch alone would lose
new conversations and edits. At that point pause writes and reconcile the new
PostgreSQL records back into DynamoDB before switching. Keep the old tables
until that rollback path has been tested and the retention period is complete.

The import creates placeholder user email addresses for users absent from
PostgreSQL. Authentication still comes from verified Cognito identity; a later
identity sync can populate profile fields. The migration does not copy active
sessions or pending actions.
