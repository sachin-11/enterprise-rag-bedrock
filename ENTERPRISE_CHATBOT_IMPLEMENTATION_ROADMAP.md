# Enterprise Chatbot: Current-State Gap Analysis and Module-wise Roadmap

## 1. Objective

Is repository ko ek production-ready, multi-tenant enterprise chatbot orchestration platform banana hai. Final platform Web, Slack aur Microsoft Teams se requests accept karega; enterprise SSO identity se tenant/role resolve karega; RAG aur business tools ko securely orchestrate karega; aur har response par authorization, citation, privacy, cost aur audit controls enforce karega.

Yeh file implementation order define karti hai. Har module ke saath current state, required files, acceptance criteria aur dependencies diye gaye hain, taaki team step by step kaam kar sake.

## 2. Current repository assessment

Legend: **Available** = usable implementation exists, **Partial** = foundation exists but target behavior incomplete, **Missing** = implementation not found.

| Capability | Status | Current evidence / gap |
|---|---|---|
| Web chat UI | Available | Next.js protected chat pages and SSE chat API exist. |
| Slack / Teams channels | Missing | No channel adapters, webhook verification, identity mapping or response formatting modules. |
| Enterprise authentication | Available | Cognito token verification, httpOnly cookie session, tenant extraction and org admin role exist. |
| API gateway rate limits | Missing | CORS exists, but no per-user/per-tenant throttling middleware or gateway/IaC policy exists. |
| Multi-tenant isolation | Available | Tenant comes from verified token; Bedrock KB retrieval applies tenant metadata filter. |
| Document ACL | Available | Admin/member filtering uses `uploaded_by` and `shared_with`; document sharing endpoints exist. |
| Chat orchestration router | Partial | RAG pipeline exists, but there is no explicit direct-generation/RAG/business-tool intent router. |
| Hybrid retrieval | Partial | Bedrock KB requests hybrid search when supported; standalone dense + BM25 + RRF code exists but is not the active chat path. |
| Reranking | Available | Cohere reranking and relevance threshold are in the active RAG pipeline. |
| Business tool calling | Missing | Admin copilot has narrow internal actions, but end-user typed tools such as leave balance or IT tickets do not exist. |
| Backend tool authorization | Missing | There is no shared tool registry/policy engine for operational tools. |
| Write confirmation | Missing | No pending-action/confirm/cancel workflow exists for model-selected write operations. |
| Idempotency keys | Missing | No idempotency store or replay-safe execution contract exists. |
| Conversation persistence | Partial | DynamoDB stores conversation history; the target statement calls for PostgreSQL. |
| Active sessions / distributed cache | Missing | Semantic cache is process-local memory; Redis is not configured. |
| Object storage | Available | Documents and metadata sidecars use S3. |
| Long-conversation memory | Available | Running summary plus recent turns is implemented and persisted. |
| LLM gateway | Missing | OpenAI clients and model IDs are created independently in several modules; no shared routing/fallback layer. |
| Token accounting / budgets | Partial | LangSmith-based cost reporting exists; deterministic per-request/per-tenant budget enforcement does not. |
| Input/output safety | Available | Bedrock Guardrail hooks, output PII/secret redaction and untrusted-context prompting exist. Production config can currently leave guardrails disabled. |
| Prompt-injection protection | Partial | Retrieved chunks are scanned, but detection is logging-only and flagged content still reaches the model. |
| Citation validation | Missing | The prompt asks for `[n]` citations and source objects are returned, but citation indices/claims are not validated before response. |
| End-to-end tracing | Available | LangSmith root and child traces carry tenant/user metadata. |
| Golden-dataset evaluation | Available | RAGAS script, test dataset and stored results exist; CI quality gates are not configured. |
| Human escalation | Partial | Error watchdog/admin notification exists; user-requested agent handoff and case lifecycle do not. |
| Document versioning | Missing | Upload hash/dedup exists, but no logical document/version model, active-version switch or rollback. |
| Per-tenant cost limits | Missing | Dashboard reporting exists, but hard/soft daily and monthly limits are not enforced. |
| Audit trail | Available | Append-only DynamoDB audit events exist for admin operations. |

