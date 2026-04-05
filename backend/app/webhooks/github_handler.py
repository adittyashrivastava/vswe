"""AWS Lambda handler for GitHub webhook events.

This module is deployed as a standalone Lambda function behind API Gateway.
It validates incoming webhooks, inspects the event type, and enqueues work
to SQS for the agent backend running on ECS Fargate.

Environment variables (set via CDK / Lambda config):
    GITHUB_WEBHOOK_SECRET  — shared secret for signature validation
    SQS_ISSUE_QUEUE_URL    — SQS queue URL for issue/comment events
    DYNAMODB_TABLE_CONFIGS — DynamoDB table name for config lookups (default: vswe-config)
    DYNAMODB_TABLE_SESSIONS — DynamoDB table name for session lookups (default: vswe-sessions)
    AWS_REGION             — provided automatically by Lambda runtime
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

import boto3

from app.webhooks.signature import verify_signature

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
_SQS_QUEUE_URL = os.environ.get("SQS_ISSUE_QUEUE_URL", "")
_TABLE_CONFIGS = os.environ.get("DYNAMODB_TABLE_CONFIGS", "vswe-config")
_TABLE_SESSIONS = os.environ.get("DYNAMODB_TABLE_SESSIONS", "vswe-sessions")

# Lazy-initialised AWS clients (kept warm across Lambda invocations).
_sqs_client: Any = None
_dynamo_resource: Any = None


def _get_sqs():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs")
    return _sqs_client


def _get_dynamo_table(table_name: str):
    global _dynamo_resource
    if _dynamo_resource is None:
        _dynamo_resource = boto3.resource("dynamodb")
    return _dynamo_resource.Table(table_name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_from_event(event: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    """Extract raw body bytes and lowercase headers from an API Gateway event.

    Supports both REST API (v1) and HTTP API (v2) event formats.
    """
    # API Gateway v2 (HTTP API) uses "headers" with lowercase keys and
    # stores the body in "body".  v1 (REST) also uses "headers" but keys
    # may be mixed-case.
    headers_raw: dict[str, str] = event.get("headers") or {}
    headers = {k.lower(): v for k, v in headers_raw.items()}

    body_str: str = event.get("body", "")
    is_base64: bool = event.get("isBase64Encoded", False)

    if is_base64 and body_str:
        body_bytes = base64.b64decode(body_str)
    else:
        body_bytes = (body_str or "").encode("utf-8")

    return body_bytes, headers


def _lookup_config(repo_full_name: str) -> dict[str, Any] | None:
    """Look up config in DynamoDB — repo-level first, then org-level.

    Returns the config item dict or ``None`` if nothing is configured.
    """
    table = _get_dynamo_table(_TABLE_CONFIGS)

    # Try repo-specific config first: "repo:owner/name"
    repo_scope = f"repo:{repo_full_name}"
    resp = table.get_item(Key={"config_scope": repo_scope, "SK": "CONFIG"})
    item = resp.get("Item")
    if item:
        return item

    # Fall back to org-level config: "org:owner"
    owner = repo_full_name.split("/")[0] if "/" in repo_full_name else repo_full_name
    org_scope = f"org:{owner}"
    resp = table.get_item(Key={"config_scope": org_scope, "SK": "CONFIG"})
    return resp.get("Item")


def _find_tracked_session(repo_full_name: str, issue_number: int) -> dict[str, Any] | None:
    """Query DynamoDB for an active session tracking this issue.

    Uses the ``github_repo-issue-index`` GSI on vswe-sessions.
    """
    from boto3.dynamodb.conditions import Key

    table = _get_dynamo_table(_TABLE_SESSIONS)
    resp = table.query(
        IndexName="github_repo-issue-index",
        KeyConditionExpression=(
            Key("github_repo_full_name").eq(repo_full_name)
            & Key("github_issue_number").eq(issue_number)
        ),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def _enqueue(payload: dict[str, Any], event_type: str) -> None:
    """Send a message to the SQS issue queue."""
    if not _SQS_QUEUE_URL:
        logger.error("SQS_ISSUE_QUEUE_URL is not configured — cannot enqueue event.")
        return

    _get_sqs().send_message(
        QueueUrl=_SQS_QUEUE_URL,
        MessageBody=json.dumps(payload),
        MessageAttributes={
            "event_type": {
                "StringValue": event_type,
                "DataType": "String",
            },
        },
    )
    logger.info("Enqueued %s event to SQS.", event_type)


# ---------------------------------------------------------------------------
# Event processors
# ---------------------------------------------------------------------------

def _handle_issues_event(payload: dict[str, Any]) -> None:
    """Process a ``issues`` webhook event."""
    action = payload.get("action")
    if action != "opened":
        logger.info("Ignoring issues event with action=%s", action)
        return

    repo_full_name = payload.get("repository", {}).get("full_name", "")
    if not repo_full_name:
        logger.warning("issues event missing repository.full_name — skipping.")
        return

    config = _lookup_config(repo_full_name)
    if config is None:
        logger.info("No config found for %s — ignoring issue.", repo_full_name)
        return

    if not config.get("enabled", True):
        logger.info("Agent disabled for %s — ignoring issue.", repo_full_name)
        return

    logger.info(
        "New issue #%s on %s — enqueuing for agent.",
        payload.get("issue", {}).get("number"),
        repo_full_name,
    )
    _enqueue(payload, "issues.opened")


def _handle_issue_comment_event(payload: dict[str, Any]) -> None:
    """Process an ``issue_comment`` webhook event."""
    action = payload.get("action")
    if action != "created":
        logger.info("Ignoring issue_comment event with action=%s", action)
        return

    repo_full_name = payload.get("repository", {}).get("full_name", "")
    issue_number = payload.get("issue", {}).get("number")

    if not repo_full_name or issue_number is None:
        logger.warning("issue_comment event missing repo or issue number — skipping.")
        return

    session = _find_tracked_session(repo_full_name, issue_number)
    if session is None:
        logger.info(
            "No tracked session for %s#%s — ignoring comment.",
            repo_full_name,
            issue_number,
        )
        return

    session_state = session.get("state", "")
    if session_state != "awaiting_clarification":
        logger.info(
            "Session %s is in state '%s', not awaiting_clarification — ignoring comment.",
            session.get("session_id"),
            session_state,
        )
        return

    logger.info(
        "Clarification received for %s#%s (session %s) — enqueuing.",
        repo_full_name,
        issue_number,
        session.get("session_id"),
    )
    _enqueue(
        {**payload, "_vswe_session_id": session.get("session_id")},
        "issue_comment.created",
    )


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

_EVENT_HANDLERS: dict[str, Any] = {
    "issues": _handle_issues_event,
    "issue_comment": _handle_issue_comment_event,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for GitHub webhook events.

    Flow:
    1. Extract payload and X-Hub-Signature-256 from API Gateway event.
    2. Validate signature using the webhook secret.
    3. Parse event type from X-GitHub-Event header.
    4. For ``issues`` events (action: opened) — look up config in DynamoDB
       (repo config first, then org config). If enabled, enqueue to SQS.
    5. For ``issue_comment`` events (action: created) — check if the issue
       is being tracked and the session is ``awaiting_clarification``.
       If so, enqueue to SQS.
    6. Return 200 immediately.
    """
    try:
        body_bytes, headers = _extract_from_event(event)
        signature = headers.get("x-hub-signature-256", "")
        github_event = headers.get("x-github-event", "")

        # --- Signature validation ---
        if not _WEBHOOK_SECRET:
            logger.error("GITHUB_WEBHOOK_SECRET is not set — rejecting request.")
            return {"statusCode": 401, "body": json.dumps({"error": "Server misconfigured"})}

        if not verify_signature(body_bytes, signature, _WEBHOOK_SECRET):
            logger.warning("Invalid webhook signature — rejecting request.")
            return {"statusCode": 401, "body": json.dumps({"error": "Invalid signature"})}

        # --- Ping event (GitHub sends this on webhook creation) ---
        if github_event == "ping":
            logger.info("Received ping event — responding OK.")
            return {"statusCode": 200, "body": json.dumps({"message": "pong"})}

        # --- Parse payload ---
        try:
            payload: dict[str, Any] = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            logger.error("Failed to parse webhook payload as JSON.")
            return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON payload"})}

        # --- Dispatch to handler ---
        event_handler = _EVENT_HANDLERS.get(github_event)
        if event_handler is None:
            logger.info("Unhandled event type: %s — ignoring.", github_event)
        else:
            event_handler(payload)

        return {"statusCode": 200, "body": json.dumps({"message": "ok"})}

    except Exception:
        logger.exception("Unhandled exception in webhook handler.")
        # Return 200 so GitHub does not retry indefinitely.  The error is
        # logged and will surface in CloudWatch.
        return {"statusCode": 200, "body": json.dumps({"message": "internal error"})}
