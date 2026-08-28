"""Triggers and tracks Bedrock Knowledge Base ingestion jobs.

Assumes the Knowledge Base, its data source, and the underlying OpenSearch
Serverless collection already exist (created via the AWS console) — this
module only starts/polls ingestion jobs, it never provisions infrastructure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from botocore.exceptions import BotoCoreError, ClientError
from langsmith import traceable

from app.core.aws_clients import get_bedrock_agent_client, get_bedrock_agent_runtime_client

TERMINAL_STATUSES = {"COMPLETE", "FAILED"}
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 300.0
TENANT_ID_METADATA_KEY = "tenant_id"
UPLOADED_BY_METADATA_KEY = "uploaded_by"
SHARED_WITH_METADATA_KEY = "shared_with"


@dataclass
class KBRetrievalResult:
    chunk_id: str
    text: str
    score: float
    doc_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BedrockKBError(Exception):
    """Raised when a Bedrock Knowledge Base sync or retrieve operation fails."""


def _client_error_message(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Message", str(exc))


def _resolve_data_source_id(kb_id: str, data_source_id: Optional[str] = None) -> str:
    if data_source_id:
        return data_source_id

    client = get_bedrock_agent_client()
    try:
        response = client.list_data_sources(knowledgeBaseId=kb_id)
    except ClientError as exc:
        raise BedrockKBError(
            f"Failed to list data sources for knowledge base '{kb_id}': {_client_error_message(exc)}"
        ) from exc
    except BotoCoreError as exc:
        raise BedrockKBError(f"Failed to reach AWS while listing data sources for '{kb_id}': {exc}") from exc

    summaries = response.get("dataSourceSummaries", [])
    if not summaries:
        raise BedrockKBError(f"Knowledge base '{kb_id}' has no data sources configured.")
    if len(summaries) > 1:
        ids = [s["dataSourceId"] for s in summaries]
        raise BedrockKBError(
            f"Knowledge base '{kb_id}' has multiple data sources ({ids}); pass data_source_id explicitly."
        )
    return summaries[0]["dataSourceId"]


def sync_knowledge_base(
    kb_id: str,
    data_source_id: Optional[str] = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Start an ingestion job for kb_id and block until it completes or fails.

    This is a synchronous, blocking call (it sleeps between polls) — callers
    on an async code path should run it in a thread pool rather than await
    it directly, or it will stall the event loop.
    """
    client = get_bedrock_agent_client()
    resolved_data_source_id = _resolve_data_source_id(kb_id, data_source_id)

    try:
        start_response = client.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=resolved_data_source_id,
        )
    except ClientError as exc:
        raise BedrockKBError(
            f"Failed to start ingestion job for knowledge base '{kb_id}': {_client_error_message(exc)}"
        ) from exc
    except BotoCoreError as exc:
        raise BedrockKBError(f"Failed to reach AWS while starting ingestion for '{kb_id}': {exc}") from exc

    job = start_response["ingestionJob"]
    job_id = job["ingestionJobId"]
    status = job["status"]

    deadline = time.monotonic() + timeout_seconds
    while status not in TERMINAL_STATUSES:
        if time.monotonic() >= deadline:
            raise BedrockKBError(
                f"Ingestion job '{job_id}' for knowledge base '{kb_id}' did not finish within "
                f"{timeout_seconds:.0f}s (last status: {status})."
            )
        time.sleep(poll_interval_seconds)
        try:
            get_response = client.get_ingestion_job(
                knowledgeBaseId=kb_id,
                dataSourceId=resolved_data_source_id,
                ingestionJobId=job_id,
            )
        except ClientError as exc:
            raise BedrockKBError(
                f"Failed to poll ingestion job '{job_id}' for knowledge base '{kb_id}': {_client_error_message(exc)}"
            ) from exc
        except BotoCoreError as exc:
            raise BedrockKBError(f"Failed to reach AWS while polling ingestion job '{job_id}': {exc}") from exc
        job = get_response["ingestionJob"]
        status = job["status"]

    return {
        "knowledge_base_id": kb_id,
        "data_source_id": resolved_data_source_id,
        "ingestion_job_id": job_id,
        "status": status,
        "statistics": job.get("statistics"),
        "failure_reasons": job.get("failureReasons"),
    }