## 3. Target request flow

1. Web, Slack or Teams adapter normalizes the incoming message into one `ChannelMessage` contract.
2. API edge verifies the channel signature/token, resolves enterprise identity, tenant and roles, applies request-size and rate limits, and creates a correlation ID.
3. Conversation service loads recent turns plus the durable summary.
4. Orchestrator runs input policy and classifies the request as `DIRECT`, `RAG`, `TOOL_READ`, `TOOL_WRITE` or `ESCALATE`.
5. RAG requests retrieve only ACL-authorized chunks, rerank them and build evidence. Tool requests go through a typed registry and deterministic authorization policy.
6. Write tools create a pending action. The user sees exact operation details and must explicitly confirm. Confirmed execution uses an idempotency key.
7. LLM gateway chooses an allowed model, checks budget, records token/cost usage and performs configured fallback.
8. Response validator checks citations, sensitive-data leakage and tenant policy before the channel adapter renders the answer.
9. Trace, audit and usage events are written for the whole request.

## 4. Implementation principles

- `tenant_id`, `user_id` and roles must always come from verified server-side identity context.
- Authorization must run again at data access and immediately before each tool execution.
- The model may propose a tool and arguments; backend code decides whether it is valid and allowed.
- Every write operation requires explicit confirmation, an idempotency key and an audit event.
- Retrieval must fail closed when tenant or ACL metadata is missing.
- Provider credentials, raw secrets and unrestricted tool clients must never enter prompts.
- Budget limits and security policy must be deterministic checks, not prompt instructions.
- New modules should initially wrap current Cognito, DynamoDB and Bedrock behavior so migration can be incremental.

## 5. Module-wise implementation plan

### Module 0 — Contracts and request context

**Goal:** All channels and services use one trusted request shape.

**Status:** Implemented. Web/authenticated API entry points now use immutable `RequestContext`; correlation IDs propagate through HTTP responses, RAG trace metadata and audit writes. Slack/Teams context builders will be added with their channel adapters in Module 2.

Create:

- `backend/app/models/context.py`: `RequestContext`, `ChannelType`, `Role`, correlation metadata.
- `backend/app/models/orchestration.py`: route types and normalized orchestrator input/output.
- `backend/app/core/request_context.py`: build context only from verified auth/channel identity.
- `backend/tests/test_request_context.py`: reject client-supplied tenant/role overrides.

Acceptance criteria:

- Downstream functions receive `RequestContext` instead of separate free-form identity arguments.
- Tenant and role cannot be set by chat payload, query parameters or arbitrary headers.
- Every request gets a correlation ID propagated into traces and audit events.

Depends on: none. Implement first.

### Module 1 — API edge, SSO hardening and rate limiting

**Goal:** Central entry boundary for authentication, throttling and abuse controls.

**Status:** Application layer implemented. Redis-backed atomic tenant/user and authentication-IP limits, trusted-proxy handling, `Retry-After`, explicit fail-closed behavior and local Docker Redis are present. API Gateway/WAF deployment policies and multi-instance load testing remain infrastructure work.

Create/change:

- `backend/app/middleware/correlation.py`
- `backend/app/middleware/rate_limit.py`
- `backend/app/services/rate_limit_service.py`
- `backend/app/core/config.py`: tenant/user/IP limits and trusted proxy settings.
- `backend/app/main.py`: register middleware.
- `infra/`: API Gateway/WAF throttling configuration when deployment IaC is introduced.

Use Redis atomic counters for application limits and API Gateway/WAF for coarse edge protection. Keys should include tenant and user, with a separate anonymous/IP bucket for authentication endpoints.

Acceptance criteria:

