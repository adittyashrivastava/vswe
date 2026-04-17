"""Fargate task size catalog for job resource matching.

Fargate has fixed CPU/memory combinations. This module maps workload
requirements to the smallest Fargate size that fits.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FargateSize:
    """A valid Fargate vCPU/memory combination."""

    vcpus: int          # vCPU units (256 = 0.25 vCPU, 1024 = 1 vCPU, etc.)
    memory_gb: float    # Memory in GB
    category: str       # "small" | "medium" | "large"


# ---------------------------------------------------------------------------
# Valid Fargate CPU/memory combos (subset of what AWS supports)
# See: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html
# ---------------------------------------------------------------------------

FARGATE_SIZES: list[FargateSize] = [
    # Small — simple scripts, data processing
    FargateSize(256, 0.5, "small"),
    FargateSize(256, 1.0, "small"),
    FargateSize(512, 1.0, "small"),
    FargateSize(512, 2.0, "small"),
    # Medium — moderate workloads
    FargateSize(1024, 2.0, "medium"),
    FargateSize(1024, 4.0, "medium"),
    FargateSize(2048, 4.0, "medium"),
    FargateSize(2048, 8.0, "medium"),
    # Large — heavy CPU or memory workloads
    FargateSize(4096, 8.0, "large"),
    FargateSize(4096, 16.0, "large"),
    FargateSize(4096, 30.0, "large"),
]


# ---------------------------------------------------------------------------
# Fargate pricing (us-east-1, per-second billing)
# ---------------------------------------------------------------------------

# Per-hour rates for Fargate (on-demand)
FARGATE_VCPU_PER_HOUR = 0.04048     # per vCPU
FARGATE_MEMORY_PER_HOUR = 0.004445  # per GB


def estimate_fargate_cost(size: FargateSize) -> float:
    """Estimate hourly cost for a Fargate task size."""
    vcpu_count = size.vcpus / 1024  # convert from CPU units to vCPU count
    return (vcpu_count * FARGATE_VCPU_PER_HOUR) + (size.memory_gb * FARGATE_MEMORY_PER_HOUR)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def select_fargate_size(
    *,
    min_memory_gb: float = 0.5,
    min_vcpus: int = 256,
) -> FargateSize:
    """Pick the smallest Fargate size that satisfies the requirements.

    Returns the cheapest option that meets both CPU and memory minimums.
    """
    for size in FARGATE_SIZES:
        if size.vcpus >= min_vcpus and size.memory_gb >= min_memory_gb:
            return size

    # Fallback to largest available
    return FARGATE_SIZES[-1]
