"""Cost tracking routes — aggregated and per-session cost breakdowns."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.config import settings
from app.db.dynamo import get_item, query_items, query_by_gsi, Key
from app.db.models import TABLE_COSTS, TABLE_SESSIONS

router = APIRouter()

CATEGORY_COLORS = {
    "llm_api": "#3B82F6",
    "compute_batch": "#F59E0B",
    "compute_fargate": "#10B981",
    "storage_efs": "#8B5CF6",
    "storage_s3": "#EC4899",
    "other": "#6B7280",
}


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class CategoryBreakdown(BaseModel):
    category: str
    amount: float
    percentage: float
    color: str


class DailyCost(BaseModel):
    date: str
    amount: float


class CostEntry(BaseModel):
    date: str
    category: str
    session_id: str | None = None
    job_id: str | None = None
    amount_usd: float = 0.0
    details: dict[str, Any] | None = None
    created_at: str | None = None


class CostSummary(BaseModel):
    total_cost: float
    budget_limit: float
    budget_remaining: float
    percentage_used: float
    period_start: str
    period_end: str
    by_category: list[CategoryBreakdown]
    by_model: list[CategoryBreakdown] = Field(default_factory=list)
    daily_costs: list[DailyCost]
    entries: list[CostEntry] = Field(default_factory=list)


class SessionCostBreakdown(BaseModel):
    session_id: str
    total_cost: float
    by_category: list[CategoryBreakdown]
    entries: list[CostEntry]


class TurnCost(BaseModel):
    turn_id: str
    total_cost: float
    iterations: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    models_used: dict[str, float]  # model_name -> cost
    tools_used: list[str]


class SessionDetailedCost(BaseModel):
    session_id: str
    total_cost: float
    turns: list[TurnCost]
    by_model: list[CategoryBreakdown]
    cache_efficiency: float  # percentage of input tokens that were cache hits


MODEL_COLORS = {
    "claude-opus-4-20250514": "#8B5CF6",
    "claude-sonnet-4-20250514": "#3B82F6",
    "claude-haiku-4-5-20251001": "#10B981",
    "gpt-4": "#F59E0B",
    "gpt-4-turbo": "#EC4899",
    "other": "#6B7280",
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/summary", response_model=CostSummary)
async def cost_summary(
    from_date: date = Query(..., description="Start date (inclusive) YYYY-MM-DD"),
    to_date: date = Query(..., description="End date (inclusive) YYYY-MM-DD"),
):
    """Aggregated cost summary over a date range."""
    if from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_date must be <= to_date.",
        )

    # Query each date in range
    all_items: list[dict[str, Any]] = []
    current = from_date
    while current <= to_date:
        date_str = current.isoformat()
        response = await query_items(TABLE_COSTS, Key("date").eq(date_str))
        all_items.extend(response.get("Items", []))
        current += timedelta(days=1)

    # Aggregate
    total = 0.0
    by_cat: dict[str, float] = defaultdict(float)
    by_day: dict[str, float] = defaultdict(float)
    by_model_map: dict[str, float] = defaultdict(float)
    entries: list[CostEntry] = []

    for item in all_items:
        cost = float(item.get("amount_usd", 0))
        cat = item.get("category", "other")
        day = item.get("date", "")
        total += cost
        by_cat[cat] += cost
        by_day[day] += cost
        # Extract model from details if present
        details = item.get("details", {}) or {}
        model_name = details.get("model")
        if model_name:
            by_model_map[model_name] += cost
        entries.append(CostEntry(**{k: v for k, v in item.items() if k != "SK"}))

    # Build category breakdown with percentages and colors
    categories = []
    for cat, amount in sorted(by_cat.items(), key=lambda x: -x[1]):
        categories.append(CategoryBreakdown(
            category=cat,
            amount=round(amount, 6),
            percentage=round((amount / total * 100) if total > 0 else 0, 1),
            color=CATEGORY_COLORS.get(cat, "#6B7280"),
        ))

    # Build daily costs (fill gaps with 0)
    daily_costs = []
    day_cursor = from_date
    while day_cursor <= to_date:
        day_str = day_cursor.isoformat()
        daily_costs.append(DailyCost(
            date=day_str,
            amount=round(by_day.get(day_str, 0), 6),
        ))
        day_cursor += timedelta(days=1)

    # Build model breakdown
    model_total = sum(by_model_map.values())
    models_breakdown = []
    for model, amount in sorted(by_model_map.items(), key=lambda x: -x[1]):
        models_breakdown.append(CategoryBreakdown(
            category=model,
            amount=round(amount, 6),
            percentage=round((amount / model_total * 100) if model_total > 0 else 0, 1),
            color=MODEL_COLORS.get(model, "#6B7280"),
        ))

    budget = settings.budget_limit_usd
    return CostSummary(
        total_cost=round(total, 6),
        budget_limit=budget,
        budget_remaining=round(max(budget - total, 0), 6),
        percentage_used=round((total / budget * 100) if budget > 0 else 0, 1),
        period_start=from_date.isoformat(),
        period_end=to_date.isoformat(),
        by_category=categories,
        by_model=models_breakdown,
        daily_costs=daily_costs,
        entries=entries,
    )


@router.get("/sessions/{session_id}", response_model=SessionCostBreakdown)
async def session_costs(session_id: str):
    """Per-session cost breakdown."""
    session = await get_item(TABLE_SESSIONS, {"session_id": session_id, "SK": "META"})
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )

    items = await query_by_gsi(
        table_name=TABLE_COSTS,
        index_name="session_id-created_at-index",
        key_name="session_id",
        key_value=session_id,
    )

    total = 0.0
    by_cat: dict[str, float] = defaultdict(float)
    entries: list[CostEntry] = []

    for item in items:
        cost = float(item.get("amount_usd", 0))
        cat = item.get("category", "other")
        total += cost
        by_cat[cat] += cost
        entries.append(CostEntry(**{k: v for k, v in item.items() if k != "SK"}))

    categories = []
    for cat, amount in sorted(by_cat.items(), key=lambda x: -x[1]):
        categories.append(CategoryBreakdown(
            category=cat,
            amount=round(amount, 6),
            percentage=round((amount / total * 100) if total > 0 else 0, 1),
            color=CATEGORY_COLORS.get(cat, "#6B7280"),
        ))

    return SessionCostBreakdown(
        session_id=session_id,
        total_cost=round(total, 6),
        by_category=categories,
        entries=entries,
    )


@router.get("/sessions/{session_id}/detailed", response_model=SessionDetailedCost)
async def session_detailed_costs(session_id: str):
    """Detailed per-session cost breakdown with per-turn drill-down."""
    session = await get_item(TABLE_SESSIONS, {"session_id": session_id, "SK": "META"})
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )

    items = await query_by_gsi(
        table_name=TABLE_COSTS,
        index_name="session_id-created_at-index",
        key_name="session_id",
        key_value=session_id,
    )

    # Group by turn_id
    turns_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total_cost = 0.0
    by_model_map: dict[str, float] = defaultdict(float)
    total_input = 0
    total_cache_read = 0
    total_cache_creation = 0

    for item in items:
        details = item.get("details", {}) or {}
        turn_id = details.get("turn_id", "unknown")
        turns_map[turn_id].append(item)

        cost = float(item.get("amount_usd", 0))
        total_cost += cost

        model_name = details.get("model", "unknown")
        by_model_map[model_name] += cost

        total_input += int(details.get("input_tokens", 0))
        total_cache_read += int(details.get("cache_read_tokens", 0))
        total_cache_creation += int(details.get("cache_creation_tokens", 0))

    # Build per-turn breakdown
    turns: list[TurnCost] = []
    for turn_id, turn_items in sorted(turns_map.items()):
        turn_cost = 0.0
        turn_input = 0
        turn_output = 0
        turn_cache_read = 0
        turn_cache_creation = 0
        turn_models: dict[str, float] = defaultdict(float)
        turn_tools: set[str] = set()

        for item in turn_items:
            cost = float(item.get("amount_usd", 0))
            details = item.get("details", {}) or {}
            turn_cost += cost
            turn_input += int(details.get("input_tokens", 0))
            turn_output += int(details.get("output_tokens", 0))
            turn_cache_read += int(details.get("cache_read_tokens", 0))
            turn_cache_creation += int(details.get("cache_creation_tokens", 0))

            model_name = details.get("model", "unknown")
            turn_models[model_name] += cost

            tools = details.get("tools_used", [])
            if isinstance(tools, list):
                turn_tools.update(tools)
            elif isinstance(tools, str) and tools:
                turn_tools.add(tools)

        turns.append(TurnCost(
            turn_id=turn_id,
            total_cost=round(turn_cost, 6),
            iterations=len(turn_items),
            input_tokens=turn_input,
            output_tokens=turn_output,
            cache_read_tokens=turn_cache_read,
            cache_creation_tokens=turn_cache_creation,
            models_used={k: round(v, 6) for k, v in turn_models.items()},
            tools_used=sorted(turn_tools),
        ))

    # Build model breakdown
    model_total = sum(by_model_map.values())
    models_breakdown = []
    for model, amount in sorted(by_model_map.items(), key=lambda x: -x[1]):
        models_breakdown.append(CategoryBreakdown(
            category=model,
            amount=round(amount, 6),
            percentage=round((amount / model_total * 100) if model_total > 0 else 0, 1),
            color=MODEL_COLORS.get(model, "#6B7280"),
        ))

    # Calculate cache efficiency
    total_all_input = total_input + total_cache_read + total_cache_creation
    cache_efficiency = (
        round(total_cache_read / total_all_input * 100, 1)
        if total_all_input > 0
        else 0.0
    )

    return SessionDetailedCost(
        session_id=session_id,
        total_cost=round(total_cost, 6),
        turns=turns,
        by_model=models_breakdown,
        cache_efficiency=cache_efficiency,
    )
