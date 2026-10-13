"""One authorized entry point for the active Bedrock retrieval path."""

from langsmith import traceable

from app.models.context import RequestContext
from app.services.bedrock_kb_service import KBRetrievalResult, retrieve_from_kb
from app.services.retrieval.authorizer import filter_authorized_results, filter_current_document_acl


@traceable(name="retrieve_authorized_evidence", run_type="retriever")
def retrieve_authorized_evidence(
    query: str,
    kb_id: str,
    context: RequestContext,
    top_k: int = 20,
) -> list[KBRetrievalResult]:
    """Retrieve managed KB chunks and enforce current document access."""
    candidates = retrieve_from_kb(
        query,
        kb_id,
        context.tenant_id,
        context.user_id,
        context.is_admin,
        top_k,
    )
    candidates = filter_authorized_results(candidates, context)
    return filter_current_document_acl(candidates, context)
