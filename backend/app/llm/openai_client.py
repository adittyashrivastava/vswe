"""Async wrapper around the OpenAI Python SDK."""

from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import openai

from app.config import settings
from app.llm.models import get_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types  (mirror the Anthropic client for a unified interface)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ToolCall:
    """A single tool-use request returned by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class LLMResponse:
    """Complete (non-streaming) response from an OpenAI model."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""


@dataclass(slots=True)
class StreamEvent:
    """A single event emitted while streaming a response."""

    type: str  # "token" | "tool_call" | "done" | "error"
    content: str = ""
    tool_call: ToolCall | None = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OpenAIClient:
    """Thin async wrapper over the ``openai`` SDK."""

    def __init__(self, api_key: str | None = None) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=api_key or settings.openai_api_key,
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _compute_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
        info = get_model(model_id)
        return (
            (input_tokens / 1_000) * info.input_price_per_1k
            + (output_tokens / 1_000) * info.output_price_per_1k
        )

    @staticmethod
    def _build_kwargs(
        messages: list[dict[str, Any]],
        model: str,
        system_prompt: str | None,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        all_messages: list[dict[str, Any]] = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": all_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            # OpenAI expects tools in {"type": "function", "function": {...}} format.
            kwargs["tools"] = tools
        return kwargs

    @staticmethod
    def _parse_tool_calls(raw_tool_calls: Any) -> list[ToolCall]:
        result: list[ToolCall] = []
        if not raw_tool_calls:
            return result
        for tc in raw_tool_calls:
            try:
                args = _json.loads(tc.function.arguments) if tc.function.arguments else {}
            except _json.JSONDecodeError:
                args = {}
            result.append(
                ToolCall(id=tc.id, name=tc.function.name, arguments=args)
            )
        return result

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
        raw = await self._client.chat.completions.create(**kwargs)

        choice = raw.choices[0]
        content = choice.message.content or ""
        tool_calls = self._parse_tool_calls(choice.message.tool_calls)

        input_tokens = raw.usage.prompt_tokens if raw.usage else 0
        output_tokens = raw.usage.completion_tokens if raw.usage else 0

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self._compute_cost(model, input_tokens, output_tokens),
            model=model,
        )

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
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        # Accumulators for streamed tool calls keyed by index.
        pending_tools: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0

        raw_stream = await self._client.chat.completions.create(**kwargs)

        async for chunk in raw_stream:
            # -- usage chunk (arrives last when stream_options.include_usage is set)
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # -- text token --
            if delta.content:
                yield StreamEvent(type="token", content=delta.content)

            # -- tool call deltas --
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in pending_tools:
                        pending_tools[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    entry = pending_tools[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

            # -- finish reason --
            if chunk.choices[0].finish_reason:
                # Emit any accumulated tool calls.
                for entry in pending_tools.values():
                    try:
                        args = _json.loads(entry["arguments"]) if entry["arguments"] else {}
                    except _json.JSONDecodeError:
                        args = {}
                    yield StreamEvent(
                        type="tool_call",
                        tool_call=ToolCall(
                            id=entry["id"],
                            name=entry["name"],
                            arguments=args,
                        ),
                    )
                pending_tools.clear()

        cost = self._compute_cost(model, input_tokens, output_tokens)
        yield StreamEvent(
            type="done",
            content=(
                f'{{"input_tokens":{input_tokens},'
                f'"output_tokens":{output_tokens},'
                f'"cost_usd":{cost:.6f}}}'
            ),
        )
