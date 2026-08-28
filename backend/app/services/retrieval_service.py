"""Hybrid retrieval: dense + sparse OpenSearch search, RRF fusion, Cohere rerank.

Expects an OpenSearch index with at least these fields per document:
  - `embedding`  (knn_vector) — used by retrieve_dense
  - `text`       (text)       — used by retrieve_sparse (BM25) and as the
                                 rerank candidate document
  - `tenant_id`  (keyword)    — used to scope every query to one tenant

Index creation/mapping is out of scope here; this module only queries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, TypeVar

import cohere
from langchain_openai import OpenAIEmbeddings
from langsmith import traceable

from app.core.aws_clients import get_opensearch_client
from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_INDEX_NAME = "documents"
EMBEDDING_FIELD = "embedding"
TEXT_FIELD = "text"
TENANT_ID_FIELD = "tenant_id"
COHERE_RERANK_MODEL = "rerank-v3.5"

# Single source of truth for the embedding model: retrieve_dense's knn query
# and whatever writes `embedding` into the index (see ingestion_service.py)
# must use the same model/dimension or vector search silently degrades.
EMBEDDING_MODEL_ID = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536


@dataclass
class RetrievalResult:
    doc_id: str
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@lru_cache
def _get_cohere_client() -> cohere.Client:
    return cohere.Client(api_key=settings.cohere_api_key)


@lru_cache
def _get_embeddings_client() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(api_key=settings.openai_api_key, model=EMBEDDING_MODEL_ID)


def embed_text(text: str) -> list[float]:
    return _get_embeddings_client().embed_query(text)


def embed_texts(texts: list[str]) -> list[list[float]]:
    return _get_embeddings_client().embed_documents(texts)


def ensure_index_exists(index_name: str = DEFAULT_INDEX_NAME) -> None:
    """Create `index_name` with the k-NN mapping this module expects, if it doesn't exist yet."""
    client = get_opensearch_client()
    if client.indices.exists(index=index_name):
        return

    client.indices.create(
        index=index_name,
        body={
            "settings": {"index": {"knn": True}},
            "mappings": {
                "properties": {
                    EMBEDDING_FIELD: {
                        "type": "knn_vector",
                        "dimension": EMBEDDING_DIMENSION,
                        "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "nmslib"},
                    },
                    TEXT_FIELD: {"type": "text"},
                    TENANT_ID_FIELD: {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "doc_name": {"type": "keyword"},
                    "chunk_id": {"type": "keyword"},
                    "char_start": {"type": "integer"},
                    "char_end": {"type": "integer"},
                    "section_heading": {"type": "keyword"},
                }
            },
        },
    )


def _parse_hits(response: dict) -> list[RetrievalResult]:
    hits = response.get("hits", {}).get("hits", [])
    return [
        RetrievalResult(
            doc_id=hit["_id"],
            score=hit.get("_score") or 0.0,
            text=hit.get("_source", {}).get(TEXT_FIELD, ""),
            metadata=hit.get("_source", {}),
        )
        for hit in hits
    ]


def retrieve_dense(
    query_embedding: list[float],
    tenant_id: str,
    top_k: int = 20,
    index_name: str = DEFAULT_INDEX_NAME,
) -> list[RetrievalResult]:
    """k-NN vector search over `index_name`, scoped to tenant_id."""
    client = get_opensearch_client()
    body = {
        "size": top_k,
        "query": {
            "bool": {
                "must": [{"knn": {EMBEDDING_FIELD: {"vector": query_embedding, "k": top_k}}}],
                "filter": [{"term": {TENANT_ID_FIELD: tenant_id}}],
            }
        },
    }
    response = client.search(index=index_name, body=body)
    return _parse_hits(response)


def retrieve_sparse(
    query_text: str,
    tenant_id: str,
    top_k: int = 20,
    index_name: str = DEFAULT_INDEX_NAME,
) -> list[RetrievalResult]:
    """BM25 keyword search (match query) over `index_name`, scoped to tenant_id."""
    client = get_opensearch_client()
    body = {
        "size": top_k,
        "query": {
            "bool": {
                "must": [{"match": {TEXT_FIELD: query_text}}],
                "filter": [{"term": {TENANT_ID_FIELD: tenant_id}}],
            }
        },
    }
    response = client.search(index=index_name, body=body)
    return _parse_hits(response)


def reciprocal_rank_fusion(
    dense_results: list[RetrievalResult],
    sparse_results: list[RetrievalResult],
    k: int = 60,
) -> list[RetrievalResult]:
    """Merge two ranked result lists with Reciprocal Rank Fusion (RRF).

    For each ranked list, a document at 1-based rank r contributes a score
    of 1 / (k + r). A document's final RRF score is the sum of that term
    over every list it appears in — so a document ranked highly in both
    dense and sparse search outranks one that only did well in one of them.

        RRF(d) = sum over lists L containing d of  1 / (k + rank_L(d))

    k dampens the impact of any single rank (without it, rank 1 vs rank 2
    would differ by 2x; with k=60 that gap shrinks to ~1.6%), which keeps a
    single list's #1 result from automatically dominating the fused order.
    k=60 is the default from the original RRF paper (Cormack et al., 2009)
    and is a common default in hybrid search.

    Documents present in both lists get their `text`/`metadata` from the
    dense result (arbitrary but deterministic — the two copies describe the
    same document). The returned `score` is the fused RRF score, not either
    original OpenSearch score.
    """
    rrf_scores: dict[str, float] = {}
    representative: dict[str, RetrievalResult] = {}

    for ranked_list in (dense_results, sparse_results):
        for rank, result in enumerate(ranked_list, start=1):
            rrf_scores[result.doc_id] = rrf_scores.get(result.doc_id, 0.0) + 1.0 / (k + rank)
            representative.setdefault(result.doc_id, result)

    fused = [replace(representative[doc_id], score=score) for doc_id, score in rrf_scores.items()]
    fused.sort(key=lambda r: r.score, reverse=True)
    return fused


RerankCandidate = TypeVar("RerankCandidate")


@traceable(name="rerank_with_cohere", run_type="tool")
def rerank_with_cohere(
    query: str,
    candidates: list[RerankCandidate],
    top_n: int = 5,
) -> list[RerankCandidate]:
    """Rerank `candidates` against `query` with the Cohere Rerank API.

    Works with any dataclass exposing `.text` (read) and `.score` (rewritten
    via `dataclasses.replace`) — both RetrievalResult (OpenSearch) and
    KBRetrievalResult (Bedrock KB, see bedrock_kb_service.py) qualify.

    Falls back to the first `top_n` candidates in their existing order if
    the Cohere call fails, so a reranker outage degrades quality rather
    than breaking retrieval entirely.
    """
    if not candidates:
        return []

    client = _get_cohere_client()
    try:
        response = client.rerank(
            model=COHERE_RERANK_MODEL,
            query=query,
            documents=[c.text for c in candidates],
            top_n=min(top_n, len(candidates)),
        )
    except Exception as exc:  # Cohere SDK / network errors: degrade, don't break retrieval.
        logger.warning("Cohere rerank failed, falling back to fusion order: %s", exc)
        return candidates[:top_n]

    return [replace(candidates[result.index], score=result.relevance_score) for result in response.results]
