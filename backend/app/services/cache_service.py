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

import math
import time
from dataclasses import dataclass, field
from typing import Optional

from app.models.chat import SourceCitation

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
    created_at: float = field(default_factory=time.monotonic)


_cache: dict[CacheScope, list[CacheEntry]] = {}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _prune_expired(entries: list[CacheEntry]) -> list[CacheEntry]:
    now = time.monotonic()
    return [entry for entry in entries if now - entry.created_at < TTL_SECONDS]


def lookup(tenant_id: str, user_id: str, is_admin: bool, query_embedding: list[float]) -> Optional[CacheEntry]:
    scope: CacheScope = (tenant_id, user_id, is_admin)
    entries = _prune_expired(_cache.get(scope, []))
    _cache[scope] = entries
    if not entries:
        return None

    best_entry = max(entries, key=lambda entry: _cosine_similarity(entry.embedding, query_embedding))
    best_score = _cosine_similarity(best_entry.embedding, query_embedding)
    return best_entry if best_score >= SIMILARITY_THRESHOLD else None


def store(
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


def invalidate_tenant(tenant_id: str) -> None:
    """Drops every cached entry for tenant_id, across all users/scopes —
    called whenever a document upload/delete/share changes what that
    tenant's retrieval can return, so a stale cached answer can never
    shadow a document that was just added, removed, or reshared.
    """
    for scope in list(_cache.keys()):
        if scope[0] == tenant_id:
            del _cache[scope]
