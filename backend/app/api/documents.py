import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import openai
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from opensearchpy.exceptions import OpenSearchException

from app.core.aws_clients import get_s3_client
from app.core.config import settings
from app.core.dependencies import get_current_user, require_admin
from app.models.document import (
    DocumentDeleteResponse,
    DocumentListResponse,
    DocumentMetadata,
    DocumentSharingResponse,
    DocumentSummary,
    DocumentUploadResponse,
    IngestResponse,
    UpdateSharingRequest,
)
from app.models.kb_sync import SyncKBRequest, SyncKBResponse
from app.models.user import CurrentUser
from app.services import auth_service
from app.services.bedrock_kb_service import (
    SHARED_WITH_METADATA_KEY,
    TENANT_ID_METADATA_KEY,
    UPLOADED_BY_METADATA_KEY,
    BedrockKBError,
    sync_knowledge_base,
)
from app.services.document_store import delete_document, get_document, save_document
from app.services.ingestion_service import DocumentNotFoundError, ingest_document

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
_READ_CHUNK_SIZE = 1024 * 1024
CONTENT_HASH_METADATA_KEY = "content-hash"


def _tenant_prefix(tenant_id: str) -> str:
    return f"{tenant_id}/"


def _list_tenant_files(tenant_id: str) -> list[dict]:
    """List every uploaded file (not its .metadata.json sidecar) under a tenant's S3 prefix.

    S3 is the durable source of truth for "what documents does this tenant
    have" — the in-memory document_store only tracks bookkeeping for the
    separate OpenSearch ingest pipeline and doesn't survive a backend
    restart, so list/delete/dedup must not depend on it.
    """
    s3_client = get_s3_client()
    paginator = s3_client.get_paginator("list_objects_v2")
    files = []
    for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=_tenant_prefix(tenant_id)):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith(".metadata.json"):
                files.append(obj)
    return files


