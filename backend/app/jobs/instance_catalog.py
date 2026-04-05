"""AWS EC2 instance catalog with hardware specs for ML workload matching."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstanceSpec:
    """Hardware specification for an EC2 instance type."""

    instance_type: str  # e.g. "g4dn.xlarge"
    vcpus: int
    memory_gb: float
    gpu_count: int
    gpu_model: str | None  # e.g. "T4", "A10G", "V100", "A100"
    gpu_memory_gb: float  # per GPU
    storage_gb: float  # instance storage
    category: str  # "gpu" | "cpu" | "memory"


# ---------------------------------------------------------------------------
# Catalog of supported instance types
# ---------------------------------------------------------------------------

INSTANCE_CATALOG: list[InstanceSpec] = [
    # --- GPU instances (NVIDIA) ---
    # G4dn — T4 GPUs, good for inference and small-to-mid training
    InstanceSpec("g4dn.xlarge", 4, 16, 1, "T4", 16, 125, "gpu"),
    InstanceSpec("g4dn.2xlarge", 8, 32, 1, "T4", 16, 225, "gpu"),
    InstanceSpec("g4dn.4xlarge", 16, 64, 1, "T4", 16, 225, "gpu"),
    InstanceSpec("g4dn.12xlarge", 48, 192, 4, "T4", 16, 900, "gpu"),
    # G5 — A10G GPUs, good balance of price and performance
    InstanceSpec("g5.xlarge", 4, 16, 1, "A10G", 24, 250, "gpu"),
    InstanceSpec("g5.2xlarge", 8, 32, 1, "A10G", 24, 450, "gpu"),
    InstanceSpec("g5.4xlarge", 16, 64, 1, "A10G", 24, 600, "gpu"),
    InstanceSpec("g5.12xlarge", 48, 192, 4, "A10G", 24, 3800, "gpu"),
    # P3 — V100 GPUs, older but powerful for training
    InstanceSpec("p3.2xlarge", 8, 61, 1, "V100", 16, 0, "gpu"),
    InstanceSpec("p3.8xlarge", 32, 244, 4, "V100", 16, 0, "gpu"),
    # --- CPU instances (compute-optimized) ---
    InstanceSpec("c5.large", 2, 4, 0, None, 0, 0, "cpu"),
    InstanceSpec("c5.xlarge", 4, 8, 0, None, 0, 0, "cpu"),
    InstanceSpec("c5.2xlarge", 8, 16, 0, None, 0, 0, "cpu"),
    InstanceSpec("c5.4xlarge", 16, 32, 0, None, 0, 0, "cpu"),
    # --- Memory-optimized instances ---
    InstanceSpec("m5.xlarge", 4, 16, 0, None, 0, 0, "memory"),
    InstanceSpec("m5.2xlarge", 8, 32, 0, None, 0, 0, "memory"),
    InstanceSpec("m5.4xlarge", 16, 64, 0, None, 0, 0, "memory"),
    InstanceSpec("r5.xlarge", 4, 32, 0, None, 0, 0, "memory"),
    InstanceSpec("r5.2xlarge", 8, 64, 0, None, 0, 0, "memory"),
]

# Pre-built lookup for O(1) access by instance_type name.
_CATALOG_BY_TYPE: dict[str, InstanceSpec] = {
    spec.instance_type: spec for spec in INSTANCE_CATALOG
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_gpu_instances() -> list[InstanceSpec]:
    """Return all GPU instance types, ordered by total GPU memory ascending."""
    return sorted(
        [s for s in INSTANCE_CATALOG if s.category == "gpu"],
        key=lambda s: s.gpu_count * s.gpu_memory_gb,
    )


def get_cpu_instances() -> list[InstanceSpec]:
    """Return all CPU-only compute-optimized instances, ordered by vCPUs."""
    return sorted(
        [s for s in INSTANCE_CATALOG if s.category == "cpu"],
        key=lambda s: s.vcpus,
    )


def get_memory_instances() -> list[InstanceSpec]:
    """Return all memory-optimized instances, ordered by memory."""
    return sorted(
        [s for s in INSTANCE_CATALOG if s.category == "memory"],
        key=lambda s: s.memory_gb,
    )


def get_instance(type_name: str) -> InstanceSpec | None:
    """Look up an instance spec by its type name (e.g. 'g5.xlarge').

    Returns ``None`` if the type is not in the catalog.
    """
    return _CATALOG_BY_TYPE.get(type_name)


def filter_instances(
    *,
    min_gpu_memory_gb: float = 0,
    min_gpu_count: int = 0,
    min_memory_gb: float = 0,
    min_vcpus: int = 0,
    category: str | None = None,
) -> list[InstanceSpec]:
    """Filter the catalog by hardware requirements.

    All filters are combined with AND logic. Returns instances sorted by total
    GPU memory (descending), then system memory.
    """
    results: list[InstanceSpec] = []
    for spec in INSTANCE_CATALOG:
        if category and spec.category != category:
            continue
        if spec.gpu_count * spec.gpu_memory_gb < min_gpu_memory_gb:
            continue
        if spec.gpu_count < min_gpu_count:
            continue
        if spec.memory_gb < min_memory_gb:
            continue
        if spec.vcpus < min_vcpus:
            continue
        results.append(spec)

    return sorted(
        results,
        key=lambda s: (s.gpu_count * s.gpu_memory_gb, s.memory_gb),
    )
