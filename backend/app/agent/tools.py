"""Tool definitions and executors for the VSWE agent.

Each tool exposes:
- A ``definition`` dict compatible with Anthropic's tool-use API format.
- An async ``execute(workspace_path, params)`` function.

Public helpers:
- ``TOOL_DEFINITIONS`` — list of definition dicts for passing to the LLM.
- ``TOOL_EXECUTORS``   — dict mapping tool name -> execute function.
- ``execute_tool()``   — convenience wrapper.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_COMMAND_TIMEOUT = 120  # seconds
_MAX_READ_BYTES = 512_000      # ~500 KB per read
_MAX_OUTPUT_CHARS = 100_000    # truncate tool output for context window

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(workspace_path: str, rel_path: str) -> str:
    """Resolve *rel_path* against *workspace_path* and validate it stays inside."""
    base = Path(workspace_path).resolve()
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"Path escapes workspace: {rel_path}")
    return str(target)


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n\n... [{len(text) - limit} chars truncated] ...\n\n" + text[-half:]


async def _run_shell(
    command: str,
    cwd: str,
    timeout: int = _DEFAULT_COMMAND_TIMEOUT,
) -> str:
    """Run a shell command asynchronously and return combined stdout+stderr."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()  # type: ignore[union-attr]
        return f"[ERROR] Command timed out after {timeout}s: {command}"

    output = ""
    if stdout:
        output += stdout.decode(errors="replace")
    if stderr:
        output += ("\n--- stderr ---\n" if output else "") + stderr.decode(errors="replace")

    exit_info = f"[exit code: {proc.returncode}]"
    result = f"{_truncate(output)}\n{exit_info}" if output else exit_info
    return result


# ---------------------------------------------------------------------------
# 1. read_file
# ---------------------------------------------------------------------------

READ_FILE_DEFINITION: dict[str, Any] = {
    "name": "read_file",
    "description": (
        "Read the contents of a file in the workspace. "
        "Optionally specify start_line and end_line to read a range."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file within the workspace.",
            },
            "start_line": {
                "type": "integer",
                "description": "1-based line number to start reading from (inclusive). Omit to read from the beginning.",
            },
            "end_line": {
                "type": "integer",
                "description": "1-based line number to stop reading at (inclusive). Omit to read to the end.",
            },
        },
        "required": ["path"],
    },
}


