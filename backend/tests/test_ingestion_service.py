from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from moto import mock_aws

from app.models.document import DocumentMetadata
from app.services.document_store import get_document, save_document
from app.services.ingestion_service import DocumentNotFoundError, ingest_document

BUCKET_NAME = "test-bucket"


def _save_uploaded_doc(document_id: str, tenant_id: str, s3_key: str, filename: str) -> None:
    save_document(
        DocumentMetadata(
            document_id=document_id,
            tenant_id=tenant_id,
            filename=filename,
            s3_key=s3_key,
            content_hash="test-content-hash",
            upload_timestamp=datetime.now(timezone.utc),
            status="uploaded",
        )
    )


@pytest.fixture
def s3_bucket():
    with mock_aws():
        import boto3

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET_NAME)
        yield s3


@pytest.fixture
def mock_opensearch(monkeypatch):
    client = MagicMock()
    client.indices.exists.return_value = False
    monkeypatch.setattr("app.services.ingestion_service.get_opensearch_client", lambda: client)
    monkeypatch.setattr("app.services.retrieval_service.get_opensearch_client", lambda: client)
    return client


@pytest.fixture
def mock_embeddings(monkeypatch):
    # Real spaCy chunking + OpenAI calls are slow/costly for a unit test;
    # a deterministic fake embedding is enough to verify the pipeline wiring.
    monkeypatch.setattr(
        "app.services.ingestion_service.embed_texts",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )


@pytest.fixture
def mock_bulk(monkeypatch):
    calls = {}

    def fake_bulk(client, actions, **kwargs):
        calls["actions"] = actions
        return len(actions), []

    monkeypatch.setattr("app.services.ingestion_service.bulk", fake_bulk)
    return calls


def test_ingest_document_raises_for_unknown_document_id(mock_opensearch, mock_embeddings, mock_bulk):
    with pytest.raises(DocumentNotFoundError):
        ingest_document("does-not-exist")


def test_ingest_document_indexes_chunks_tagged_with_tenant(s3_bucket, mock_opensearch, mock_embeddings, mock_bulk):
    s3_key = "tenant-a/doc-1/policy.docx"
    s3_bucket.put_object(Bucket=BUCKET_NAME, Key=s3_key, Body=b"placeholder")
    _save_uploaded_doc("doc-1", "tenant-a", s3_key, "policy.docx")

    with patch("app.core.config.settings.s3_bucket_name", BUCKET_NAME), patch(
        "app.services.ingestion_service.extract_text_from_file",
        return_value="This is the first sentence. This is the second sentence.",
    ):
        result = ingest_document("doc-1")

    assert result.document_id == "doc-1"
    assert result.chunks_indexed == len(mock_bulk["actions"])
    assert result.chunks_indexed >= 1

    for action in mock_bulk["actions"]:
        source = action["_source"]
        assert source["tenant_id"] == "tenant-a"
        assert source["doc_name"] == "policy.docx"
        assert source["document_id"] == "doc-1"
        assert source["embedding"] == [0.1, 0.2, 0.3]
        assert action["_index"] == "documents"

    mock_opensearch.indices.create.assert_called_once()


def test_ingest_document_creates_index_only_when_missing(s3_bucket, mock_opensearch, mock_embeddings, mock_bulk):
    mock_opensearch.indices.exists.return_value = True

    s3_key = "tenant-a/doc-2/policy.docx"
    s3_bucket.put_object(Bucket=BUCKET_NAME, Key=s3_key, Body=b"placeholder")
    _save_uploaded_doc("doc-2", "tenant-a", s3_key, "policy.docx")

    with patch("app.core.config.settings.s3_bucket_name", BUCKET_NAME), patch(
        "app.services.ingestion_service.extract_text_from_file",
        return_value="Just one sentence here.",
    ):
        ingest_document("doc-2")

    mock_opensearch.indices.create.assert_not_called()


def test_ingest_document_updates_status_to_ingested(s3_bucket, mock_opensearch, mock_embeddings, mock_bulk):
    s3_key = "tenant-a/doc-3/policy.docx"
    s3_bucket.put_object(Bucket=BUCKET_NAME, Key=s3_key, Body=b"placeholder")
    _save_uploaded_doc("doc-3", "tenant-a", s3_key, "policy.docx")

    with patch("app.core.config.settings.s3_bucket_name", BUCKET_NAME), patch(
        "app.services.ingestion_service.extract_text_from_file",
        return_value="Some sentence to chunk and index.",
    ):
        ingest_document("doc-3")

    assert get_document("doc-3").status == "ingested"


def test_ingest_document_marks_status_failed_on_error(s3_bucket, mock_opensearch, mock_embeddings, mock_bulk):
    s3_key = "tenant-a/doc-4/policy.docx"
    s3_bucket.put_object(Bucket=BUCKET_NAME, Key=s3_key, Body=b"placeholder")
    _save_uploaded_doc("doc-4", "tenant-a", s3_key, "policy.docx")

    with patch("app.core.config.settings.s3_bucket_name", BUCKET_NAME), patch(
        "app.services.ingestion_service.extract_text_from_file",
        side_effect=RuntimeError("corrupt file"),
    ):
        with pytest.raises(RuntimeError):
            ingest_document("doc-4")

    assert get_document("doc-4").status == "ingestion_failed"
