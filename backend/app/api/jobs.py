"""Job management routes — list, inspect, and control agent jobs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.db.dynamo import get_item, query_by_gsi, query_by_partition, scan_table
from app.db.models import TABLE_JOBS, TABLE_CHECKPOINTS

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class JobOut(BaseModel):
    job_id: str
    session_id: str
    status: str
    instance_type: str | None = None
    spot_price: float | None = None
    script_path: str | None = None
    profile: dict[str, Any] | None = None
    batch_job_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    total_cost_usd: float = 0.0


class JobListResponse(BaseModel):
    jobs: list[JobOut]
    count: int


class CheckpointOut(BaseModel):
    checkpoint_id: str
    job_id: str
    epoch: int
    storage_tier: str
    efs_path: str | None = None
    s3_uri: str | None = None
    size_bytes: int = 0
    validation_metric: float | None = None
    is_best: bool = False
    created_at: str


class CheckpointListResponse(BaseModel):
    checkpoints: list[CheckpointOut]
    count: int


class JobStopResponse(BaseModel):
    job_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/", response_model=JobListResponse)
async def list_jobs(
    session_id: str | None = Query(None, description="Filter by session ID"),
    limit: int = Query(50, ge=1, le=200),
):
    """List jobs, optionally filtered by session_id."""
    if session_id:
        items = await query_by_gsi(
            table_name=TABLE_JOBS,
            index_name="session_id-started_at-index",
            key_name="session_id",
            key_value=session_id,
            limit=limit,
        )
    else:
        items = await scan_table(table_name=TABLE_JOBS, limit=limit)
    jobs = [JobOut(**item) for item in items]
    return JobListResponse(jobs=jobs, count=len(jobs))


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str):
    """Get details for a single job."""
    item = await get_item(TABLE_JOBS, {"job_id": job_id, "SK": "META"})
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found.",
        )
    return JobOut(**item)


@router.get("/{job_id}/checkpoints", response_model=CheckpointListResponse)
async def list_checkpoints(
    job_id: str,
    limit: int = Query(50, ge=1, le=200),
):
    """List checkpoints for a given job."""
    job = await get_item(TABLE_JOBS, {"job_id": job_id, "SK": "META"})
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found.",
        )

    items, _ = await query_by_partition(
        table_name=TABLE_CHECKPOINTS,
        key_name="job_id",
        key_value=job_id,
        limit=limit,
        scan_forward=True,
    )
    checkpoints = [CheckpointOut(**item) for item in items]
    return CheckpointListResponse(checkpoints=checkpoints, count=len(checkpoints))


@router.post("/{job_id}/stop", response_model=JobStopResponse)
async def stop_job(job_id: str):
    """Stop a running job (stub — will send cancellation to Batch)."""
    item = await get_item(TABLE_JOBS, {"job_id": job_id, "SK": "META"})
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found.",
        )

    current_status = item.get("status", "unknown")
    if current_status not in ("profiling", "queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} is '{current_status}' and cannot be stopped.",
        )

    return JobStopResponse(
        job_id=job_id,
        status="stopped",
        message=f"Stop signal sent for job {job_id}.",
    )
