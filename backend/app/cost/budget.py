"""Budget enforcement — check before every LLM call and job submission."""

from __future__ import annotations

import logging

from app.config import settings

from .tracker import cost_tracker

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when cumulative cost exceeds the configured budget limit."""

    def __init__(self, current_cost: float, limit: float, session_id: str | None = None):
        self.current_cost = current_cost
        self.limit = limit
        self.session_id = session_id
        scope = f"session {session_id}" if session_id else "global"
        super().__init__(
            f"Budget exceeded for {scope}: "
            f"${current_cost:.4f} >= ${limit:.4f}"
        )


async def check_budget(session_id: str | None = None) -> None:
    """Raise ``BudgetExceededError`` if cumulative cost exceeds the budget.

    Call this **before** every LLM call and job submission.

    If *session_id* is provided, only that session's cost is checked.
    Otherwise the global total across all sessions is checked.

    The budget limit is read from ``settings.budget_limit_usd``
    (default $5.00, configurable via the ``BUDGET_LIMIT_USD`` env var).
    """
    limit = settings.budget_limit_usd

    if session_id:
        current = await cost_tracker.get_session_cost(session_id)
    else:
        current = await cost_tracker.get_total_cost()

    if current >= limit:
        logger.warning(
            "Budget exceeded: $%.4f >= $%.4f (session=%s)",
            current, limit, session_id or "GLOBAL",
        )
        raise BudgetExceededError(current, limit, session_id)

    logger.debug(
        "Budget check passed: $%.4f / $%.4f (session=%s)",
        current, limit, session_id or "GLOBAL",
    )
