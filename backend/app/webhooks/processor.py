"""Shared webhook processing logic used by both the local FastAPI handler
and the production Lambda handler.

This module contains the business logic that is common across deployment
modes: config lookup, session tracking, payload extraction, and event
routing decisions. The actual execution strategy (in-process vs SQS) is
left to the caller.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db import dynamo
from app.db.models import TABLE_CONFIGS, TABLE_SESSIONS
from app.webhooks.signature import verify_signature

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config lookup
# ---------------------------------------------------------------------------

async def lookup_config(repo_full_name: str) -> dict[str, Any] | None:
    """Look up agent config — repo-level first, then org-level.

    Returns the config item dict or ``None`` if nothing is configured.
    """
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


# ---------------------------------------------------------------------------
# Session tracking
# ---------------------------------------------------------------------------

async def find_tracked_session(
    repo_full_name: str, issue_number: int,
) -> dict[str, Any] | None:
    """Find an active session tracking a GitHub issue."""
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


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------

def validate_and_parse(
    raw_body: bytes,
    signature: str,
    secret: str,
) -> dict[str, Any] | None:
    """Validate signature and parse webhook payload.

    Returns the parsed payload dict, or ``None`` if validation/parsing fails.
    """
    if not verify_signature(raw_body, signature, secret):
        logger.warning("Invalid webhook signature")
        return None

    try:
        return json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse webhook payload as JSON")
        return None


# ---------------------------------------------------------------------------
# Event routing
# ---------------------------------------------------------------------------

def should_process_issue(payload: dict[str, Any]) -> bool:
    """Check if an issues event should be processed (action == opened)."""
    return payload.get("action") == "opened"


def should_process_comment(payload: dict[str, Any]) -> bool:
    """Check if an issue_comment event should be processed.

    Filters: action == created, not from a bot.
    """
    if payload.get("action") != "created":
        return False
    comment_user_info = payload.get("comment", {}).get("user", {})
    # GitHub sets type="Bot" for app installations, and appends [bot] to the login
    if comment_user_info.get("type") == "Bot":
        return False
    if comment_user_info.get("login", "").endswith("[bot]"):
        return False
    return True


def extract_issue_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract common fields from an issue or issue_comment webhook payload."""
    repo = payload.get("repository", {})
    issue = payload.get("issue", {})
    return {
        "repo_full_name": repo.get("full_name", ""),
        "issue_number": issue.get("number"),
        "issue_title": issue.get("title", ""),
        "issue_body": issue.get("body", ""),
        "issue_user": issue.get("user", {}).get("login", "unknown"),
        "installation_id": payload.get("installation", {}).get("id"),
    }


def extract_comment_metadata(payload: dict[str, Any]) -> dict[str, str]:
    """Extract comment-specific fields from an issue_comment webhook payload."""
    comment = payload.get("comment", {})
    return {
        "comment_body": comment.get("body", ""),
        "comment_user": comment.get("user", {}).get("login", ""),
    }
