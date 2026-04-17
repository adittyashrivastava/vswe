"""Authentication routes — GitHub App OAuth flow with JWT session management."""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.db.dynamo import get_item, put_item, query_by_gsi
from app.db.models import TABLE_USERS, UserItem

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE = "https://api.github.com"

def _get_oauth_redirect_uri() -> str:
    return f"{settings.backend_url}/api/auth/github/callback"

def _get_frontend_success_url() -> str:
    return f"{settings.frontend_url}/auth/success"

# In-memory store for OAuth state tokens. In production, use Redis or DynamoDB
# with a TTL.  Each entry maps state -> creation timestamp.
_pending_states: dict[str, float] = {}
_STATE_TTL_SECONDS = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class UserInfo(BaseModel):
    """Authenticated user context — used as a FastAPI dependency throughout
    the application."""
    user_id: str
    github_login: str
    name: str | None = None
    avatar_url: str | None = None
    github_access_token: str | None = None


class UserInfoResponse(BaseModel):
    """Public user info returned by /me (no access token)."""
    user_id: str
    github_login: str
    name: str | None = None
    avatar_url: str | None = None
    email: str | None = None
    orgs: list[str] = Field(default_factory=list)


class RepoInfo(BaseModel):
    full_name: str
    private: bool
    permissions: dict[str, bool] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _create_jwt(user_id: str, github_login: str) -> str:
    """Mint a signed JWT with standard claims."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "github_login": github_login,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + settings.jwt_expiry_hours * 3600,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _decode_jwt(token: str) -> dict[str, Any]:
    """Decode and validate a JWT.  Raises HTTPException on failure."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        )


# ---------------------------------------------------------------------------
# State management helpers
# ---------------------------------------------------------------------------

def _generate_state() -> str:
    """Create a random state string and store it for later validation."""
    _purge_expired_states()
    state = secrets.token_urlsafe(32)
    _pending_states[state] = time.time()
    return state


def _validate_state(state: str) -> bool:
    """Check that the state was issued by us and has not expired."""
    _purge_expired_states()
    created = _pending_states.pop(state, None)
    if created is None:
        return False
    return (time.time() - created) < _STATE_TTL_SECONDS


def _purge_expired_states() -> None:
    """Remove expired state tokens to prevent unbounded memory growth."""
    cutoff = time.time() - _STATE_TTL_SECONDS
    expired = [s for s, t in _pending_states.items() if t < cutoff]
    for s in expired:
        _pending_states.pop(s, None)


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

async def _exchange_code_for_token(code: str) -> str:
    """Exchange an OAuth authorization code for a GitHub access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
    if resp.status_code != 200:
        logger.error("GitHub token exchange failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to exchange code with GitHub.",
        )
    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        error = data.get("error_description", data.get("error", "unknown"))
        logger.error("GitHub token exchange returned error: %s", error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub OAuth error: {error}",
        )
    return access_token


async def _get_github_user(token: str) -> dict[str, Any]:
    """Fetch the authenticated user's profile from GitHub."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15.0,
        )
    if resp.status_code != 200:
        logger.error("GitHub /user failed: %s", resp.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch user from GitHub.",
        )
    return resp.json()


async def _get_github_user_orgs(token: str) -> list[str]:
    """Fetch the authenticated user's org memberships."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/user/orgs",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15.0,
        )
    if resp.status_code != 200:
        logger.warning("GitHub /user/orgs failed: %s", resp.text)
        return []
    return [org["login"] for org in resp.json()]


async def _get_github_user_installations(token: str) -> dict[str, int]:
    """Fetch GitHub App installations accessible to the user.

    Returns a dict mapping account login -> installation_id.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/user/installations",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15.0,
        )
    if resp.status_code != 200:
        logger.warning("GitHub /user/installations failed: %s", resp.text)
        return {}
    data = resp.json()
    installations: dict[str, int] = {}
    for inst in data.get("installations", []):
        account = inst.get("account", {})
        login = account.get("login")
        inst_id = inst.get("id")
        if login and inst_id:
            installations[login] = inst_id
    return installations


