"""Job management routes — list, inspect, and control agent jobs."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.db.dynamo import get_item, query_by_gsi, query_by_partition, scan_table, update_item
from app.db.models import TABLE_JOBS, TABLE_CHECKPOINTS

logger = logging.getLogger(__name__)

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

async def _sync_job_status(item: dict[str, Any]) -> dict[str, Any]:
    """Check ECS for the current status of a job and update DynamoDB if changed."""
    task_arn = item.get("batch_job_id")
    current_status = item.get("status", "")

    # Only sync jobs that have a task ARN and aren't already terminal
    if not task_arn or current_status in ("completed", "failed"):
        return item

    try:
        from app.jobs.scheduler import JobScheduler
        scheduler = JobScheduler()
        ecs_status = await scheduler.get_job_status(task_arn)

        ecs_state = ecs_status.get("status", "")
        new_status = current_status

        if ecs_state == "STOPPED":
            exit_code = ecs_status.get("exit_code")
            new_status = "completed" if exit_code == 0 else "failed"
        elif ecs_state in ("RUNNING", "ACTIVATING"):
            new_status = "running"
        elif ecs_state in ("PENDING", "PROVISIONING"):
            new_status = "queued"

        if new_status != current_status:
            await update_item(
                TABLE_JOBS,
                key={"job_id": item["job_id"], "SK": "META"},
                update_expression="SET #st = :s",
                expression_attribute_names={"#st": "status"},
                expression_attribute_values={":s": new_status},
            )
            item["status"] = new_status

    except Exception:
        logger.warning("Failed to sync job status for %s", item.get("job_id"), exc_info=True)

    return item


@router.get("/", response_model=JobListResponse)
async def list_jobs(
    session_id: str | None = Query(None, description="Filter by session ID"),
    limit: int = Query(50, ge=1, le=200),
):
    """List jobs, optionally filtered by session_id. Syncs status with ECS."""
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

    # Sync non-terminal jobs with ECS
    for item in items:
        await _sync_job_status(item)

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


class JobLogsResponse(BaseModel):
    job_id: str
    logs: list[str]


@router.get("/{job_id}/logs", response_model=JobLogsResponse)
async def get_job_logs(
    job_id: str,
    limit: int = Query(50, ge=1, le=200),
):
    """Fetch CloudWatch logs for a job's ECS task."""
    item = await get_item(TABLE_JOBS, {"job_id": job_id, "SK": "META"})
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found.",
        )

    task_arn = item.get("batch_job_id")
    if not task_arn:
        return JobLogsResponse(job_id=job_id, logs=["No task ARN — job was never submitted."])

    # Extract task ID from ARN: arn:aws:ecs:...:task/cluster-name/task-id
    task_id = task_arn.rsplit("/", 1)[-1]
    log_stream = f"vswe-job/job/{task_id}"

    try:
        import boto3
        logs_client = boto3.client("logs")

        # Find the right log group (name varies per deployment)
        log_groups = logs_client.describe_log_groups()["logGroups"]
        job_log_group = None
        for lg in log_groups:
            if "JobTaskDef" in lg["logGroupName"]:
                # Check if this group has our stream
                try:
                    logs_client.describe_log_streams(
                        logGroupName=lg["logGroupName"],
                        logStreamNamePrefix=log_stream,
                        limit=1,
                    )
                    streams = logs_client.describe_log_streams(
                        logGroupName=lg["logGroupName"],
                        logStreamNamePrefix=log_stream,
                        limit=1,
                    ).get("logStreams", [])
                    if streams:
                        job_log_group = lg["logGroupName"]
                        break
                except Exception:
                    continue

        if not job_log_group:
            return JobLogsResponse(job_id=job_id, logs=["Log stream not found."])

        response = logs_client.get_log_events(
            logGroupName=job_log_group,
            logStreamName=log_stream,
            limit=limit,
            startFromHead=True,
        )
        lines = [event["message"] for event in response.get("events", [])]
        return JobLogsResponse(job_id=job_id, logs=lines if lines else ["No logs available."])

    except Exception as exc:
        logger.warning("Failed to fetch logs for job %s: %s", job_id, exc)
        return JobLogsResponse(job_id=job_id, logs=[f"Failed to fetch logs: {exc}"])


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
