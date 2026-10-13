"""Post-retrieval authorization before evidence reaches the answer model."""

from __future__ import annotations

import logging
import json
from typing import Protocol, TypeVar
from urllib.parse import unquote, urlsplit

from app.core.aws_clients import get_s3_client
from app.core.config import settings
from app.models.context import RequestContext

logger = logging.getLogger(__name__)


class MetadataResult(Protocol):
    metadata: dict
    chunk_id: str
    s3_uri: str


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


def _sidecar_key(s3_uri: str, tenant_id: str) -> str | None:
    parsed = urlsplit(s3_uri)
    if parsed.scheme != "s3" or parsed.netloc != settings.s3_bucket_name or parsed.query or parsed.fragment:
        return None
    key = unquote(parsed.path.lstrip("/"))
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != tenant_id or any(part in ("", ".", "..") for part in parts):
        return None
    if key.endswith(".metadata.json"):
        return None
    return f"{key}.metadata.json"


def _current_acl(sidecar_key: str) -> dict:
    response = get_s3_client().get_object(Bucket=settings.s3_bucket_name, Key=sidecar_key)
    body = json.loads(response["Body"].read())
    attrs = body["metadataAttributes"]
    return {
        "tenant_id": attrs["tenant_id"]["value"]["stringValue"],
        "uploaded_by": attrs.get("uploaded_by", {}).get("value", {}).get("stringValue"),
        "shared_with": attrs.get("shared_with", {}).get("value", {}).get("stringListValue", []),
    }


def filter_current_document_acl(results: list[ResultT], context: RequestContext) -> list[ResultT]:
    """Recheck S3 source metadata so a revoked share takes effect before KB sync."""
    authorized: list[ResultT] = []
    acl_by_key: dict[str, dict | None] = {}
    for result in results:
        sidecar_key = _sidecar_key(result.s3_uri, context.tenant_id)
        reason: str | None = None
        if sidecar_key is None:
            reason = "invalid_source_uri"
        else:
            if sidecar_key not in acl_by_key:
                try:
                    acl_by_key[sidecar_key] = _current_acl(sidecar_key)
                except Exception:
                    logger.exception(
                        "retrieval_acl_read_failed tenant=%s correlation=%s chunk=%s",
                        context.tenant_id,
                        context.correlation_id,
                        result.chunk_id,
                    )
                    acl_by_key[sidecar_key] = None
            acl = acl_by_key[sidecar_key]
            if acl is None or acl.get("tenant_id") != context.tenant_id:
                reason = "missing_or_mismatched_current_acl"
            elif not context.is_admin:
                shared_with = acl.get("shared_with")
                if not isinstance(shared_with, list) or not all(isinstance(user, str) for user in shared_with):
                    reason = "malformed_current_acl"
                elif acl.get("uploaded_by") != context.user_id and context.user_id not in shared_with:
                    reason = "current_document_access_denied"
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
