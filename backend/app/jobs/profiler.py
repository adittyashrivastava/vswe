"""Deterministic ML job profiler.

Parses a Python training script with the ``ast`` module, extracts framework
usage, model architecture, hyperparameters, and precision settings, then
estimates resource requirements and recommends the cheapest Spot instance.

This is intentionally *not* LLM-based. All analysis is deterministic so the
output is reproducible and testable.
"""

from __future__ import annotations

import ast
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .instance_catalog import (
    InstanceSpec,
    filter_instances,
    get_gpu_instances,
    get_instance,
)
from .spot_prices import get_on_demand_price, get_spot_prices

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BYTES_PER_PARAM: dict[str, int] = {
    "fp32": 4,
    "fp16": 2,
    "bf16": 2,
    "int8": 1,
}

# Bytes per parameter consumed by common optimizers (Adam: 2 fp32 states).
OPTIMIZER_BYTES_PER_PARAM: int = 8  # Adam default

# Safety multiplier applied on top of estimated total GPU memory.  Accounts
# for CUDA context, fragmentation, cuDNN workspace, etc.
GPU_MEMORY_OVERHEAD: float = 1.20

# Rough per-sample activation memory (bytes) when we cannot introspect the
# graph.  Deliberately conservative — it is better to over-provision than OOM.
DEFAULT_ACTIVATION_BYTES_PER_SAMPLE: int = 50 * 1024 * 1024  # 50 MiB

# ---------------------------------------------------------------------------
# Known model architectures — name (lowercased) -> approximate param count
# ---------------------------------------------------------------------------

KNOWN_ARCHITECTURES: dict[str, int] = {
    # Vision
    "resnet18": 11_700_000,
    "resnet34": 21_800_000,
    "resnet50": 25_600_000,
    "resnet101": 44_500_000,
    "resnet152": 60_200_000,
    "vgg16": 138_000_000,
    "vgg19": 144_000_000,
    "alexnet": 61_100_000,
    "mobilenet_v2": 3_400_000,
    "mobilenetv2": 3_400_000,
    "efficientnet_b0": 5_300_000,
    "efficientnet_b7": 66_000_000,
    "vit_base": 86_000_000,
    "vit_large": 307_000_000,
    "vit_huge": 632_000_000,
    # NLP — Transformers
    "bert-base": 110_000_000,
    "bert-large": 340_000_000,
    "gpt2": 124_000_000,
    "gpt2-medium": 355_000_000,
    "gpt2-large": 774_000_000,
    "gpt2-xl": 1_500_000_000,
    "t5-small": 60_000_000,
    "t5-base": 220_000_000,
    "t5-large": 770_000_000,
    "t5-3b": 3_000_000_000,
    "llama-7b": 6_700_000_000,
    "llama-13b": 13_000_000_000,
    "mistral-7b": 7_000_000_000,
    # Diffusion
    "stable-diffusion": 860_000_000,
    "stable-diffusion-xl": 3_500_000_000,
}


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class JobProfile:
    """Complete resource profile for a training job."""

    framework: str  # "pytorch" | "tensorflow" | "jax" | "unknown"
    model_param_count: int  # estimated total parameters
    precision: str  # "fp32" | "fp16" | "bf16" | "int8"
    batch_size: int
    epochs: int
    needs_gpu: bool
    estimated_gpu_memory_gb: float
    estimated_cpu_memory_gb: float
    estimated_storage_gb: float
    estimated_runtime_hours: float | None  # None when we cannot estimate
    recommended_instance: InstanceSpec
    estimated_spot_price: float  # USD/hr
    estimated_total_cost: float  # spot_price * runtime (or spot_price * 1 hr)
    checkpoint_interval_epochs: int
    analysis_details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def profile_job(script_path: str, workspace_path: str) -> JobProfile:
    """Analyse a training script and return a full :class:`JobProfile`.

    The function is intentionally best-effort: if it cannot extract a value it
    falls back to conservative defaults rather than raising.
    """
    source = Path(script_path).read_text(encoding="utf-8", errors="replace")

    try:
        tree = ast.parse(source, filename=script_path)
    except SyntaxError as exc:
        logger.warning("Failed to parse %s: %s — using fallback profile", script_path, exc)
        return await _fallback_profile(workspace_path)

    ctx = _AnalysisContext(source=source, tree=tree, workspace=workspace_path)

    # 1. Framework detection
    framework = _detect_framework(ctx)

    # 2. GPU usage
    needs_gpu = _detect_gpu_usage(ctx)

    # 3. Precision
    precision = _detect_precision(ctx)

    # 4. Parameter count
    param_count = _estimate_param_count(ctx)

    # 5. Batch size
    batch_size = _extract_batch_size(ctx)

    # 6. Epochs
    epochs = _extract_epochs(ctx)

    # 7. Memory estimates
    gpu_mem_gb = _estimate_gpu_memory_gb(param_count, precision, batch_size) if needs_gpu else 0.0
    cpu_mem_gb = _estimate_cpu_memory_gb(param_count, precision, batch_size)

    # 8. Storage estimate (workspace + checkpoints + overhead)
    storage_gb = _estimate_storage_gb(param_count, precision, workspace_path)

    # 9. Instance selection
    instance, spot_price = await _select_instance(
        needs_gpu=needs_gpu,
        gpu_mem_gb=gpu_mem_gb,
        cpu_mem_gb=cpu_mem_gb,
        storage_gb=storage_gb,
    )

    # 10. Runtime estimate (very rough — we don't know dataset size)
    runtime_hours: float | None = None  # TODO: dataset-size estimation

    # 11. Cost
    total_cost = spot_price * (runtime_hours if runtime_hours else 1.0)

    # 12. Checkpoint interval
    checkpoint_interval = _recommend_checkpoint_interval(param_count, precision, epochs)

    return JobProfile(
        framework=framework,
        model_param_count=param_count,
        precision=precision,
        batch_size=batch_size,
        epochs=epochs,
        needs_gpu=needs_gpu,
        estimated_gpu_memory_gb=round(gpu_mem_gb, 2),
        estimated_cpu_memory_gb=round(cpu_mem_gb, 2),
        estimated_storage_gb=round(storage_gb, 2),
        estimated_runtime_hours=runtime_hours,
        recommended_instance=instance,
        estimated_spot_price=round(spot_price, 4),
        estimated_total_cost=round(total_cost, 4),
        checkpoint_interval_epochs=checkpoint_interval,
        analysis_details={
            "framework_detection": framework,
            "gpu_indicators_found": needs_gpu,
            "precision_detected": precision,
            "param_estimation_method": ctx.param_estimation_method,
            "known_arch_matched": ctx.known_arch_matched,
            "layers_counted": ctx.layers_counted,
            "batch_size_source": ctx.batch_size_source,
            "epochs_source": ctx.epochs_source,
        },
    )


