"""Document ingestion: S3 -> extract text -> chunk -> embed -> OpenSearch index.

This is the piece that connects /documents/upload (raw file in S3) to
/chat/query's retrieve_dense/retrieve_sparse (which expect chunks already
sitting in an OpenSearch index with `embedding`/`text`/`tenant_id` fields).
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from opensearchpy.helpers import bulk

from app.core.aws_clients import get_opensearch_client, get_s3_client
from app.core.config import settings
from app.services.chunking_service import chunk_text, extract_text_from_file
from app.services.document_store import get_document, save_document
from app.services.retrieval_service import DEFAULT_INDEX_NAME, embed_texts, ensure_index_exists

logger = logging.getLogger(__name__)


class DocumentNotFoundError(Exception):
    """Raised when ingest_document is called with an unknown document_id."""


@dataclass
class IngestionResult:
    document_id: str
    chunks_indexed: int
    index_name: str


def _download_from_s3(s3_key: str) -> bytes:
    client = get_s3_client()
    response = client.get_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    return response["Body"].read()


def ingest_document(document_id: str, index_name: str = DEFAULT_INDEX_NAME) -> IngestionResult:
    """Run the full ingestion pipeline for a previously uploaded document.

    Downloads the file from S3, extracts and chunks its text, embeds every
    chunk, and bulk-indexes them into OpenSearch tagged with the document's
    tenant_id — the same field retrieve_dense/retrieve_sparse filter on.
    """
    metadata = get_document(document_id)
    if metadata is None:
        raise DocumentNotFoundError(f"No document found with id '{document_id}'")

    save_document(metadata.model_copy(update={"status": "ingesting"}))

    try:
        file_bytes = _download_from_s3(metadata.s3_key)
        suffix = Path(metadata.filename).suffix

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                tmp_file.write(file_bytes)
                tmp_path = tmp_file.name

            raw_text = extract_text_from_file(tmp_path, suffix.lstrip("."))
        finally:
            if tmp_path:
                os.unlink(tmp_path)

        chunks = chunk_text(raw_text)
        if not chunks:
            logger.warning("No chunks produced for document '%s' — nothing to index.", document_id)
            save_document(metadata.model_copy(update={"status": "ingested"}))
            return IngestionResult(document_id=document_id, chunks_indexed=0, index_name=index_name)

        embeddings = embed_texts([chunk.text for chunk in chunks])

        ensure_index_exists(index_name)

        client = get_opensearch_client()
        actions = [
            {
                "_index": index_name,
                "_id": f"{document_id}-{chunk.chunk_id}",
                "_source": {
                    "text": chunk.text,
                    "embedding": embedding,
                    "tenant_id": metadata.tenant_id,
                    "document_id": document_id,
                    "doc_name": metadata.filename,
                    "chunk_id": f"{document_id}-{chunk.chunk_id}",
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "section_heading": chunk.section_heading,
                },
            }
            for chunk, embedding in zip(chunks, embeddings)
        ]
        success_count, errors = bulk(client, actions, raise_on_error=False)
        if errors:
            logger.warning("Some chunks failed to index for document '%s': %s", document_id, errors)
    except Exception:
        save_document(metadata.model_copy(update={"status": "ingestion_failed"}))
        raise

    save_document(metadata.model_copy(update={"status": "ingested"}))
    return IngestionResult(document_id=document_id, chunks_indexed=success_count, index_name=index_name)
