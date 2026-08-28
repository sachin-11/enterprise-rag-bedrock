# Enterprise RAG Bedrock

Enterprise-grade Retrieval-Augmented Generation application built on AWS Bedrock.

## Structure

```
enterprise-rag-bedrock/
├── backend/            FastAPI service (Poetry-managed)
│   └── app/
│       ├── api/        Route handlers
│       ├── services/   Business logic / integrations
│       ├── models/     Pydantic / data models
│       ├── core/       Config, settings
│       └── utils/      Shared helpers
├── frontend/            Next.js 14 (App Router) + TypeScript + Tailwind CSS
└── docker-compose.yml   Local dev orchestration
```

## Backend

Requires [Poetry](https://python-poetry.org/).

```bash
cd backend
poetry install
cp .env.example .env   # fill in your AWS / OpenSearch / Cohere values
poetry run uvicorn app.main:app --reload
```

Health check: `GET http://localhost:8000/health` → `{"status": "ok"}`

### Environment variables (`backend/.env`)

| Variable | Description |
|---|---|
| `AWS_REGION` | AWS region for Bedrock/S3 |
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `BEDROCK_KB_ID` | Bedrock Knowledge Base ID — retrieval source for `/chat/query` |
| `S3_BUCKET_NAME` | S3 bucket used for document storage (Bedrock KB's data source) |
| `OPENSEARCH_ENDPOINT` | Only needed if you switch to the standalone OpenSearch retrieval path (see below) — unused by default |
| `COHERE_API_KEY` | Cohere API key, used for reranking (`retrieval_service.rerank_with_cohere`) |
| `OPENAI_API_KEY` | Used for query rewriting, HyDE, and final answer generation |

### Retrieval architecture

`/chat/query` retrieves context from the **Bedrock Knowledge Base** (`retrieve_from_kb` in
`bedrock_kb_service.py`) — chunking, embedding, and indexing are handled internally by Bedrock
when you call `POST /documents/sync-kb`. Tenant isolation is enforced via a metadata filter:
`/documents/upload` writes a `<key>.metadata.json` sidecar tagging every file with its
`tenant_id`, and every retrieve call filters on it.

The codebase also has a **standalone hybrid retrieval path** (`chunking_service.py` →
`ingestion_service.py` → `retrieval_service.py`: dense + BM25 + RRF fusion against a self-managed
OpenSearch index, via `POST /documents/{id}/ingest`) that isn't currently wired into
`/chat/query`, kept as an alternative if you provision a real OpenSearch domain/collection later.

## Frontend

```bash
cd frontend
npm install
npm run dev
```

App runs at `http://localhost:3000`.

## Docker Compose (local dev)

```bash
docker compose up --build
```

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:3000`

## API endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health check |
| `POST /documents/upload` | Upload a PDF/DOCX to S3 (requires `X-Tenant-ID` header) |
| `POST /documents/sync-kb` | Trigger a Bedrock KB ingestion job (chunk + embed + index) |
| `POST /documents/{document_id}/ingest` | Alternative path: chunk/embed/index into a standalone OpenSearch index |
| `POST /chat/query` | Ask a question; returns a cited answer (requires `X-Tenant-ID` header) |