async def _execute_read_file(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    path = _resolve(workspace_path, params["path"])

    if not os.path.isfile(path):
        return f"[ERROR] File not found: {params['path']}"

    size = os.path.getsize(path)
    if size > _MAX_READ_BYTES:
        if "start_line" not in params:
            return (
                f"[ERROR] File is {size:,} bytes — too large to read in full. "
                "Specify start_line/end_line to read a portion."
            )

    try:
        loop = asyncio.get_running_loop()
        content = await loop.run_in_executor(
            None, lambda: Path(path).read_text(errors="replace")
        )
    except Exception as exc:
        return f"[ERROR] {exc}"

    lines = content.splitlines(keepends=True)
    start = params.get("start_line")
    end = params.get("end_line")

    if start is not None or end is not None:
        s = max((start or 1) - 1, 0)
        e = end if end is not None else len(lines)
        selected = lines[s:e]
        numbered = [f"{s + i + 1}\t{line}" for i, line in enumerate(selected)]
        return _truncate("".join(numbered))

    # Full file with line numbers
    numbered = [f"{i + 1}\t{line}" for i, line in enumerate(lines)]
    return _truncate("".join(numbered))


# ---------------------------------------------------------------------------
# 2. edit_file
# ---------------------------------------------------------------------------

EDIT_FILE_DEFINITION: dict[str, Any] = {
    "name": "edit_file",
    "description": (
        "Edit a file by replacing an exact string match with new content. "
        "The old_string must appear exactly once in the file."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file within the workspace.",
            },
            "old_string": {
                "type": "string",
                "description": "The exact string to find and replace (must match exactly once).",
            },
            "new_string": {
                "type": "string",
                "description": "The replacement string.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
}


async def _execute_edit_file(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    path = _resolve(workspace_path, params["path"])

    if not os.path.isfile(path):
        return f"[ERROR] File not found: {params['path']}"

    old_string = params["old_string"]
    new_string = params["new_string"]

    if old_string == new_string:
        return "[ERROR] old_string and new_string are identical."

    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(
            None, lambda: Path(path).read_text(errors="replace")
        )
    except Exception as exc:
        return f"[ERROR] Reading file: {exc}"

    count = content.count(old_string)
    if count == 0:
        return "[ERROR] old_string not found in the file."
    if count > 1:
        return f"[ERROR] old_string matches {count} locations — must be unique. Provide more context."

    new_content = content.replace(old_string, new_string, 1)

    try:
        await loop.run_in_executor(
            None, lambda: Path(path).write_text(new_content)
        )
    except Exception as exc:
        return f"[ERROR] Writing file: {exc}"

    return f"OK — edited {params['path']}"


# ---------------------------------------------------------------------------
# 3. write_file
# ---------------------------------------------------------------------------

WRITE_FILE_DEFINITION: dict[str, Any] = {
    "name": "write_file",
    "description": (
        "Create a new file or completely overwrite an existing file. "
        "Parent directories are created automatically."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file within the workspace.",
            },
            "content": {
                "type": "string",
                "description": "The full content to write to the file.",
            },
        },
        "required": ["path", "content"],
    },
}


async def _execute_write_file(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    path = _resolve(workspace_path, params["path"])
    content = params["content"]

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: Path(path).parent.mkdir(parents=True, exist_ok=True))
        await loop.run_in_executor(None, lambda: Path(path).write_text(content))
    except Exception as exc:
        return f"[ERROR] {exc}"

    return f"OK — wrote {len(content)} bytes to {params['path']}"


# ---------------------------------------------------------------------------
# 4. search_code
# ---------------------------------------------------------------------------

SEARCH_CODE_DEFINITION: dict[str, Any] = {
    "name": "search_code",
    "description": (
        "Search for a regex pattern across files in the workspace using ripgrep. "
        "Returns matching lines with file paths and line numbers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "Relative directory or file path to search within. Defaults to the workspace root.",
            },
            "file_glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g. '*.py', '*.ts').",
            },
        },
        "required": ["pattern"],
    },
}


async def _execute_search_code(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    search_path = workspace_path
    if "path" in params and params["path"]:
        search_path = _resolve(workspace_path, params["path"])

    cmd_parts = ["rg", "--no-heading", "--line-number", "--max-count=200"]
    if "file_glob" in params and params["file_glob"]:
        cmd_parts.extend(["--glob", params["file_glob"]])

    cmd_parts.append("--")
    cmd_parts.append(params["pattern"])
    cmd_parts.append(search_path)

    # Build shell-safe command
    import shlex
    cmd = " ".join(shlex.quote(p) for p in cmd_parts)

    result = await _run_shell(cmd, cwd=workspace_path, timeout=30)

    # rg returns exit code 1 for no matches
    if "[exit code: 1]" in result and not result.strip().replace("[exit code: 1]", "").strip():
        return "No matches found."

    return result


# ---------------------------------------------------------------------------
# 5. list_files
# ---------------------------------------------------------------------------

LIST_FILES_DEFINITION: dict[str, Any] = {
    "name": "list_files",
    "description": (
        "List files and directories at a given path. "
        "Use recursive=true to list all files in the tree."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative directory path. Defaults to the workspace root.",
            },
            "recursive": {
                "type": "boolean",
                "description": "If true, recursively list all files. Defaults to false.",
            },
        },
        "required": [],
    },
}


async def _execute_list_files(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    target = workspace_path
    if params.get("path"):
        target = _resolve(workspace_path, params["path"])

    if not os.path.isdir(target):
        return f"[ERROR] Not a directory: {params.get('path', '.')}"

    recursive = params.get("recursive", False)

    if recursive:
        result = await _run_shell(
            "find . -type f -not -path '*/.git/*' | head -1000 | sort",
            cwd=target,
            timeout=15,
        )
    else:
        result = await _run_shell("ls -la", cwd=target, timeout=10)

    return result


# ---------------------------------------------------------------------------
# 6. run_command
# ---------------------------------------------------------------------------

RUN_COMMAND_DEFINITION: dict[str, Any] = {
    "name": "run_command",
    "description": (
        "Run a shell command in the workspace. "
        "Use for building, testing, linting, installing dependencies, etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Defaults to 120.",
            },
        },
        "required": ["command"],
    },
}


