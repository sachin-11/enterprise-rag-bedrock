import logging

from app.models.context import ChannelType, CorrelationMetadata, RequestContext, Role
from app.services.bedrock_kb_service import KBRetrievalResult
from app.services.retrieval.authorizer import filter_authorized_results


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