- Limits apply per tenant and per user and return `429` with `Retry-After`.
- One tenant cannot consume another tenant's quota.
- Login/reset endpoints have stricter abuse limits.
- Load tests prove counters behave correctly across multiple backend instances.

Depends on: Module 0 and Module 4 Redis foundation.

### Module 2 — Channel adapters: Web, Slack and Teams

**Goal:** Channel-specific protocol code stays outside orchestration logic.

Create:

- `backend/app/channels/base.py`: normalized inbound/outbound contracts.
- `backend/app/channels/web.py`
- `backend/app/channels/slack.py`: signing-secret verification, replay window, event deduplication.
- `backend/app/channels/teams.py`: Bot Framework/JWT validation and activity mapping.
- `backend/app/api/channels.py`: webhooks/callback endpoints.
- `backend/app/services/identity_mapping_service.py`: external channel identity to enterprise user/tenant mapping.
- `backend/tests/channels/`

Acceptance criteria:

- Invalid Slack signatures and invalid/expired Teams tokens are rejected before processing.
- Duplicate channel events produce no duplicate answer or write action.
- A mapped identity resolves to the same internal `RequestContext` used by Web.
- Responses support citations and confirmation buttons/cards in each channel.

Depends on: Modules 0 and 1.

### Module 3 — Chat orchestrator and intent routing

**Goal:** Replace the single hard-wired RAG path with explicit routing.

Create/change:

- `backend/app/services/orchestrator_service.py`
- `backend/app/services/intent_router.py`
- `backend/app/services/direct_generation_service.py`
- Move the current `_run_rag_pipeline` from `api/chat.py` into `backend/app/services/rag_pipeline.py`.
- Keep `backend/app/api/chat.py` as transport/SSE handling only.

Routes:

- `DIRECT`: greetings, supported non-knowledge transformations and safe general responses.
- `RAG`: enterprise knowledge questions requiring authorized documents.
- `TOOL_READ`: read-only business operation.
- `TOOL_WRITE`: action requiring confirmation.
- `ESCALATE`: explicit human request, repeated low confidence or configured policy trigger.

Prefer deterministic rules for obvious cases, then constrained structured model output. Treat routing output as untrusted and validate it with a strict schema.

Acceptance criteria:

- Route decision is structured, traced and testable.
- Unknown or invalid route output fails safely to RAG or a clarification response.
- Transport code has no retrieval/provider logic.
- Existing RAG endpoint tests continue to pass through the new orchestrator.

Depends on: Module 0.

### Module 4 — Durable data layer: PostgreSQL and Redis

**Goal:** Provide transactional workflow state and distributed ephemeral state.

Create:

- `backend/app/db/session.py`, `backend/app/db/base.py`
- `backend/app/db/models/`: tenant, user, conversation, message, pending action, idempotency record, document version, usage ledger and escalation case.
- `backend/app/repositories/`: repository interfaces and SQL implementations.
- `backend/alembic/`: schema migrations.
- `backend/app/core/redis.py`
- Update `docker-compose.yml` with PostgreSQL and Redis services.

Migration approach:

1. Introduce repository interfaces around current DynamoDB conversation calls.
2. Implement PostgreSQL repositories.
3. Backfill and verify counts/checksums.
4. Dual-read or controlled cutover behind configuration.
5. Retire the old path only after rollback testing.

Redis should hold active-session state, rate-limit counters, short-lived pending-confirmation tokens and semantic cache entries. PostgreSQL remains the source of truth for conversations, approvals, idempotency, usage and cases.

Acceptance criteria:

- Tenant keys exist on every tenant-owned table and every repository query scopes by tenant.
- Database constraints prevent cross-tenant foreign-key associations.
- Multiple API instances share cache/session state.
- Restarting a process does not lose conversations or confirmed-action records.

Depends on: Module 0. Start early; later modules build on it.

### Module 5 — Retrieval and authorization hardening

**Goal:** Make hybrid retrieval and document ACL behavior explicit and testable.

