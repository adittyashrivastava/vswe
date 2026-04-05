"""WebSocket endpoint for real-time chat streaming."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.orchestrator import AgentOrchestrator
from app.config import settings
from app.db.dynamo import get_item
from app.db.models import TABLE_SESSIONS

router = APIRouter()
logger = logging.getLogger(__name__)

# Active WebSocket connections keyed by session_id.
_active_connections: dict[str, WebSocket] = {}

# Active orchestrators keyed by session_id (reuse across messages in same WS connection).
_active_orchestrators: dict[str, AgentOrchestrator] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_default(obj: Any) -> Any:
    """Handle non-serializable types for JSON encoding."""
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def _send_event(ws: WebSocket, event_type: str, data: dict[str, Any]) -> None:
    """Send a JSON event to the client."""
    payload = {"type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **data}
    text = json.dumps(payload, default=_json_default)
    await ws.send_text(text)


def _validate_client_message(raw: dict[str, Any]) -> tuple[str, str, str]:
    """Parse and validate an incoming client message.

    Returns ``(msg_type, content, model)`` or raises ``ValueError``.
    """
    msg_type = raw.get("type")
    if msg_type not in ("message", "cancel"):
        raise ValueError(f"Unsupported message type: {msg_type}")

    if msg_type == "cancel":
        return msg_type, "", ""

    content = raw.get("content")
    if not content or not isinstance(content, str) or not content.strip():
        raise ValueError("Message content must be a non-empty string.")

    model = raw.get("model") or settings.default_model
    return msg_type, content.strip(), model


def _extract_repo_full_name(repo_url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub URL or shorthand.

    Supports formats like:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    - owner/repo
    """
    import re

    # HTTPS: https://github.com/owner/repo(.git)
    m = re.match(r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url)
    if m:
        return m.group(1)

    # SSH: git@github.com:owner/repo(.git)
    m = re.match(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?$", repo_url)
    if m:
        return m.group(1)

    # Shorthand: owner/repo
    m = re.match(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$", repo_url)
    if m:
        return m.group(1)

    return None


async def _get_or_create_orchestrator(
    session_id: str,
    model: str,
) -> AgentOrchestrator:
    """Get an existing orchestrator or create a new one for the session."""
    if session_id in _active_orchestrators:
        orch = _active_orchestrators[session_id]
        # Update model if changed
        if orch.model != model:
            orch.model = model
        return orch

    # Look up session to get workspace path and github token
    session = await get_item(TABLE_SESSIONS, {"session_id": session_id, "SK": "META"})
    workspace_path = (
        session.get("workspace_path", f"{settings.workspace_root}/{session_id}")
        if session
        else f"{settings.workspace_root}/{session_id}"
    )
    github_access_token = session.get("github_access_token") if session else None

    # Ensure workspace directory exists
    os.makedirs(workspace_path, exist_ok=True)

    orch = AgentOrchestrator(
        session_id=session_id,
        workspace_path=workspace_path,
        model=model,
        session_type="chat",
        github_access_token=github_access_token,
    )
    await orch.load_state()

    # Load user context (GitHub login + accessible repos)
    github_login = None
    if session and session.get("user_id"):
        user_id = session["user_id"]
        logger.info("Loading user record for user_id=%s", user_id)
        user_record = await get_item("vswe-users", {"user_id": user_id, "SK": "META"})
        if user_record:
            github_login = user_record.get("github_login")
            github_access_token = github_access_token or user_record.get("github_access_token")
            orch.github_access_token = github_access_token
            logger.info("Found user: login=%s, has_token=%s", github_login, bool(github_access_token))
        else:
            logger.warning("No user record found for user_id=%s", user_id)
    await orch.load_user_context(github_login=github_login)

    # If the session is linked to a repository, resolve permissions
    repo_url = session.get("repo_url") if session else None
    if repo_url and github_access_token:
        repo_full_name = _extract_repo_full_name(repo_url)
        if repo_full_name:
            try:
                await orch.resolve_repo_permissions(repo_full_name)
            except Exception:
                logger.exception(
                    "Failed to resolve repo permissions for session=%s repo=%s",
                    session_id,
                    repo_full_name,
                )

    _active_orchestrators[session_id] = orch
    return orch


def _make_event_callback(ws: WebSocket):
    """Create an on_event callback that streams events to a WebSocket."""

    async def on_event(event_type: str, data: dict[str, Any]) -> None:
        try:
            await _send_event(ws, event_type, data)
        except Exception:
            logger.warning("Failed to send event %s via WebSocket", event_type)

    return on_event


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws/sessions/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """Bidirectional WebSocket for a chat session.

    Client sends::

        {"type": "message", "content": "...", "model": "..."}

    Server streams back events with types:
    ``status``, ``tool_call``, ``tool_result``, ``token``, ``done``, ``error``.
    """
    await websocket.accept()
    _active_connections[session_id] = websocket
    logger.info("WebSocket connected: session=%s", session_id)

    try:
        while True:
            raw_text = await websocket.receive_text()

            # Parse JSON
            try:
                raw = json.loads(raw_text)
            except json.JSONDecodeError:
                await _send_event(websocket, "error", {"detail": "Invalid JSON payload."})
                continue

            # Validate message
            try:
                msg_type, content, model = _validate_client_message(raw)
            except ValueError as exc:
                await _send_event(websocket, "error", {"detail": str(exc)})
                continue

            if msg_type == "cancel":
                await _send_event(websocket, "status", {"status": "cancelled"})
                continue

            # Run the agent orchestrator
            try:
                logger.info("Processing message for session=%s model=%s content=%s", session_id, model, content[:100])
                orch = await _get_or_create_orchestrator(session_id, model)
                logger.info("Orchestrator ready, calling LLM...")
                on_event = _make_event_callback(websocket)
                await orch.run(content, on_event=on_event)
                await orch.update_session_cost()
            except Exception as exc:
                logger.exception("Error processing message for session=%s", session_id)
                await _send_event(websocket, "error", {"detail": f"Internal error: {exc}"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session=%s", session_id)
    except Exception:
        logger.exception("Unexpected WebSocket error: session=%s", session_id)
    finally:
        _active_connections.pop(session_id, None)
        _active_orchestrators.pop(session_id, None)
