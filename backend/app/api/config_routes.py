"""Configuration routes — per-org and per-repo settings."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.auth import UserInfo, get_current_user
from app.db.dynamo import get_item, put_item, delete_item
from app.db.models import TABLE_CONFIGS

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ConfigBody(BaseModel):
    enabled: bool = True
    default_model: str | None = Field(None, description="Override default LLM model for this scope")
    auto_respond: bool = Field(False, description="Auto-respond to new issues")
    max_cost_per_issue: float | None = Field(None, description="Max cost per issue in USD")
    allowed_tools: list[str] | None = Field(None, description="Allowed tools for the agent")


class ConfigOut(BaseModel):
    config_scope: str
    enabled: bool
    default_model: str | None = None
    auto_respond: bool = False
    max_cost_per_issue: float | None = None
    allowed_tools: list[str] | None = None
    updated_at: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{scope:path}", response_model=ConfigOut)
async def get_config(scope: str):
    """Get configuration for a scope (e.g. ``org:mycompany`` or ``repo:owner/repo``)."""
    item = await get_item(TABLE_CONFIGS, {"config_scope": scope, "SK": "CONFIG"})
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Config for scope '{scope}' not found.",
        )
    return ConfigOut(**item)


@router.put("/{scope:path}", response_model=ConfigOut)
async def upsert_config(
    scope: str,
    body: ConfigBody,
    current_user: UserInfo = Depends(get_current_user),
):
    """Create or update configuration for a scope."""
    now = datetime.now(timezone.utc).isoformat()
    config = {
        "config_scope": scope,
        "SK": "CONFIG",
        "enabled": body.enabled,
        "default_model": body.default_model,
        "auto_respond": body.auto_respond,
        "max_cost_per_issue": body.max_cost_per_issue,
        "allowed_tools": body.allowed_tools,
        "updated_at": now,
        "created_at": now,
    }
    config = {k: v for k, v in config.items() if v is not None}
    await put_item(TABLE_CONFIGS, config)
    return ConfigOut(**config)


@router.delete("/{scope:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    scope: str,
    current_user: UserInfo = Depends(get_current_user),
):
    """Remove configuration for a scope."""
    item = await get_item(TABLE_CONFIGS, {"config_scope": scope, "SK": "CONFIG"})
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Config for scope '{scope}' not found.",
        )
    await delete_item(TABLE_CONFIGS, {"config_scope": scope, "SK": "CONFIG"})
