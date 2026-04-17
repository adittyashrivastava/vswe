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
from app.agent.phases import AgentPhase, get_tools_for_phase_and_permission
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
        # Phase-gated workflow state — both chat and GitHub issue sessions
        # go through CLARIFY → PLAN_REVIEW → EXECUTE
        self._phase: AgentPhase = AgentPhase.CLARIFY
        self._pending_plan: str | None = None

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
        - ``"status"``        — informational status updates
        - ``"tool_call"``     — the LLM wants to invoke a tool
        - ``"tool_result"``   — a tool finished executing
        - ``"token"``         — streamed text token from the LLM
        - ``"plan_review"``   — agent submitted a plan; waiting for approval
        - ``"done"``          — final text response from the LLM

        Returns the final assistant text response.
        """
        self._turn_counter += 1
        self._current_turn_id = f"turn_{self._turn_counter:03d}"
        self._context.mark_new_iteration()

        # -- Handle plan approval / rejection when in PLAN_REVIEW phase --------
        if self._phase == AgentPhase.PLAN_REVIEW:
            user_message = self._handle_plan_review_input(user_message)

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

            # Compact old tool results BEFORE calling the LLM so that the
            # cache breakpoint is placed on already-compacted content.
            # If compaction ran after the LLM call, it would mutate the
            # cached prefix and cause a cache miss on the next iteration.
            self._context.compact_tool_results(self.workspace_path)

            # Truncate context if it is getting too large
            self._context.truncate_if_needed(_MAX_CONTEXT_TOKENS)

            # Call the LLM
            logger.info(
                "Calling LLM: session=%s phase=%s iteration=%d",
                self.session_id, self._phase.value, iteration,
            )
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
                # If we just finished executing, reset to CLARIFY for the
                # next user message.
                if self._phase == AgentPhase.EXECUTE:
                    self._phase = AgentPhase.CLARIFY
                    self._pending_plan = None
                await self.save_state()
                return text_content

            # -- Check for submit_plan tool call (phase transition) ------------
            submit_plan_tc = next(
                (tc for tc in tool_calls if tc.name == "submit_plan"), None,
            )
            if submit_plan_tc is not None:
                plan_text = submit_plan_tc.arguments.get("plan", "")
                self._pending_plan = plan_text
                self._phase = AgentPhase.PLAN_REVIEW

                # Add a synthetic tool result so the context stays valid
                self._context.add_tool_result(
                    submit_plan_tc.id,
                    "Plan submitted. Waiting for user approval.",
                )

                # Stream any text the assistant produced alongside the plan
                if text_content:
                    await self._emit(on_event, "token", {"content": text_content})

                mid = _ulid()
                await self._persist_assistant_message(text_content, response, message_id=mid)
                await self._persist_tool_message(submit_plan_tc, "Plan submitted. Waiting for user approval.", response)

                # Emit assistant_message so the frontend commits the
                # intermediate message before we emit plan_review.
                await self._emit(on_event, "assistant_message", {
                    "message": {
                        "id": mid,
                        "session_id": self.session_id,
                        "role": "assistant",
                        "content": text_content,
                        "model": self.model,
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cost_usd": response.cost_usd,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                })

                # Emit the plan_review event and pause
                await self._emit(on_event, "plan_review", {"plan": plan_text})
                await self.save_state()
                return text_content or f"Here's my plan:\n\n{plan_text}"

            # Stream partial text if present
            if text_content:
                await self._emit(on_event, "token", {"content": text_content})

            # Persist the intermediate assistant message BEFORE tool results
            # so the DB ordering is: assistant, tool, tool — the frontend's
            # groupMessages() attaches tool results to the PRECEDING assistant.
            mid = _ulid()
            await self._persist_assistant_message(text_content, response, message_id=mid)

            # Execute each tool call
            for tc in tool_calls:
                await self._emit(on_event, "tool_call", {
                    "tool_use_id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                })

                result = await execute_tool(tc.name, self.workspace_path, tc.arguments, github_token=self.github_access_token, session_id=self.session_id)

                self._context.add_tool_result(tc.id, result)

                await self._emit(on_event, "tool_result", {
                    "tool_use_id": tc.id,
                    "name": tc.name,
                    "result": result[:2000],  # Truncate for the event stream
                })

                # Persist tool call + result as messages
                await self._persist_tool_message(tc, result, response)

            # Emit "assistant_message" so the frontend can commit the
            # intermediate message (with its tool calls) into the message list
            # and clear streaming state for the next iteration.
            await self._emit(on_event, "assistant_message", {
                "message": {
                    "id": mid,
                    "session_id": self.session_id,
                    "role": "assistant",
                    "content": text_content,
                    "model": self.model,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cost_usd": response.cost_usd,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            })

            # Save state after each iteration for crash recovery
            await self.save_state()

            await self._emit(on_event, "status", {
                "message": f"Iteration {iteration}/{_MAX_ITERATIONS} — continuing...",
            })

        # Max iterations reached — summarize progress with Haiku, then
        # emit iteration_limit so the consumer can set the session to
        # INACTIVE (allowing the user to resume).
        final_text = await self._summarize_progress()
        self._context.add_assistant_message(final_text)
        await self._emit(on_event, "iteration_limit", {"content": final_text})
        await self.save_state()
        return final_text

    # -- phase helpers ---------------------------------------------------------

    def _handle_plan_review_input(self, user_message: str) -> str:
        """Process user input during the PLAN_REVIEW phase.

        The only approval signal is the literal ``[PLAN_APPROVED]`` token,
        which is sent deterministically by:
        - Chat GUI: the PlanReviewCard "Approve" button
        - GitHub issues: the Haiku classifier after it decides the user approved

        Any other message is treated as a change request.

        Returns the (possibly augmented) user message to add to context.
        """
        if user_message.strip() == "[PLAN_APPROVED]":
            self._phase = AgentPhase.EXECUTE
            return (
                f"[PLAN APPROVED] Proceed with the following plan:\n\n"
                f"{self._pending_plan}\n\n"
                f"Execute the plan now. Do not ask any more questions."
            )
        else:
            # User wants changes — go back to CLARIFY
            self._phase = AgentPhase.CLARIFY
            self._pending_plan = None
            return user_message

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
                # Restore phase workflow state
                phase_str = item.get("phase")
                if phase_str:
                    try:
                        self._phase = AgentPhase(phase_str)
                    except ValueError:
                        self._phase = AgentPhase.CLARIFY
                self._pending_plan = item.get("pending_plan")
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
                    "phase": self._phase.value,
                    "pending_plan": self._pending_plan,
                },
            )
        except Exception:
            logger.exception("Failed to save state for session %s", self.session_id)

    # -- internals ----------------------------------------------------------

    async def _summarize_progress(self) -> str:
        """Use Haiku to generate a concise summary of what the agent has done.

        Called when the agent hits the iteration limit so the user knows
        what was accomplished and what remains.
        """
        # Build a compact summary of tool calls from the conversation
        tool_actions: list[str] = []
        for msg in self._context._messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    args = block.get("input", {})
                    if name in ("read_file", "write_file", "edit_file"):
                        tool_actions.append(f"{name}: {args.get('path', '?')}")
                    elif name == "run_command":
                        cmd = str(args.get("command", ""))[:80]
                        tool_actions.append(f"run_command: {cmd}")
                    elif name in ("clone_repo", "create_branch", "commit_and_push", "create_pull_request"):
                        tool_actions.append(f"{name}: {args}")
                    elif name == "submit_plan":
                        tool_actions.append("submit_plan")

        actions_text = "\n".join(f"- {a}" for a in tool_actions[-30:])  # last 30

        try:
            summary_response = await self._llm.chat_fast(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "You are summarizing an AI agent's progress on a task. "
                            "The agent hit its iteration limit. Here are the tool "
                            "actions it took:\n\n"
                            f"{actions_text}\n\n"
                            "Write a concise summary (3-5 bullet points) of:\n"
                            "1. What was completed\n"
                            "2. What remains to be done\n"
                            "End with: 'Reply if you'd like me to continue.'"
                        ),
                    },
                ],
                system_prompt="You are a concise technical summarizer.",
                max_tokens=512,
            )
            # Track the cost of this Haiku call
            try:
                await cost_tracker.record_llm_cost(
                    session_id=self.session_id,
                    model="claude-haiku-4-5-20251001",
                    input_tokens=summary_response.input_tokens,
                    output_tokens=summary_response.output_tokens,
                    turn_id=self._current_turn_id,
                    cache_read_tokens=getattr(summary_response, 'cache_read_input_tokens', 0),
                    cache_creation_tokens=getattr(summary_response, 'cache_creation_input_tokens', 0),
                )
            except Exception:
                logger.warning("Failed to record summary cost", exc_info=True)
            return summary_response.content
        except Exception:
            logger.warning("Failed to generate progress summary", exc_info=True)
            return (
                "I've reached the maximum number of iterations for this turn. "
                "Here's what I've done so far — reply if you'd like me to continue."
            )

    async def _call_llm(self):
        """Call the LLM with the current context and tools.

        Tools are filtered by both the current **phase** (clarify / execute)
        and the user's repo **permissions** (read / write / admin).  A
        permission notice is appended to the system prompt when available.

        Returns an ``LLMResponse`` (from the Anthropic or OpenAI client).
        """
        # Determine permission-scoped tools (or None if not resolved)
        permission_tools: list[dict[str, Any]] | None = None
        system_prompt = self._system_prompt

        if self._permissions is not None:
            permission_tools = get_tools_for_permission_level(self._permissions.level)
            snippet = PERMISSION_PROMPT_SNIPPETS.get(self._permissions.level, "")
            if snippet:
                system_prompt = f"{system_prompt}\n\n{snippet}"

        # Intersect with phase-gated tools
        tools = get_tools_for_phase_and_permission(self._phase, permission_tools)
        logger.info(
            "Tools for phase=%s: %s",
            self._phase.value,
            [t["name"] for t in tools],
        )

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

    async def _persist_assistant_message(
        self, text: str, response: Any, message_id: str | None = None,
    ) -> str:
        """Write an assistant text message to the messages table.

        Returns the message ID used.
        """
        mid = message_id or _ulid()
        try:
            msg = MessageItem(
                session_id=self.session_id,
                message_id=mid,
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
        return mid

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
