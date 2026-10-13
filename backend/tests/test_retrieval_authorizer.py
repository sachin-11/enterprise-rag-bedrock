import logging
import io
import json

from app.models.context import ChannelType, CorrelationMetadata, RequestContext, Role
from app.services.bedrock_kb_service import KBRetrievalResult
from app.services.retrieval.authorizer import _current_acl, filter_authorized_results, filter_current_document_acl


def _context(tenant: str, user: str, role: Role = Role.MEMBER) -> RequestContext:
    return RequestContext(
        tenant_id=tenant,
        user_id=user,
        email=f"{user}@example.com",
        role=role,
        channel=ChannelType.WEB,
        correlation=CorrelationMetadata(correlation_id="trace-1"),
    )


def _chunk(chunk_id: str, metadata: dict) -> KBRetrievalResult:
    return KBRetrievalResult(chunk_id, "secret text", 0.9, "document.pdf", metadata)


def test_member_sees_only_owned_or_explicitly_shared_chunks(caplog):
    chunks = [
        _chunk("owned", {"tenant_id": "a", "uploaded_by": "alice"}),
        _chunk("shared", {"tenant_id": "a", "uploaded_by": "bob", "shared_with": ["alice"]}),
        _chunk("private", {"tenant_id": "a", "uploaded_by": "bob"}),
        _chunk("foreign", {"tenant_id": "b", "uploaded_by": "alice"}),
        _chunk("missing", {}),
        _chunk("malformed", {"tenant_id": "a", "shared_with": "alice"}),
    ]

    with caplog.at_level(logging.WARNING):
        authorized = filter_authorized_results(chunks, _context("a", "alice"))

    assert [result.chunk_id for result in authorized] == ["owned", "shared"]
    assert "reason=missing_or_mismatched_tenant" in caplog.text
    assert "reason=malformed_document_acl" in caplog.text
    assert "correlation=trace-1" in caplog.text


def test_admin_still_cannot_read_other_tenant():
    chunks = [
        _chunk("same-tenant", {"tenant_id": "a"}),
        _chunk("other-tenant", {"tenant_id": "b", "uploaded_by": "admin"}),
    ]

    authorized = filter_authorized_results(chunks, _context("a", "admin", Role.ADMIN))

    assert [result.chunk_id for result in authorized] == ["same-tenant"]


def test_current_acl_rejects_missing_uri_and_source_read_failure(monkeypatch):
    monkeypatch.setattr("app.services.retrieval.authorizer.settings.s3_bucket_name", "test-bucket")

    def _failed_read(key):
        raise RuntimeError("S3 unavailable")

    monkeypatch.setattr("app.services.retrieval.authorizer._current_acl", _failed_read)
    chunks = [
        _chunk("missing-uri", {"tenant_id": "a", "uploaded_by": "alice"}),
        KBRetrievalResult(
            "read-failed", "text", 0.9, "doc.pdf",
            {"tenant_id": "a", "uploaded_by": "alice"},
            "s3://test-bucket/a/doc-1/doc.pdf",
        ),
    ]

    assert filter_current_document_acl(chunks, _context("a", "alice")) == []


def test_current_acl_reads_only_the_expected_tenant_sidecar(monkeypatch):
    monkeypatch.setattr("app.services.retrieval.authorizer.settings.s3_bucket_name", "test-bucket")
    calls = []

    class FakeS3:
        def get_object(self, **kwargs):
            calls.append(kwargs)
            body = {
                "metadataAttributes": {
                    "tenant_id": {"value": {"stringValue": "a"}},
                    "uploaded_by": {"value": {"stringValue": "bob"}},
                    "shared_with": {"value": {"stringListValue": ["alice"]}},
                }
            }
            return {"Body": io.BytesIO(json.dumps(body).encode())}

    monkeypatch.setattr("app.services.retrieval.authorizer.get_s3_client", FakeS3)
    allowed = KBRetrievalResult(
        "allowed", "text", 0.9, "doc.pdf", {"tenant_id": "a", "shared_with": ["alice"]},
        "s3://test-bucket/a/doc-1/doc.pdf",
    )
    foreign_uri = KBRetrievalResult(
        "foreign", "text", 0.9, "doc.pdf", {"tenant_id": "a", "shared_with": ["alice"]},
        "s3://test-bucket/b/doc-1/doc.pdf",
    )

    assert filter_current_document_acl([allowed, foreign_uri], _context("a", "alice")) == [allowed]
    assert calls == [{"Bucket": "test-bucket", "Key": "a/doc-1/doc.pdf.metadata.json"}]
    assert _current_acl("a/doc-1/doc.pdf.metadata.json")["shared_with"] == ["alice"]