Create/change:

- `backend/app/services/retrieval/authorizer.py`
- `backend/app/services/retrieval/hybrid.py`
- `backend/app/services/retrieval/reranker.py`
- `backend/app/services/retrieval/evidence_builder.py`
- Consolidate current `bedrock_kb_service.py` and standalone `retrieval_service.py` behind one interface.

Decide one production path:

- Bedrock Knowledge Base with confirmed HYBRID support and metadata filters; or
- OpenSearch dense + BM25 + RRF pipeline already present in the repository.

Do not advertise hybrid retrieval until the deployed backend capability is verified. Add post-retrieval authorization as defense in depth by checking every result's tenant/document ACL before prompt construction.

Acceptance criteria:

- Cross-tenant and unshared-document adversarial tests return zero unauthorized chunks.
- Dense and keyword branches are observable separately.
- Reranking keeps source identity and ACL metadata intact.
- Missing security metadata causes a chunk to be discarded and audited.

Depends on: Modules 0 and 4.

### Module 6 — Typed business-tool platform

**Goal:** Safely support operations such as leave balance and IT tickets.

Create:

- `backend/app/tools/base.py`: typed input/output, risk level and required permission.
- `backend/app/tools/registry.py`: allow-listed tool registration.
- `backend/app/tools/policy.py`: deterministic RBAC/ABAC authorization.
- `backend/app/tools/hr/get_leave_balance.py`
- `backend/app/tools/it/create_ticket.py`
- `backend/app/services/tool_executor.py`
- `backend/app/models/tools.py`
- `backend/tests/tools/`

Each tool definition must include name, purpose, strict argument schema, read/write classification, timeout, permission, tenant scoping and audit behavior. Connector credentials remain backend-only.

Acceptance criteria:

- The model cannot invoke unregistered tools or add unknown arguments.
- Backend derives actor and tenant from `RequestContext`, never model arguments.
- Tool authorization denial is deterministic and audited.
- External calls have timeouts, bounded retries and sanitized errors.

Depends on: Modules 0, 3 and 4.

### Module 7 — Confirmation and idempotent write execution

**Goal:** Prevent unintended or duplicate business actions.

Create:

- `backend/app/services/approval_service.py`
- `backend/app/services/idempotency_service.py`
- `backend/app/api/actions.py`: pending action detail, confirm and cancel endpoints.
- PostgreSQL `pending_actions` and `idempotency_records` tables.
- Channel confirmation UI/cards.

Flow:

1. Model proposes a write tool and typed arguments.
2. Backend validates and authorizes it, then stores an immutable pending action with expiry.
3. User receives human-readable exact action details.
4. Confirm endpoint re-authenticates context, re-authorizes and atomically claims the idempotency key.
5. Executor calls the external system once and stores the result.
6. Repeated confirmation returns the stored result.

Acceptance criteria:

- No write tool runs before explicit confirmation.
- Pending actions are bound to tenant, user and conversation and expire.
- Concurrent confirmations execute the external mutation at most once.
- Changed permissions between proposal and confirmation cause denial.

Depends on: Modules 2, 4 and 6.

### Module 8 — Central LLM gateway, provider fallback and budgets

**Goal:** Remove direct provider construction from business modules.

Create/change:

- `backend/app/llm/gateway.py`
- `backend/app/llm/providers/base.py`, `openai.py`, `bedrock.py`
- `backend/app/llm/model_policy.py`
- `backend/app/llm/usage_service.py`
- `backend/app/llm/budget_service.py`
- Replace `ChatOpenAI(...)` construction in chat, query, copilot and watchdog services.

Gateway request should declare task type, latency class, required capabilities, tenant policy and maximum token budget. Fallback should occur only for retryable provider failures and only to a model permitted for that tenant/data classification.

Acceptance criteria:

