"""Async wrapper around the Anthropic Python SDK."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import anthropic

from app.config import settings
from app.llm.models import get_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ToolCall:
    """A single tool-use request returned by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class LLMResponse:
    """Complete (non-streaming) response from an Anthropic model."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(slots=True)
class StreamEvent:
    """A single event emitted while streaming a response."""

    type: str  # "token" | "tool_call" | "done" | "error"
    content: str = ""
    tool_call: ToolCall | None = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class AnthropicClient:
    """Thin async wrapper over the ``anthropic`` SDK."""

    def __init__(self, api_key: str | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or settings.anthropic_api_key,
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _compute_cost(
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_creation: int = 0,
    ) -> float:
        info = get_model(model_id)
        # Non-cached input tokens at full price
        regular_input = max(0, input_tokens - cache_read - cache_creation)
        cost = (regular_input / 1_000) * info.input_price_per_1k
        # Cache reads at 10% of input price
        cost += (cache_read / 1_000) * info.input_price_per_1k * 0.1
        # Cache writes at 125% of input price
        cost += (cache_creation / 1_000) * info.input_price_per_1k * 1.25
        # Output at output price
        cost += (output_tokens / 1_000) * info.output_price_per_1k
        return cost

    @staticmethod
    def _add_cache_breakpoints(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add cache_control breakpoints to conversation messages.

        Creates a deep copy so we never mutate the caller's data.
        The strategy places an ephemeral breakpoint on the second-to-last
        user message so the conversation prefix up to the previous turn
        is cached.
        """
        messages = copy.deepcopy(messages)

        # Find the second-to-last user message
        user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        if len(user_indices) < 2:
            return messages

        target_idx = user_indices[-2]
        target_msg = messages[target_idx]
        content = target_msg.get("content")

        if isinstance(content, list) and content:
            # Add cache_control to the last content block
            content[-1]["cache_control"] = {"type": "ephemeral"}
        elif isinstance(content, str):
            # Wrap string content in a content block with cache_control
            target_msg["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        return messages

    @staticmethod
    def _build_kwargs(
        messages: list[dict[str, Any]],
        model: str,
        system_prompt: str | None,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        messages = AnthropicClient._add_cache_breakpoints(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if tools:
            kwargs["tools"] = tools
        return kwargs

    @staticmethod
    def _extract_response(raw: Any, model: str) -> LLMResponse:
        """Parse an ``anthropic.types.Message`` into an ``LLMResponse``."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in raw.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input)
                )

        input_tokens = raw.usage.input_tokens
        output_tokens = raw.usage.output_tokens
        cache_read = getattr(raw.usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(raw.usage, "cache_creation_input_tokens", 0) or 0

        cost = AnthropicClient._compute_cost(
            model, input_tokens, output_tokens, cache_read, cache_creation
        )

        logger.info(
            "Anthropic call: model=%s input=%d output=%d cache_read=%d cache_write=%d cost=$%.4f",
            model, input_tokens, output_tokens, cache_read, cache_creation, cost,
        )

        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=model,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
        )

    # -- public API ---------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4_096,
    ) -> LLMResponse:
        """Send a non-streaming chat request and return the full response."""
        kwargs = self._build_kwargs(messages, model, system_prompt, tools, max_tokens)
        raw = await self._client.messages.create(**kwargs)
        return self._extract_response(raw, model)

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4_096,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat response, yielding ``StreamEvent`` objects."""
        kwargs = self._build_kwargs(messages, model, system_prompt, tools, max_tokens)

        current_tool_id: str | None = None
        current_tool_name: str | None = None
        tool_json_buf: str = ""

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                # -- text delta --
                if event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        yield StreamEvent(type="token", content=event.delta.text)
                    elif hasattr(event.delta, "partial_json"):
                        tool_json_buf += event.delta.partial_json

                # -- tool block start --
                elif event.type == "content_block_start":
                    cb = event.content_block
                    if hasattr(cb, "type") and cb.type == "tool_use":
                        current_tool_id = cb.id
                        current_tool_name = cb.name
                        tool_json_buf = ""

                # -- tool block stop --
                elif event.type == "content_block_stop":
                    if current_tool_id and current_tool_name:
                        import json as _json

                        try:
                            args = _json.loads(tool_json_buf) if tool_json_buf else {}
                        except _json.JSONDecodeError:
                            args = {}
                        yield StreamEvent(
                            type="tool_call",
                            tool_call=ToolCall(
                                id=current_tool_id,
                                name=current_tool_name,
                                arguments=args,
                            ),
                        )
                        current_tool_id = None
                        current_tool_name = None
                        tool_json_buf = ""

            # Final message carries usage info — compute cost.
            final = await stream.get_final_message()
            response = self._extract_response(final, model)
            yield StreamEvent(
                type="done",
                content=(
                    f'{{"input_tokens":{response.input_tokens},'
                    f'"output_tokens":{response.output_tokens},'
                    f'"cost_usd":{response.cost_usd:.6f}}}'
                ),
            )
