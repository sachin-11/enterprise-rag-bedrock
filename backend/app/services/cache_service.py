"""In-memory semantic cache for chat answers.

Same in-memory-module-dict pattern as document_store.py, with the same
accepted v1 tradeoff: lost on restart, doesn't span multiple backend
processes. A real distributed cache (Redis) is the natural upgrade path if
this app ever runs more than one backend replica — not needed at current
scale.

Cache key is a query EMBEDDING, not a hash of the query text — a lookup
finds the best cosine-similarity match among a scope's entries rather than
an exact string. Scoped to (tenant_id, user_id, is_admin): retrieve_from_kb's
per-document sharing filter means two members of the same org can
legitimately get different, both-correct answers to the identical
question — sharing a cache entry across users would risk serving someone an
answer built from documents they can't actually see, so a cache hit must
stay exactly as scoped as a real retrieval would have been.
"""

from __future__ import annotations

import json
import logging
import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from app.models.chat import SourceCitation
from app.core.config import settings
from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

# A false-positive hit (confidently serving a wrong answer to a subtly
# different question) is worse than a false-negative (just paying for a
# normal pipeline run) — so this starts high rather than tuned for maximum
# hit-rate.
#
# Measured directly against text-embedding-3-small before picking this
# number: cosine similarity does NOT cleanly separate "same question,
# reworded" from "different question, similar structure" — e.g.
# "vacation days?" vs "sick days?" scored 0.71, HIGHER than "refund
# policy?" vs "what is our refund policy?" at 0.70, a genuine paraphrase.
# Any threshold low enough to reliably catch real paraphrases (~0.80-0.85)
# would also catch that kind of false positive. 0.93 accepts a lower hit
# rate (mostly near-identical repeats/minor rewording) in exchange for
# never serving a wrong cached answer — a deliberate, confirmed choice, not
# an untested guess.
SIMILARITY_THRESHOLD = 0.93
TTL_SECONDS = 600
MAX_ENTRIES_PER_SCOPE = 50

CacheScope = tuple[str, str, bool]  # (tenant_id, user_id, is_admin)


@dataclass
class CacheEntry:
    standalone_query: str
    embedding: list[float]
    answer_text: str
    sources: list[SourceCitation]
    created_at: float = field(default_factory=time.time)


_cache: dict[CacheScope, list[CacheEntry]] = {}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _prune_expired(entries: list[CacheEntry]) -> list[CacheEntry]:
    now = time.time()
    return [entry for entry in entries if now - entry.created_at < TTL_SECONDS]


def _memory_lookup(tenant_id: str, user_id: str, is_admin: bool, query_embedding: list[float]) -> Optional[CacheEntry]:
    scope: CacheScope = (tenant_id, user_id, is_admin)
    entries = _prune_expired(_cache.get(scope, []))
    _cache[scope] = entries
    if not entries:
        return None

    best_entry = max(entries, key=lambda entry: _cosine_similarity(entry.embedding, query_embedding))
    best_score = _cosine_similarity(best_entry.embedding, query_embedding)
    return best_entry if best_score >= SIMILARITY_THRESHOLD else None


def _memory_store(
    tenant_id: str,
    user_id: str,
    is_admin: bool,
    standalone_query: str,
    query_embedding: list[float],
    answer_text: str,
    sources: list[SourceCitation],
) -> None:
    scope: CacheScope = (tenant_id, user_id, is_admin)
    entries = _cache.setdefault(scope, [])
    entries.append(
        CacheEntry(
            standalone_query=standalone_query,
            embedding=query_embedding,
            answer_text=answer_text,
            sources=sources,
        )
    )
    # Oldest evicted first once the scope's cap is exceeded.
    if len(entries) > MAX_ENTRIES_PER_SCOPE:
        del entries[: len(entries) - MAX_ENTRIES_PER_SCOPE]


def _memory_invalidate_tenant(tenant_id: str) -> None:
    """Drops every cached entry for tenant_id, across all users/scopes —
    called whenever a document upload/delete/share changes what that
    tenant's retrieval can return, so a stale cached answer can never
    shadow a document that was just added, removed, or reshared.
    """
    for scope in list(_cache.keys()):
        if scope[0] == tenant_id:
            del _cache[scope]


