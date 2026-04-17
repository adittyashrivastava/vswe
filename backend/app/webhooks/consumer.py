"""GitHub event consumer — unified business logic for processing webhook events.

This module defines a base ``GitHubEventConsumer`` class that owns all the
business logic for handling GitHub issue and comment events. Two concrete
subclasses handle the transport layer:

- ``LocalEventConsumer`` — mounts a FastAPI route, receives webhooks from
  ngrok, and processes events in-process as asyncio background tasks.
- ``CloudEventConsumer`` — polls an SQS queue in a long-running loop and
  processes events sequentially. Runs inside the ECS Fargate container.

Both share identical processing logic via the base class.
"""

import abc
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response

from app.agent.orchestrator import AgentOrchestrator
from app.agent.phases import AgentPhase
from app.config import settings
from app.db import dynamo
from app.db.models import TABLE_SESSIONS, SessionState
from app.llm.router import LLMRouter
from app.webhooks.processor import (
    extract_comment_metadata,
    extract_issue_metadata,
    find_tracked_session,
    lookup_config,
    should_process_comment,
    should_process_issue,
    validate_and_parse,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plan approval classifier (Haiku) — shared by all consumers
# ---------------------------------------------------------------------------

_PLAN_CLASSIFIER_PROMPT = """\
You are a simple classifier. You are given a proposed plan and a user's \
response to that plan. Determine whether the user is approving the plan \
or requesting changes. Use the appropriate tool to indicate your decision."""

_PLAN_CLASSIFIER_TOOLS = [
    {
        "name": "approve_plan",
        "description": "The user has approved the plan and wants to proceed with implementation.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "request_changes",
        "description": "The user wants changes to the plan or has provided feedback that requires revising the approach.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what the user wants changed.",
                },
            },
            "required": [],
        },
    },
]


# ============================================================================
# Base class — all business logic lives here
# ============================================================================