def get_kb_status(kb_id: str, data_source_id: Optional[str] = None) -> dict[str, Any]:
    """Return the status of the most recently started ingestion job for kb_id."""
    client = get_bedrock_agent_client()
    resolved_data_source_id = _resolve_data_source_id(kb_id, data_source_id)

    try:
        response = client.list_ingestion_jobs(
            knowledgeBaseId=kb_id,
            dataSourceId=resolved_data_source_id,
            sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
            maxResults=1,
        )
    except ClientError as exc:
        raise BedrockKBError(
            f"Failed to list ingestion jobs for knowledge base '{kb_id}': {_client_error_message(exc)}"
        ) from exc
    except BotoCoreError as exc:
        raise BedrockKBError(f"Failed to reach AWS while listing ingestion jobs for '{kb_id}': {exc}") from exc

    summaries = response.get("ingestionJobSummaries", [])
    if not summaries:
        return {
            "knowledge_base_id": kb_id,
            "data_source_id": resolved_data_source_id,
            "ingestion_job_id": None,
            "status": "NEVER_SYNCED",
        }

    latest = summaries[0]
    return {
        "knowledge_base_id": kb_id,
        "data_source_id": resolved_data_source_id,
        "ingestion_job_id": latest["ingestionJobId"],
        "status": latest["status"],
        "started_at": latest.get("startedAt"),
        "updated_at": latest.get("updatedAt"),
    }


def _build_retrieval_filter(tenant_id: str, user_id: str, is_admin: bool) -> dict:
    tenant_filter = {"equals": {"key": TENANT_ID_METADATA_KEY, "value": tenant_id}}
    if is_admin:
        # Admins see every document in their org regardless of per-document
        # sharing — this is what makes "admin uploads, then chooses who to
        # share with" work, and stays correct even if a future promote-flow
        # creates a second admin whose uploaded_by differs from the first.
        return tenant_filter

    # Non-admins only see documents they uploaded themselves or that were
    # explicitly shared with them. Documents uploaded before this feature
    # shipped have no uploaded_by/shared_with attribute at all (known v1
    # gap, not solved) — they fail BOTH clauses below and become invisible
    # to non-admins until re-uploaded, which is the fail-safe direction.
    return {
        "andAll": [
            tenant_filter,
            {
                "orAll": [
                    {"equals": {"key": UPLOADED_BY_METADATA_KEY, "value": user_id}},
                    {"listContains": {"key": SHARED_WITH_METADATA_KEY, "value": user_id}},
                ]
            },
        ]
    }


@traceable(name="retrieve_from_kb", run_type="retriever")
def retrieve_from_kb(
    query_text: str,
    kb_id: str,
    tenant_id: str,
    user_id: str,
    is_admin: bool,
    top_k: int = 20,
) -> list[KBRetrievalResult]:
    """Query kb_id's managed vector store, scoped to tenant_id and, for
    non-admins, to documents they uploaded or that were explicitly shared
    with them.

    SECURITY / tenant isolation boundary: the tenant_id clause is the *only*
    thing standing between tenants sharing one Knowledge Base and one tenant
    reading another tenant's documents. It relies on every ingested S3
    object having a `<key>.metadata.json` sidecar with a `tenant_id`
    attribute (written by the upload endpoint) — without that sidecar, a
    chunk simply won't match this filter and will be silently excluded,
    which is the fail-safe direction (under-retrieval, never cross-tenant
    leakage). The uploaded_by/shared_with clause is the equivalent boundary
    for per-document sharing within a tenant — same fail-safe direction.
    """
    client = get_bedrock_agent_runtime_client()
    try:
        response = client.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query_text},
            # "Managed" (Quick-create) knowledge bases require managedSearchConfiguration,
            # not vectorSearchConfiguration (which is only for self-managed vector stores
            # like OpenSearch/Pinecone) — confirmed against this KB's actual type via
            # `aws bedrock-agent get-knowledge-base`. It always runs hybrid search
            # internally, so there's no overrideSearchType to set here.
            retrievalConfiguration={
                "managedSearchConfiguration": {
                    "numberOfResults": top_k,
                    "filter": _build_retrieval_filter(tenant_id, user_id, is_admin),
                }
            },
        )
    except ClientError as exc:
        raise BedrockKBError(
            f"Failed to retrieve from knowledge base '{kb_id}': {_client_error_message(exc)}"
        ) from exc
    except BotoCoreError as exc:
        raise BedrockKBError(f"Failed to reach AWS while retrieving from '{kb_id}': {exc}") from exc

    results = []
    for result in response.get("retrievalResults", []):
        metadata = result.get("metadata", {})
        s3_uri = result.get("location", {}).get("s3Location", {}).get("uri", "")
        doc_name = metadata.get("_document_title") or (s3_uri.rsplit("/", 1)[-1] if s3_uri else "unknown")
        chunk_id = metadata.get("_chunk_id") or s3_uri
        results.append(
            KBRetrievalResult(
                chunk_id=chunk_id,
                text=result.get("content", {}).get("text", ""),
                score=result.get("score") or 0.0,
                doc_name=doc_name,
                metadata=metadata,
            )
        )
    return results
