"""Fargate pricing helpers.

Fargate has deterministic per-second pricing based on vCPU and memory.
No spot price lookups needed.
"""

from __future__ import annotations

from .instance_catalog import FargateSize, FARGATE_VCPU_PER_HOUR, FARGATE_MEMORY_PER_HOUR


def get_fargate_hourly_cost(size: FargateSize) -> float:
    """Return the hourly cost (USD) for a Fargate task of the given size."""
    vcpu_count = size.vcpus / 1024
    return round(
        (vcpu_count * FARGATE_VCPU_PER_HOUR) + (size.memory_gb * FARGATE_MEMORY_PER_HOUR),
        6,
    )
