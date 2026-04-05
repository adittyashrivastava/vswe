"""Local webhook handler — FastAPI route that replaces Lambda + SQS for local dev.

In production, GitHub webhooks go through:
    GitHub → API Gateway → Lambda → SQS → Fargate agent

For local dev with ngrok, we short-circuit this to:
    GitHub → ngrok → FastAPI → process directly (no SQS)

This module should ONLY be mounted when ENV=local.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response

from app.agent.orchestrator import AgentOrchestrator
from app.config import settings
from app.db import dynamo
from app.db.models import (
    TABLE_CONFIGS,
    TABLE_SESSIONS,
    SessionState,
)
from app.webhooks.signature import verify_signature

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _lookup_config(repo_full_name: str) -> dict[str, Any] | None:
    """Look up config — repo-level first, then org-level."""
    # Try repo scope
    item = await dynamo.get_item(
        TABLE_CONFIGS,
        {"config_scope": f"repo:{repo_full_name}", "SK": "CONFIG"},
    )
    if item:
        return item

    # Fall back to org scope
    owner = repo_full_name.split("/")[0] if "/" in repo_full_name else repo_full_name
    return await dynamo.get_item(
        TABLE_CONFIGS,
        {"config_scope": f"org:{owner}", "SK": "CONFIG"},
    )


async def _find_tracked_session(repo_full_name: str, issue_number: int) -> dict[str, Any] | None:
    """Find an active session tracking this GitHub issue."""
    from app.db.dynamo import query_by_gsi

    items = await query_by_gsi(
        table_name=TABLE_SESSIONS,
        index_name="github_repo-issue-index",
        key_name="github_repo_full_name",
        key_value=repo_full_name,
        limit=10,
    )
    for item in items:
        if item.get("github_issue_number") == issue_number:
            return item
    return None


async def _process_new_issue(payload: dict[str, Any]) -> None:
    """Handle a new GitHub issue — create session, ask clarifying questions."""
    repo_full_name = payload["repository"]["full_name"]
    issue = payload["issue"]
    issue_number = issue["number"]
    issue_title = issue.get("title", "")
    issue_body = issue.get("body", "")
    installation_id = payload.get("installation", {}).get("id")

    # Create a session for this issue
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    workspace_path = f"{settings.efs_mount_path}/workspaces/{session_id}"

    session = {
        "session_id": session_id,
        "SK": "META",
        "user_id": f"github:{issue.get('user', {}).get('login', 'unknown')}",
        "type": "github_issue",
        "repo_url": f"https://github.com/{repo_full_name}",
        "github_issue_number": issue_number,
        "github_repo_full_name": repo_full_name,
        "model": settings.default_model,
        "state": "active",
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

    # Run the agent in a background task
    asyncio.create_task(
        _run_agent_for_issue(session_id, workspace_path, repo_full_name, issue_number, issue_title, issue_body, installation_id)
    )


async def _run_agent_for_issue(
    session_id: str,
    workspace_path: str,
    repo_full_name: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    installation_id: int | None,
) -> None:
    """Run the agent orchestrator for a GitHub issue (background task)."""
    import os
    os.makedirs(workspace_path, exist_ok=True)

    orch = AgentOrchestrator(
        session_id=session_id,
        workspace_path=workspace_path,
        model=settings.default_model,
        session_type="github_issue",
    )

    # Post a comment that we're looking at the issue
    await _post_github_comment(
        repo_full_name, issue_number, installation_id,
        "👋 I'm looking at this issue. Let me analyze it and I'll get back to you shortly...",
    )

    user_message = f"""New GitHub Issue #{issue_number} on {repo_full_name}:

**Title:** {issue_title}

**Body:**
{issue_body}

Please analyze this issue. If it's ambiguous or you need more information, formulate clarifying questions. Otherwise, proceed with implementing a fix."""

    async def on_event(event_type: str, data: dict[str, Any]) -> None:
        """Post status updates as GitHub comments."""
        if event_type == "status":
            logger.info("[Issue #%s] Status: %s", issue_number, data.get("message"))
        elif event_type == "done":
            content = data.get("content", "")
            await _post_github_comment(repo_full_name, issue_number, installation_id, content)
            # If the agent is asking questions, mark as awaiting clarification
            if "?" in content and len(content) < 2000:
                await orch.update_session_state(SessionState.AWAITING_CLARIFICATION)
            else:
                await orch.update_session_state(SessionState.COMPLETED)

    try:
        await orch.run(user_message, on_event=on_event)
    except Exception:
        logger.exception("Agent failed for issue #%s", issue_number)
        await _post_github_comment(
            repo_full_name, issue_number, installation_id,
            "❌ I encountered an error while working on this issue. Please check the logs.",
        )


