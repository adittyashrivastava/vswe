"""GitHub webhook signature validation.

Validates the ``X-Hub-Signature-256`` header sent with every GitHub webhook
delivery using HMAC-SHA256.
"""

from __future__ import annotations

import hashlib
import hmac


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Validate a GitHub webhook signature against the raw request body.

    Parameters
    ----------
    payload:
        The raw request body bytes exactly as received (before any JSON
        parsing).
    signature:
        The value of the ``X-Hub-Signature-256`` header, expected in the
        form ``sha256=<hex_digest>``.
    secret:
        The webhook secret configured in the GitHub App / webhook settings.

    Returns
    -------
    bool
        ``True`` if the signature is valid, ``False`` otherwise.

    Notes
    -----
    Uses ``hmac.compare_digest`` for constant-time comparison to prevent
    timing-based side-channel attacks.
    """
    if not signature or not signature.startswith("sha256="):
        return False

    expected_mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # The signature header looks like "sha256=abc123..."
    received_hex = signature.removeprefix("sha256=")

    return hmac.compare_digest(expected_mac, received_hex)
