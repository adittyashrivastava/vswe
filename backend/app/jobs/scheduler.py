"""ECS Fargate job scheduler for VSWE compute tasks.

Submits jobs as on-demand ECS Fargate tasks. Each job runs in a container
that auto-installs dependencies and executes the user's script.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .profiler import JobProfile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# ---------------------------------------------------------------------------

_AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
_ECS_CLUSTER = os.getenv("VSWE_ECS_CLUSTER", "vswe-cluster")
_JOB_TASK_DEF = os.getenv("VSWE_JOB_TASK_DEF", "vswe-job")
_CONTAINER_NAME = "job"
_SUBNETS = os.getenv("VSWE_PRIVATE_SUBNETS", "")  # comma-separated
_SECURITY_GROUPS = os.getenv("VSWE_SECURITY_GROUPS", "")  # comma-separated


class JobScheduler:
    """Submits compute jobs as ECS Fargate tasks."""

    def __init__(self, region: str | None = None) -> None:
        self.ecs_client = boto3.client("ecs", region_name=region or _AWS_REGION)

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    async def submit_job(
        self,
        job_id: str,
        profile: JobProfile,
        script_path: str,
        workspace_path: str,
    ) -> str:
        """Submit a job as an ECS Fargate task and return the task ARN.

        The task runs the vswe_checkpoint.runner entrypoint which reads
        env vars, auto-installs dependencies, and executes the script.
        """
        environment = self._build_environment(
            job_id=job_id,
            profile=profile,
            script_path=script_path,
            workspace_path=workspace_path,
        )

        # Fargate CPU/memory override from profile
        fargate_size = profile.recommended_instance

        overrides: dict[str, Any] = {
            "containerOverrides": [
                {
                    "name": _CONTAINER_NAME,
                    "environment": environment,
                },
            ],
            "cpu": str(fargate_size.vcpus),
            "memory": str(int(fargate_size.memory_gb * 1024)),
        }

        # Network configuration
        network_config: dict[str, Any] = {
            "awsvpcConfiguration": {
                "assignPublicIp": "DISABLED",
            }
        }
        if _SUBNETS:
            network_config["awsvpcConfiguration"]["subnets"] = [
                s.strip() for s in _SUBNETS.split(",") if s.strip()
            ]
        if _SECURITY_GROUPS:
            network_config["awsvpcConfiguration"]["securityGroups"] = [
                s.strip() for s in _SECURITY_GROUPS.split(",") if s.strip()
            ]

        try:
            response = self.ecs_client.run_task(
                cluster=_ECS_CLUSTER,
                taskDefinition=_JOB_TASK_DEF,
                launchType="FARGATE",
                overrides=overrides,
                networkConfiguration=network_config,
                count=1,
                startedBy=f"vswe-{job_id}",
                tags=[
                    {"key": "vswe:job_id", "value": job_id},
                    {"key": "vswe:framework", "value": profile.framework},
                ],
            )

            tasks = response.get("tasks", [])
            if not tasks:
                failures = response.get("failures", [])
                reason = failures[0].get("reason", "Unknown") if failures else "No task started"
                raise RuntimeError(f"ECS RunTask returned no tasks: {reason}")

            task_arn: str = tasks[0]["taskArn"]
            logger.info(
                "Started ECS task %s (cluster=%s, taskDef=%s)",
                task_arn, _ECS_CLUSTER, _JOB_TASK_DEF,
            )
            return task_arn

        except ClientError as exc:
            logger.error("Failed to start ECS task for %s: %s", job_id, exc)
            raise

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_job_status(self, task_arn: str) -> dict[str, Any]:
        """Return current status of an ECS task.

        Returns dict with keys:
        - status: PROVISIONING | PENDING | ACTIVATING | RUNNING |
                  DEACTIVATING | STOPPING | DEPROVISIONING | STOPPED
        - status_reason: human-readable reason (if available)
        - started_at: ISO timestamp (if started)
        - stopped_at: ISO timestamp (if finished)
        - exit_code: container exit code (if finished)
        - log_stream_name: CloudWatch log stream
        """
        try:
            response = self.ecs_client.describe_tasks(
                cluster=_ECS_CLUSTER,
                tasks=[task_arn],
            )
        except ClientError as exc:
            logger.error("describe_tasks failed for %s: %s", task_arn, exc)
            raise

        tasks = response.get("tasks", [])
        if not tasks:
            return {"status": "UNKNOWN", "status_reason": "Task not found"}

        task = tasks[0]
        container = {}
        for c in task.get("containers", []):
            if c.get("name") == _CONTAINER_NAME:
                container = c
                break

        started_at = task.get("startedAt")
        stopped_at = task.get("stoppedAt")

        return {
            "status": task.get("lastStatus", "UNKNOWN"),
            "status_reason": task.get("stoppedReason", ""),
            "started_at": started_at.isoformat() if started_at else None,
            "stopped_at": stopped_at.isoformat() if stopped_at else None,
            "exit_code": container.get("exitCode"),
            "log_stream_name": None,  # Available via CloudWatch log group
        }

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel_job(self, task_arn: str, reason: str = "User requested") -> None:
        """Stop a running ECS task."""
        try:
            self.ecs_client.stop_task(
                cluster=_ECS_CLUSTER,
                task=task_arn,
                reason=reason,
            )
            logger.info("Stopped ECS task %s: %s", task_arn, reason)
        except ClientError as exc:
            logger.error("Failed to stop task %s: %s", task_arn, exc)
            raise

    # ------------------------------------------------------------------
    # List active
    # ------------------------------------------------------------------

    async def list_active_jobs(self) -> list[dict[str, Any]]:
        """List all running VSWE job tasks."""
        active: list[dict[str, Any]] = []
        try:
            response = self.ecs_client.list_tasks(
                cluster=_ECS_CLUSTER,
                family=_JOB_TASK_DEF,
                desiredStatus="RUNNING",
                maxResults=100,
            )
            task_arns = response.get("taskArns", [])
            if task_arns:
                desc = self.ecs_client.describe_tasks(
                    cluster=_ECS_CLUSTER,
                    tasks=task_arns,
                )
                for task in desc.get("tasks", []):
                    started_at = task.get("startedAt")
                    active.append({
                        "task_arn": task["taskArn"],
                        "status": task.get("lastStatus", ""),
                        "started_at": started_at.isoformat() if started_at else None,
                        "started_by": task.get("startedBy", ""),
                    })
        except ClientError as exc:
            logger.warning("list_tasks failed: %s", exc)

        return active

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_environment(
        *,
        job_id: str,
        profile: JobProfile,
        script_path: str,
        workspace_path: str,
    ) -> list[dict[str, str]]:
        """Build the container environment variable list."""
        return [
            {"name": "VSWE_JOB_ID", "value": job_id},
            {"name": "VSWE_SCRIPT_PATH", "value": script_path},
            {"name": "VSWE_WORKSPACE_PATH", "value": workspace_path},
            {"name": "VSWE_FRAMEWORK", "value": profile.framework},
            {"name": "VSWE_PRECISION", "value": profile.precision},
            {"name": "VSWE_BATCH_SIZE", "value": str(profile.batch_size)},
            {"name": "VSWE_EPOCHS", "value": str(profile.epochs)},
            {"name": "VSWE_CHECKPOINT_INTERVAL", "value": str(profile.checkpoint_interval_epochs)},
            {"name": "VSWE_EFS_MOUNT", "value": "/efs"},
            {"name": "VSWE_CHECKPOINT_DIR", "value": f"/efs/checkpoints/{job_id}"},
            {"name": "VSWE_INSTANCE_TYPE", "value": f"fargate-{profile.recommended_instance.vcpus}vcpu"},
        ]