async def _process_issue_comment(payload: dict[str, Any], session: dict[str, Any]) -> None:
    """Handle a new comment on a tracked issue — resume the agent."""
    repo_full_name = payload["repository"]["full_name"]
    issue_number = payload["issue"]["number"]
    comment_body = payload["comment"]["body"]
    comment_user = payload["comment"]["user"]["login"]
    installation_id = payload.get("installation", {}).get("id")
    session_id = session["session_id"]
    workspace_path = session.get("workspace_path", f"{settings.efs_mount_path}/workspaces/{session_id}")

    # Don't respond to our own comments
    # (GitHub App bot username typically ends with [bot])
    if comment_user.endswith("[bot]"):
        return

    orch = AgentOrchestrator(
        session_id=session_id,
        workspace_path=workspace_path,
        model=settings.default_model,
        session_type="github_issue",
    )
    await orch.load_state()

    # Update state
    await orch.update_session_state(SessionState.ACTIVE)

    user_message = f"""The user replied to the issue with this comment:

**@{comment_user}:**
{comment_body}

Continue working on this issue based on the clarification provided."""

    async def on_event(event_type: str, data: dict[str, Any]) -> None:
        if event_type == "done":
            content = data.get("content", "")
            await _post_github_comment(repo_full_name, issue_number, installation_id, content)

    asyncio.create_task(_run_resumed_agent(orch, user_message, on_event, issue_number))


async def _run_resumed_agent(orch, user_message, on_event, issue_number):
    """Run the resumed agent in a background task."""
    try:
        await orch.run(user_message, on_event=on_event)
    except Exception:
        logger.exception("Agent failed for issue #%s on resume", issue_number)


async def _post_github_comment(
    repo_full_name: str,
    issue_number: int,
    installation_id: int | None,
    body: str,
) -> None:
    """Post a comment on a GitHub issue via the GitHub App."""
    if not settings.github_app_id or not settings.github_app_private_key:
        logger.warning("GitHub App not configured — skipping comment post. Would have posted: %s", body[:200])
        return

    try:
        from app.github_app.client import GitHubAppClient

        client = GitHubAppClient(settings.github_app_id, settings.github_app_private_key)
        await client.post_comment(repo_full_name, issue_number, body, installation_id or 0)
    except Exception:
        logger.exception("Failed to post GitHub comment")


# ---------------------------------------------------------------------------
# Webhook route
# ---------------------------------------------------------------------------

@router.post("/webhooks/github")
async def github_webhook(request: Request) -> Response:
    """Local dev webhook endpoint — replaces Lambda + API Gateway.

    GitHub sends webhooks here via ngrok tunnel.
    """
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    github_event = request.headers.get("x-github-event", "")

    # Validate signature
    if settings.github_webhook_secret:
        if not verify_signature(body, signature, settings.github_webhook_secret):
            logger.warning("Invalid webhook signature")
            return Response(
                content=json.dumps({"error": "Invalid signature"}),
                status_code=401,
                media_type="application/json",
            )

    # Ping event
    if github_event == "ping":
        logger.info("Received GitHub ping event")
        return Response(
            content=json.dumps({"message": "pong"}),
            status_code=200,
            media_type="application/json",
        )

    # Parse payload
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return Response(
            content=json.dumps({"error": "Invalid JSON"}),
            status_code=400,
            media_type="application/json",
        )

    # Handle issues
    if github_event == "issues" and payload.get("action") == "opened":
        repo_full_name = payload.get("repository", {}).get("full_name", "")
        config = await _lookup_config(repo_full_name)
        if config and config.get("enabled", True):
            logger.info("Processing new issue #%s on %s", payload["issue"]["number"], repo_full_name)
            await _process_new_issue(payload)
        else:
            logger.info("Agent not enabled for %s — ignoring", repo_full_name)

    # Handle issue comments
    elif github_event == "issue_comment" and payload.get("action") == "created":
        repo_full_name = payload.get("repository", {}).get("full_name", "")
        issue_number = payload.get("issue", {}).get("number")
        if repo_full_name and issue_number is not None:
            session = await _find_tracked_session(repo_full_name, issue_number)
            if session and session.get("state") == "awaiting_clarification":
                logger.info("Processing comment on tracked issue #%s", issue_number)
                await _process_issue_comment(payload, session)

    return Response(
        content=json.dumps({"message": "ok"}),
        status_code=200,
        media_type="application/json",
    )
