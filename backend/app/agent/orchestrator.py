"""Agent orchestrator — the main tool-use loop for VSWE.

The ``AgentOrchestrator`` drives a multi-turn conversation between the LLM
and the tool executors, streaming events back to the caller via an optional
``on_event`` callback.
"""

from __future__ import annotations

import json
import logging
import time
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from app.agent.context import ConversationContext
from app.agent.permissions import (
    PERMISSION_PROMPT_SNIPPETS,
    RepoPermissions,
    check_repo_permissions,
    get_tools_for_permission_level,
)
from app.agent.system_prompts import CHAT_SYSTEM_PROMPT, GITHUB_ISSUE_SYSTEM_PROMPT
from app.agent.tools import TOOL_DEFINITIONS, execute_tool
from app.config import settings
from app.db import dynamo
from app.db.models import (
    MessageItem,
    MessageRole,
    SessionItem,
    SessionState,
    TABLE_MESSAGES,
    TABLE_SESSIONS,
)
from app.cost.tracker import cost_tracker
from app.llm.router import LLMRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# Event callback signature: async (event_type: str, data: dict) -> None
EventCallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ITERATIONS = 20
_MAX_CONTEXT_TOKENS = 180_000  # Leave headroom below the 200k model limit


def _ulid() -> str:
    """Generate a ULID-like sortable unique ID (timestamp + random)."""
    import uuid
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().hex[:10]
    return f"{ts:013x}-{rand}"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class AgentOrchestrator:
    """Drives the agent loop for a single session.

    Usage::

        orch = AgentOrchestrator(session_id, workspace_path, model="claude-opus-4-20250514")
        await orch.load_state()      # reload from DynamoDB if resuming
        final = await orch.run("Fix the login bug", on_event=ws_callback)
    """

    def __init__(
        self,
        session_id: str,
        workspace_path: str,
        model: str | None = None,
        session_type: str = "chat",
        github_access_token: str | None = None,
        repo_full_name: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.workspace_path = workspace_path
        self.model = model or settings.default_model
        self.session_type = session_type
        self.github_access_token = github_access_token
        self.repo_full_name = repo_full_name

        self._context = ConversationContext()
        self._llm = LLMRouter(
            anthropic_api_key=settings.anthropic_api_key,
            openai_api_key=settings.openai_api_key,
        )
        self._system_prompt = (
            GITHUB_ISSUE_SYSTEM_PROMPT
            if session_type == "github_issue"
            else CHAT_SYSTEM_PROMPT
        )
        self._permissions: RepoPermissions | None = None
        self._github_login: str | None = None
        self._accessible_repos: list[str] | None = None
        self._turn_counter: int = 0
        self._current_turn_id: str | None = None

    # -- permissions --------------------------------------------------------

    async def resolve_repo_permissions(self, repo_full_name: str) -> None:
        """Resolve the user's permissions on the given repository.

        Calls the GitHub API with the user's token and stores the result
        in ``self._permissions``.  The permission level is used to scope
        the tools available to the LLM and to append guidance to the
        system prompt.
        """
        if not self.github_access_token:
            logger.warning(
                "Cannot resolve permissions — no github_access_token for session %s",
                self.session_id,
            )
            return

        self._permissions = await check_repo_permissions(
            self.github_access_token, repo_full_name,
        )
        self.repo_full_name = repo_full_name
        logger.info(
            "Resolved permissions for session %s on %s: %s",
            self.session_id,
            repo_full_name,
            self._permissions.level.value,
        )

    # -- public API ---------------------------------------------------------

    async def load_user_context(self, github_login: str | None = None) -> None:
        """Load the user's GitHub context (login, accessible repos) for the system prompt."""
        if github_login:
            self._github_login = github_login

        if self.github_access_token and not self._accessible_repos:
            try:
                import httpx

                async with httpx.AsyncClient() as client:
                    # Get user info if we don't have the login
                    if not self._github_login:
                        resp = await client.get(
                            "https://api.github.com/user",
                            headers={
                                "Authorization": f"Bearer {self.github_access_token}",
                                "Accept": "application/vnd.github+json",
                            },
                            timeout=10.0,
                        )
                        if resp.status_code == 200:
                            self._github_login = resp.json().get("login")

                    # Get accessible repos via installations
                    resp = await client.get(
                        "https://api.github.com/user/installations",
                        headers={
                            "Authorization": f"Bearer {self.github_access_token}",
                            "Accept": "application/vnd.github+json",
                        },
                        timeout=10.0,
                    )
                    repos: list[str] = []
                    if resp.status_code == 200:
                        for inst in resp.json().get("installations", []):
                            inst_id = inst["id"]
                            repos_resp = await client.get(
                                f"https://api.github.com/user/installations/{inst_id}/repositories",
                                headers={
                                    "Authorization": f"Bearer {self.github_access_token}",
                                    "Accept": "application/vnd.github+json",
                                },
                                timeout=10.0,
                            )
                            if repos_resp.status_code == 200:
                                for repo in repos_resp.json().get("repositories", []):
                                    repos.append(repo["full_name"])
                    self._accessible_repos = repos
                    logger.info("Loaded %d accessible repos for user %s", len(repos), self._github_login)
            except Exception:
                logger.exception("Failed to load user context")
                self._accessible_repos = []

    async def run(
        self,
        user_message: str,
        on_event: EventCallback | None = None,
    ) -> str:
        """Execute the agent loop for a single user message.

        *on_event* is called with ``(event_type, data)`` for real-time
        streaming to a WebSocket.

        Event types:
        - ``"status"``      — informational status updates
        - ``"tool_call"``   — the LLM wants to invoke a tool
        - ``"tool_result"`` — a tool finished executing
        - ``"token"``       — streamed text token from the LLM
        - ``"done"``        — final text response from the LLM

        Returns the final assistant text response.
        """
        self._turn_counter += 1
        self._current_turn_id = f"turn_{self._turn_counter:03d}"
        self._context.mark_new_iteration()

        self._context.add_user_message(user_message)

        # Persist user message
        try:
            msg = MessageItem(
                session_id=self.session_id,
                message_id=_ulid(),
                role=MessageRole.USER,
                content=user_message,
            )
            await dynamo.put_item(TABLE_MESSAGES, msg.to_dynamo_item())
        except Exception:
            logger.warning("Failed to persist user message", exc_info=True)

        await self._emit(on_event, "status", {"message": "Thinking..."})

        iteration = 0
        while iteration < _MAX_ITERATIONS:
            iteration += 1

            # Truncate context if it is getting too large
            self._context.truncate_if_needed(_MAX_CONTEXT_TOKENS)

            # Call the LLM
            response = await self._call_llm()

            text_content = response.content
            tool_calls = response.tool_calls

            # Record cost
            try:
                await cost_tracker.record_llm_cost(
                    session_id=self.session_id,
                    model=self.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    turn_id=self._current_turn_id,
                    iteration=iteration,
                    cache_read_tokens=getattr(response, 'cache_read_input_tokens', 0),
                    cache_creation_tokens=getattr(response, 'cache_creation_input_tokens', 0),
                    tool_calls=[tc.name for tc in tool_calls] if tool_calls else None,
                )
            except Exception:
                logger.warning("Failed to record LLM cost", exc_info=True)

            # Persist the assistant message
            tool_call_dicts = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in tool_calls
            ] if tool_calls else None

            self._context.add_assistant_message(text_content, tool_call_dicts)

            # If there are no tool calls, we are done
            if not tool_calls:
                await self._emit(on_event, "done", {"content": text_content})
                await self._persist_assistant_message(text_content, response)
                await self.save_state()
                return text_content

            # Stream partial text if present
            if text_content:
                await self._emit(on_event, "token", {"content": text_content})

            # Execute each tool call
            for tc in tool_calls:
                await self._emit(on_event, "tool_call", {
                    "tool_use_id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                })

                result = await execute_tool(tc.name, self.workspace_path, tc.arguments, github_token=self.github_access_token)

                self._context.add_tool_result(tc.id, result)

                await self._emit(on_event, "tool_result", {
                    "tool_use_id": tc.id,
                    "name": tc.name,
                    "result": result[:2000],  # Truncate for the event stream
                })

                # Persist tool call + result as messages
                await self._persist_tool_message(tc, result, response)

            # Save state after each iteration for crash recovery
            await self.save_state()

            # Compact old tool results to save context space
            self._context.compact_tool_results(self.workspace_path)

            await self._emit(on_event, "status", {
                "message": f"Iteration {iteration}/{_MAX_ITERATIONS} — continuing...",
            })

        # Max iterations reached
        final_text = (
            "I've reached the maximum number of tool-use iterations. "
            "Here's what I've done so far — please let me know if you'd "
            "like me to continue."
        )
        self._context.add_assistant_message(final_text)
        await self._emit(on_event, "done", {"content": final_text})
        await self.save_state()
        return final_text

    # -- state persistence --------------------------------------------------

    async def load_state(self) -> None:
        """Load conversation state from DynamoDB."""
        try:
            item = await dynamo.get_item(
                TABLE_SESSIONS,
                {"session_id": self.session_id, "SK": "CONTEXT"},
            )
            if item and "messages" in item:
                data = item["messages"]
                # DynamoDB stores as a list; parse if it was stored as JSON string
                if isinstance(data, str):
                    data = json.loads(data)
                self._context = ConversationContext.from_serializable(data)
                # Restore turn counter and iteration from persisted state
                self._turn_counter = int(item.get("turn_counter", 0))
                self._context._current_iteration = int(item.get("current_iteration", 0))
                logger.info(
                    "Loaded %d messages for session %s",
                    self._context.message_count,
                    self.session_id,
                )
        except Exception:
            logger.exception("Failed to load state for session %s", self.session_id)

    async def save_state(self) -> None:
        """Save conversation state to DynamoDB."""
        try:
            messages_data = self._context.to_serializable()
            await dynamo.put_item(
                TABLE_SESSIONS,
                {
                    "session_id": self.session_id,
                    "SK": "CONTEXT",
                    "messages": messages_data,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "message_count": self._context.message_count,
                    "estimated_tokens": self._context.estimated_tokens,
                    "turn_counter": self._turn_counter,
                    "current_iteration": self._context._current_iteration,
                },
            )
        except Exception:
            logger.exception("Failed to save state for session %s", self.session_id)

    # -- internals ----------------------------------------------------------

    async def _call_llm(self):
        """Call the LLM with the current context and tools.

        If repo permissions have been resolved, the tool set is scoped to
        the user's access level and a permission notice is appended to
        the system prompt.  Otherwise the full tool set is used.

        Returns an ``LLMResponse`` (from the Anthropic or OpenAI client).
        """
        if self._permissions is not None:
            tools = get_tools_for_permission_level(self._permissions.level)
            snippet = PERMISSION_PROMPT_SNIPPETS.get(self._permissions.level, "")
            system_prompt = f"{self._system_prompt}\n\n{snippet}" if snippet else self._system_prompt
        else:
            tools = TOOL_DEFINITIONS
            system_prompt = self._system_prompt

        # Inject user context (GitHub username + accessible repos)
        user_context_parts: list[str] = []
        if self._github_login:
            user_context_parts.append(f"The current user is GitHub user: @{self._github_login}")
        if self._accessible_repos:
            repo_list = ", ".join(f"`{r}`" for r in self._accessible_repos)
            user_context_parts.append(
                f"The user has given you access to the following repositories: {repo_list}. "
                f"When the user refers to a repo by partial name, match it against this list. "
                f"Use the `clone_repo` tool with the full GitHub URL (https://github.com/owner/repo) to clone a repo before working on it."
            )
        if user_context_parts:
            system_prompt = system_prompt + "\n\n" + "\n".join(user_context_parts)

        return await self._llm.chat(
            messages=self._context.get_messages(),
            model=self.model,
            system_prompt=system_prompt,
            tools=tools,
            max_tokens=4_096,
        )

    async def _persist_assistant_message(self, text: str, response: Any) -> None:
        """Write the final assistant text message to the messages table."""
        try:
            msg = MessageItem(
                session_id=self.session_id,
                message_id=_ulid(),
                role=MessageRole.ASSISTANT,
                content=text,
                model=self.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=response.cost_usd,
            )
            await dynamo.put_item(TABLE_MESSAGES, msg.to_dynamo_item())
        except Exception:
            logger.exception("Failed to persist assistant message")

    async def _persist_tool_message(self, tool_call: Any, result: str, response: Any) -> None:
        """Write a tool invocation record to the messages table."""
        try:
            msg = MessageItem(
                session_id=self.session_id,
                message_id=_ulid(),
                role=MessageRole.TOOL,
                content=result[:10_000],  # cap stored output
                tool_name=tool_call.name,
                tool_input=tool_call.arguments,
                tool_output=result[:10_000],
                model=self.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=0.0,  # Cost is attributed to the assistant turn
            )
            await dynamo.put_item(TABLE_MESSAGES, msg.to_dynamo_item())
        except Exception:
            logger.exception("Failed to persist tool message")

    @staticmethod
    async def _emit(
        callback: EventCallback | None,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Fire an event to the caller, silently ignoring errors."""
        if callback is None:
            return
        try:
            await callback(event_type, data)
        except Exception:
            logger.warning("Event callback raised for %s", event_type, exc_info=True)

    # -- session lifecycle helpers ------------------------------------------

    async def update_session_state(self, state: SessionState) -> None:
        """Update the session state in DynamoDB."""
        try:
            await dynamo.update_item(
                TABLE_SESSIONS,
                key={"session_id": self.session_id, "SK": "META"},
                update_expression="SET #st = :s, updated_at = :u",
                expression_attribute_names={"#st": "state"},
                expression_attribute_values={
                    ":s": state.value,
                    ":u": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            logger.exception("Failed to update session state to %s", state)

    async def update_session_cost(self) -> None:
        """Sync the LLM router's cumulative cost to the session record."""
        usage = self._llm.usage
        try:
            await dynamo.update_item(
                TABLE_SESSIONS,
                key={"session_id": self.session_id, "SK": "META"},
                update_expression=(
                    "SET total_cost_usd = :c, "
                    "total_input_tokens = :it, "
                    "total_output_tokens = :ot, "
                    "updated_at = :u"
                ),
                expression_attribute_values={
                    ":c": Decimal(str(usage.total_cost_usd)),
                    ":it": usage.total_input_tokens,
                    ":ot": usage.total_output_tokens,
                    ":u": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            logger.exception("Failed to update session cost")