def _parse_document_id_and_filename(tenant_id: str, key: str) -> tuple[str, str] | None:
    # Keys are written as "{tenant_id}/{document_id}/{filename}" at upload time.
    rest = key[len(_tenant_prefix(tenant_id)) :]
    parts = rest.split("/", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _find_duplicate_by_hash(tenant_id: str, content_hash: str) -> dict | None:
    s3_client = get_s3_client()
    for obj in _list_tenant_files(tenant_id):
        head = s3_client.head_object(Bucket=settings.s3_bucket_name, Key=obj["Key"])
        if head.get("Metadata", {}).get(CONTENT_HASH_METADATA_KEY) == content_hash:
            parsed = _parse_document_id_and_filename(tenant_id, obj["Key"])
            if parsed:
                return {"document_id": parsed[0], "s3_key": obj["Key"]}
    return None


def _resolve_document_s3_key(tenant_id: str, document_id: str) -> str | None:
    """Finds the primary (non-sidecar) S3 key for a document_id, scoped to the
    caller's own tenant prefix — same "nothing to find outside your own
    prefix" isolation as delete_document_endpoint.
    """
    s3_client = get_s3_client()
    prefix = f"{tenant_id}/{document_id}/"
    response = s3_client.list_objects_v2(Bucket=settings.s3_bucket_name, Prefix=prefix)
    for obj in response.get("Contents", []):
        if not obj["Key"].endswith(".metadata.json"):
            return obj["Key"]
    return None


def _get_document_sharing(s3_key: str) -> dict:
    """Reads uploaded_by/shared_with from a document's .metadata.json sidecar.

    The sidecar is the single source of truth (it's what Bedrock KB's
    ingestion crawler actually reads for retrieval filtering — see
    bedrock_kb_service.py) — there's no second copy to keep in sync.
    Documents uploaded before this feature shipped have neither attribute;
    they come back empty, which is the fail-safe (invisible-to-non-admins)
    direction.
    """
    s3_client = get_s3_client()
    obj = s3_client.get_object(Bucket=settings.s3_bucket_name, Key=f"{s3_key}.metadata.json")
    body = json.loads(obj["Body"].read())
    attrs = body.get("metadataAttributes", {})
    uploaded_by = attrs.get(UPLOADED_BY_METADATA_KEY, {}).get("value", {}).get("stringValue", "")
    shared_with = attrs.get(SHARED_WITH_METADATA_KEY, {}).get("value", {}).get("stringListValue", [])
    return {"uploaded_by": uploaded_by, "shared_with": shared_with}


def _set_document_sharing(s3_key: str, user_ids: list[str]) -> dict:
    """Rewrites a document's shared_with list in its sidecar. Callers must
    trigger a background KB resync afterward — Bedrock KB only re-reads
    sidecar metadata during an ingestion job, not live.
    """
    s3_client = get_s3_client()
    sidecar_key = f"{s3_key}.metadata.json"
    obj = s3_client.get_object(Bucket=settings.s3_bucket_name, Key=sidecar_key)
    body = json.loads(obj["Body"].read())
    attrs = body.setdefault("metadataAttributes", {})
    uploaded_by = attrs.get(UPLOADED_BY_METADATA_KEY, {}).get("value", {}).get("stringValue", "")
    if user_ids:
        attrs[SHARED_WITH_METADATA_KEY] = {
            "value": {"type": "STRING_LIST", "stringListValue": user_ids},
            "includeForEmbedding": False,
        }
    else:
        # Bedrock KB rejects a STRING_LIST attribute with an empty
        # stringListValue (fails ingestion for the whole document, not just
        # this attribute) — "shared with no one" must be represented by
        # omitting the key entirely, same as at upload time.
        attrs.pop(SHARED_WITH_METADATA_KEY, None)
    s3_client.put_object(
        Bucket=settings.s3_bucket_name,
        Key=sidecar_key,
        Body=json.dumps(body),
        ContentType="application/json",
    )
    return {"uploaded_by": uploaded_by, "shared_with": user_ids}


def _filter_files_for_member(user_id: str, files: list[dict]) -> list[dict]:
    """Non-admins only see documents they uploaded or that were explicitly
    shared with them — reads every file's sidecar once per request, which is
    fine at this app's current scale (same class of tradeoff as the existing
    O(n) head_object loop in _find_duplicate_by_hash above).
    """
    visible = []
    for obj in files:
        sharing = _get_document_sharing(obj["Key"])
        if sharing["uploaded_by"] == user_id or user_id in sharing["shared_with"]:
            visible.append(obj)
    return visible


async def _read_with_limit(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds maximum allowed size of {max_bytes // (1024 * 1024)}MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_admin),
) -> DocumentUploadResponse:
    tenant_id = current_user.tenant_id

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is missing a filename")

    extension = Path(file.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{extension}'. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    contents = await _read_with_limit(file, MAX_FILE_SIZE_BYTES)
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    content_hash = hashlib.sha256(contents).hexdigest()
    try:
        existing = await run_in_threadpool(_find_duplicate_by_hash, tenant_id, content_hash)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to check for duplicate uploads: {exc}",
        ) from exc
    if existing is not None:
        return DocumentUploadResponse(
            document_id=existing["document_id"],
            s3_key=existing["s3_key"],
            status="uploaded",
            duplicate=True,
        )

    document_id = str(uuid.uuid4())
    s3_key = f"{tenant_id}/{document_id}/{file.filename}"

    s3_client = get_s3_client()
    try:
        s3_client.put_object(
            Bucket=settings.s3_bucket_name,
            Key=s3_key,
            Body=contents,
            ContentType=file.content_type or "application/octet-stream",
            Metadata={CONTENT_HASH_METADATA_KEY: content_hash},
        )
        # Bedrock KB reads this sidecar during ingestion and attaches these
        # attributes to every chunk derived from this file — that's what
        # retrieve_from_kb's filter matches against (see
        # bedrock_kb_service.py::_build_retrieval_filter). Without tenant_id,
        # the filter would exclude this document entirely (fails closed).
        # shared_with is OMITTED (not written as an empty list) — a newly
        # uploaded document is private to its uploader (always the admin,
        # since upload is admin-only) until explicitly shared via
        # PUT /documents/{id}/shares. Bedrock KB rejects a STRING_LIST
        # metadata attribute with an empty stringListValue outright
        # ("String list values cannot be empty" — confirmed via the KB's
        # CloudWatch ingestion logs after hitting exactly this), failing
        # ingestion for the WHOLE document, not just that attribute — so an
        # absent key is the only valid way to represent "shared with no one".
        s3_client.put_object(
            Bucket=settings.s3_bucket_name,
            Key=f"{s3_key}.metadata.json",
            Body=json.dumps(
                {
                    "metadataAttributes": {
                        TENANT_ID_METADATA_KEY: {
                            "value": {"type": "STRING", "stringValue": tenant_id},
                            "includeForEmbedding": False,
                        },
                        UPLOADED_BY_METADATA_KEY: {
                            "value": {"type": "STRING", "stringValue": current_user.user_id},
                            "includeForEmbedding": False,
                        },
                    }
                }
            ),
            ContentType="application/json",
        )
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to upload file to S3: {exc}",
        ) from exc

    metadata = DocumentMetadata(
        document_id=document_id,
        tenant_id=tenant_id,
        filename=file.filename,
        s3_key=s3_key,
        content_hash=content_hash,
        upload_timestamp=datetime.now(timezone.utc),
        status="uploaded",
    )
    save_document(metadata)

    return DocumentUploadResponse(document_id=document_id, s3_key=s3_key, status=metadata.status)


@router.get("", response_model=DocumentListResponse, status_code=status.HTTP_200_OK)
async def list_documents_endpoint(current_user: CurrentUser = Depends(get_current_user)) -> DocumentListResponse:
    tenant_id = current_user.tenant_id

    def _load() -> list[dict]:
        files = _list_tenant_files(tenant_id)
        if current_user.is_admin:
            return files  # admins see every document in their org, unfiltered
        return _filter_files_for_member(current_user.user_id, files)

    try:
        files = await run_in_threadpool(_load)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to list documents: {exc}",
        ) from exc

    summaries = []
    for obj in files:
        parsed = _parse_document_id_and_filename(tenant_id, obj["Key"])
        if parsed is None:
            continue
        document_id, filename = parsed
        summaries.append(
            DocumentSummary(
                document_id=document_id,
                filename=filename,
                upload_timestamp=obj["LastModified"],
                status="uploaded",
            )
        )

    summaries.sort(key=lambda doc: doc.upload_timestamp, reverse=True)
    return DocumentListResponse(documents=summaries)


