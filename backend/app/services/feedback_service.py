"""Answer feedback (👍/👎), stored directly on the LangSmith run it rates —
no separate database. Kept apart from admin_service.py deliberately: that
module is conceptually admin-scoped (only ever imported by the admin-gated
router), while submitting feedback is a regular-member action on their own
chat, not an admin operation.
"""

from __future__ import annotations

from functools import lru_cache

from langsmith import Client

from app.core.config import settings

FEEDBACK_KEY = "user_score"


class FeedbackError(Exception):
    """Raised when a run doesn't exist or doesn't belong to the caller's org."""


@lru_cache
def _langsmith_client() -> Client:
    return Client(api_key=settings.langsmith_api_key)


def submit_feedback(tenant_id: str, run_id: str, is_positive: bool, comment: str | None) -> None:
    """Attaches feedback to run_id, after verifying it belongs to tenant_id.

    Re-verifying the run's own tenant_id metadata (rather than trusting the
    caller-supplied run_id outright) is the actual authorization check here
    — same pattern as admin_service.get_retry_inputs — so a member can't
    attach feedback to a run_id borrowed/guessed from another organization.
    """
    client = _langsmith_client()
    rows = list(client.list_runs(run_ids=[run_id], select=["extra"]))
    if not rows:
        raise FeedbackError("Run not found.")

    metadata = (rows[0].extra or {}).get("metadata", {}) or {}
    if metadata.get("tenant_id") != tenant_id:
        raise FeedbackError("Run not found.")  # same message as "not found" — no cross-org existence leak

    client.create_feedback(run_id=run_id, key=FEEDBACK_KEY, score=is_positive, comment=comment)
