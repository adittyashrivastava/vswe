"""LLM model definitions and pricing information."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Metadata and pricing for a single LLM model."""

    id: str
    provider: Literal["anthropic", "openai"]
    display_name: str
    input_price_per_1k: float   # USD per 1 000 input tokens
    output_price_per_1k: float  # USD per 1 000 output tokens
    max_context: int            # maximum context window in tokens
    supports_tools: bool = True


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

AVAILABLE_MODELS: dict[str, ModelInfo] = {
    # ── Anthropic ──────────────────────────────────────────────────────
    "claude-opus-4-20250514": ModelInfo(
        id="claude-opus-4-20250514",
        provider="anthropic",
        display_name="Claude Opus 4",
        input_price_per_1k=0.015,
        output_price_per_1k=0.075,
        max_context=200_000,
        supports_tools=True,
    ),
    "claude-sonnet-4-20250514": ModelInfo(
        id="claude-sonnet-4-20250514",
        provider="anthropic",
        display_name="Claude Sonnet 4",
        input_price_per_1k=0.003,
        output_price_per_1k=0.015,
        max_context=200_000,
        supports_tools=True,
    ),
    "claude-3-haiku-20250307": ModelInfo(
        id="claude-3-haiku-20250307",
        provider="anthropic",
        display_name="Claude 3 Haiku",
        input_price_per_1k=0.00025,
        output_price_per_1k=0.00125,
        max_context=200_000,
        supports_tools=True,
    ),
    # ── OpenAI ─────────────────────────────────────────────────────────
    "gpt-4": ModelInfo(
        id="gpt-4",
        provider="openai",
        display_name="GPT-4",
        input_price_per_1k=0.03,
        output_price_per_1k=0.06,
        max_context=8_192,
        supports_tools=True,
    ),
    "gpt-4-turbo": ModelInfo(
        id="gpt-4-turbo",
        provider="openai",
        display_name="GPT-4 Turbo",
        input_price_per_1k=0.01,
        output_price_per_1k=0.03,
        max_context=128_000,
        supports_tools=True,
    ),
    "gpt-4o": ModelInfo(
        id="gpt-4o",
        provider="openai",
        display_name="GPT-4o",
        input_price_per_1k=0.005,
        output_price_per_1k=0.015,
        max_context=128_000,
        supports_tools=True,
    ),
}


def get_model(model_id: str) -> ModelInfo:
    """Return *ModelInfo* for *model_id* or raise ``KeyError``."""
    try:
        return AVAILABLE_MODELS[model_id]
    except KeyError:
        raise KeyError(
            f"Unknown model '{model_id}'. "
            f"Available: {', '.join(sorted(AVAILABLE_MODELS))}"
        ) from None