class GitHubEventConsumer(abc.ABC):
    """Abstract base for processing GitHub webhook events.

    Subclasses implement :meth:`start` to define how events arrive
    (FastAPI route vs SQS polling). All event processing logic is in
    the base class methods below.
    """

    @abc.abstractmethod
    async def start(self) -> None:
        """Start consuming events. Subclasses implement this."""

    # ------------------------------------------------------------------
    # Issue opened
    # ------------------------------------------------------------------

    async def process_issue_opened(self, payload: dict[str, Any]) -> None:
        """Handle a new GitHub issue — check config, create session, run agent."""
        meta = extract_issue_metadata(payload)
        repo_full_name = meta["repo_full_name"]

        config = await lookup_config(repo_full_name)
        if not config or not config.get("enabled", True):
            logger.info("Agent not enabled for %s — ignoring issue", repo_full_name)
            return

        issue_number = meta["issue_number"]
        installation_id = meta["installation_id"]

        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        workspace_path = os.path.join(settings.workspace_root, session_id)

        session = {
            "session_id": session_id,
            "SK": "META",
            "user_id": f"github:{meta['issue_user']}",
            "type": "github_issue",
            "repo_url": f"https://github.com/{repo_full_name}",
            "github_issue_number": issue_number,
            "github_repo_full_name": repo_full_name,
            "model": settings.default_model,
            "state": SessionState.ACTIVE.value,
            "workspace_path": workspace_path,
            "created_at": now,
            "updated_at": now,
            "total_cost_usd": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        if installation_id:
            session["installation_id"] = installation_id

        await dynamo.put_item(TABLE_SESSIONS, session)
        logger.info(
            "Created session %s for issue #%s on %s",
            session_id, issue_number, repo_full_name,
        )

        await self._run_agent_for_issue(
            session_id=session_id,
            workspace_path=workspace_path,
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            issue_title=meta["issue_title"],
            issue_body=meta["issue_body"],
            installation_id=installation_id,
        )

    # ------------------------------------------------------------------
    # Issue comment
    # ------------------------------------------------------------------

    async def process_issue_comment(self, payload: dict[str, Any]) -> None:
        """Handle a comment on a tracked issue — classify and resume agent."""
        meta = extract_issue_metadata(payload)
        repo_full_name = meta["repo_full_name"]
        issue_number = meta["issue_number"]

        session = await find_tracked_session(repo_full_name, issue_number)
        if not session:
            logger.info("No tracked session for %s#%s — ignoring", repo_full_name, issue_number)
            return

        # Only block if the agent is currently running (avoid race conditions).
        # All other states (awaiting_clarification, completed, error) are resumable.
        if session.get("state") == SessionState.ACTIVE.value:
            logger.info(
                "Session %s is active (agent running) — ignoring comment to avoid race condition",
                session["session_id"],
            )
            return

        comment_meta = extract_comment_metadata(payload)
        installation_id = meta["installation_id"]
        session_id = session["session_id"]
        workspace_path = session.get(
            "workspace_path",
            os.path.join(settings.workspace_root, session_id),
        )

        github_token = await self._get_installation_token(installation_id)

        orch = AgentOrchestrator(
            session_id=session_id,
            workspace_path=workspace_path,
            model=settings.default_model,
            session_type="github_issue",
            github_access_token=github_token,
        )
        await orch.load_state()

        # If the agent is waiting for plan approval, classify with Haiku
        if orch._phase == AgentPhase.PLAN_REVIEW and orch._pending_plan:
            logger.info("[Issue #%s] Classifying plan response with Haiku", issue_number)
            decision = await self._classify_plan_response(
                orch._pending_plan, comment_meta["comment_body"],
            )
            logger.info("[Issue #%s] Plan classifier decision: %s", issue_number, decision)

            if decision == "approve":
                user_message = "[PLAN_APPROVED]"
            else:
                user_message = comment_meta["comment_body"]
        else:
            user_message = (
                f"The user replied to the issue with this comment:\n\n"
                f"**@{comment_meta['comment_user']}:**\n"
                f"{comment_meta['comment_body']}\n\n"
                f"Continue working on this issue based on the clarification provided."
            )

        await orch.update_session_state(SessionState.ACTIVE)
        on_event = self._make_on_event(orch, repo_full_name, issue_number, installation_id)

        try:
            await orch.run(user_message, on_event=on_event)
        except Exception:
            logger.exception("Agent failed for issue #%s on resume", issue_number)

    # ------------------------------------------------------------------
    # Agent execution
    # ------------------------------------------------------------------

    async def _run_agent_for_issue(
        self,
        session_id: str,
        workspace_path: str,
        repo_full_name: str,
        issue_number: int,
        issue_title: str,
        issue_body: str,
        installation_id: int | None,
    ) -> None:
        """Create orchestrator, post initial comment, run the agent."""
        os.makedirs(workspace_path, exist_ok=True)

        github_token = await self._get_installation_token(installation_id)

        orch = AgentOrchestrator(
            session_id=session_id,
            workspace_path=workspace_path,
            model=settings.default_model,
            session_type="github_issue",
            github_access_token=github_token,
        )

        await self._post_github_comment(
            repo_full_name, issue_number, installation_id,
            "I'm looking at this issue. Let me analyze it and I'll get back to you shortly...",
        )

        user_message = (
            f"New GitHub Issue #{issue_number} on {repo_full_name}:\n\n"
            f"**Title:** {issue_title}\n\n"
            f"**Body:**\n{issue_body}\n\n"
            f"Please analyze this issue. If it's ambiguous or you need more "
            f"information, formulate clarifying questions. Otherwise, implement "
            f"a fix and open a pull request."
        )

        on_event = self._make_on_event(orch, repo_full_name, issue_number, installation_id)

        try:
            await orch.run(user_message, on_event=on_event)
        except Exception:
            logger.exception("Agent failed for issue #%s", issue_number)
            await self._post_github_comment(
                repo_full_name, issue_number, installation_id,
                "I encountered an error while working on this issue. Please check the logs.",
            )

    # ------------------------------------------------------------------
    # Event callback
    # ------------------------------------------------------------------

    def _make_on_event(
        self,
        orch: AgentOrchestrator,
        repo_full_name: str,
        issue_number: int,
        installation_id: int | None,
    ) -> Any:
        """Create an on_event callback that posts agent output as GitHub comments."""

        async def on_event(event_type: str, data: dict[str, Any]) -> None:
            if event_type == "status":
                logger.info("[Issue #%s] Status: %s", issue_number, data.get("message"))
            elif event_type == "plan_review":
                plan = data.get("plan", "")
                comment = (
                    "Here's my proposed plan:\n\n"
                    f"{plan}\n\n"
                    "---\n"
                    "Please review and let me know if this looks good to proceed, "
                    "or if you'd like any changes."
                )
                await self._post_github_comment(
                    repo_full_name, issue_number, installation_id, comment,
                )
                await orch.update_session_state(SessionState.INACTIVE)
            elif event_type in ("done", "iteration_limit"):
                content = data.get("content", "")
                await self._post_github_comment(
                    repo_full_name, issue_number, installation_id, content,
                )
                await orch.update_session_state(SessionState.INACTIVE)

        return on_event

    # ------------------------------------------------------------------
    # Plan classifier
    # ------------------------------------------------------------------

    async def _classify_plan_response(self, plan: str, user_comment: str) -> str:
        """Use Haiku (via chat_fast) to classify whether a comment approves
        or rejects a plan.

        Returns ``"approve"`` or ``"changes"``.
        """
        llm = LLMRouter(
            anthropic_api_key=settings.anthropic_api_key,
            openai_api_key=settings.openai_api_key,
        )

        messages = [
            {
                "role": "user",
                "content": (
                    f"**Proposed plan:**\n{plan}\n\n"
                    f"**User's response:**\n{user_comment}"
                ),
            },
        ]

        try:
            response = await llm.chat_fast(
                messages=messages,
                system_prompt=_PLAN_CLASSIFIER_PROMPT,
                tools=_PLAN_CLASSIFIER_TOOLS,
                max_tokens=256,
            )

            for tc in response.tool_calls:
                if tc.name == "approve_plan":
                    return "approve"
                if tc.name == "request_changes":
                    return "changes"

            logger.warning("Plan classifier returned no tool call, defaulting to changes")
            return "changes"

        except Exception:
            logger.exception("Plan classifier failed, defaulting to changes")
            return "changes"

    # ------------------------------------------------------------------
    # GitHub comment posting
    # ------------------------------------------------------------------

    async def _get_installation_token(self, installation_id: int | None) -> str | None:
        """Get a GitHub App installation access token for git operations."""
        if not installation_id or not settings.github_app_id or not settings.github_app_private_key:
            return None
        try:
            from app.github_app.client import GitHubAppClient
            client = GitHubAppClient(settings.github_app_id, settings.github_app_private_key)
            return await client.get_installation_token(installation_id)
        except Exception:
            logger.exception("Failed to get installation token")
            return None

    async def _post_github_comment(
        self,
        repo_full_name: str,
        issue_number: int,
        installation_id: int | None,
        body: str,
    ) -> None:
        """Post a comment on a GitHub issue via the GitHub App."""
        if not settings.github_app_id or not settings.github_app_private_key:
            logger.warning("GitHub App credentials not configured — cannot post comment.")
            return

        try:
            from app.github_app.client import GitHubAppClient

            client = GitHubAppClient(settings.github_app_id, settings.github_app_private_key)
            await client.post_comment(repo_full_name, issue_number, body, installation_id or 0)
        except Exception:
            logger.exception("Failed to post GitHub comment")


# ============================================================================
# Local consumer — FastAPI route, processes in-process
# ============================================================================


class LocalEventConsumer(GitHubEventConsumer):
    """Receives webhooks directly via a FastAPI route (local dev with ngrok).

    Events are processed as asyncio background tasks so the webhook
    returns 200 immediately.
    """

    def __init__(self) -> None:
        self.router = APIRouter()
        self._register_routes()

    def _register_routes(self) -> None:
        @self.router.post("/webhooks/github")
        async def github_webhook(request: Request) -> Response:
            raw_body = await request.body()
            signature = request.headers.get("x-hub-signature-256", "")
            github_event = request.headers.get("x-github-event", "")

            if github_event == "ping":
                return Response(content='{"message": "pong"}', status_code=200)

            payload = validate_and_parse(raw_body, signature, settings.github_webhook_secret)
            if payload is None:
                return Response(
                    content='{"error": "Invalid signature or payload"}',
                    status_code=401,
                )

            if github_event == "issues" and should_process_issue(payload):
                asyncio.create_task(self.process_issue_opened(payload))

            elif github_event == "issue_comment" and should_process_comment(payload):
                asyncio.create_task(self.process_issue_comment(payload))

            return Response(content='{"message": "ok"}', status_code=200)

    async def start(self) -> None:
        """No-op — the FastAPI route is registered in __init__."""
        pass


# ============================================================================
# Cloud consumer — polls SQS, processes sequentially
# ============================================================================


class CloudEventConsumer(GitHubEventConsumer):
    """Polls an SQS queue for webhook events (production on ECS Fargate).

    Runs as a long-lived loop alongside the FastAPI process. Each message
    is processed sequentially (one job at a time per PoC design).
    """

    def __init__(
        self,
        queue_url: str | None = None,
        region: str | None = None,
    ) -> None:
        import boto3
        self._queue_url = queue_url or os.environ.get("VSWE_SQS_QUEUE_URL", "")
        self._sqs = boto3.client("sqs", region_name=region or settings.aws_region)
        self._running = False

    async def start(self) -> None:
        """Poll SQS in a loop and process events."""
        if not self._queue_url:
            logger.error("VSWE_SQS_QUEUE_URL not configured — SQS consumer not starting.")
            return

        self._running = True
        logger.info("CloudEventConsumer started — polling %s", self._queue_url)

        while self._running:
            try:
                messages = await asyncio.to_thread(
                    self._sqs.receive_message,
                    QueueUrl=self._queue_url,
                    MaxNumberOfMessages=1,
                    WaitTimeSeconds=20,  # Long polling
                    MessageAttributeNames=["All"],
                )

                for msg in messages.get("Messages", []):
                    await self._process_sqs_message(msg)
                    # Delete after successful processing
                    await asyncio.to_thread(
                        self._sqs.delete_message,
                        QueueUrl=self._queue_url,
                        ReceiptHandle=msg["ReceiptHandle"],
                    )

            except Exception:
                logger.exception("SQS polling error — retrying in 5s")
                await asyncio.sleep(5)

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False

    async def _process_sqs_message(self, msg: dict[str, Any]) -> None:
        """Parse an SQS message and dispatch to the appropriate handler."""
        try:
            payload = json.loads(msg["Body"])
        except (json.JSONDecodeError, KeyError):
            logger.error("Failed to parse SQS message body")
            return

        event_type = (
            msg.get("MessageAttributes", {})
            .get("event_type", {})
            .get("StringValue", "")
        )

        logger.info("Processing SQS message: event_type=%s", event_type)

        if event_type == "issues.opened":
            await self.process_issue_opened(payload)
        elif event_type == "issue_comment.created":
            await self.process_issue_comment(payload)
        else:
            logger.warning("Unknown event type: %s — skipping", event_type)
