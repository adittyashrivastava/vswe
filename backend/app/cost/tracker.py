"""Cost tracker — records and aggregates cost events in DynamoDB."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ulid import ULID

from app.db import dynamo
from app.db.models import CostCategory, CostItem, TABLE_COSTS

from .pricing import calculate_llm_cost

logger = logging.getLogger(__name__)


class CostTracker:
    """Records individual cost events to the ``vswe-costs`` DynamoDB table
    and provides aggregation queries.

    Designed as a lightweight, reusable object (not a true singleton) — callers
    can share a module-level instance or create their own.
    """

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    async def record_llm_cost(
        self,
        session_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        turn_id: str | None = None,
        iteration: int | None = None,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        tool_calls: list[str] | None = None,
    ) -> CostItem:
        """Calculate and persist the cost of a single LLM call.

        Returns the persisted ``CostItem``.
        """
        amount = calculate_llm_cost(model, input_tokens, output_tokens)
        now = datetime.now(timezone.utc)

        details: dict[str, Any] = {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "turn_id": turn_id,
            "iteration": iteration,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "tool_calls": tool_calls,
        }

        item = CostItem(
            date=now.strftime("%Y-%m-%d"),
            cost_entry_id=str(ULID()),
            category=CostCategory.LLM_API,
            session_id=session_id,
            amount_usd=amount,
            details=details,
            created_at=now.isoformat(),
        )

        await dynamo.put_item(TABLE_COSTS, item.to_dynamo_item())
        logger.debug(
            "Recorded LLM cost $%.6f for session %s (model=%s, in=%d, out=%d)",
            amount, session_id, model, input_tokens, output_tokens,
        )
        return item

    async def record_compute_cost(
        self,
        job_id: str,
        session_id: str,
        category: str,
        amount: float,
        details: dict,
    ) -> CostItem:
        """Record an arbitrary compute cost (Batch, Fargate, storage, etc.).

        Parameters
        ----------
        job_id : str
            The job this cost is associated with (may be empty for non-job costs).
        session_id : str
            The session that triggered the cost.
        category : str
            Must match a ``CostCategory`` value (e.g. ``"compute_batch"``).
        amount : float
            Cost in USD.
        details : dict
            Free-form metadata (instance type, duration, etc.).
        """
        now = datetime.now(timezone.utc)

        item = CostItem(
            date=now.strftime("%Y-%m-%d"),
            cost_entry_id=str(ULID()),
            category=CostCategory(category),
            session_id=session_id,
            job_id=job_id or None,
            amount_usd=amount,
            details=details,
            created_at=now.isoformat(),
        )

        await dynamo.put_item(TABLE_COSTS, item.to_dynamo_item())
        logger.debug(
            "Recorded %s cost $%.6f for session %s (job=%s)",
            category, amount, session_id, job_id,
        )
        return item

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    async def get_total_cost(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> float:
        """Return the total cost in USD across all sessions.

        Parameters
        ----------
        from_date : str, optional
            Inclusive start date (ISO format, e.g. ``"2026-04-01"``).
            Defaults to ``"2020-01-01"`` to capture everything.
        to_date : str, optional
            Inclusive end date. Defaults to today.
        """
        from boto3.dynamodb.conditions import Key

        start = from_date or "2020-01-01"
        end = to_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        total = 0.0
        # Iterate over each date partition in the range.  For a production
        # system with high cardinality you would use a GSI or pre-aggregated
        # counters, but for a solo/small-team budget tracker this is fine.
        from datetime import date as _date, timedelta
        current = _date.fromisoformat(start)
        end_date = _date.fromisoformat(end)

        while current <= end_date:
            date_str = current.isoformat()
            items = await dynamo.query_all_items(
                TABLE_COSTS,
                Key("date").eq(date_str),
            )
            for item in items:
                # DynamoDB returns Decimal; float() handles both Decimal and float
                total += float(item.get("amount_usd", 0))
            current += timedelta(days=1)

        return round(total, 8)

    async def get_session_cost(self, session_id: str) -> float:
        """Return the total cost for a single session."""
        from boto3.dynamodb.conditions import Key

        items = await dynamo.query_all_items(
            TABLE_COSTS,
            Key("session_id").eq(session_id),
            index_name="session_id-created_at-index",
        )
        total = sum(float(item.get("amount_usd", 0)) for item in items)
        return round(total, 8)


# Module-level convenience instance
cost_tracker = CostTracker()