async def _execute_run_command(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    command = params["command"]
    timeout = params.get("timeout", _DEFAULT_COMMAND_TIMEOUT)

    # Basic safety checks
    dangerous_patterns = ["rm -rf /", "rm -rf /*", ":(){ :|:& };:", "> /dev/sda"]
    for pat in dangerous_patterns:
        if pat in command:
            return f"[ERROR] Refused to execute dangerous command: {command}"

    # Block raw git push/clone — must use dedicated tools for authenticated operations
    git_blocked = ["git push", "git clone"]
    for pat in git_blocked:
        if pat in command:
            return (
                f"[ERROR] '{pat}' is not allowed via run_command. "
                f"Use the dedicated '{pat.split()[1]}_repo' or 'commit_and_push' tool instead — "
                f"they handle GitHub authentication automatically."
            )

    # Prevent git from hanging on credential prompts
    env_prefix = "GIT_TERMINAL_PROMPT=0 " if "git " in command else ""
    return await _run_shell(f"{env_prefix}{command}", cwd=workspace_path, timeout=timeout)


# ---------------------------------------------------------------------------
# 7. clone_repo
# ---------------------------------------------------------------------------

CLONE_REPO_DEFINITION: dict[str, Any] = {
    "name": "clone_repo",
    "description": (
        "Clone a GitHub repository into the workspace. "
        "The repo is cloned into a subdirectory matching the repo name."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": (
                    "GitHub repository URL (HTTPS or SSH) or shorthand 'owner/repo'."
                ),
            },
        },
        "required": ["repo_url"],
    },
}


def _inject_token_in_url(repo_url: str, token: str | None) -> str:
    """Inject a GitHub token into an HTTPS repo URL for authenticated git operations."""
    if not token:
        return repo_url
    # https://github.com/owner/repo.git -> https://x-access-token:{token}@github.com/owner/repo.git
    if repo_url.startswith("https://github.com/"):
        return repo_url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/")
    return repo_url


