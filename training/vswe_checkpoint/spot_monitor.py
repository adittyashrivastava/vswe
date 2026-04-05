"""Spot instance termination monitor for AWS EC2.

Polls the EC2 instance metadata service for spot termination notices and
invokes a callback when termination is imminent (2-minute warning).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# EC2 instance metadata endpoint for spot termination
_SPOT_METADATA_URL = "http://169.254.169.254/latest/meta-data/spot/instance-action"
_IMDS_TOKEN_URL = "http://169.254.169.254/latest/api/token"

_POLL_INTERVAL_S = 5.0
_REQUEST_TIMEOUT_S = 2.0
_TOKEN_TTL_S = 300


class SpotTerminationMonitor:
    """Monitors EC2 spot instance termination notices via IMDS.

    Runs a daemon thread that polls the instance metadata service every 5 seconds.
    When a termination notice is detected:
    1. Sets ``termination_pending`` to ``True``.
    2. Calls the ``on_termination`` callback exactly once.

    If the metadata endpoint is unreachable (e.g., not running on EC2), the
    monitor silently continues polling without raising errors.
    """

    def __init__(self, on_termination: Callable[[], None]) -> None:
        """Start the spot termination monitor.

        Args:
            on_termination: Callback invoked (once) when a termination notice is received.
                            Must be safe to call from a background thread.
        """
        self._on_termination = on_termination
        self._termination_pending = False
        self._callback_fired = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._imds_token: str | None = None

        self._thread = threading.Thread(
            target=self._poll_loop,
            name=f"spot-monitor-{id(self):x}",
            daemon=True,
        )
        self._thread.start()
        logger.debug("SpotTerminationMonitor started")

    def is_termination_pending(self) -> bool:
        """Check if spot termination has been signaled."""
        return self._termination_pending

    def stop(self) -> None:
        """Stop the monitor thread.

        Blocks until the thread exits (up to one poll interval).
        """
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=_POLL_INTERVAL_S + 1)
        logger.debug("SpotTerminationMonitor stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Main polling loop running in a daemon thread."""
        while not self._stop_event.is_set():
            try:
                if self._check_termination():
                    self._handle_termination()
                    # Keep running so is_termination_pending() stays True,
                    # but stop polling — no need to check again.
                    break
            except Exception:
                # Swallow all exceptions to keep the monitor resilient.
                # Common case: not on EC2, metadata endpoint unreachable.
                pass

            self._stop_event.wait(timeout=_POLL_INTERVAL_S)

    def _get_imds_token(self) -> str | None:
        """Obtain an IMDSv2 session token.

        Returns None if the token cannot be obtained (e.g., not on EC2 or
        IMDSv2 is not enforced).
        """
        try:
            req = urllib.request.Request(
                _IMDS_TOKEN_URL,
                method="PUT",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": str(_TOKEN_TTL_S)},
            )
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            return None

    def _check_termination(self) -> bool:
        """Poll the instance metadata endpoint.

        Returns True if a spot termination notice is present, False otherwise.
        """
        # Attempt IMDSv2 first, fall back to IMDSv1 (no token)
        if self._imds_token is None:
            self._imds_token = self._get_imds_token()

        headers = {}
        if self._imds_token:
            headers["X-aws-ec2-metadata-token"] = self._imds_token

        req = urllib.request.Request(_SPOT_METADATA_URL, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
                # A 200 response means a termination notice is present.
                if resp.status == 200:
                    body = resp.read().decode("utf-8", errors="replace")
                    logger.warning("Spot termination notice received: %s", body)
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # 404 means no termination notice — normal operation.
                return False
            # 401 means token expired; clear it so we refresh next cycle.
            if e.code == 401:
                self._imds_token = None
                return False
            raise
        except urllib.error.URLError:
            # Network error (not on EC2, metadata service unreachable, etc.)
            return False

        return False

    def _handle_termination(self) -> None:
        """Set the termination flag and fire the callback exactly once."""
        with self._lock:
            self._termination_pending = True
            if self._callback_fired:
                return
            self._callback_fired = True

        # Fire callback outside lock to avoid deadlocks
        try:
            self._on_termination()
        except Exception:
            logger.exception("Error in spot termination callback")