- Every model call records tenant, user, task, provider, model, input/output tokens, estimated cost, latency and outcome.
- Soft limits warn; hard limits reject before provider invocation.
- Concurrent requests cannot overspend a hard tenant budget because reservations are atomic.
- Fallback is traced and never bypasses data-residency/model allow-list policy.

Depends on: Modules 0 and 4. Integrate before adding many tools.

### Module 9 — Response validation and policy enforcement

**Goal:** Validate the final answer before any channel receives it.

Create:

- `backend/app/services/response_validation/citations.py`
- `backend/app/services/response_validation/data_loss.py`
- `backend/app/services/response_validation/tenant_policy.py`
- `backend/app/services/response_validation/pipeline.py`

Citation validation should at minimum ensure that every `[n]` refers to supplied evidence and reject/remove invalid indices. The next level should split factual claims, map each claim to cited evidence and flag unsupported claims. Preserve the existing PII/secret redaction as one stage of this pipeline.

Acceptance criteria:

- Invalid citation indices never reach users.
- RAG answers with factual claims but no valid evidence follow a configured refusal/qualification policy.
- Sensitive-data tests cover secrets, government IDs and tenant-configured patterns.
- Validation outcome appears in the trace and audit record without logging sensitive content.

Depends on: Modules 3, 5 and 8.

### Module 10 — Prompt-injection defense

**Goal:** Turn current detection into enforceable risk handling.

Create/change:

- Extend `injection_service.py` to return severity, category and recommended action.
- Add source trust metadata during ingestion.
- Apply quarantine/drop/down-rank policy before evidence reaches the LLM.
- Restrict tool availability when retrieved content is untrusted or injection risk is high.

Acceptance criteria:

- High-severity instruction-like chunks are excluded or quarantined according to tenant policy.
- Retrieved text can never enable a tool or alter permissions.
- An adversarial evaluation set covers indirect prompt injection, data exfiltration and fake system messages.
- Security events contain document/chunk IDs and policy outcome.

Depends on: Modules 5, 6 and 9.

### Module 11 — Document lifecycle and versioning

**Goal:** Preserve lineage and allow controlled re-index/rollback.

Create:

- `backend/app/db/models/document.py` with logical document and immutable version entities.
- `backend/app/repositories/document_repository.py`
- `backend/app/services/document_version_service.py`
- Index metadata: `document_id`, `version_id`, `is_active`, checksum and effective dates.

Acceptance criteria:

- Uploading a changed file creates a new version instead of overwriting lineage.
- Only active versions are retrieved by default.
- Admin can inspect versions, activate a previous one and audit the change.
- Index cleanup does not remove versions still referenced by audit/conversation records.

Depends on: Modules 4 and 5.

### Module 12 — Human escalation

**Goal:** Support controlled handoff when automation should stop.

Create:

- `backend/app/services/escalation_service.py`
- `backend/app/api/escalations.py`
- `backend/app/db/models/escalation.py`
- Optional ticketing adapter built through the tool framework.

Triggers include explicit user request, policy requirement, repeated tool/provider failure, low groundedness and sensitive workflow. Store a minimal redacted transcript and consent/policy metadata.

Acceptance criteria:

- User receives a case ID and status.
- Case access is tenant-scoped and role-controlled.
- Transcript export obeys retention and redaction policy.
- Existing error-watchdog notification remains an operational alert, while escalation becomes a user-visible case workflow.

Depends on: Modules 3, 4, 6 and 9.

### Module 13 — Observability, evaluation and production gates

**Goal:** Convert existing tracing/evaluation features into release controls.

Create/change:

- `backend/app/observability/metrics.py`: OpenTelemetry-compatible request/retrieval/tool/LLM metrics.
- Expand `backend/eval/test_qa.json` into tenant isolation, ACL, injection, citation, routing and tool-safety datasets.
- Add CI commands for unit, integration, migration and evaluation threshold checks.
- Add dashboards/alerts for p95 latency, error rate, retrieval-empty rate, fallback rate, tool failure, budget rejection and guardrail events.

