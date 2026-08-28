from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.retrieval_service import (
    RetrievalResult,
    reciprocal_rank_fusion,
    rerank_with_cohere,
    retrieve_dense,
    retrieve_sparse,
)


def _hit(doc_id, score, text, tenant_id="tenant-1"):
    return {"_id": doc_id, "_score": score, "_source": {"text": text, "tenant_id": tenant_id}}


def test_retrieve_dense_sends_knn_query_scoped_to_tenant(monkeypatch):
    mock_client = MagicMock()
    mock_client.search.return_value = {
        "hits": {"hits": [_hit("doc-1", 0.9, "first passage"), _hit("doc-2", 0.7, "second passage")]}
    }
    monkeypatch.setattr("app.services.retrieval_service.get_opensearch_client", lambda: mock_client)

    results = retrieve_dense([0.1, 0.2, 0.3], tenant_id="tenant-1", top_k=5)

    assert [r.doc_id for r in results] == ["doc-1", "doc-2"]
    assert results[0].score == 0.9
    assert results[0].text == "first passage"

    _, kwargs = mock_client.search.call_args
    body = kwargs["body"]
    assert body["query"]["bool"]["filter"] == [{"term": {"tenant_id": "tenant-1"}}]
    knn_clause = body["query"]["bool"]["must"][0]["knn"]["embedding"]
    assert knn_clause["vector"] == [0.1, 0.2, 0.3]
    assert knn_clause["k"] == 5


def test_retrieve_sparse_sends_bm25_match_query_scoped_to_tenant(monkeypatch):
    mock_client = MagicMock()
    mock_client.search.return_value = {"hits": {"hits": [_hit("doc-3", 4.2, "matched passage")]}}
    monkeypatch.setattr("app.services.retrieval_service.get_opensearch_client", lambda: mock_client)

    results = retrieve_sparse("enterprise RAG pipeline", tenant_id="tenant-1", top_k=10)

    assert len(results) == 1
    assert results[0].doc_id == "doc-3"
    assert results[0].score == 4.2

    _, kwargs = mock_client.search.call_args
    body = kwargs["body"]
    assert body["query"]["bool"]["must"] == [{"match": {"text": "enterprise RAG pipeline"}}]
    assert body["query"]["bool"]["filter"] == [{"term": {"tenant_id": "tenant-1"}}]


def test_reciprocal_rank_fusion_boosts_docs_ranked_well_in_both_lists():
    dense = [
        RetrievalResult(doc_id="A", score=0.9, text="doc A"),
        RetrievalResult(doc_id="B", score=0.8, text="doc B"),
    ]
    sparse = [
        RetrievalResult(doc_id="B", score=5.0, text="doc B"),
        RetrievalResult(doc_id="C", score=4.0, text="doc C"),
    ]

    fused = reciprocal_rank_fusion(dense, sparse, k=60)

    expected_a = 1 / 61
    expected_b = 1 / 62 + 1 / 61
    expected_c = 1 / 62

    scores = {r.doc_id: r.score for r in fused}
    assert scores["A"] == expected_a
    assert scores["B"] == expected_b
    assert scores["C"] == expected_c

    # B appears near the top of both lists, so it should fuse to #1 even
    # though A had the single best rank in either individual list.
    assert fused[0].doc_id == "B"


def test_reciprocal_rank_fusion_returns_sorted_descending():
    dense = [RetrievalResult(doc_id="X", score=1.0, text="x")]
    sparse = [RetrievalResult(doc_id="Y", score=1.0, text="y"), RetrievalResult(doc_id="X", score=0.5, text="x")]

    fused = reciprocal_rank_fusion(dense, sparse)

    scores = [r.score for r in fused]
    assert scores == sorted(scores, reverse=True)


def test_rerank_with_cohere_reorders_and_truncates(monkeypatch):
    candidates = [
        RetrievalResult(doc_id="1", score=0.1, text="irrelevant passage"),
        RetrievalResult(doc_id="2", score=0.1, text="highly relevant passage"),
        RetrievalResult(doc_id="3", score=0.1, text="somewhat relevant passage"),
    ]
    mock_response = SimpleNamespace(
        results=[
            SimpleNamespace(index=1, relevance_score=0.95),
            SimpleNamespace(index=2, relevance_score=0.60),
        ]
    )
    mock_cohere_client = MagicMock()
    mock_cohere_client.rerank.return_value = mock_response
    monkeypatch.setattr("app.services.retrieval_service._get_cohere_client", lambda: mock_cohere_client)

    result = rerank_with_cohere("find the relevant passage", candidates, top_n=2)

    assert [r.doc_id for r in result] == ["2", "3"]
    assert result[0].score == 0.95
    assert result[1].score == 0.60

    _, kwargs = mock_cohere_client.rerank.call_args
    assert kwargs["top_n"] == 2
    assert kwargs["documents"] == [c.text for c in candidates]


def test_rerank_with_cohere_falls_back_to_original_order_on_failure(monkeypatch):
    candidates = [
        RetrievalResult(doc_id="1", score=0.9, text="a"),
        RetrievalResult(doc_id="2", score=0.8, text="b"),
        RetrievalResult(doc_id="3", score=0.7, text="c"),
    ]
    mock_cohere_client = MagicMock()
    mock_cohere_client.rerank.side_effect = RuntimeError("Cohere API unavailable")
    monkeypatch.setattr("app.services.retrieval_service._get_cohere_client", lambda: mock_cohere_client)

    result = rerank_with_cohere("query", candidates, top_n=2)

    assert [r.doc_id for r in result] == ["1", "2"]


def test_rerank_with_cohere_handles_empty_candidates(monkeypatch):
    mock_cohere_client = MagicMock()
    monkeypatch.setattr("app.services.retrieval_service._get_cohere_client", lambda: mock_cohere_client)

    result = rerank_with_cohere("query", [], top_n=5)

    assert result == []
    mock_cohere_client.rerank.assert_not_called()
