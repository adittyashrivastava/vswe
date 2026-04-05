"""GitHub App JWT generation.

Provides a standalone function for creating short-lived JWTs used to
authenticate as a GitHub App.  Used by both the GitHub client and the
Lambda webhook handler.
"""

from __future__ import annotations

import time

import jwt  # PyJWT


def generate_app_jwt(app_id: str, private_key: str) -> str:
    """Generate a JWT for GitHub App authentication.

    The token is valid for 10 minutes (the maximum allowed by GitHub).
    GitHub recommends issuing the JWT with an ``iat`` claim set 60 seconds
    in the past to account for clock drift.

    Parameters
    ----------
    app_id:
        The GitHub App's numeric ID (as a string).
    private_key:
        The PEM-encoded RSA private key for the GitHub App.

    Returns
    -------
    str
        An encoded JWT string suitable for the ``Authorization: Bearer <jwt>``
        header when calling GitHub App endpoints.
    """
    now = int(time.time())

    payload = {
        "iat": now - 60,       # Issued at — 60s in the past for clock drift
        "exp": now + (10 * 60),  # Expires in 10 minutes
        "iss": app_id,
    }

    return jwt.encode(payload, private_key, algorithm="RS256")
