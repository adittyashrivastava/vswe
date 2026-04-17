"""Permission-based tool scoping for the VSWE agent.

Checks a user's permissions on a GitHub repository and returns the
appropriate tool definitions for their access level.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

from app.agent.tools import (
    CLONE_REPO_DEFINITION,
    COMMIT_AND_PUSH_DEFINITION,
    CREATE_BRANCH_DEFINITION,
    CREATE_PULL_REQUEST_DEFINITION,
    EDIT_FILE_DEFINITION,
    LIST_FILES_DEFINITION,
    READ_FILE_DEFINITION,
    RUN_COMMAND_DEFINITION,
    SEARCH_CODE_DEFINITION,
    TOOL_DEFINITIONS,
    WRITE_FILE_DEFINITION,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class RepoPermissionLevel(str, Enum):
    NONE = "none"
    READ = "read"       # pull=True, push=False
    WRITE = "write"     # pull=True, push=True
    ADMIN = "admin"     # admin=True


@dataclass
class RepoPermissions:
    level: RepoPermissionLevel
    repo_full_name: str
    can_read: bool
    can_write: bool
    can_admin: bool


# ---------------------------------------------------------------------------
# GitHub API check
# ---------------------------------------------------------------------------

_GITHUB_API_BASE = "https://api.github.com"


async def check_repo_permissions(
    github_access_token: str,
    repo_full_name: str,
) -> RepoPermissions:
    """Check a user's permissions on a repository via GitHub API.

    Calls GET /repos/{owner}/{repo} with the user's token.
    The response includes a ``permissions`` field:
    ``{"pull": bool, "push": bool, "admin": bool}``

    Returns a :class:`RepoPermissions` object.
    If the API returns 404 or 403, the user has NO access.
    """
    headers = {
        "Authorization": f"Bearer {github_access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_GITHUB_API_BASE}/repos/{repo_full_name}",
            headers=headers,
        )

    if resp.status_code in (403, 404):
        logger.info(
            "No access to %s (HTTP %s)", repo_full_name, resp.status_code,
        )
        return RepoPermissions(
            level=RepoPermissionLevel.NONE,
            repo_full_name=repo_full_name,
            can_read=False,
            can_write=False,
            can_admin=False,
        )

    resp.raise_for_status()
    data = resp.json()
    perms = data.get("permissions", {})

    can_admin = perms.get("admin", False)
    can_push = perms.get("push", False)
    can_pull = perms.get("pull", False)

    if can_admin:
        level = RepoPermissionLevel.ADMIN
    elif can_push:
        level = RepoPermissionLevel.WRITE
    elif can_pull:
        level = RepoPermissionLevel.READ
    else:
        level = RepoPermissionLevel.NONE

    return RepoPermissions(
        level=level,
        repo_full_name=repo_full_name,
        can_read=can_pull,
        can_write=can_push,
        can_admin=can_admin,
    )


# ---------------------------------------------------------------------------
# Tool sets by permission level
# ---------------------------------------------------------------------------

# Read-only commands: the run_command tool with a restricted description is
# the same definition object — the orchestrator simply includes or excludes
# tools.  We define separate lists so callers can pick the right one.

_READ_ONLY_TOOL_NAMES: set[str] = {
    "read_file",
    "search_code",
    "list_files",
    "clone_repo",
    "run_command",
    "get_job_status",
}

_WRITE_TOOL_NAMES: set[str] = _READ_ONLY_TOOL_NAMES | {
    "edit_file",
    "write_file",
    "create_branch",
    "commit_and_push",
    "create_pull_request",
    "submit_training_job",
    "get_job_status",
}


def get_read_only_tools() -> list[dict[str, Any]]:
    """Return only read-safe tool definitions."""
    return [t for t in TOOL_DEFINITIONS if t["name"] in _READ_ONLY_TOOL_NAMES]


def get_full_tools() -> list[dict[str, Any]]:
    """Return all tool definitions."""
    return list(TOOL_DEFINITIONS)


def get_tools_for_permission_level(
    level: RepoPermissionLevel,
) -> list[dict[str, Any]]:
    """Return the filtered list of tool definitions based on permission level.

    READ access tools:
    - read_file, search_code, list_files, clone_repo, run_command
      (read-only commands only)

    WRITE access tools (all READ tools plus):
    - edit_file, write_file, create_branch, commit_and_push,
      create_pull_request, run_command (full)

    ADMIN access tools:
    - Same as WRITE (no additional tools for now)

    NONE:
    - Empty list
    """
    if level == RepoPermissionLevel.NONE:
        return []
    if level == RepoPermissionLevel.READ:
        return get_read_only_tools()
    # WRITE and ADMIN get the full tool set
    return get_full_tools()


# ---------------------------------------------------------------------------
# System prompt snippets
# ---------------------------------------------------------------------------

PERMISSION_PROMPT_SNIPPETS: dict[RepoPermissionLevel, str] = {
    RepoPermissionLevel.NONE: (
        "You do NOT have access to this repository. "
        "Inform the user that you cannot access the repository and suggest "
        "they check the repository URL or their permissions."
    ),
    RepoPermissionLevel.READ: (
        "You have READ-ONLY access to this repository. "
        "You can read and search code but cannot make changes, create branches, "
        "or open PRs. If the user asks for changes, explain what you would change "
        "and suggest they make the changes themselves or request write access."
    ),
    RepoPermissionLevel.WRITE: (
        "You have full READ and WRITE access to this repository. "
        "You can read, edit, create branches, commit, push, and open pull requests."
    ),
    RepoPermissionLevel.ADMIN: (
        "You have ADMIN access to this repository. "
        "You can read, edit, create branches, commit, push, and open pull requests."
    ),
}
