from app.models.orchestration import RouteType


def direct_response(route: RouteType) -> str:
    if route is RouteType.DIRECT:
        return "Hello! How can I help with your organization's knowledge or services?"
    if route is RouteType.ESCALATE:
        return "Human support handoff is not configured yet. Please contact your organization administrator."
    if route is RouteType.TOOL_READ:
        return "Business-system read tools are not configured yet."
    if route is RouteType.TOOL_WRITE:
        return "Business-system write tools are not configured yet, so no action was performed."
    raise ValueError(f"Route {route.value} requires the RAG pipeline.")
