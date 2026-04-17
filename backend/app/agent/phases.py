"""Agent workflow phases — clarify → plan → execute.

The orchestrator gates which tools the LLM can see based on the current
phase, making it *impossible* for the model to edit files before the user
has approved a plan.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from app.agent.tools import TOOL_DEFINITIONS, SUBMIT_PLAN_DEFINITION


class AgentPhase(str, Enum):
    """The three phases of a user-initiated task."""

    CLARIFY = "clarify"          # Read-only exploration + ask questions + submit_plan
    PLAN_REVIEW = "plan_review"  # Paused — waiting for user to approve / revise
    EXECUTE = "execute"          # Full tool access (minus submit_plan)


# Tool names available during the CLARIFY phase.
# The LLM can read code, search, run read-only commands, and clone — but
# it cannot edit, write, branch, commit, push, or create PRs.
_CLARIFY_TOOL_NAMES: set[str] = {
    "read_file",
    "search_code",
    "list_files",
    "run_command",
    "clone_repo",
    "submit_plan",
    "get_job_status",  # Read-only — checking status is safe in any phase
}

# Everything except submit_plan during EXECUTE.
_EXECUTE_TOOL_NAMES: set[str] = {t["name"] for t in TOOL_DEFINITIONS}


def get_tools_for_phase(phase: AgentPhase) -> list[dict[str, Any]]:
    """Return the tool definitions the LLM should see in *phase*."""

    if phase == AgentPhase.CLARIFY:
        tools = [t for t in TOOL_DEFINITIONS if t["name"] in _CLARIFY_TOOL_NAMES]
        tools.append(SUBMIT_PLAN_DEFINITION)
        return tools

    if phase == AgentPhase.EXECUTE:
        return [t for t in TOOL_DEFINITIONS if t["name"] in _EXECUTE_TOOL_NAMES]

    # PLAN_REVIEW — no LLM call should happen, but return empty just in case
    return []


def get_tools_for_phase_and_permission(
    phase: AgentPhase,
    permission_tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Intersect phase-gated tools with permission-gated tools.

    If *permission_tools* is ``None`` (permissions not resolved yet),
    phase filtering alone is applied.
    """
    phase_tools = get_tools_for_phase(phase)

    if permission_tools is None:
        return phase_tools

    # Build a set of tool names allowed by permissions
    allowed_names = {t["name"] for t in permission_tools}
    # submit_plan is always allowed (it's a meta-tool, not a repo operation)
    allowed_names.add("submit_plan")

    return [t for t in phase_tools if t["name"] in allowed_names]
