"""Channel-neutral route planning for all inbound messages."""

from langsmith import get_current_run_tree, traceable

from app.models.orchestration import OrchestratorInput, OrchestratorOutput, RouteType
from app.services.direct_generation_service import direct_response
from app.services.intent_router import route_request


@traceable(name="orchestrator_route", run_type="chain")
def plan_request(request: OrchestratorInput) -> OrchestratorOutput:
    route = route_request(request.message)
    run = get_current_run_tree()
    if run is not None:
        run.extra.setdefault("metadata", {}).update(
            {
                "tenant_id": request.context.tenant_id,
                "user_id": request.context.user_id,
                "correlation_id": request.context.correlation_id,
                "channel": request.context.channel.value,
                "route": route.value,
            }
        )
    return OrchestratorOutput(
        route=route,
        response_text="" if route is RouteType.RAG else direct_response(route),
        conversation_id=request.conversation_id,
        trace_id=str(run.id) if run is not None else None,
    )
