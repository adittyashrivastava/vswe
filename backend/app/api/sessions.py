"""Session management routes — CRUD for chat sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.auth import UserInfo, get_current_user
from app.config import settings
from app.db.dynamo import get_item, put_item, delete_item, query_by_gsi
from app.db.models import TABLE_SESSIONS

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SessionCreate(BaseModel):
    repo_url: str | None = Field(None, description="Optional GitHub repo URL")
    model: str | None = Field(None, description="Override default LLM model")
    title: str | None = Field(None, description="Human-readable session title")


class SessionOut(BaseModel):
    session_id: str
    user_id: str
    repo_url: str | None = None
    model: str
    title: str | None = None
    state: str = "active"
    created_at: str
    updated_at: str


class SessionListResponse(BaseModel):
    sessions: list[SessionOut]
    count: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=SessionListResponse)
async def list_sessions(
    user_id: str = Query(..., description="Filter sessions by user ID"),
    limit: int = Query(50, ge=1, le=200),
):
    """List all sessions for a given user."""
    items = await query_by_gsi(
        table_name=TABLE_SESSIONS,
        index_name="user_id-created_at-index",
        key_name="user_id",
        key_value=user_id,
        limit=limit,
    )
    sessions = [SessionOut(**item) for item in items]
    return SessionListResponse(sessions=sessions, count=len(sessions))


@router.post("/", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    current_user: UserInfo = Depends(get_current_user),
):
    """Create a new chat session."""
    now = datetime.now(timezone.utc).isoformat()
    session_id = str(uuid.uuid4())
    session = {
        "session_id": session_id,
        "SK": "META",
        "user_id": current_user.user_id,
        "type": "chat",
        "repo_url": body.repo_url,
        "model": body.model or settings.default_model,
        "title": body.title or "New Session",
        "state": "active",
        "workspace_path": f"{settings.workspace_root}/{session_id}",
        "created_at": now,
        "updated_at": now,
        "total_cost_usd": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }
    # Strip None values before writing
    session = {k: v for k, v in session.items() if v is not None}
    await put_item(TABLE_SESSIONS, session)
    return SessionOut(**session)


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: str):
    """Get details for a single session."""
    item = await get_item(TABLE_SESSIONS, {"session_id": session_id, "SK": "META"})
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    return SessionOut(**item)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str):
    """Delete (end) a session."""
    item = await get_item(TABLE_SESSIONS, {"session_id": session_id, "SK": "META"})
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )
    await delete_item(TABLE_SESSIONS, {"session_id": session_id, "SK": "META"})
