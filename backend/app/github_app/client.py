"""GitHub App client for the VSWE project.

Provides async methods for authenticating as a GitHub App installation and
interacting with the GitHub REST API (posting comments, creating PRs,
cloning/pushing repos).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from app.github_app.auth import generate_app_jwt

logger = logging.getLogger(__name__)

_GITHUB_API_BASE = "https://api.github.com"

# Installation tokens are valid for 1 hour.  We refresh 5 minutes early
# to avoid using an expired token mid-request.
_TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60


@dataclass
class _CachedToken:
    """A cached installation access token with its expiry time."""
    token: str
    expires_at: float  # Unix timestamp


class GitHubAppClient:
    """Async client that authenticates as a GitHub App installation.

    Usage::

        client = GitHubAppClient(app_id="12345", private_key=PEM_STRING)
        token = await client.get_installation_token(installation_id)
        await client.post_comment("owner/repo", 42, "Hello!", installation_id)
    """

    def __init__(self, app_id: str, private_key: str) -> None:
        """Initialise with GitHub App credentials.

        Parameters
        ----------
        app_id:
            The GitHub App's numeric ID (as a string).
        private_key:
            The PEM-encoded RSA private key for the GitHub App.
        """
        self._app_id = app_id
        self._private_key = private_key
        self._token_cache: dict[int, _CachedToken] = {}
        self._token_locks: dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _generate_jwt(self) -> str:
        """Generate a JWT for GitHub App authentication (10 min expiry)."""
        return generate_app_jwt(self._app_id, self._private_key)

    def _get_lock(self, installation_id: int) -> asyncio.Lock:
        """Return (or create) a per-installation asyncio lock."""
        if installation_id not in self._token_locks:
            self._token_locks[installation_id] = asyncio.Lock()
        return self._token_locks[installation_id]

    async def get_installation_token(self, installation_id: int) -> str:
        """Exchange the App JWT for an installation access token.

        Tokens are cached and reused until they are within 5 minutes of
        expiry.  Concurrent requests for the same installation share the
        same token via an asyncio lock.
        """
        cached = self._token_cache.get(installation_id)
        if cached and cached.expires_at > time.time() + _TOKEN_REFRESH_MARGIN_SECONDS:
            return cached.token

        lock = self._get_lock(installation_id)
        async with lock:
            # Double-check after acquiring the lock.
            cached = self._token_cache.get(installation_id)
            if cached and cached.expires_at > time.time() + _TOKEN_REFRESH_MARGIN_SECONDS:
                return cached.token

            jwt_token = self._generate_jwt()
            url = f"{_GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {jwt_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                _raise_for_status(resp, "get installation token")
                data = resp.json()

            token = data["token"]
            # Parse the ISO-8601 expiry from GitHub; fall back to 1 hour.
            expires_at = time.time() + 3600
            if "expires_at" in data:
                from datetime import datetime, timezone

                try:
                    dt = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
                    expires_at = dt.timestamp()
                except (ValueError, TypeError):
                    pass

            self._token_cache[installation_id] = _CachedToken(
                token=token,
                expires_at=expires_at,
            )
            logger.info("Obtained new installation token for installation %s.", installation_id)
            return token

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        installation_id: int,
        *,
        json_body: dict | None = None,
    ) -> httpx.Response:
        """Make an authenticated request to the GitHub API."""
        token = await self.get_installation_token(installation_id)

        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method,
                url,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=json_body,
                timeout=30.0,
            )

        return resp

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def post_comment(
        self,
        repo_full_name: str,
        issue_number: int,
        body: str,
        installation_id: int,
    ) -> dict:
        """Post a comment on a GitHub issue or pull request.

        Parameters
        ----------
        repo_full_name:
            Repository in ``owner/repo`` format.
        issue_number:
            The issue or PR number.
        body:
            Markdown body of the comment.
        installation_id:
            GitHub App installation ID with access to the repository.

        Returns
        -------
        dict
            The created comment object from the GitHub API.
        """
        url = f"{_GITHUB_API_BASE}/repos/{repo_full_name}/issues/{issue_number}/comments"
        resp = await self._request("POST", url, installation_id, json_body={"body": body})
        _raise_for_status(resp, f"post comment on {repo_full_name}#{issue_number}")
        return resp.json()

    async def create_pull_request(
        self,
        repo_full_name: str,
        title: str,
        body: str,
        head: str,
        base: str,
        installation_id: int,
    ) -> dict:
        """Create a pull request.

        Parameters
        ----------
        repo_full_name:
            Repository in ``owner/repo`` format.
        title:
            PR title.
        body:
            PR description (Markdown).
        head:
            The branch containing the changes.
        base:
            The branch to merge into (e.g. ``main``).
        installation_id:
            GitHub App installation ID.

        Returns
        -------
        dict
            The created pull request object from the GitHub API.
        """
        url = f"{_GITHUB_API_BASE}/repos/{repo_full_name}/pulls"
        resp = await self._request(
            "POST",
            url,
            installation_id,
            json_body={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
        _raise_for_status(resp, f"create PR on {repo_full_name}")
        return resp.json()

    async def clone_repo(
        self,
        repo_full_name: str,
        target_path: str,
        installation_id: int,
    ) -> None:
        """Clone a repository using the installation token for authentication.

        The clone uses HTTPS with the installation token embedded in the URL,
        which avoids the need for SSH keys or credential helpers.

        Parameters
        ----------
        repo_full_name:
            Repository in ``owner/repo`` format.
        target_path:
            Local filesystem path to clone into.
        installation_id:
            GitHub App installation ID.
        """
        token = await self.get_installation_token(installation_id)
        clone_url = f"https://x-access-token:{token}@github.com/{repo_full_name}.git"

        process = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", clone_url, target_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            # Scrub the token from error messages before logging/raising.
            safe_stderr = stderr.decode("utf-8", errors="replace").replace(token, "***")
            raise RuntimeError(
                f"git clone failed (exit {process.returncode}) for {repo_full_name}: {safe_stderr}"
            )

        logger.info("Cloned %s to %s.", repo_full_name, target_path)

    async def push_branch(
        self,
        workspace_path: str,
        branch_name: str,
        installation_id: int,
        repo_full_name: str,
    ) -> None:
        """Push a local branch to the remote.

        Configures the remote URL with the installation token so that
        ``git push`` authenticates correctly.

        Parameters
        ----------
        workspace_path:
            Path to the local git repository.
        branch_name:
            The local branch to push.
        installation_id:
            GitHub App installation ID.
        repo_full_name:
            Repository in ``owner/repo`` format.
        """
        token = await self.get_installation_token(installation_id)
        remote_url = f"https://x-access-token:{token}@github.com/{repo_full_name}.git"

        # Set the remote URL (overwrites existing origin).
        await self._run_git(
            workspace_path,
            "git", "remote", "set-url", "origin", remote_url,
            token=token,
        )

        # Push the branch.
        await self._run_git(
            workspace_path,
            "git", "push", "-u", "origin", branch_name,
            token=token,
        )

        logger.info("Pushed branch %s to %s.", branch_name, repo_full_name)

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    @staticmethod
    async def _run_git(
        cwd: str,
        *cmd: str,
        token: str = "",
    ) -> str:
        """Run a git subprocess and return stdout.

        Raises ``RuntimeError`` on non-zero exit, with the token scrubbed
        from error output.
        """
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            safe_stderr = stderr.decode("utf-8", errors="replace")
            if token:
                safe_stderr = safe_stderr.replace(token, "***")
            raise RuntimeError(
                f"git command failed (exit {process.returncode}): {safe_stderr}"
            )

        return stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class GitHubAPIError(Exception):
    """Raised when a GitHub API request returns an unexpected status."""

    def __init__(self, message: str, status_code: int, response_body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class GitHubRateLimitError(GitHubAPIError):
    """Raised when GitHub returns a 403 due to rate limiting."""


def _raise_for_status(resp: httpx.Response, context: str) -> None:
    """Check an HTTP response and raise a descriptive error if it failed."""
    if resp.is_success:
        return

    body_text = resp.text[:500]

    # Rate limit handling
    if resp.status_code == 403 and "rate limit" in body_text.lower():
        retry_after = resp.headers.get("Retry-After", "unknown")
        raise GitHubRateLimitError(
            f"GitHub rate limit exceeded while trying to {context}. "
            f"Retry after: {retry_after}s.",
            status_code=resp.status_code,
            response_body=body_text,
        )

    # Secondary rate limit (abuse detection)
    if resp.status_code == 403 and "abuse" in body_text.lower():
        raise GitHubRateLimitError(
            f"GitHub secondary rate limit (abuse detection) while trying to {context}.",
            status_code=resp.status_code,
            response_body=body_text,
        )

    raise GitHubAPIError(
        f"GitHub API error ({resp.status_code}) while trying to {context}: {body_text}",
        status_code=resp.status_code,
        response_body=body_text,
    )
