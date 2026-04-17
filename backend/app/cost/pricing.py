"""LLM and compute pricing tables for cost estimation."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# LLM pricing — cost per 1,000 tokens (USD)
# ---------------------------------------------------------------------------

LLM_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-20250514": {"input_per_1k": 0.015, "output_per_1k": 0.075},
    "claude-sonnet-4-20250514": {"input_per_1k": 0.003, "output_per_1k": 0.015},
    "claude-haiku-4-5-20251001": {"input_per_1k": 0.001, "output_per_1k": 0.005},
    "gpt-4": {"input_per_1k": 0.03, "output_per_1k": 0.06},
    "gpt-4-turbo": {"input_per_1k": 0.01, "output_per_1k": 0.03},
    "gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015},
}

# ---------------------------------------------------------------------------
# AWS Fargate pricing (us-east-1)
# ---------------------------------------------------------------------------

FARGATE_PRICING: dict[str, float] = {
    "vcpu_per_hour": 0.04048,
    "gb_per_hour": 0.004445,
    "spot_discount": 0.7,  # Spot is ~70% cheaper than on-demand
}


# ---------------------------------------------------------------------------
# Calculators
# ---------------------------------------------------------------------------

def calculate_llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return the USD cost for a single LLM call.

    If the model is not in the pricing table, falls back to the most
    expensive model (claude-opus-4-20250514) pricing as a conservative estimate.
    """
    pricing = LLM_PRICING.get(model, LLM_PRICING["claude-opus-4-20250514"])
    input_cost = (input_tokens / 1_000) * pricing["input_per_1k"]
    output_cost = (output_tokens / 1_000) * pricing["output_per_1k"]
    return round(input_cost + output_cost, 8)


def calculate_fargate_cost(
    vcpu: float,
    memory_gb: float,
    hours: float,
    spot: bool = True,
) -> float:
    """Return the USD cost for a Fargate task.

    Parameters
    ----------
    vcpu : float
        Number of vCPUs allocated (e.g. 0.25, 0.5, 1, 2, 4).
    memory_gb : float
        Memory in GB (e.g. 0.5, 1, 2, 4, 8).
    hours : float
        Duration in hours.
    spot : bool
        If ``True``, applies the Fargate Spot discount.
    """
    vcpu_cost = vcpu * FARGATE_PRICING["vcpu_per_hour"] * hours
    mem_cost = memory_gb * FARGATE_PRICING["gb_per_hour"] * hours
    total = vcpu_cost + mem_cost
    if spot:
        total *= (1 - FARGATE_PRICING["spot_discount"])
    return round(total, 8)


def calculate_batch_cost(spot_price_per_hour: float, hours: float) -> float:
    """Return the USD cost for an ECS Fargate compute job.

    Parameters
    ----------
    spot_price_per_hour : float
        Fargate hourly rate (based on vCPU + memory).
    hours : float
        Wall-clock duration of the job in hours.
    """
    return round(spot_price_per_hour * hours, 8)