def _redis_scope_key(tenant_id: str, user_id: str, is_admin: bool) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{user_id}:{int(is_admin)}".encode()).hexdigest()[:32]
    return f"semantic-cache:scope:{digest}"


def _redis_tenant_index(tenant_id: str) -> str:
    digest = hashlib.sha256(tenant_id.encode()).hexdigest()[:32]
    return f"semantic-cache:tenant:{digest}:scopes"


def _deserialize(raw: str) -> CacheEntry:
    value = json.loads(raw)
    return CacheEntry(
        standalone_query=value["standalone_query"],
        embedding=value["embedding"],
        answer_text=value["answer_text"],
        sources=[SourceCitation(**source) for source in value["sources"]],
        created_at=value["created_at"],
    )


def _redis_lookup(
    tenant_id: str,
    user_id: str,
    is_admin: bool,
    query_embedding: list[float],
) -> Optional[CacheEntry]:
    key = _redis_scope_key(tenant_id, user_id, is_admin)
    try:
        entries = _prune_expired([_deserialize(raw) for raw in get_redis_client().lrange(key, 0, -1)])
    except Exception as exc:
        logger.warning("Redis semantic-cache lookup failed; treating as a miss: %s", exc)
        return None
    if not entries:
        return None
    best = max(entries, key=lambda entry: _cosine_similarity(entry.embedding, query_embedding))
    return best if _cosine_similarity(best.embedding, query_embedding) >= SIMILARITY_THRESHOLD else None


def _redis_store(
    tenant_id: str,
    user_id: str,
    is_admin: bool,
    standalone_query: str,
    query_embedding: list[float],
    answer_text: str,
    sources: list[SourceCitation],
) -> None:
    key = _redis_scope_key(tenant_id, user_id, is_admin)
    index = _redis_tenant_index(tenant_id)
    payload = json.dumps(
        {
            "standalone_query": standalone_query,
            "embedding": query_embedding,
            "answer_text": answer_text,
            "sources": [source.model_dump() for source in sources],
            "created_at": time.time(),
        }
    )
    try:
        pipe = get_redis_client().pipeline(transaction=True)
        pipe.lpush(key, payload).ltrim(key, 0, MAX_ENTRIES_PER_SCOPE - 1).expire(key, TTL_SECONDS)
        pipe.sadd(index, key).expire(index, TTL_SECONDS)
        pipe.execute()
    except Exception as exc:
        logger.warning("Redis semantic-cache store failed; answer was not cached: %s", exc)


def lookup(
    tenant_id: str,
    user_id: str,
    is_admin: bool,
    query_embedding: list[float],
) -> Optional[CacheEntry]:
    if settings.semantic_cache_backend == "redis":
        return _redis_lookup(tenant_id, user_id, is_admin, query_embedding)
    return _memory_lookup(tenant_id, user_id, is_admin, query_embedding)


def store(
    tenant_id: str,
    user_id: str,
    is_admin: bool,
    standalone_query: str,
    query_embedding: list[float],
    answer_text: str,
    sources: list[SourceCitation],
) -> None:
    if settings.semantic_cache_backend == "redis":
        return _redis_store(tenant_id, user_id, is_admin, standalone_query, query_embedding, answer_text, sources)
    return _memory_store(tenant_id, user_id, is_admin, standalone_query, query_embedding, answer_text, sources)


def invalidate_tenant(tenant_id: str) -> None:
    _memory_invalidate_tenant(tenant_id)
    if settings.semantic_cache_backend != "redis":
        return
    index = _redis_tenant_index(tenant_id)
    try:
        redis = get_redis_client()
        keys = list(redis.smembers(index))
        if keys:
            redis.delete(*keys)
        redis.delete(index)
    except Exception as exc:
        logger.warning("Redis semantic-cache invalidation failed for tenant %s: %s", tenant_id, exc)
