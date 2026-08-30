import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("BEDROCK_KB_ID", "test-kb")
os.environ.setdefault("OPENSEARCH_ENDPOINT", "https://example.us-east-1.aoss.amazonaws.com")
os.environ.setdefault("COHERE_API_KEY", "test-cohere-key")

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.main import app
from app.models.user import CurrentUser
from app.services.bedrock_kb_service import KBRetrievalResult

client = TestClient(app)

# Two fake tenants asking about the *same kind* of thing (Q1 revenue), with
# near-identical phrasing, so a bug that ignored tenant_id and matched on
# content similarity alone would still (wrongly) return the other tenant's
# chunk. Isolation must come from the tenant_id filter, not content shape.
FAKE_KB = {
    "tenant-a": [
        KBRetrievalResult(
            chunk_id="a-doc-1-chunk-0",
            text="TENANT-A-ONLY: Q1 revenue for Acme Corp was $4.2M, up 12% year over year.",
            score=0.95,
            doc_name="acme-q1-report.pdf",
            metadata={"tenant_id": "tenant-a"},
        )
    ],
    "tenant-b": [
        KBRetrievalResult(
            chunk_id="b-doc-1-chunk-0",
            text="TENANT-B-ONLY: Q1 revenue for Globex Corp was $4.2M, up 12% year over year.",
            score=0.95,
            doc_name="globex-q1-report.pdf",
            metadata={"tenant_id": "tenant-b"},
        )
    ],
}


def _fake_retrieve_from_kb(query_text, kb_id, tenant_id, user_id, is_admin, top_k=20):
    return list(FAKE_KB.get(tenant_id, []))


def _fake_rerank(query, candidates, top_n=5):
    return candidates[:top_n]


def _as_user(
    tenant_id: str, user_id: str = "test-user", email: str = "user@example.com", is_admin: bool = False
) -> None:
    """Simulate a logged-in request as a member of `tenant_id`'s org.

    Overriding get_current_user (rather than mocking Cognito/JWT verification)
    tests exactly what chat.py actually depends on: whatever tenant_id the
    dependency resolves to, never anything client-suppliable.
    """
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user_id, email=email, tenant_id=tenant_id, is_admin=is_admin
    )


def _parse_sse(body: str) -> list[dict]:
    """Parses the "data: {...}\\n\\n" event stream chat_query returns into a list of dicts."""
    events = []
    for raw in body.split("\n\n"):
        raw = raw.strip()
        if not raw.startswith("data: "):
            continue
        events.append(json.loads(raw[len("data: ") :]))
    return events


class _FakeAstream:
    """Mimics ChatOpenAI.astream()'s return value: an async iterator of chunks
    exposing `.content`, the shape app/api/chat.py's streaming loop expects."""

    def __init__(self, full_text: str):
        self._full_text = full_text

    def __aiter__(self):
        return self._generate()

    async def _generate(self):
        yield SimpleNamespace(content=self._full_text)


