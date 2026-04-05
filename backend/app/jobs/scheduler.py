"""AWS Batch job scheduler for ML training workloads.

Submits profiled training jobs to AWS Batch using Spot instances, manages
job definitions, and provides status / cancellation helpers.
"""

from __future__ import annotations

import json
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
_JOB_QUEUE_GPU = os.getenv("VSWE_BATCH_GPU_QUEUE", "vswe-gpu-spot")
_JOB_QUEUE_CPU = os.getenv("VSWE_BATCH_CPU_QUEUE", "vswe-cpu-spot")
_EFS_VOLUME_ID = os.getenv("VSWE_EFS_VOLUME_ID", "")
_EFS_MOUNT_POINT = "/mnt/efs"
_CONTAINER_IMAGE = os.getenv("VSWE_TRAINING_IMAGE", "vswe-training:latest")
_JOB_ROLE_ARN = os.getenv("VSWE_BATCH_JOB_ROLE_ARN", "")
_EXECUTION_ROLE_ARN = os.getenv("VSWE_BATCH_EXECUTION_ROLE_ARN", "")


class JobScheduler:
    """Thin wrapper around the AWS Batch API tailored for VSWE training jobs."""

    def __init__(self, region: str | None = None) -> None:
        self.batch_client = boto3.client("batch", region_name=region or _AWS_REGION)

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
        """Submit a training job to AWS Batch and return the Batch job ID.

        The method:
        1. Registers (or re-uses) a job definition matching the instance type.
        2. Selects the appropriate queue (GPU-spot or CPU-spot).
        3. Passes environment variables so the training container can locate
           the script, workspace, and configure the checkpoint manager.
        """
        job_def_name = self._job_definition_name(profile)
        job_def_arn = self._ensure_job_definition(job_def_name, profile)

        queue = _JOB_QUEUE_GPU if profile.needs_gpu else _JOB_QUEUE_CPU

        environment = self._build_environment(
            job_id=job_id,
            profile=profile,
            script_path=script_path,
            workspace_path=workspace_path,
        )

        resource_requirements = self._build_resource_requirements(profile)

        try:
            response = self.batch_client.submit_job(
                jobName=f"vswe-{job_id}",
                jobQueue=queue,
                jobDefinition=job_def_arn,
                containerOverrides={
                    "environment": environment,
                    "resourceRequirements": resource_requirements,
                },
                retryStrategy={
                    # Spot interruptions trigger a retry (SPOT_CAPACITY).
                    "attempts": 3,
                    "evaluateOnExit": [
                        {
                            "onStatusReason": "Host EC2*",
                            "action": "RETRY",
                        },
                        {
                            "onReason": "SPOT_CAPACITY",
                            "action": "RETRY",
                        },
                        {
                            "onExitCode": "0",
                            "action": "EXIT",
                        },
                    ],
                },
                timeout={
                    # Hard cap: 24 hours.  Individual jobs should finish faster;
                    # this prevents runaway costs from buggy scripts.
                    "attemptDurationSeconds": 86400,
                },
                tags={
                    "vswe:job_id": job_id,
                    "vswe:instance_type": profile.recommended_instance.instance_type,
                    "vswe:framework": profile.framework,
                },
            )
            batch_job_id: str = response["jobId"]
            logger.info(
                "Submitted Batch job %s (queue=%s, definition=%s)",
                batch_job_id,
                queue,
                job_def_arn,
            )
            return batch_job_id

        except ClientError as exc:
            logger.error("Failed to submit Batch job for %s: %s", job_id, exc)
            raise

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_job_status(self, batch_job_id: str) -> dict[str, Any]:
        """Return current status of a Batch job.

        The returned dict contains at minimum:
        - ``status``: one of SUBMITTED | PENDING | RUNNABLE | STARTING |
          RUNNING | SUCCEEDED | FAILED
        - ``status_reason``: human-readable reason (if available)
        - ``started_at``: epoch ms (if started)
        - ``stopped_at``: epoch ms (if finished)
        - ``log_stream_name``: CloudWatch log stream (if available)
        """
        try:
            response = self.batch_client.describe_jobs(jobs=[batch_job_id])
        except ClientError as exc:
            logger.error("describe_jobs failed for %s: %s", batch_job_id, exc)
            raise

        jobs = response.get("jobs", [])
        if not jobs:
            return {"status": "UNKNOWN", "status_reason": "Job not found"}

        job = jobs[0]
        container = job.get("container", {})

        return {
            "status": job.get("status", "UNKNOWN"),
            "status_reason": job.get("statusReason", ""),
            "started_at": job.get("startedAt"),
            "stopped_at": job.get("stoppedAt"),
            "log_stream_name": container.get("logStreamName"),
            "exit_code": container.get("exitCode"),
            "attempts": len(job.get("attempts", [])),
        }

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel_job(self, batch_job_id: str, reason: str = "User requested") -> None:
        """Cancel a Batch job that is in SUBMITTED, PENDING, or RUNNABLE state,
        or terminate it if already RUNNING.
        """
        try:
            # cancel_job works for jobs not yet RUNNING.
            self.batch_client.cancel_job(jobId=batch_job_id, reason=reason)
            logger.info("Cancelled Batch job %s: %s", batch_job_id, reason)
        except ClientError:
            # If the job is already RUNNING, we need terminate_job instead.
            try:
                self.batch_client.terminate_job(jobId=batch_job_id, reason=reason)
                logger.info("Terminated Batch job %s: %s", batch_job_id, reason)
            except ClientError as exc:
                logger.error("Failed to cancel/terminate %s: %s", batch_job_id, exc)
                raise

    # ------------------------------------------------------------------
    # List active jobs
    # ------------------------------------------------------------------

    async def list_active_jobs(self) -> list[dict[str, Any]]:
        """List all active (non-terminal) VSWE jobs across both queues."""
        active: list[dict[str, Any]] = []

        for queue in (_JOB_QUEUE_GPU, _JOB_QUEUE_CPU):
            for status in ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"):
                try:
                    response = self.batch_client.list_jobs(
                        jobQueue=queue,
                        jobStatus=status,
                        maxResults=100,
                    )
                    for summary in response.get("jobSummaryList", []):
                        active.append({
                            "batch_job_id": summary["jobId"],
                            "job_name": summary.get("jobName", ""),
                            "status": summary.get("status", ""),
                            "started_at": summary.get("startedAt"),
                            "queue": queue,
                        })
                except ClientError as exc:
                    logger.warning("list_jobs failed for queue=%s status=%s: %s", queue, status, exc)

        return active

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _job_definition_name(profile: JobProfile) -> str:
        """Deterministic job-definition name based on instance category + GPU model."""
        inst = profile.recommended_instance
        if inst.gpu_model:
            suffix = f"{inst.gpu_model.lower()}-{inst.gpu_count}gpu"
        else:
            suffix = f"cpu-{inst.vcpus}vcpu"
        return f"vswe-training-{suffix}"

    def _ensure_job_definition(self, name: str, profile: JobProfile) -> str:
        """Register a new job definition revision (or return the latest).

        AWS Batch job definitions are versioned; registering the same
        definition is cheap and idempotent in practice.
        """
        inst = profile.recommended_instance

        volumes: list[dict[str, Any]] = []
        mount_points: list[dict[str, Any]] = []

        if _EFS_VOLUME_ID:
            volumes.append({
                "name": "efs",
                "efsVolumeConfiguration": {
                    "fileSystemId": _EFS_VOLUME_ID,
                    "rootDirectory": "/",
                    "transitEncryption": "ENABLED",
                },
            })
            mount_points.append({
                "sourceVolume": "efs",
                "containerPath": _EFS_MOUNT_POINT,
                "readOnly": False,
            })

        resource_requirements = self._build_resource_requirements(profile)

        container_properties: dict[str, Any] = {
            "image": _CONTAINER_IMAGE,
            "resourceRequirements": resource_requirements,
            "mountPoints": mount_points,
            "volumes": volumes,
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": "/vswe/training",
                    "awslogs-stream-prefix": name,
                },
            },
            "command": ["python", "-m", "vswe_checkpoint.runner"],
        }

        if _JOB_ROLE_ARN:
            container_properties["jobRoleArn"] = _JOB_ROLE_ARN
        if _EXECUTION_ROLE_ARN:
            container_properties["executionRoleArn"] = _EXECUTION_ROLE_ARN

        try:
            resp = self.batch_client.register_job_definition(
                jobDefinitionName=name,
                type="container",
                containerProperties=container_properties,
                platformCapabilities=["EC2"],
                retryStrategy={"attempts": 3},
            )
            arn: str = resp["jobDefinitionArn"]
            logger.info("Registered job definition %s -> %s", name, arn)
            return arn
        except ClientError as exc:
            logger.error("register_job_definition failed for %s: %s", name, exc)
            raise

    @staticmethod
    def _build_resource_requirements(profile: JobProfile) -> list[dict[str, str]]:
        """Build the ``resourceRequirements`` list for Batch."""
        inst = profile.recommended_instance
        reqs: list[dict[str, str]] = [
            {"type": "VCPU", "value": str(inst.vcpus)},
            {"type": "MEMORY", "value": str(int(inst.memory_gb * 1024))},  # MiB
        ]
        if inst.gpu_count > 0:
            reqs.append({"type": "GPU", "value": str(inst.gpu_count)})
        return reqs

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
            {"name": "VSWE_EFS_MOUNT", "value": _EFS_MOUNT_POINT},
            {"name": "VSWE_CHECKPOINT_DIR", "value": f"{_EFS_MOUNT_POINT}/checkpoints/{job_id}"},
            {"name": "VSWE_INSTANCE_TYPE", "value": profile.recommended_instance.instance_type},
        ]
