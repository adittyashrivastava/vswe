"""Metrics streaming for ML training jobs.

Buffers training metrics in memory and flushes them to DynamoDB on a
configurable interval, minimising write pressure while keeping the
dashboard reasonably up-to-date.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class MetricsStreamer:
    """Buffer training metrics and flush to DynamoDB periodically.

    Metrics are stored as a JSON list appended to the job's DynamoDB item
    under the ``metrics_log`` attribute. Step-level and epoch-level metrics
    are stored separately to allow efficient queries.

    Thread-safe: ``log`` and ``log_epoch`` may be called from any thread.
    """

    def __init__(
        self,
        job_id: str,
        dynamodb_table: str = "vswe-jobs",
        flush_interval: float = 10.0,
    ) -> None:
        """Initialise the metrics streamer.

        Args:
            job_id: Unique identifier for this training job.
            dynamodb_table: DynamoDB table where job items live.
            flush_interval: Seconds between automatic flushes.
        """
        self.job_id = job_id
        self.dynamodb_table = dynamodb_table
        self.flush_interval = flush_interval

        self._lock = threading.Lock()
        self._step_buffer: list[dict[str, Any]] = []
        self._epoch_buffer: list[dict[str, Any]] = []
        self._closed = False

        # Lazy DynamoDB table reference
        self._table: Any = None

        # Background flush timer
        self._stop_event = threading.Event()
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name=f"metrics-flush-{id(self):x}",
            daemon=True,
        )
        self._flush_thread.start()
        logger.debug("MetricsStreamer started for job %s (interval=%.1fs)", job_id, flush_interval)

    # ------------------------------------------------------------------
    # Lazy AWS client
    # ------------------------------------------------------------------
    def _get_table(self):
        if self._table is None:
            self._table = boto3.resource("dynamodb").Table(self.dynamodb_table)
        return self._table

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, step: int, **metrics: Any) -> None:
        """Log metrics for a training step.

        Example::

            streamer.log(step=100, loss=0.5, lr=0.001, accuracy=0.85)

        Args:
            step: Global training step number.
            **metrics: Arbitrary key-value metric pairs.
        """
        if self._closed:
            raise RuntimeError("MetricsStreamer is closed")

        entry = {
            "step": step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **metrics,
        }
        with self._lock:
            self._step_buffer.append(entry)

    def log_epoch(self, epoch: int, **metrics: Any) -> None:
        """Log epoch-level metrics.

        Example::

            streamer.log_epoch(epoch=5, val_loss=0.3, val_accuracy=0.92)

        Args:
            epoch: Epoch number.
            **metrics: Arbitrary key-value metric pairs.
        """
        if self._closed:
            raise RuntimeError("MetricsStreamer is closed")

        entry = {
            "epoch": epoch,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **metrics,
        }
        with self._lock:
            self._epoch_buffer.append(entry)

    def close(self) -> None:
        """Flush remaining metrics and stop the background timer."""
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=self.flush_interval + 5)
        # Final flush
        self._flush()
        logger.info("MetricsStreamer closed for job %s", self.job_id)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    # Background flush
    # ------------------------------------------------------------------

    def _flush_loop(self) -> None:
        """Periodically flush buffered metrics until stopped."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self.flush_interval)
            if not self._stop_event.is_set() or self._has_buffered_data():
                self._flush()

    def _has_buffered_data(self) -> bool:
        with self._lock:
            return bool(self._step_buffer or self._epoch_buffer)

    def _flush(self) -> None:
        """Write buffered metrics to DynamoDB.

        Uses ``UpdateExpression`` with ``list_append`` to atomically append
        new entries to the existing metrics lists, avoiding read-modify-write races.
        """
        with self._lock:
            steps = self._step_buffer[:]
            epochs = self._epoch_buffer[:]
            self._step_buffer.clear()
            self._epoch_buffer.clear()

        if not steps and not epochs:
            return

        try:
            table = self._get_table()

            # Build update expression dynamically based on what we have
            update_parts: list[str] = []
            attr_values: dict[str, Any] = {}
            attr_names: dict[str, str] = {}

            if steps:
                update_parts.append(
                    "step_metrics = list_append(if_not_exists(step_metrics, :empty_list), :steps)"
                )
                attr_values[":steps"] = self._serialise_for_dynamo(steps)
                attr_values[":empty_list"] = []

            if epochs:
                update_parts.append(
                    "epoch_metrics = list_append(if_not_exists(epoch_metrics, :empty_list2), :epochs)"
                )
                attr_values[":epochs"] = self._serialise_for_dynamo(epochs)
                if ":empty_list" not in attr_values:
                    attr_values[":empty_list2"] = []
                else:
                    attr_values[":empty_list2"] = []

            # Always update last_updated
            update_parts.append("#lu = :now")
            attr_values[":now"] = datetime.now(timezone.utc).isoformat()
            attr_names["#lu"] = "last_updated"

            update_expr = "SET " + ", ".join(update_parts)

            table.update_item(
                Key={"job_id": self.job_id},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=attr_values,
                ExpressionAttributeNames=attr_names,
            )

            logger.debug(
                "Flushed %d step metrics and %d epoch metrics for job %s",
                len(steps),
                len(epochs),
                self.job_id,
            )

        except ClientError:
            # Re-buffer the entries so they are not lost
            logger.exception("Failed to flush metrics to DynamoDB — re-buffering")
            with self._lock:
                self._step_buffer = steps + self._step_buffer
                self._epoch_buffer = epochs + self._epoch_buffer
        except Exception:
            logger.exception("Unexpected error flushing metrics — data may be lost")

    @staticmethod
    def _serialise_for_dynamo(entries: list[dict]) -> list[str]:
        """Serialise metric entries as JSON strings for DynamoDB list storage.

        DynamoDB has a 400KB item limit. Storing each entry as a compact JSON
        string in a list keeps the schema simple and avoids nested-map overhead.
        """
        return [json.dumps(entry, separators=(",", ":"), default=str) for entry in entries]