# ---------------------------------------------------------------------------
# Internal analysis context — collects metadata as we walk the AST
# ---------------------------------------------------------------------------


class _AnalysisContext:
    """Mutable bag of state accumulated during AST analysis."""

    def __init__(self, source: str, tree: ast.Module, workspace: str) -> None:
        self.source = source
        self.tree = tree
        self.workspace = workspace

        # Collected imports (module -> alias or None)
        self.imports: dict[str, str | None] = {}
        # Top-level assignments: name -> ast node (for simple literals)
        self.assignments: dict[str, ast.expr] = {}

        # Provenance tracking for the final profile
        self.param_estimation_method: str = "default"
        self.known_arch_matched: str | None = None
        self.layers_counted: int = 0
        self.batch_size_source: str = "default"
        self.epochs_source: str = "default"

        self._collect_imports()
        self._collect_assignments()

    # -- helpers --

    def _collect_imports(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports[alias.name] = alias.asname
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    full = f"{module}.{alias.name}" if module else alias.name
                    self.imports[full] = alias.asname

    def _collect_assignments(self) -> None:
        for node in ast.iter_child_nodes(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.assignments[target.id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
                self.assignments[node.target.id] = node.value

    def has_import(self, prefix: str) -> bool:
        """Return True if any imported module starts with *prefix*."""
        return any(mod.startswith(prefix) for mod in self.imports)


# ---------------------------------------------------------------------------
# Step 1: Framework detection
# ---------------------------------------------------------------------------


def _detect_framework(ctx: _AnalysisContext) -> str:
    if ctx.has_import("torch") or ctx.has_import("pytorch"):
        return "pytorch"
    if ctx.has_import("tensorflow") or ctx.has_import("tf") or ctx.has_import("keras"):
        return "tensorflow"
    if ctx.has_import("jax") or ctx.has_import("flax"):
        return "jax"
    return "unknown"


# ---------------------------------------------------------------------------
# Step 2: GPU usage detection
# ---------------------------------------------------------------------------

_GPU_INDICATORS = (
    ".cuda()",
    ".to(device",
    ".to('cuda",
    '.to("cuda',
    "torch.device",
    "tf.device",
    "/gpu:",
    "jax.devices",
    "accelerator",
    "use_gpu",
    "gpus",
)


def _detect_gpu_usage(ctx: _AnalysisContext) -> bool:
    """Heuristic: search the source for common GPU-related patterns."""
    source_lower = ctx.source.lower()
    for indicator in _GPU_INDICATORS:
        if indicator.lower() in source_lower:
            return True
    # If the script imports a GPU framework, assume GPU by default.
    if _detect_framework(ctx) in ("pytorch", "tensorflow", "jax"):
        return True
    return False


# ---------------------------------------------------------------------------
# Step 3: Precision detection
# ---------------------------------------------------------------------------


def _detect_precision(ctx: _AnalysisContext) -> str:
    src = ctx.source
    # Check for explicit AMP / autocast usage
    if "autocast" in src or "GradScaler" in src or "amp" in src.lower():
        if "bfloat16" in src or "bf16" in src:
            return "bf16"
        return "fp16"
    if "bfloat16" in src or "bf16" in src:
        return "bf16"
    if "float16" in src or "fp16" in src or "half()" in src:
        return "fp16"
    if "int8" in src or "quantize" in src.lower():
        return "int8"
    return "fp32"


# ---------------------------------------------------------------------------
# Step 4: Parameter count estimation
# ---------------------------------------------------------------------------


def _estimate_param_count(ctx: _AnalysisContext) -> int:
    """Try multiple strategies in order of confidence and return the first hit."""

    # Strategy A: known architecture name in source
    count = _match_known_architecture(ctx)
    if count > 0:
        return count

    # Strategy B: walk AST for nn.Module subclass bodies and sum layer params
    count = _count_params_from_layers(ctx)
    if count > 0:
        return count

    # Strategy C: look for explicit num_parameters / param_count assignments
    count = _extract_explicit_param_count(ctx)
    if count > 0:
        return count

    # Fallback: assume a moderate model (roughly ResNet-50 scale)
    ctx.param_estimation_method = "default"
    return 25_000_000


def _match_known_architecture(ctx: _AnalysisContext) -> int:
    """Search for known model names in string literals and identifiers."""
    source_lower = ctx.source.lower()
    # Check longer names first to prefer more specific matches.
    for name in sorted(KNOWN_ARCHITECTURES, key=len, reverse=True):
        if name.lower().replace("-", "_") in source_lower.replace("-", "_"):
            ctx.param_estimation_method = "known_architecture"
            ctx.known_arch_matched = name
            return KNOWN_ARCHITECTURES[name]
    return 0


def _count_params_from_layers(ctx: _AnalysisContext) -> int:
    """Walk AST looking for nn.Linear / nn.Conv2d / nn.Embedding calls."""
    total = 0
    layers = 0

    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue

        func_name = _resolve_call_name(node)
        if func_name is None:
            continue

        args = _resolve_int_args(node)

        if func_name in ("Linear", "nn.Linear"):
            # Linear(in_features, out_features, bias=True)
            if len(args) >= 2:
                in_f, out_f = args[0], args[1]
                bias = _resolve_keyword_bool(node, "bias", default=True)
                total += in_f * out_f + (out_f if bias else 0)
                layers += 1

        elif func_name in ("Conv2d", "nn.Conv2d"):
            # Conv2d(in_channels, out_channels, kernel_size, ...)
            if len(args) >= 3:
                in_c, out_c, k = args[0], args[1], args[2]
                groups = _resolve_keyword_int(node, "groups", default=1)
                bias = _resolve_keyword_bool(node, "bias", default=True)
                total += (in_c // groups) * out_c * k * k + (out_c if bias else 0)
                layers += 1

        elif func_name in ("Conv1d", "nn.Conv1d"):
            if len(args) >= 3:
                in_c, out_c, k = args[0], args[1], args[2]
                groups = _resolve_keyword_int(node, "groups", default=1)
                bias = _resolve_keyword_bool(node, "bias", default=True)
                total += (in_c // groups) * out_c * k + (out_c if bias else 0)
                layers += 1

        elif func_name in ("Embedding", "nn.Embedding"):
            # Embedding(num_embeddings, embedding_dim)
            if len(args) >= 2:
                total += args[0] * args[1]
                layers += 1

        elif func_name in ("LayerNorm", "nn.LayerNorm", "BatchNorm2d", "nn.BatchNorm2d",
                           "BatchNorm1d", "nn.BatchNorm1d", "GroupNorm", "nn.GroupNorm"):
            # 2 * normalized_shape (gamma + beta)
            if len(args) >= 1:
                total += 2 * args[0]
                layers += 1

        elif func_name in ("LSTM", "nn.LSTM", "GRU", "nn.GRU"):
            # LSTM(input_size, hidden_size, num_layers=1, ...)
            if len(args) >= 2:
                input_size, hidden_size = args[0], args[1]
                num_layers = args[2] if len(args) >= 3 else _resolve_keyword_int(node, "num_layers", 1)
                bidirectional = _resolve_keyword_bool(node, "bidirectional", False)
                gate_mult = 4 if "LSTM" in func_name else 3
                directions = 2 if bidirectional else 1
                # First layer
                layer_params = gate_mult * (input_size * hidden_size + hidden_size * hidden_size + 2 * hidden_size)
                # Subsequent layers
                subsequent_input = hidden_size * directions
                subsequent_params = gate_mult * (subsequent_input * hidden_size + hidden_size * hidden_size + 2 * hidden_size)
                total += directions * (layer_params + (num_layers - 1) * subsequent_params)
                layers += 1

        elif func_name in ("MultiheadAttention", "nn.MultiheadAttention"):
            # MultiheadAttention(embed_dim, num_heads, ...)
            if len(args) >= 1:
                embed_dim = args[0]
                # Q, K, V projections + output projection
                total += 4 * embed_dim * embed_dim + 4 * embed_dim
                layers += 1

    if total > 0:
        ctx.param_estimation_method = "layer_counting"
        ctx.layers_counted = layers
    return total


def _extract_explicit_param_count(ctx: _AnalysisContext) -> int:
    """Look for variable assignments that look like param counts."""
    keywords = ("num_parameters", "param_count", "n_params", "total_params", "num_params")
    for name, value_node in ctx.assignments.items():
        if name.lower() in keywords:
            val = _try_eval_literal(value_node)
            if isinstance(val, (int, float)) and val > 0:
                ctx.param_estimation_method = "explicit_assignment"
                return int(val)
    return 0


# ---------------------------------------------------------------------------
# Step 5 & 6: Hyperparameter extraction
# ---------------------------------------------------------------------------


def _extract_batch_size(ctx: _AnalysisContext) -> int:
    """Extract batch_size from DataLoader calls, argparse, or assignments."""

    # A — DataLoader(..., batch_size=N)
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Call) and _resolve_call_name(node) in ("DataLoader", "data.DataLoader"):
            val = _resolve_keyword_int(node, "batch_size")
            if val is not None and val > 0:
                ctx.batch_size_source = "DataLoader"
                return val

    # B — argparse: parser.add_argument("--batch_size", ..., default=N)
    val = _extract_argparse_default(ctx, ("batch_size", "batch-size", "bs", "train_batch_size"))
    if val is not None and val > 0:
        ctx.batch_size_source = "argparse"
        return val

    # C — plain assignment: batch_size = N
    for name in ("batch_size", "BATCH_SIZE", "bs", "train_batch_size"):
        if name in ctx.assignments:
            val = _try_eval_literal(ctx.assignments[name])
            if isinstance(val, int) and val > 0:
                ctx.batch_size_source = "assignment"
                return val

    ctx.batch_size_source = "default"
    return 32


def _extract_epochs(ctx: _AnalysisContext) -> int:
    """Extract epoch count from argparse or assignments."""

    val = _extract_argparse_default(ctx, ("epochs", "num_epochs", "n_epochs", "max_epochs"))
    if val is not None and val > 0:
        ctx.epochs_source = "argparse"
        return val

    for name in ("epochs", "num_epochs", "EPOCHS", "n_epochs", "max_epochs", "NUM_EPOCHS"):
        if name in ctx.assignments:
            val = _try_eval_literal(ctx.assignments[name])
            if isinstance(val, int) and val > 0:
                ctx.epochs_source = "assignment"
                return val

    ctx.epochs_source = "default"
    return 10


def _extract_argparse_default(ctx: _AnalysisContext, arg_names: tuple[str, ...]) -> int | None:
    """Find ``parser.add_argument("--<name>", ..., default=N)``."""
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _resolve_call_name(node)
        if func_name not in ("add_argument", "parser.add_argument"):
            continue
        # First positional arg is the flag name.
        if not node.args:
            continue
        flag = _try_eval_literal(node.args[0])
        if not isinstance(flag, str):
            continue
        stripped = flag.lstrip("-").replace("-", "_")
        if stripped not in arg_names:
            continue
        default_val = _resolve_keyword_int(node, "default")
        if default_val is not None:
            return default_val
    return None


# ---------------------------------------------------------------------------
# Step 7: Memory estimation
# ---------------------------------------------------------------------------


def _estimate_gpu_memory_gb(param_count: int, precision: str, batch_size: int) -> float:
    """Estimate peak GPU memory in GiB.

    Formula::

        model_mem     = params * bytes_per_param
        gradient_mem  = model_mem  (same dtype as model by default)
        optimizer_mem = params * 8  (Adam keeps 2 fp32 states)
        activation_mem = batch_size * DEFAULT_ACTIVATION_BYTES_PER_SAMPLE
        total = (model + gradient + optimizer + activation) * overhead
    """
    bpp = BYTES_PER_PARAM.get(precision, 4)
    model_mem = param_count * bpp
    gradient_mem = model_mem
    optimizer_mem = param_count * OPTIMIZER_BYTES_PER_PARAM
    activation_mem = batch_size * DEFAULT_ACTIVATION_BYTES_PER_SAMPLE

    total_bytes = (model_mem + gradient_mem + optimizer_mem + activation_mem) * GPU_MEMORY_OVERHEAD
    return total_bytes / (1024 ** 3)


def _estimate_cpu_memory_gb(param_count: int, precision: str, batch_size: int) -> float:
    """Estimate peak CPU/system memory.

    CPU memory typically holds a copy of the data batch plus overhead for data
    loading workers.  We budget 2x the GPU estimate as a conservative floor.
    """
    gpu_est = _estimate_gpu_memory_gb(param_count, precision, batch_size)
    return max(gpu_est * 2, 4.0)


def _estimate_storage_gb(param_count: int, precision: str, workspace_path: str) -> float:
    """Estimate required instance storage.

    Components: workspace (code + data on disk) + checkpoint files + overhead.
    """
    # Workspace size
    ws_bytes = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(workspace_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    ws_bytes += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    ws_gb = ws_bytes / (1024 ** 3)

    # Checkpoint size: model + optimizer state
    bpp = BYTES_PER_PARAM.get(precision, 4)
    ckpt_gb = (param_count * bpp + param_count * OPTIMIZER_BYTES_PER_PARAM) / (1024 ** 3)
    # Keep room for 2 checkpoints (current + previous)
    ckpt_gb *= 2

    return ws_gb + ckpt_gb + 5.0  # 5 GB flat overhead for OS, containers, etc.


# ---------------------------------------------------------------------------
# Step 8: Instance selection
# ---------------------------------------------------------------------------


async def _select_instance(
    *,
    needs_gpu: bool,
    gpu_mem_gb: float,
    cpu_mem_gb: float,
    storage_gb: float,
) -> tuple[InstanceSpec, float]:
    """Pick the cheapest Spot instance that satisfies the resource requirements.

    Returns ``(instance_spec, spot_price_usd_per_hour)``.
    """
    if needs_gpu:
        candidates = filter_instances(
            min_gpu_memory_gb=gpu_mem_gb,
            min_memory_gb=cpu_mem_gb,
            category="gpu",
        )
    else:
        candidates = filter_instances(
            min_memory_gb=cpu_mem_gb,
            category="cpu",
        )
        if not candidates:
            candidates = filter_instances(min_memory_gb=cpu_mem_gb, category="memory")

    if not candidates:
        # Ultimate fallback: largest GPU instance in catalog.
        gpu_instances = get_gpu_instances()
        candidates = [gpu_instances[-1]] if gpu_instances else []
        if not candidates:
            fallback = get_instance("g5.xlarge")
            assert fallback is not None
            candidates = [fallback]

    # Fetch spot prices for all candidates and pick cheapest.
    type_names = [c.instance_type for c in candidates]
    prices = await get_spot_prices(type_names)

    best: InstanceSpec | None = None
    best_price = float("inf")
    for c in candidates:
        p = prices.get(c.instance_type, get_on_demand_price(c.instance_type))
        if p < best_price:
            best_price = p
            best = c

    assert best is not None
    return best, best_price


# ---------------------------------------------------------------------------
# Step 9: Checkpoint interval recommendation
# ---------------------------------------------------------------------------


def _recommend_checkpoint_interval(param_count: int, precision: str, epochs: int) -> int:
    """Decide how often to write checkpoints (in epochs).

    Spot instances have ~5% interruption rate per hour. We want to lose at
    most ~30 min of work per interruption, but also avoid thrashing on I/O for
    tiny models.

    Rules:
    - Checkpoint size < 500 MB  -> every epoch
    - Checkpoint size < 2 GB    -> every 2 epochs
    - Checkpoint size < 10 GB   -> every 5 epochs
    - Larger                    -> every 10 epochs
    But never more than epochs // 2 (at least 2 checkpoints per run).
    """
    bpp = BYTES_PER_PARAM.get(precision, 4)
    ckpt_bytes = param_count * (bpp + OPTIMIZER_BYTES_PER_PARAM)
    ckpt_gb = ckpt_bytes / (1024 ** 3)

    if ckpt_gb < 0.5:
        interval = 1
    elif ckpt_gb < 2:
        interval = 2
    elif ckpt_gb < 10:
        interval = 5
    else:
        interval = 10

    # Ensure at least 2 checkpoints over the entire run.
    if epochs > 1:
        interval = min(interval, max(1, epochs // 2))

    return interval


# ---------------------------------------------------------------------------
# Fallback profile (when we cannot parse the script at all)
# ---------------------------------------------------------------------------


async def _fallback_profile(workspace_path: str) -> JobProfile:
    """Conservative fallback when parsing fails entirely."""
    instance = get_instance("g5.xlarge")
    assert instance is not None
    price = get_on_demand_price("g5.xlarge") * 0.35  # rough spot estimate
    return JobProfile(
        framework="unknown",
        model_param_count=25_000_000,
        precision="fp32",
        batch_size=32,
        epochs=10,
        needs_gpu=True,
        estimated_gpu_memory_gb=4.0,
        estimated_cpu_memory_gb=8.0,
        estimated_storage_gb=20.0,
        estimated_runtime_hours=None,
        recommended_instance=instance,
        estimated_spot_price=round(price, 4),
        estimated_total_cost=round(price, 4),
        checkpoint_interval_epochs=1,
        analysis_details={"error": "failed to parse script, using conservative fallback"},
    )


# ---------------------------------------------------------------------------
# AST helpers — safe node resolution utilities
# ---------------------------------------------------------------------------


def _resolve_call_name(node: ast.Call) -> str | None:
    """Return the dotted name of a Call node's function, e.g. 'nn.Linear'."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = [func.attr]
        current: ast.expr = func.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _resolve_int_args(node: ast.Call) -> list[int]:
    """Return the positional arguments that are integer literals."""
    result: list[int] = []
    for arg in node.args:
        val = _try_eval_literal(arg)
        if isinstance(val, int):
            result.append(val)
        else:
            # Stop at first non-literal — positional order matters.
            break
    return result


def _resolve_keyword_int(node: ast.Call, keyword: str, default: int | None = None) -> int | None:
    """Extract an integer keyword argument from a Call node."""
    for kw in node.keywords:
        if kw.arg == keyword:
            val = _try_eval_literal(kw.value)
            if isinstance(val, int):
                return val
    return default


def _resolve_keyword_bool(node: ast.Call, keyword: str, default: bool = True) -> bool:
    """Extract a boolean keyword argument from a Call node."""
    for kw in node.keywords:
        if kw.arg == keyword:
            val = _try_eval_literal(kw.value)
            if isinstance(val, bool):
                return val
    return default


def _try_eval_literal(node: ast.expr) -> Any:
    """Safely evaluate an AST node if it is a literal (number, string, bool, tuple).

    Returns ``None`` for anything non-trivial.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, RecursionError):
        return None
