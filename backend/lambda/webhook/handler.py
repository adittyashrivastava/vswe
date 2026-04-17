"""AWS Lambda handler for GitHub webhook delivery.

This is a thin receiver that validates the webhook signature, then
forwards the payload to an SQS queue for async processing by the
ECS-hosted agent. No heavy logic runs here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SQS_QUEUE_URL = os.environ["SQS_QUEUE_URL"]
AWS_REGION = os.environ.get("AWS_REGION_NAME", "us-east-1")

sqs = boto3.client("sqs", region_name=AWS_REGION)
ssm = boto3.client("ssm", region_name=AWS_REGION)

# Cache the webhook secret for the lifetime of the Lambda container.
_webhook_secret: str | None = None


def _get_webhook_secret() -> str:
    """Fetch the GitHub webhook secret from SSM Parameter Store (cached)."""
    global _webhook_secret
    if _webhook_secret is None:
        resp = ssm.get_parameter(
            Name="/vswe/github-webhook-secret",
            WithDecryption=True,
        )
        _webhook_secret = resp["Parameter"]["Value"]
    return _webhook_secret


def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Validate the X-Hub-Signature-256 header."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


def lambda_handler(event: dict, context) -> dict:
    """Entry point for API Gateway HTTP API (payload format 2.0)."""
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body = event.get("body", "")
    is_base64 = event.get("isBase64Encoded", False)

    if is_base64:
        import base64
        raw_body = base64.b64decode(body)
    else:
        raw_body = body.encode("utf-8") if isinstance(body, str) else body

    # -- Signature verification ------------------------------------------------
    signature = headers.get("x-hub-signature-256", "")
    try:
        secret = _get_webhook_secret()
    except Exception:
        logger.exception("Failed to retrieve webhook secret from SSM")
        return {"statusCode": 500, "body": "Internal error"}

    if not _verify_signature(raw_body, signature, secret):
        logger.warning("Invalid webhook signature")
        return {"statusCode": 401, "body": "Invalid signature"}

    # -- Parse and forward to SQS ----------------------------------------------
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON body")
        return {"statusCode": 400, "body": "Invalid JSON"}

    github_event = headers.get("x-github-event", "unknown")
    action = payload.get("action", "")

    # Determine event type for the consumer
    # Consumer expects: "issues.opened", "issue_comment.created"
    if github_event == "issues" and action == "opened":
        event_type = "issues.opened"
    elif github_event == "issue_comment" and action == "created":
        # Filter bot comments
        comment_user = payload.get("comment", {}).get("user", {})
        if comment_user.get("type") == "Bot" or comment_user.get("login", "").endswith("[bot]"):
            logger.info("Ignoring bot comment")
            return {"statusCode": 200, "body": "OK"}
        event_type = "issue_comment.created"
    else:
        logger.info("Ignoring event: %s action=%s", github_event, action)
        return {"statusCode": 200, "body": "OK"}

    try:
        sqs.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps(payload),
            MessageAttributes={
                "event_type": {
                    "DataType": "String",
                    "StringValue": event_type,
                },
            },
        )
    except Exception:
        logger.exception("Failed to enqueue webhook")
        return {"statusCode": 500, "body": "Failed to enqueue"}

    logger.info("Enqueued %s event (event_type: %s)", github_event, event_type)
    return {"statusCode": 200, "body": "OK"}
