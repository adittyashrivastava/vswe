"""Conversation context management for the VSWE agent.

Manages message history, serialisation (for DynamoDB persistence),
token-budget truncation, tool-result compaction, and disk persistence.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Rough estimate: 1 token ~ 4 characters.  Good enough for budget decisions.
_CHARS_PER_TOKEN = 4

# Tool-result compaction thresholds
TOOL_RESULT_COMPACT_THRESHOLD = 2000   # chars
TOOL_RESULT_PREVIEW_SIZE = 500          # chars


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Quick-and-dirty token estimate from a list of messages."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(json.dumps(block, default=str))
                else:
                    total_chars += len(str(block))
    return total_chars // _CHARS_PER_TOKEN


class ConversationContext:
    """Manages the message history for a single agent session.

    Messages are stored in the Anthropic API format:
    - ``{"role": "user", "content": "..."}``
    - ``{"role": "assistant", "content": [...]}``  (may include tool_use blocks)
    - ``{"role": "user", "content": [{"type": "tool_result", ...}]}``  (tool results)
    """

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []
        self._current_iteration: int = 0

    # -- iteration tracking ----------------------------------------------------

    def mark_new_iteration(self) -> None:
        """Increment the current iteration counter."""
        self._current_iteration += 1

    # -- adding messages -------------------------------------------------------

    def add_user_message(self, content: str) -> None:
        """Append a user text message."""
        self._messages.append({"role": "user", "content": content})

    def add_assistant_message(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """Append an assistant response.

        *tool_calls* should be a list of dicts, each with keys
        ``id``, ``name``, ``arguments`` (matching ``ToolCall`` from the
        Anthropic client).  They are stored as ``tool_use`` content blocks.
        """
        blocks: list[dict[str, Any]] = []

        if content:
            blocks.append({"type": "text", "text": content})

        if tool_calls:
            for tc in tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["arguments"],
                })

        msg: dict[str, Any] = {
            "role": "assistant",
            "content": blocks,
            "_meta_iteration": self._current_iteration,
        }
        self._messages.append(msg)

    def add_tool_result(self, tool_use_id: str, content: str) -> None:
        """Append a tool result for a preceding tool_use block.

        If the last message is already a user message containing tool_result
        blocks, the new result is appended to it (Anthropic requires all
        tool results for a single turn in one user message).
        """
        result_block = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }

        # Merge into existing tool-result user message if present
        if self._messages and self._messages[-1]["role"] == "user":
            last_content = self._messages[-1]["content"]
            if isinstance(last_content, list) and last_content and last_content[0].get("type") == "tool_result":
                last_content.append(result_block)
                return

        msg: dict[str, Any] = {
            "role": "user",
            "content": [result_block],
            "_meta_iteration": self._current_iteration,
        }
        self._messages.append(msg)

    # -- retrieval -------------------------------------------------------------

    def get_messages(self) -> list[dict[str, Any]]:
        """Return the message list formatted for the LLM API.

        Strips ``_meta_iteration`` keys so the LLM never sees internal metadata.
        """
        cleaned: list[dict[str, Any]] = []
        for msg in self._messages:
            out = {k: v for k, v in msg.items() if not k.startswith("_meta_")}
            cleaned.append(out)
        return cleaned

    @property
    def message_count(self) -> int:
        return len(self._messages)

    @property
    def estimated_tokens(self) -> int:
        return _estimate_tokens(self._messages)

    # -- tool-result compaction ------------------------------------------------

    def _get_tool_name_for_id(self, tool_use_id: str) -> str:
        """Scan assistant messages for a tool_use block matching *tool_use_id*.

        Returns the tool name, or ``"unknown_tool"`` if not found.
        """
        for msg in self._messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id") == tool_use_id
                ):
                    return block.get("name", "unknown_tool")
        return "unknown_tool"

    def _find_cache_safe_boundary(self) -> int:
        """Return the index of the 2nd-to-last user message.

        Messages at or before this index are in the cached prefix and
        must NOT be mutated — doing so would invalidate prompt caching.
        Messages after this index are in the "fresh" zone and can be
        safely compacted.

        Returns -1 if there are fewer than 2 user messages (no cached
        prefix to protect).
        """
        user_indices = [
            i for i, m in enumerate(self._messages) if m.get("role") == "user"
        ]
        if len(user_indices) < 2:
            return -1
        return user_indices[-2]

    def compact_tool_results(self, workspace_path: str) -> None:
        """Compact tool-result blocks based on iteration age.

        Only compacts messages AFTER the cache breakpoint (2nd-to-last
        user message) to avoid invalidating prompt caching. Messages in
        the cached prefix are left untouched.

        Rules for compactable messages:
        - Current iteration: untouched.
        - 1-2 iterations ago, over threshold: truncated with preview, full
          content saved to disk.
        - 3+ iterations ago (any size): replaced with a short summary line.
        """
        cache_boundary = self._find_cache_safe_boundary()
        results_dir = os.path.join(workspace_path, ".vswe", "tool-results")

        for idx, msg in enumerate(self._messages):
            # Never compact messages in the cached prefix
            if idx <= cache_boundary:
                continue

            # Never compact the first user message
            if idx == 0 and msg.get("role") == "user":
                continue

            content = msg.get("content")
            if not isinstance(content, list):
                continue

            msg_iteration = msg.get("_meta_iteration")
            if msg_iteration is None:
                continue

            iteration_age = self._current_iteration - msg_iteration

            # Current iteration: never touch
            if iteration_age <= 0:
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue

                tool_use_id = block.get("tool_use_id", "")
                raw_content = block.get("content", "")
                if not isinstance(raw_content, str):
                    raw_content = json.dumps(raw_content, default=str)

                if iteration_age >= 3:
                    # 3+ iterations ago: replace entirely
                    tool_name = self._get_tool_name_for_id(tool_use_id)
                    block["content"] = (
                        f"[{tool_name} — output removed, iteration {msg_iteration}]"
                    )

                elif iteration_age >= 1:
                    # 1-2 iterations ago: truncate if over threshold
                    if len(raw_content) > TOOL_RESULT_COMPACT_THRESHOLD:
                        # Ensure directory exists (only create when needed)
                        os.makedirs(results_dir, exist_ok=True)

                        # Persist full content to disk
                        file_path = os.path.join(results_dir, f"{tool_use_id}.txt")
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(raw_content)

                        original_length = len(raw_content)
                        preview = raw_content[:TOOL_RESULT_PREVIEW_SIZE]
                        block["content"] = (
                            f"[Truncated — {original_length} chars. Preview below]\n"
                            f"{preview}\n"
                            f"[Full output saved to .vswe/tool-results/{tool_use_id}.txt]"
                        )

    # -- truncation ------------------------------------------------------------

    def truncate_if_needed(self, max_tokens: int) -> None:
        """If estimated tokens exceed *max_tokens*, drop older messages.

        Strategy:
        1. Always keep the first user message (original request context).
        2. Always keep the last 6 messages (recent conversation).
        3. Replace dropped middle messages with a single summary message.

        This is a coarse heuristic; a production system might use the LLM
        itself to summarise, but that adds latency and cost.
        """
        if _estimate_tokens(self._messages) <= max_tokens:
            return

        if len(self._messages) <= 8:
            # Too few messages to meaningfully truncate — leave as is.
            return

        keep_front = 1
        keep_back = 6

        front = self._messages[:keep_front]
        back = self._messages[-keep_back:]
        dropped_count = len(self._messages) - keep_front - keep_back

        summary = {
            "role": "user",
            "content": (
                f"[System note: {dropped_count} earlier messages were truncated "
                "to fit the context window. The conversation continues below.]"
            ),
        }

        self._messages = front + [summary] + back
        logger.info(
            "Truncated conversation: dropped %d messages, estimated tokens now %d",
            dropped_count,
            _estimate_tokens(self._messages),
        )

    # -- serialisation (DynamoDB) ----------------------------------------------

    def to_serializable(self) -> dict[str, Any]:
        """Return a JSON-safe dict for DynamoDB storage.

        Includes ``_current_iteration`` and preserves ``_meta_iteration`` on
        each message so compaction state survives save/load cycles.
        """
        return {
            "_current_iteration": self._current_iteration,
            "messages": copy.deepcopy(self._messages),
        }

    @classmethod
    def from_serializable(cls, data: list[dict[str, Any]] | dict[str, Any]) -> ConversationContext:
        """Reconstruct a ``ConversationContext`` from persisted data.

        Accepts both the new dict format (with ``_current_iteration``) and the
        legacy plain-list format for backwards compatibility.
        """
        ctx = cls()

        if isinstance(data, list):
            # Legacy format: plain list of messages
            ctx._messages = _strip_decimals(copy.deepcopy(data)) if data else []
            ctx._current_iteration = 0
        elif isinstance(data, dict):
            ctx._current_iteration = int(data.get("_current_iteration", 0))
            raw_messages = data.get("messages", [])
            ctx._messages = _strip_decimals(copy.deepcopy(raw_messages)) if raw_messages else []
        else:
            ctx._messages = []
            ctx._current_iteration = 0

        return ctx


def _strip_decimals(obj: Any) -> Any:
    """Recursively convert Decimal values back to int/float (DynamoDB returns Decimals)."""
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    if isinstance(obj, dict):
        return {k: _strip_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_decimals(item) for item in obj]
    return obj
