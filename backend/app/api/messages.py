"""Message history routes — paginated retrieval of session messages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.db.dynamo import get_item, query_by_partition
from app.db.models import TABLE_MESSAGES, TABLE_SESSIONS

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class MessageOut(BaseModel):
    message_id: str
    session_id: str
    role: str
    content: str
    model: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: str


class MessageListResponse(BaseModel):
    messages: list[MessageOut]
    count: int
    last_key: str | None = Field(
        None, description="Pagination cursor — pass to next request as last_key"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{session_id}/messages", response_model=MessageListResponse)
async def list_messages(
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    last_key: str | None = Query(None, description="Pagination cursor from previous response"),
):
    """Return paginated message history for a session (oldest first)."""
    session = await get_item(TABLE_SESSIONS, {"session_id": session_id, "SK": "META"})
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found.",
        )

    items, new_last_key = await query_by_partition(
        table_name=TABLE_MESSAGES,
        key_name="session_id",
        key_value=session_id,
        limit=limit,
        last_key=last_key,
        scan_forward=True,
    )
    messages = [MessageOut(**item) for item in items]
    return MessageListResponse(
        messages=messages,
        count=len(messages),
        last_key=new_last_key,
    )