@router.delete(
    "/{document_id}",
    response_model=DocumentDeleteResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_document_endpoint(
    document_id: str,
    current_user: CurrentUser = Depends(require_admin),
) -> DocumentDeleteResponse:
    tenant_id = current_user.tenant_id

    # Scoping the lookup to the caller's own tenant prefix is what prevents
    # one tenant from deleting another tenant's document_id even if they
    # guess a valid UUID — there's nothing to find outside their own prefix.
    prefix = f"{tenant_id}/{document_id}/"
    s3_client = get_s3_client()
    try:
        response = await run_in_threadpool(s3_client.list_objects_v2, Bucket=settings.s3_bucket_name, Prefix=prefix)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to look up document: {exc}",
        ) from exc

    keys = [obj["Key"] for obj in response.get("Contents", [])]
    if not keys:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No document found with id '{document_id}'")

    try:
        for key in keys:
            s3_client.delete_object(Bucket=settings.s3_bucket_name, Key=key)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to delete file from S3: {exc}",
        ) from exc

    # Best-effort: also clears any bookkeeping left by the separate
    # OpenSearch ingest pipeline (app/services/ingestion_service.py), which
    # is keyed off this same in-memory store. A no-op if it was never there.
    delete_document(document_id)

    return DocumentDeleteResponse(document_id=document_id, deleted=True)