Acceptance criteria:

- Correlation ID connects channel event, API request, retrieval, model call, tool call and audit event.
- CI fails when quality/security thresholds regress beyond an approved tolerance.
- Logs never contain tokens, secrets or raw sensitive prompts.
- Runbooks exist for provider outage, index lag, budget exhaustion and suspected cross-tenant leakage.

Depends on: all functional modules; extend continuously rather than waiting until the end.

## 6. Recommended delivery sequence

### Phase 1 — Secure foundation

Modules 0, 1 and 4. Establish request context, PostgreSQL/Redis and distributed limits first. This prevents later features from creating incompatible identity and state patterns.

Exit criteria: trusted context everywhere, durable schema migrations, Redis shared across instances, tenant-aware rate limits.

### Phase 2 — Clean orchestration and retrieval

Modules 3 and 5, then Module 9 citation baseline. Extract the current RAG implementation without changing user behavior, select one hybrid retrieval path and add post-retrieval ACL verification.

Exit criteria: route contract works, current chat tests pass, unauthorized retrieval tests pass, invalid citations are blocked.

### Phase 3 — LLM governance

Module 8, followed by remaining Module 9 work. Move all provider calls behind the gateway and enforce token/cost limits.

Exit criteria: no direct model client construction outside provider adapters; usage ledger reconciles with provider/LangSmith reports; hard budgets work under concurrency.

### Phase 4 — Business actions

Modules 6 and 7. Start with one read tool (`get_leave_balance`) and one write tool (`create_it_ticket`) as reference implementations.

Exit criteria: schema validation, authorization, confirmation, idempotency and audit tests pass end to end.

### Phase 5 — Enterprise channels and operations

Modules 2, 10, 11 and 12. Add Slack first, then Teams using the same normalized contract; enforce injection policy; add versions and human cases.

Exit criteria: identical policy decisions across Web/Slack/Teams, replay-safe webhooks, version rollback and tenant-scoped escalation cases.

### Phase 6 — Production gate

Module 13 and final threat/load/failure testing.

Exit criteria: SLO dashboards and alerts live, golden evaluation thresholds pass, disaster/fallback runbooks exercised, security review signs off tenant and tool boundaries.

## 7. First implementation sprint

Keep the first sprint small and reviewable:

1. Add `RequestContext` and refactor chat entry to consume it.
2. Extract current RAG pipeline from `api/chat.py` into `services/rag_pipeline.py` without behavior changes.
3. Add orchestrator route enum and initially route all knowledge queries to the extracted RAG service.
4. Add citation index validation before output.
5. Add tests for forged tenant context, invalid citations and preservation of current streaming behavior.
6. Draft PostgreSQL/Redis schema and migration plan; do not combine the datastore cutover with the extraction refactor.

Sprint completion means architecture boundaries exist while current Web chat continues to work. The next sprint can then add durable state and the first typed read tool safely.

## 8. Definition of done for every module

- Unit tests cover validation and denial paths, not only success paths.
- At least one cross-tenant negative test exists for every tenant-owned resource.
- Config and environment variables are documented.
- Trace and audit fields are defined and sensitive values are excluded.
- Failure behavior, timeout, retry and rollback are documented.
- API contracts are versioned or backward compatible.
- README architecture and deployment instructions match actual active code paths.

## 9. Key decisions to record before production

Create Architecture Decision Records under `docs/adr/` for:

1. PostgreSQL migration versus retaining DynamoDB for conversation history.
2. Bedrock KB hybrid retrieval versus self-managed OpenSearch dense/BM25/RRF.
3. Redis provider/topology and eviction policy.
4. Approved LLM providers/models by tenant data classification and region.
5. Confirmation expiry and idempotency retention windows.
6. Human escalation destination and transcript retention.
7. Per-tenant soft/hard budgets and administrative override process.

These choices affect operations and compliance, but they do not block the first sprint's code-boundary work.
