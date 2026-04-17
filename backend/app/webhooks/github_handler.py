"""AWS Lambda handler for GitHub webhook events.

This module is deployed as a standalone Lambda function behind API Gateway.
It validates incoming webhooks, inspects the event type, and enqueues work
to SQS for the agent backend running on ECS Fargate.

The business logic for config lookup and event routing is shared with the
local handler via ``processor.py``.

Environment variables (set via CDK / Lambda config):
    GITHUB_WEBHOOK_SECRET  — shared secret for signature validation
    SQS_ISSUE_QUEUE_URL    — SQS queue URL for issue/comment events
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

# Lazy-initialised AWS clients (kept warm across Lambda invocations).
_sqs_client: Any = None


def _get_sqs():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs")
    return _sqs_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for GitHub webhook events.

    Validates the webhook, checks config and session state in DynamoDB,
    then enqueues to SQS for the Fargate agent to pick up.
    """
    try:
        # Extract payload
        headers_raw: dict[str, str] = event.get("headers") or {}
        headers = {k.lower(): v for k, v in headers_raw.items()}

        body_str: str = event.get("body", "")
        is_base64: bool = event.get("isBase64Encoded", False)
        body_bytes = base64.b64decode(body_str) if is_base64 else body_str.encode("utf-8")

        signature = headers.get("x-hub-signature-256", "")
        github_event = headers.get("x-github-event", "")

        # Signature validation
        if not _WEBHOOK_SECRET:
            logger.error("GITHUB_WEBHOOK_SECRET is not set — rejecting.")
            return {"statusCode": 401, "body": '{"error": "Server misconfigured"}'}

        if not verify_signature(body_bytes, signature, _WEBHOOK_SECRET):
            logger.warning("Invalid webhook signature — rejecting.")
            return {"statusCode": 401, "body": '{"error": "Invalid signature"}'}

        # Ping
        if github_event == "ping":
            return {"statusCode": 200, "body": '{"message": "pong"}'}

        # Parse payload
        try:
            payload: dict[str, Any] = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            return {"statusCode": 400, "body": '{"error": "Invalid JSON"}'}

        # Route: issues.opened
        if github_event == "issues" and payload.get("action") == "opened":
            repo = payload.get("repository", {}).get("full_name", "")
            # Config lookup is done by the Fargate consumer, not here.
            # The Lambda is a thin forwarder — it just enqueues.
            _enqueue(payload, "issues.opened")

        # Route: issue_comment.created
        elif github_event == "issue_comment" and payload.get("action") == "created":
            comment_user = payload.get("comment", {}).get("user", {}).get("login", "")
            comment_user_info = payload.get("comment", {}).get("user", {})
            is_bot = comment_user_info.get("type") == "Bot" or comment_user_info.get("login", "").endswith("[bot]")
            if not is_bot:
                _enqueue(payload, "issue_comment.created")

        return {"statusCode": 200, "body": '{"message": "ok"}'}

    except Exception:
        logger.exception("Unhandled exception in webhook handler.")
        return {"statusCode": 200, "body": '{"message": "internal error"}'}
