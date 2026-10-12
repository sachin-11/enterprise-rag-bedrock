"""Post-retrieval authorization before evidence reaches the answer model."""

from __future__ import annotations

import logging
from typing import Protocol, TypeVar

from app.models.context import RequestContext

logger = logging.getLogger(__name__)


class MetadataResult(Protocol):
    metadata: dict
    chunk_id: str


ResultT = TypeVar("ResultT", bound=MetadataResult)


def filter_authorized_results(results: list[ResultT], context: RequestContext) -> list[ResultT]:
    """Discard chunks without verified tenant and document access metadata.

    Admins may read all documents in their tenant. Members may read only
    documents they uploaded or documents explicitly shared with their user ID.
    Unknown or malformed metadata fails closed.
    """
    authorized: list[ResultT] = []
    for result in results:
        metadata = result.metadata
        reason: str | None = None
        if not isinstance(metadata, dict) or metadata.get("tenant_id") != context.tenant_id:
            reason = "missing_or_mismatched_tenant"
        elif not context.is_admin:
            shared_with = metadata.get("shared_with", [])
            if not isinstance(shared_with, list) or not all(isinstance(user, str) for user in shared_with):
                reason = "malformed_document_acl"
            elif metadata.get("uploaded_by") != context.user_id and context.user_id not in shared_with:
                reason = "document_access_denied"
        if reason:
            logger.warning(
                "retrieval_chunk_denied tenant=%s user=%s correlation=%s chunk=%s reason=%s",
                context.tenant_id,
                context.user_id,
                context.correlation_id,
                result.chunk_id,
                reason,
            )
            continue
        authorized.append(result)
    return authorized
