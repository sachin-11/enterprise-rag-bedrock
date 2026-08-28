from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DocumentMetadata(BaseModel):
    document_id: str
    tenant_id: str
    filename: str
    s3_key: str
    content_hash: str
    upload_timestamp: datetime
    status: Literal["uploaded", "ingesting", "ingested", "ingestion_failed"]


class DocumentUploadResponse(BaseModel):
    document_id: str
    s3_key: str
    status: str
    duplicate: bool = False


class DocumentSummary(BaseModel):
    document_id: str
    filename: str
    upload_timestamp: datetime
    status: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]


class DocumentDeleteResponse(BaseModel):
    document_id: str
    deleted: bool


class IngestResponse(BaseModel):
    document_id: str
    chunks_indexed: int
    index_name: str


class DocumentSharingResponse(BaseModel):
    document_id: str
    uploaded_by: str
    shared_with: list[str]


class UpdateSharingRequest(BaseModel):
    user_ids: list[str]