@pytest.fixture(autouse=True)
def _mock_pipeline(monkeypatch):
    monkeypatch.setattr("app.api.chat.rewrite_query", lambda query, chat_history: query)
    monkeypatch.setattr("app.api.chat.generate_hyde_passage", lambda query: f"Hypothetical answer about {query}")
    monkeypatch.setattr("app.api.chat.retrieve_from_kb", _fake_retrieve_from_kb)
    monkeypatch.setattr("app.api.chat.rerank_with_cohere", _fake_rerank)

    # No real OpenAI embedding calls in tests, and every test deterministically
    # exercises the full pipeline rather than depending on cache state left
    # over from a previous test (cache_service's store is module-level and
    # would otherwise persist across test functions within the same run).
    monkeypatch.setattr("app.api.chat.embed_text", lambda text: [0.0] * 1536)
    monkeypatch.setattr("app.services.cache_service.lookup", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.cache_service.store", lambda *args, **kwargs: None)

    mock_llm = MagicMock()
    mock_llm.astream.return_value = _FakeAstream("Revenue grew 12% year over year [1].")
    monkeypatch.setattr("app.api.chat._get_answer_llm", lambda: mock_llm)

    # DynamoDB-backed conversation history isn't under test here — chat.py's
    # own tests only care about the retrieval/answer pipeline, so history
    # persistence is stubbed out to a no-op / fixed new-conversation-id.
    monkeypatch.setattr(
        "app.api.chat.create_conversation",
        lambda user_id, tenant_id, title: SimpleNamespace(conversation_id="fake-conversation-id"),
    )
    monkeypatch.setattr("app.api.chat.append_message", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.api.chat.touch_conversation", lambda *args, **kwargs: None)

    yield
    app.dependency_overrides.clear()


def test_tenant_a_never_receives_tenant_b_chunks():
    _as_user("tenant-a")
    response = client.post("/chat/query", json={"query": "What was Q1 revenue?"})

    assert response.status_code == 200
    events = _parse_sse(response.text)

    assert events[0] == {"type": "conversation", "conversation_id": "fake-conversation-id"}

    sources = next(e["sources"] for e in events if e["type"] == "sources")
    assert len(sources) == 1
    assert sources[0]["doc_name"] == "acme-q1-report.pdf"
    assert "TENANT-A-ONLY" in sources[0]["excerpt"]
    assert "TENANT-B-ONLY" not in sources[0]["excerpt"]
    assert "globex" not in sources[0]["doc_name"].lower()

    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert "Globex" not in answer

    assert events[-1] == {"type": "done"}


def test_tenant_b_never_receives_tenant_a_chunks():
    _as_user("tenant-b")
    response = client.post("/chat/query", json={"query": "What was Q1 revenue?"})

    assert response.status_code == 200
    events = _parse_sse(response.text)

    sources = next(e["sources"] for e in events if e["type"] == "sources")
    assert len(sources) == 1
    assert sources[0]["doc_name"] == "globex-q1-report.pdf"
    assert "TENANT-B-ONLY" in sources[0]["excerpt"]
    assert "TENANT-A-ONLY" not in sources[0]["excerpt"]
    assert "acme" not in sources[0]["doc_name"].lower()


def test_missing_session_cookie_returns_401():
    # No _as_user() override and no cookie on the request — exercises the
    # real get_current_user dependency, not a stand-in. Runs before any
    # streaming starts, so this is still a plain JSON 401, not an SSE event.
    response = client.post("/chat/query", json={"query": "What was Q1 revenue?"})

    assert response.status_code == 401


def test_hyde_failure_falls_back_to_standalone_query_as_retrieval_text(monkeypatch):
    _as_user("tenant-a")
    monkeypatch.setattr("app.api.chat.generate_hyde_passage", lambda query: None)

    captured = {}

    def _capture_retrieve(query_text, kb_id, tenant_id, user_id, is_admin, top_k=20):
        captured["query_text"] = query_text
        return []

    monkeypatch.setattr("app.api.chat.retrieve_from_kb", _capture_retrieve)

    response = client.post("/chat/query", json={"query": "What was Q1 revenue?"})

    assert response.status_code == 200
    assert captured["query_text"] == "What was Q1 revenue?"


def test_existing_conversation_id_is_looked_up_and_reused(monkeypatch):
    _as_user("tenant-a")

    captured = {}

    def _fake_get_conversation(user_id, conversation_id):
        captured["user_id"] = user_id
        captured["conversation_id"] = conversation_id
        return SimpleNamespace(conversation_id=conversation_id, messages=[])

    monkeypatch.setattr("app.api.chat.get_conversation", _fake_get_conversation)

    response = client.post(
        "/chat/query", json={"query": "What was Q1 revenue?", "conversation_id": "existing-convo-id"}
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[0] == {"type": "conversation", "conversation_id": "existing-convo-id"}
    assert captured == {"user_id": "test-user", "conversation_id": "existing-convo-id"}


def test_unknown_conversation_id_returns_404(monkeypatch):
    _as_user("tenant-a")
    monkeypatch.setattr("app.api.chat.get_conversation", lambda user_id, conversation_id: None)

    # Also runs before any streaming starts (the lookup happens first), so
    # this is a plain JSON 404, same as the pre-streaming behavior.
    response = client.post("/chat/query", json={"query": "hi", "conversation_id": "does-not-exist"})

    assert response.status_code == 404
