"""Checkpoint manager for ML training jobs on AWS Spot instances.

Handles saving/loading model checkpoints to EFS with automatic tiering to S3,
cost-aware checkpoint scheduling, and DynamoDB metadata tracking.
"""

from __future__ import annotations

import io
import json
import logging
import os
import pickle
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from vswe_checkpoint.spot_monitor import SpotTerminationMonitor

logger = logging.getLogger(__name__)

# Try importing torch; fall back to pickle-only mode if unavailable.
try:
    import torch

    _HAS_TORCH = True
except ImportError:
    torch = None  # type: ignore[assignment]
    _HAS_TORCH = False


class CheckpointManager:
    """Manages ML checkpoint lifecycle: save, load, tier, and prune.

    Thread-safe. Multiple training processes may call ``save`` concurrently.

    EFS layout::

        /efs/checkpoints/{job_id}/epoch_0001.pt
        /efs/checkpoints/{job_id}/epoch_0002.pt
        /efs/checkpoints/{job_id}/best.pt  -> symlink to best epoch file

    S3 layout::

        s3://{bucket}/checkpoints/{job_id}/epoch_0001.pt
    """

    # -------------------------------------------------------------------
    # Configuration defaults
    # -------------------------------------------------------------------
    _DEFAULT_INTERVAL_EPOCHS: int = 5
    _EFS_KEEP_COUNT: int = 2  # latest N checkpoints kept on EFS
    _S3_PRUNE_DAYS: int = 7  # non-best S3 checkpoints older than this are pruned
    _COST_SKIP_RATIO: float = 0.5  # skip checkpoint if recompute cost < ckpt cost * ratio

    def __init__(
        self,
        job_id: str,
        efs_path: str = "/efs/checkpoints",
        s3_bucket: str = "vswe-artifacts",
        dynamodb_table: str = "vswe-checkpoints",
        interval_epochs: int | None = None,
        instance_cost_per_hour: float = 0.50,
    ) -> None:
        """Initialize checkpoint manager.

        Creates checkpoint directory on EFS, initialises DynamoDB client,
        starts spot termination monitor thread.

        Args:
            job_id: Unique identifier for this training job.
            efs_path: Root EFS mount for checkpoints.
            s3_bucket: S3 bucket for long-term storage.
            dynamodb_table: DynamoDB table for checkpoint metadata.
            interval_epochs: Checkpoint every N epochs (default 5).
            instance_cost_per_hour: $/hr for this instance type (used in cost-aware logic).
        """
        self.job_id = job_id
        self.efs_path = efs_path
        self.s3_bucket = s3_bucket
        self.dynamodb_table = dynamodb_table
        self.interval_epochs = interval_epochs or self._DEFAULT_INTERVAL_EPOCHS
        self._instance_cost_per_s = instance_cost_per_hour / 3600.0

        # Checkpoint directory on EFS
        self._ckpt_dir = Path(efs_path) / job_id
        self._ckpt_dir.mkdir(parents=True, exist_ok=True)

        # Thread safety
        self._lock = threading.Lock()
        self._closed = False

        # Tracking state
        self._best_metric: float | None = None
        self._best_epoch: int | None = None
        self._latest_epoch: int = 0
        self._last_checkpoint_epoch: int = 0
        self._epoch_times: list[float] = []  # seconds per epoch, for cost estimation

        # AWS clients (created lazily to avoid import-time side effects in tests)
        self._dynamodb: Any = None
        self._s3: Any = None

        # Background tiering thread
        self._tier_thread: threading.Thread | None = None
        self._tier_event = threading.Event()

        # Spot termination monitor
        self._spot_monitor = SpotTerminationMonitor(on_termination=self._on_spot_termination)

        # Recover state from existing checkpoints on EFS
        self._recover_state()

        logger.info("CheckpointManager initialised for job %s (dir=%s)", job_id, self._ckpt_dir)

    # -------------------------------------------------------------------
    # Lazy AWS client accessors
    # -------------------------------------------------------------------
    def _get_dynamodb(self):
        if self._dynamodb is None:
            self._dynamodb = boto3.resource("dynamodb").Table(self.dynamodb_table)
        return self._dynamodb

    def _get_s3(self):
        if self._s3 is None:
            self._s3 = boto3.client("s3")
        return self._s3

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def should_checkpoint(
        self,
        epoch: int,
        val_metric: float | None = None,
        epoch_time_s: float | None = None,
    ) -> bool:
        """Decide whether to checkpoint at this epoch.

        Logic:
        1. Always checkpoint if val_metric is a new best.
        2. Always checkpoint on the regular interval.
        3. Cost-aware: compare checkpoint_cost vs recompute_cost.
           If recompute_cost < checkpoint_cost * 0.5, skip.
        4. Always checkpoint if spot termination is pending.

        Args:
            epoch: Current epoch number.
            val_metric: Validation metric (higher is better). ``None`` to skip best-check.
            epoch_time_s: How long this epoch took in seconds (for cost estimation).

        Returns:
            True if a checkpoint should be saved.
        """
        if epoch_time_s is not None:
            self._epoch_times.append(epoch_time_s)

        # Spot termination — always checkpoint immediately
        if self._spot_monitor.is_termination_pending():
            logger.warning("Spot termination pending — forcing checkpoint at epoch %d", epoch)
            return True

        # New best metric
        if val_metric is not None:
            if self._best_metric is None or val_metric > self._best_metric:
                return True

        # Regular interval
        if epoch % self.interval_epochs == 0:
            return True

        # Cost-aware skip logic
        if self._epoch_times:
            avg_epoch_time = sum(self._epoch_times) / len(self._epoch_times)
            epochs_since = epoch - self._last_checkpoint_epoch
            recompute_cost = epochs_since * avg_epoch_time * self._instance_cost_per_s
            estimated_save_time = self._estimate_save_time_s()
            checkpoint_cost = estimated_save_time * self._instance_cost_per_s
            if checkpoint_cost > 0 and recompute_cost < checkpoint_cost * self._COST_SKIP_RATIO:
                logger.debug(
                    "Cost-aware skip: recompute=$%.4f < checkpoint=$%.4f * %.1f",
                    recompute_cost,
                    checkpoint_cost,
                    self._COST_SKIP_RATIO,
                )
                return False

        return False

    def save(
        self,
        epoch: int,
        model: Any,
        optimizer: Any,
        metrics: dict | None = None,
        is_best: bool = False,
    ) -> str:
        """Save a checkpoint to EFS and record metadata in DynamoDB.

        Args:
            epoch: Current epoch number.
            model: Model object (must have ``state_dict()`` for PyTorch, or be picklable).
            optimizer: Optimizer object (must have ``state_dict()`` for PyTorch, or be picklable).
            metrics: Optional dict of metrics to store alongside the checkpoint.
            is_best: Whether this is the best checkpoint so far.

        Returns:
            Path to the saved checkpoint file on EFS.
        """
        if self._closed:
            raise RuntimeError("CheckpointManager is closed")

        filename = f"epoch_{epoch:04d}.pt"
        filepath = self._ckpt_dir / filename
        metrics = metrics or {}

        payload = {
            "epoch": epoch,
            "model_state_dict": self._extract_state_dict(model),
            "optimizer_state_dict": self._extract_state_dict(optimizer),
            "metrics": metrics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": self.job_id,
        }

        with self._lock:
            # Save to EFS
            start = time.monotonic()
            self._save_payload(filepath, payload)
            save_time = time.monotonic() - start

            size_bytes = filepath.stat().st_size

            # Update internal state
            self._latest_epoch = epoch
            self._last_checkpoint_epoch = epoch

            if is_best:
                self._best_metric = metrics.get("val_metric", self._best_metric)
                self._best_epoch = epoch
                # Create/update symlink for best checkpoint
                best_link = self._ckpt_dir / "best.pt"
                if best_link.is_symlink() or best_link.exists():
                    best_link.unlink()
                best_link.symlink_to(filepath)

            logger.info(
                "Saved checkpoint epoch=%d size=%.1fMB time=%.1fs is_best=%s path=%s",
                epoch,
                size_bytes / (1024 * 1024),
                save_time,
                is_best,
                filepath,
            )

        # Record to DynamoDB (outside lock — non-critical)
        try:
            self._record_checkpoint(epoch, str(filepath), is_best, size_bytes, metrics)
        except Exception:
            logger.exception("Failed to record checkpoint metadata in DynamoDB")

        # Trigger async storage tiering
        self._trigger_tiering()

        return str(filepath)

    def load_latest(self) -> dict | None:
        """Load the most recent checkpoint from EFS.

        Returns:
            Dict with ``epoch``, ``model_state_dict``, ``optimizer_state_dict``, ``metrics``,
            or ``None`` if no checkpoint exists.
        """
        checkpoint_files = self._list_checkpoint_files()
        if not checkpoint_files:
            logger.info("No checkpoints found for job %s", self.job_id)
            return None
        latest = checkpoint_files[-1]  # sorted ascending by epoch
        logger.info("Loading latest checkpoint: %s", latest)
        return self._load_payload(latest)

    def load_best(self) -> dict | None:
        """Load the best checkpoint (by validation metric).

        Checks for a ``best.pt`` symlink first, then falls back to DynamoDB lookup.

        Returns:
            Dict with checkpoint data or ``None``.
        """
        best_link = self._ckpt_dir / "best.pt"
        if best_link.is_symlink() and best_link.exists():
            logger.info("Loading best checkpoint from symlink: %s", best_link)
            return self._load_payload(best_link)

        # Fallback: try S3 if local best not available
        if self._best_epoch is not None:
            efs_path = self._ckpt_dir / f"epoch_{self._best_epoch:04d}.pt"
            if efs_path.exists():
                return self._load_payload(efs_path)
            # Try downloading from S3
            try:
                return self._download_from_s3(self._best_epoch)
            except Exception:
                logger.exception("Failed to load best checkpoint from S3")

        logger.info("No best checkpoint found for job %s", self.job_id)
        return None

    @property
    def latest_epoch(self) -> int:
        """Return the epoch of the most recent checkpoint."""
        return self._latest_epoch

    def close(self) -> None:
        """Stop background threads, flush any pending uploads."""
        if self._closed:
            return
        self._closed = True
        self._spot_monitor.stop()
        # Wait for any in-progress tiering
        if self._tier_thread is not None and self._tier_thread.is_alive():
            self._tier_event.set()
            self._tier_thread.join(timeout=30)
        logger.info("CheckpointManager closed for job %s", self.job_id)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -------------------------------------------------------------------
    # Storage tiering
    # -------------------------------------------------------------------

    def _trigger_tiering(self) -> None:
        """Start tiering in a background thread if not already running."""
        if self._tier_thread is not None and self._tier_thread.is_alive():
            return  # already running
        self._tier_thread = threading.Thread(target=self._tier_checkpoints, daemon=True)
        self._tier_thread.start()

    def _tier_checkpoints(self) -> None:
        """Storage tiering:

        - Keep latest N checkpoints on EFS.
        - Move older ones to S3.
        - Always keep the best-metric checkpoint on EFS.
        - Prune non-best S3 checkpoints older than 7 days.
        """
        try:
            checkpoint_files = self._list_checkpoint_files()
            if len(checkpoint_files) <= self._EFS_KEEP_COUNT:
                return

            # Determine which files to keep on EFS
            keep_on_efs = set(checkpoint_files[-self._EFS_KEEP_COUNT:])

            # Always keep best
            if self._best_epoch is not None:
                best_path = self._ckpt_dir / f"epoch_{self._best_epoch:04d}.pt"
                if best_path.exists():
                    keep_on_efs.add(best_path)

            # Move excess to S3, then delete from EFS
            for ckpt_path in checkpoint_files:
                if ckpt_path in keep_on_efs:
                    continue
                try:
                    s3_key = f"checkpoints/{self.job_id}/{ckpt_path.name}"
                    self._get_s3().upload_file(str(ckpt_path), self.s3_bucket, s3_key)
                    ckpt_path.unlink()
                    logger.info("Tiered %s -> s3://%s/%s", ckpt_path.name, self.s3_bucket, s3_key)
                except Exception:
                    logger.exception("Failed to tier checkpoint %s to S3", ckpt_path)

            # Prune old non-best S3 checkpoints
            self._prune_s3_checkpoints()

        except Exception:
            logger.exception("Error during checkpoint tiering")

    def _prune_s3_checkpoints(self) -> None:
        """Remove non-best S3 checkpoints older than the configured retention period."""
        try:
            prefix = f"checkpoints/{self.job_id}/"
            s3 = self._get_s3()
            paginator = s3.get_paginator("list_objects_v2")
            cutoff = datetime.now(timezone.utc).timestamp() - (self._S3_PRUNE_DAYS * 86400)

            for page in paginator.paginate(Bucket=self.s3_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    last_modified = obj["LastModified"].timestamp()

                    # Skip best checkpoint
                    if self._best_epoch is not None:
                        best_name = f"epoch_{self._best_epoch:04d}.pt"
                        if key.endswith(best_name):
                            continue

                    if last_modified < cutoff:
                        s3.delete_object(Bucket=self.s3_bucket, Key=key)
                        logger.info("Pruned old S3 checkpoint: %s", key)

        except Exception:
            logger.exception("Error pruning S3 checkpoints")

    # -------------------------------------------------------------------
    # DynamoDB metadata
    # -------------------------------------------------------------------

    def _record_checkpoint(
        self, epoch: int, path: str, is_best: bool, size_bytes: int, metrics: dict
    ) -> None:
        """Write checkpoint metadata to DynamoDB."""
        item = {
            "job_id": self.job_id,
            "epoch": epoch,
            "path": path,
            "s3_key": f"checkpoints/{self.job_id}/epoch_{epoch:04d}.pt",
            "is_best": is_best,
            "size_bytes": size_bytes,
            "metrics": json.dumps(metrics),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._get_dynamodb().put_item(Item=item)
        except ClientError:
            logger.exception("DynamoDB put_item failed for epoch %d", epoch)

    # -------------------------------------------------------------------
    # Serialization helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _extract_state_dict(obj: Any) -> Any:
        """Extract state_dict if available (PyTorch), otherwise return the object as-is."""
        if hasattr(obj, "state_dict") and callable(obj.state_dict):
            return obj.state_dict()
        return obj

    @staticmethod
    def _save_payload(filepath: Path, payload: dict) -> None:
        """Save payload to disk using torch.save if available, else pickle."""
        # Write to a temp file then rename for atomicity
        tmp_path = filepath.with_suffix(".tmp")
        try:
            if _HAS_TORCH:
                torch.save(payload, str(tmp_path))
            else:
                with open(tmp_path, "wb") as f:
                    pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            tmp_path.rename(filepath)
        except BaseException:
            # Clean up temp file on any failure
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    @staticmethod
    def _load_payload(filepath: Path) -> dict:
        """Load payload from disk using torch.load if available, else pickle."""
        if _HAS_TORCH:
            return torch.load(str(filepath), map_location="cpu", weights_only=False)
        else:
            with open(filepath, "rb") as f:
                return pickle.load(f)  # noqa: S301

    def _download_from_s3(self, epoch: int) -> dict:
        """Download a checkpoint from S3 and load it."""
        s3_key = f"checkpoints/{self.job_id}/epoch_{epoch:04d}.pt"
        local_path = self._ckpt_dir / f"epoch_{epoch:04d}.pt"

        self._get_s3().download_file(self.s3_bucket, s3_key, str(local_path))
        logger.info("Downloaded checkpoint from S3: %s", s3_key)
        return self._load_payload(local_path)

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _list_checkpoint_files(self) -> list[Path]:
        """List checkpoint files on EFS sorted by epoch number ascending."""
        files = sorted(
            self._ckpt_dir.glob("epoch_*.pt"),
            key=lambda p: int(p.stem.split("_")[1]),
        )
        return files

    def _recover_state(self) -> None:
        """Recover internal state from existing checkpoint files on EFS."""
        checkpoint_files = self._list_checkpoint_files()
        if not checkpoint_files:
            return

        latest = checkpoint_files[-1]
        self._latest_epoch = int(latest.stem.split("_")[1])
        self._last_checkpoint_epoch = self._latest_epoch

        # Check for best symlink
        best_link = self._ckpt_dir / "best.pt"
        if best_link.is_symlink() and best_link.exists():
            target = best_link.resolve()
            try:
                self._best_epoch = int(target.stem.split("_")[1])
            except (ValueError, IndexError):
                pass

        logger.info(
            "Recovered state: latest_epoch=%d, best_epoch=%s",
            self._latest_epoch,
            self._best_epoch,
        )

    def _estimate_save_time_s(self) -> float:
        """Estimate how long a checkpoint save will take in seconds.

        Uses the size of the latest checkpoint file and a conservative EFS write speed.
        """
        checkpoint_files = self._list_checkpoint_files()
        if not checkpoint_files:
            return 5.0  # default estimate

        latest_size = checkpoint_files[-1].stat().st_size
        # Conservative EFS write speed: ~100 MB/s
        efs_write_speed = 100 * 1024 * 1024
        return max(latest_size / efs_write_speed, 1.0)

    def _on_spot_termination(self) -> None:
        """Callback invoked when spot termination notice is received."""
        logger.critical(
            "Spot termination notice received for job %s! "
            "Training code should checkpoint ASAP.",
            self.job_id,
        )