async def _get_installation_repos(
    token: str, installation_id: int,
) -> list[dict[str, Any]]:
    """Fetch repositories accessible through a specific GitHub App installation."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/user/installations/{installation_id}/repositories",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=15.0,
        )
    if resp.status_code != 200:
        logger.warning(
            "GitHub installation repos failed for %s: %s",
            installation_id, resp.text,
        )
        return []
    return resp.json().get("repositories", [])


# ---------------------------------------------------------------------------
# DynamoDB user helpers
# ---------------------------------------------------------------------------

async def _upsert_user(
    github_user: dict[str, Any],
    access_token: str,
    orgs: list[str],
    installations: dict[str, int],
) -> UserItem:
    """Create or update a user record in DynamoDB."""
    github_id = github_user["id"]
    user_id = f"github:{github_id}"
    now = datetime.now(timezone.utc).isoformat()

    # Check if user already exists.
    existing = await get_item(TABLE_USERS, {"user_id": user_id, "SK": "META"})

    user_item = UserItem(
        user_id=user_id,
        github_id=github_id,
        github_login=github_user["login"],
        name=github_user.get("name"),
        email=github_user.get("email"),
        avatar_url=github_user.get("avatar_url"),
        github_access_token=access_token,
        orgs=orgs,
        installations=installations,
        created_at=existing["created_at"] if existing else now,
        updated_at=now,
        last_login_at=now,
    )
    await put_item(TABLE_USERS, user_item.to_dynamo_item())
    return user_item


async def _load_user_from_db(user_id: str) -> dict[str, Any] | None:
    """Load a user record from DynamoDB by user_id."""
    return await get_item(TABLE_USERS, {"user_id": user_id, "SK": "META"})


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def get_current_user(
    authorization: str | None = Header(None, description="Bearer <token>"),
) -> UserInfo:
    """Dependency that extracts and validates the current user from the
    ``Authorization`` header.

    In local dev with no auth configured, returns a default local user.
    """
    if not authorization or authorization.strip() == "":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required.",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header. Expected 'Bearer <token>'.",
        )

    claims = _decode_jwt(token)
    user_id: str = claims.get("sub", "")
    github_login: str = claims.get("github_login", "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required claims.",
        )

    # Load fresh user data from DynamoDB.
    user_record = await _load_user_from_db(user_id)
    if not user_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )

    return UserInfo(
        user_id=user_id,
        github_login=user_record.get("github_login", github_login),
        name=user_record.get("name"),
        avatar_url=user_record.get("avatar_url"),
        github_access_token=user_record.get("github_access_token"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/github/login")
async def github_login():
    """Redirect the user to GitHub's OAuth authorization page."""
    state = _generate_state()
    params = (
        f"client_id={settings.github_client_id}"
        f"&redirect_uri={_get_oauth_redirect_uri()}"
        f"&state={state}"
    )
    return RedirectResponse(
        url=f"{GITHUB_AUTHORIZE_URL}?{params}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/github/callback")
async def github_callback(code: str, state: str):
    """Handle the OAuth callback from GitHub.

    Validates state, exchanges code for token, fetches user data, upserts
    the user record, and redirects to the frontend with a JWT.
    """
    # 1. Validate CSRF state.
    if not _validate_state(state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state parameter.",
        )

    # 2. Exchange code for access token.
    access_token = await _exchange_code_for_token(code)

    # 3. Fetch user profile, orgs, and installations in parallel.
    async with httpx.AsyncClient() as _:
        github_user = await _get_github_user(access_token)
        orgs = await _get_github_user_orgs(access_token)
        installations = await _get_github_user_installations(access_token)

    # 4. Upsert user in DynamoDB.
    user_item = await _upsert_user(github_user, access_token, orgs, installations)

    # 5. Mint JWT.
    token = _create_jwt(user_item.user_id, user_item.github_login)

    # 6. Redirect to frontend with token.
    return RedirectResponse(
        url=f"{_get_frontend_success_url()}?token={token}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/me", response_model=UserInfoResponse)
async def me(current_user: UserInfo = Depends(get_current_user)):
    """Return information about the currently authenticated user.

    Fetches fresh data from DynamoDB rather than relying solely on JWT claims.
    """
    user_record = await _load_user_from_db(current_user.user_id)
    if not user_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    return UserInfoResponse(
        user_id=current_user.user_id,
        github_login=user_record.get("github_login", current_user.github_login),
        name=user_record.get("name"),
        avatar_url=user_record.get("avatar_url"),
        email=user_record.get("email"),
        orgs=user_record.get("orgs", []),
    )


@router.get("/github/repos", response_model=list[RepoInfo])
async def list_repos(current_user: UserInfo = Depends(get_current_user)):
    """List repositories accessible to the authenticated user via the GitHub App.

    Iterates over all GitHub App installations the user has access to and
    returns a flat list of repositories.
    """
    if not current_user.github_access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No GitHub access token available.",
        )

    # Load installations from the user record.
    user_record = await _load_user_from_db(current_user.user_id)
    if not user_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    installations: dict[str, Any] = user_record.get("installations", {})
    if not installations:
        return []

    repos: list[RepoInfo] = []
    for _account, installation_id in installations.items():
        raw_repos = await _get_installation_repos(
            current_user.github_access_token,
            int(installation_id),
        )
        for repo in raw_repos:
            repos.append(
                RepoInfo(
                    full_name=repo.get("full_name", ""),
                    private=repo.get("private", False),
                    permissions=repo.get("permissions", {}),
                )
            )

    return repos


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout():
    """Logout endpoint.

    JWT tokens are stateless so there is nothing to invalidate server-side.
    The frontend should discard the token.  This endpoint exists for
    API completeness and future server-side session invalidation.
    """
    return {"detail": "Logged out successfully."}