def _extract_repo_full_name(repo_url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub URL."""
    import re
    m = re.match(r"https?://(?:x-access-token:[^@]+@)?github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url)
    if m:
        return m.group(1)
    m = re.match(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$", repo_url)
    if m:
        return m.group(1)
    return None


async def _execute_clone_repo(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    repo_url = params["repo_url"]
    github_token = kwargs.get("github_token")

    # Normalise shorthand "owner/repo" to HTTPS URL
    if "/" in repo_url and "://" not in repo_url and not repo_url.startswith("git@"):
        repo_url = f"https://github.com/{repo_url}.git"

    # Extract repo name for the target directory
    repo_name = repo_url.rstrip("/").rsplit("/", 1)[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    target = os.path.join(workspace_path, repo_name)
    if os.path.isdir(target):
        return f"Directory '{repo_name}' already exists in the workspace. Using existing clone."

    # Inject token for authenticated clone
    auth_url = _inject_token_in_url(repo_url, github_token)

    result = await _run_shell(
        f"GIT_TERMINAL_PROMPT=0 git clone --depth 50 {auth_url} {repo_name}",
        cwd=workspace_path,
        timeout=300,
    )
    # Scrub token from output
    if github_token and github_token in result:
        result = result.replace(github_token, "***")
    return result


# ---------------------------------------------------------------------------
# 8. create_branch
# ---------------------------------------------------------------------------

CREATE_BRANCH_DEFINITION: dict[str, Any] = {
    "name": "create_branch",
    "description": "Create and check out a new Git branch in the workspace repository.",
    "input_schema": {
        "type": "object",
        "properties": {
            "branch_name": {
                "type": "string",
                "description": "Name of the new branch.",
            },
        },
        "required": ["branch_name"],
    },
}


async def _execute_create_branch(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    branch_name = params["branch_name"]
    # Find the git repo root inside the workspace
    repo_dir = await _find_git_root(workspace_path)
    if not repo_dir:
        return "[ERROR] No git repository found in the workspace."

    return await _run_shell(
        f"git checkout -b {branch_name}",
        cwd=repo_dir,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# 9. commit_and_push
# ---------------------------------------------------------------------------

COMMIT_AND_PUSH_DEFINITION: dict[str, Any] = {
    "name": "commit_and_push",
    "description": "Stage files, commit, and push to the remote repository.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Commit message.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of file paths to stage. If omitted, all modified/new files are staged."
                ),
            },
        },
        "required": ["message"],
    },
}


async def _execute_commit_and_push(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    repo_dir = await _find_git_root(workspace_path)
    if not repo_dir:
        return "[ERROR] No git repository found in the workspace."

    import shlex

    github_token = kwargs.get("github_token")
    message = params["message"]
    files = params.get("files")

    # Step 1: Stage files
    if files:
        resolved = _resolve_file_paths_to_repo(files, workspace_path, repo_dir)
        file_args = " ".join(shlex.quote(f) for f in resolved)
        stage_result = await _run_shell(f"git add {file_args}", cwd=repo_dir, timeout=15)
    else:
        stage_result = await _run_shell("git add -A", cwd=repo_dir, timeout=15)

    # Step 2: Commit (skip if nothing to commit — still proceed to push)
    has_changes = True
    status_result = await _run_shell("git diff --cached --quiet; echo $?", cwd=repo_dir, timeout=5)
    if status_result.strip().endswith("0"):
        # Nothing staged — check if there are unpushed commits
        has_changes = False
        logger.info("Nothing to commit, will attempt to push existing commits")

    commit_result = ""
    if has_changes:
        commit_result = await _run_shell(
            f"git commit -m {shlex.quote(message)}",
            cwd=repo_dir, timeout=15,
        )

    # Step 3: Set up authenticated push
    original_url = None
    if github_token:
        get_remote = await _run_shell("git remote get-url origin", cwd=repo_dir, timeout=5)
        original_url = get_remote.strip().split("\n")[0].strip()
        if original_url and "github.com" in original_url:
            auth_url = _inject_token_in_url(original_url, github_token)
            await _run_shell(f"git remote set-url origin {shlex.quote(auth_url)}", cwd=repo_dir, timeout=5)

    # Step 4: Push
    branch_result = await _run_shell("git rev-parse --abbrev-ref HEAD", cwd=repo_dir, timeout=5)
    branch = branch_result.strip().split("\n")[0].strip() or "HEAD"

    push_result = await _run_shell(
        f"GIT_TERMINAL_PROMPT=0 git push -u origin {branch}",
        cwd=repo_dir, timeout=60,
    )

    # Step 5: Restore original remote URL
    if original_url and github_token:
        await _run_shell(f"git remote set-url origin {shlex.quote(original_url)}", cwd=repo_dir, timeout=5)

    # Build response
    parts = []
    if commit_result:
        parts.append(commit_result.strip())
    elif not has_changes:
        parts.append("No new changes to commit.")
    parts.append(push_result.strip())
    result = "\n".join(parts)

    # Scrub token from output
    if github_token and github_token in result:
        result = result.replace(github_token, "***")
    return result


# ---------------------------------------------------------------------------
# 10. create_pull_request
# ---------------------------------------------------------------------------

CREATE_PULL_REQUEST_DEFINITION: dict[str, Any] = {
    "name": "create_pull_request",
    "description": "Create a pull request on GitHub for the current repository.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "PR title.",
            },
            "body": {
                "type": "string",
                "description": "PR description body (Markdown).",
            },
            "head_branch": {
                "type": "string",
                "description": "The branch containing your changes.",
            },
            "base_branch": {
                "type": "string",
                "description": "The branch to merge into (e.g. 'main').",
            },
        },
        "required": ["title", "body", "head_branch", "base_branch"],
    },
}


async def _execute_create_pull_request(workspace_path: str, params: dict[str, Any], **kwargs: Any) -> str:
    github_token = kwargs.get("github_token")
    if not github_token:
        return "[ERROR] GitHub token required to create a pull request."

    repo_dir = await _find_git_root(workspace_path)
    if not repo_dir:
        return "[ERROR] No git repository found in the workspace."

    # Get repo full name from remote URL
    get_remote = await _run_shell("git remote get-url origin", cwd=repo_dir, timeout=5)
    remote_url = get_remote.strip().split("\n")[0].strip()
    repo_full_name = _extract_repo_full_name(remote_url)
    if not repo_full_name:
        return f"[ERROR] Could not determine repo from remote URL: {remote_url}"

    title = params["title"]
    body = params["body"]
    head = params["head_branch"]
    base = params["base_branch"]

    # Create PR via GitHub API
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/repos/{repo_full_name}/pulls",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                },
                json={
                    "title": title,
                    "body": body,
                    "head": head,
                    "base": base,
                },
                timeout=15.0,
            )
        if resp.status_code == 201:
            pr_data = resp.json()
            return f"Pull request created: {pr_data['html_url']}"
        else:
            error = resp.json().get("message", resp.text)
            return f"[ERROR] GitHub API returned {resp.status_code}: {error}"
    except Exception as exc:
        return f"[ERROR] Failed to create pull request: {exc}"


# ---------------------------------------------------------------------------
# Git helper
# ---------------------------------------------------------------------------

def _resolve_file_paths_to_repo(files: list[str], workspace_path: str, repo_dir: str) -> list[str]:
    """Resolve file paths that may be relative to workspace or repo root.

    The LLM sometimes provides paths like 'RepoName/file.py' (relative to workspace)
    instead of 'file.py' (relative to repo root). This function normalizes them.
    """
    resolved = []
    for f in files:
        full_path = os.path.join(workspace_path, f)
        # If the path exists relative to workspace, convert to relative-to-repo
        if os.path.exists(full_path):
            try:
                rel = os.path.relpath(full_path, repo_dir)
                resolved.append(rel)
                continue
            except ValueError:
                pass

        # If path exists relative to repo dir, use as-is
        if os.path.exists(os.path.join(repo_dir, f)):
            resolved.append(f)
            continue

        # If path starts with the repo directory name, strip it
        repo_name = os.path.basename(repo_dir)
        if f.startswith(repo_name + "/"):
            stripped = f[len(repo_name) + 1:]
            if os.path.exists(os.path.join(repo_dir, stripped)):
                resolved.append(stripped)
                continue

        # Fall back to original path — git add will report if it doesn't exist
        resolved.append(f)

    return resolved


async def _find_git_root(workspace_path: str) -> str | None:
    """Find the git repository root inside the workspace.

    Checks the workspace itself first, then looks one level deep for a
    subdirectory containing a ``.git`` folder.
    """
    if os.path.isdir(os.path.join(workspace_path, ".git")):
        return workspace_path

    try:
        for entry in os.scandir(workspace_path):
            if entry.is_dir() and os.path.isdir(os.path.join(entry.path, ".git")):
                return entry.path
    except OSError:
        pass

    return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    READ_FILE_DEFINITION,
    EDIT_FILE_DEFINITION,
    WRITE_FILE_DEFINITION,
    SEARCH_CODE_DEFINITION,
    LIST_FILES_DEFINITION,
    RUN_COMMAND_DEFINITION,
    CLONE_REPO_DEFINITION,
    CREATE_BRANCH_DEFINITION,
    COMMIT_AND_PUSH_DEFINITION,
    CREATE_PULL_REQUEST_DEFINITION,
]

TOOL_EXECUTORS: dict[str, Any] = {
    "read_file": _execute_read_file,
    "edit_file": _execute_edit_file,
    "write_file": _execute_write_file,
    "search_code": _execute_search_code,
    "list_files": _execute_list_files,
    "run_command": _execute_run_command,
    "clone_repo": _execute_clone_repo,
    "create_branch": _execute_create_branch,
    "commit_and_push": _execute_commit_and_push,
    "create_pull_request": _execute_create_pull_request,
}


async def execute_tool(
    tool_name: str,
    workspace_path: str,
    params: dict[str, Any],
    github_token: str | None = None,
) -> str:
    """Execute a tool by name and return its string output.

    Raises ``KeyError`` if the tool name is unknown.
    """
    executor = TOOL_EXECUTORS.get(tool_name)
    if executor is None:
        raise KeyError(f"Unknown tool: {tool_name}")

    logger.info("Executing tool %s with params %s", tool_name, json.dumps(params, default=str)[:500])
    try:
        result = await executor(workspace_path, params, github_token=github_token)
    except Exception as exc:
        logger.exception("Tool %s raised an exception", tool_name)
        result = f"[ERROR] Tool execution failed: {exc}"

    return result