@router.get(
    "/{document_id}/shares",
    response_model=DocumentSharingResponse,
    status_code=status.HTTP_200_OK,
)
async def get_document_shares(
    document_id: str,
    current_user: CurrentUser = Depends(require_admin),
) -> DocumentSharingResponse:
    tenant_id = current_user.tenant_id

    s3_key = await run_in_threadpool(_resolve_document_s3_key, tenant_id, document_id)
    if s3_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No document found with id '{document_id}'")

    try:
        sharing = await run_in_threadpool(_get_document_sharing, s3_key)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to read document sharing: {exc}",
        ) from exc

    return DocumentSharingResponse(
        document_id=document_id, uploaded_by=sharing["uploaded_by"], shared_with=sharing["shared_with"]
    )


@router.put(
    "/{document_id}/shares",
    response_model=DocumentSharingResponse,
    status_code=status.HTTP_200_OK,
)
async def update_document_shares(
    document_id: str,
    payload: UpdateSharingRequest,
    current_user: CurrentUser = Depends(require_admin),
) -> DocumentSharingResponse:
    tenant_id = current_user.tenant_id

    s3_key = await run_in_threadpool(_resolve_document_s3_key, tenant_id, document_id)
    if s3_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No document found with id '{document_id}'")

    # Same membership check as suspend_user's _require_org_member — stops an
    # admin from sharing a document with a sub outside their own org.
    try:
        valid_subs = {member.user_id for member in await run_in_threadpool(auth_service.list_org_members, tenant_id)}
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    unknown = [uid for uid in payload.user_ids if uid not in valid_subs]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown user id(s) for this organization: {', '.join(unknown)}",
        )

    try:
        sharing = await run_in_threadpool(_set_document_sharing, s3_key, payload.user_ids)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to update document sharing: {exc}",
        ) from exc

    return DocumentSharingResponse(
        document_id=document_id, uploaded_by=sharing["uploaded_by"], shared_with=sharing["shared_with"]
    )


@router.post(
    "/{document_id}/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
)
async def ingest_document_endpoint(
    document_id: str,
    current_user: CurrentUser = Depends(require_admin),
) -> IngestResponse:
    # This endpoint had NO auth check at all until this fix — anyone, logged
    # in or not, could trigger embedding calls (real cost) and an
    # OpenSearch write for any document_id, with no tenant scoping either.
    # Gated to require_admin (consistent with upload/delete/share) and the
    # document's own tenant_id must match the caller's, so an admin still
    # can't trigger ingestion of another tenant's document by guessing its id.
    existing = get_document(document_id)
    if existing is None or existing.tenant_id != current_user.tenant_id:
        # Same detail/status for "doesn't exist" and "exists in another
        # tenant" — a 403 would confirm the id is valid, just not yours.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No document found with id '{document_id}'")

    try:
        # ingest_document blocks (S3 download, embedding calls, OpenSearch bulk write),
        # so it runs off the event loop.
        result = await run_in_threadpool(ingest_document, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ClientError, BotoCoreError, openai.APIError, OpenSearchException) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ingestion failed: {exc}",
        ) from exc

    return IngestResponse(
        document_id=result.document_id,
        chunks_indexed=result.chunks_indexed,
        index_name=result.index_name,
    )


@router.post(
    "/sync-kb",
    response_model=SyncKBResponse,
    status_code=status.HTTP_200_OK,
)
async def sync_kb(
    payload: SyncKBRequest = SyncKBRequest(),
    _current_user: CurrentUser = Depends(get_current_user),
) -> SyncKBResponse:
    # No tenant scoping here — a Bedrock KB sync reconciles the whole
    # knowledge base at once, not one tenant's slice of it. Still requires a
    # valid session so this can't be triggered by an anonymous request.
    kb_id = payload.kb_id or settings.bedrock_kb_id
    if not kb_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No kb_id was provided and BEDROCK_KB_ID is not configured.",
        )

    try:
        # sync_knowledge_base blocks (it polls with time.sleep), so it runs off the event loop.
        result = await run_in_threadpool(sync_knowledge_base, kb_id, payload.data_source_id)
    except BedrockKBError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return SyncKBResponse(**result)
