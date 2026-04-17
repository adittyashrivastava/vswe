"""LLM Router — dispatches requests to the correct provider client."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.config import settings
from app.llm.models import AVAILABLE_MODELS, ModelInfo, get_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unified response / event types (re-exported from provider clients)
# ---------------------------------------------------------------------------

# We import lazily inside methods to avoid import-time SDK requirements when
# only one provider is used.  The canonical types are defined in each client
# module; this router simply forwards them.


@dataclass(slots=True)
class UsageSnapshot:
    """Cumulative token / cost counters."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    request_count: int = 0


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class LLMRouter:
    """Route ``chat`` / ``stream_chat`` calls to the right provider client.

    Maintains thread-safe cumulative usage statistics.
    """

    def __init__(
        self,
        anthropic_api_key: str | None = None,
        openai_api_key: str | None = None,
    ) -> None:
        self._anthropic_client: Any | None = None
        self._openai_client: Any | None = None
        self._anthropic_api_key = anthropic_api_key
        self._openai_api_key = openai_api_key

        # Thread-safe counters
        self._lock = threading.Lock()
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cost_usd: float = 0.0
        self._request_count: int = 0

    # -- lazy client init ---------------------------------------------------

    def _get_anthropic_client(self) -> Any:
        if self._anthropic_client is None:
            from app.llm.anthropic_client import AnthropicClient

            self._anthropic_client = AnthropicClient(
                api_key=self._anthropic_api_key,
            )
        return self._anthropic_client

    def _get_openai_client(self) -> Any:
        if self._openai_client is None:
            from app.llm.openai_client import OpenAIClient

            self._openai_client = OpenAIClient(
                api_key=self._openai_api_key,
            )
        return self._openai_client

    def _client_for_model(self, model_id: str) -> Any:
        info = get_model(model_id)
        if info.provider == "anthropic":
            return self._get_anthropic_client()
        if info.provider == "openai":
            return self._get_openai_client()
        raise ValueError(f"Unsupported provider: {info.provider}")

    # -- usage tracking -----------------------------------------------------

    def _record_usage(
        self, input_tokens: int, output_tokens: int, cost_usd: float
    ) -> None:
        with self._lock:
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            self._total_cost_usd += cost_usd
            self._request_count += 1

    @property
    def usage(self) -> UsageSnapshot:
        """Return a snapshot of cumulative usage (thread-safe read)."""
        with self._lock:
            return UsageSnapshot(
                total_input_tokens=self._total_input_tokens,
                total_output_tokens=self._total_output_tokens,
                total_cost_usd=self._total_cost_usd,
                request_count=self._request_count,
            )

    # -- public API ---------------------------------------------------------

    @staticmethod
    def list_models() -> dict[str, ModelInfo]:
        """Return the full registry of available models."""
        return dict(AVAILABLE_MODELS)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4_096,
    ) -> Any:
        """Non-streaming chat — delegates to the right provider client.

        Returns the provider-specific ``LLMResponse`` dataclass.
        """
        model = model or settings.default_model
        client = self._client_for_model(model)

        response = await client.chat(
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            max_tokens=max_tokens,
        )

        self._record_usage(response.input_tokens, response.output_tokens, response.cost_usd)
        logger.debug(
            "chat model=%s in=%d out=%d cost=$%.4f",
            model,
            response.input_tokens,
            response.output_tokens,
            response.cost_usd,
        )
        return response

    async def chat_fast(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> Any:
        """Use the cheapest/fastest model for simple internal tasks.
        Routes to Haiku for summarization, classification, etc."""
        return await self.chat(
            messages=messages,
            model="claude-haiku-4-5-20251001",
            system_prompt=system_prompt,
            tools=tools,
            max_tokens=max_tokens,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4_096,
    ) -> AsyncIterator[Any]:
        """Streaming chat — delegates to the right provider client.

        Yields provider-specific ``StreamEvent`` objects.  The final event
        (``type="done"``) carries a JSON payload with token counts and cost;
        the router parses it to update cumulative usage.
        """
        model = model or settings.default_model
        client = self._client_for_model(model)

        async for event in client.stream_chat(
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            max_tokens=max_tokens,
        ):
            # Intercept the "done" event to track usage.
            if event.type == "done" and event.content:
                try:
                    import json as _json

                    payload = _json.loads(event.content)
                    self._record_usage(
                        payload.get("input_tokens", 0),
                        payload.get("output_tokens", 0),
                        payload.get("cost_usd", 0.0),
                    )
                except Exception:
                    logger.warning("Failed to parse done-event payload for usage tracking")
            yield event
