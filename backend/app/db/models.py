"""Pydantic v2 models for all VSWE DynamoDB tables."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_serializer


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SessionType(str, Enum):
    GITHUB_ISSUE = "github_issue"
    CHAT = "chat"


class SessionState(str, Enum):
    ACTIVE = "active"      # Agent is currently running — block new comments
    INACTIVE = "inactive"  # Agent is idle — ready to process new comments


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class JobStatus(str, Enum):
    PROFILING = "profiling"
    QUEUED = "queued"
    RUNNING = "running"
    CHECKPOINTING = "checkpointing"
    COMPLETED = "completed"
    FAILED = "failed"
    SPOT_INTERRUPTED = "spot_interrupted"


class StorageTier(str, Enum):
    EFS = "efs"
    S3 = "s3"
    PRUNED = "pruned"


class CostCategory(str, Enum):
    LLM_API = "llm_api"
    COMPUTE_BATCH = "compute_batch"
    COMPUTE_FARGATE = "compute_fargate"
    STORAGE_EFS = "storage_efs"
    STORAGE_S3 = "storage_s3"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dynamo_value(value: Any) -> Any:
    """Recursively convert a Python value to a DynamoDB-safe representation.

    - float -> Decimal  (DynamoDB does not accept float)
    - None values inside dicts are dropped
    - Enum -> its .value
    - Nested dicts and lists are processed recursively
    """
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo_value(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_to_dynamo_value(item) for item in value]
    return value


def _from_dynamo_value(value: Any) -> Any:
    """Recursively convert a DynamoDB item value back to plain Python types.

    - Decimal -> int if whole number, else float
    """
    if isinstance(value, Decimal):
        if value == int(value):
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo_value(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------

class DynamoModel(BaseModel):
    """Base model with DynamoDB serialisation helpers."""

    model_config = {"populate_by_name": True, "use_enum_values": True}

    @field_serializer("*", mode="plain")
    @classmethod
    def _serialize_all(cls, value: Any) -> Any:  # noqa: ANN401
        """Ensure enums are serialised to their string value."""
        if isinstance(value, Enum):
            return value.value
        return value

    # -- serialisation -------------------------------------------------------

    def to_dynamo_item(self) -> dict[str, Any]:
        """Return a dict ready for ``table.put_item(Item=...)``.

        * ``None`` values are omitted (DynamoDB does not store nulls well).
        * Floats are converted to ``Decimal``.
        """
        raw = self.model_dump(mode="python", exclude_none=True, by_alias=True)
        return _to_dynamo_value(raw)

    @classmethod
    def from_dynamo_item(cls, item: dict[str, Any]) -> "DynamoModel":
        """Construct a model instance from a raw DynamoDB item dict."""
        cleaned = _from_dynamo_value(item)
        return cls.model_validate(cleaned)


# ---------------------------------------------------------------------------
# 1. vswe-sessions  (PK: session_id, SK: "META")
# ---------------------------------------------------------------------------

class SessionItem(DynamoModel):
    session_id: str
    sk: str = Field(default="META", alias="SK")

    user_id: str
    type: SessionType
    repo_url: str | None = None
    github_issue_number: int | None = None
    github_repo_full_name: str | None = None
    model: str
    state: SessionState = SessionState.ACTIVE
    workspace_path: str

    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)

    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


# ---------------------------------------------------------------------------
# 2. vswe-messages  (PK: session_id, SK: message_id — ULID)
# ---------------------------------------------------------------------------

class MessageItem(DynamoModel):
    session_id: str
    message_id: str  # ULID — used as SK

    role: MessageRole
    content: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    created_at: str = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# 3. vswe-config  (PK: config_scope, SK: "CONFIG")
# ---------------------------------------------------------------------------

class ConfigItem(DynamoModel):
    config_scope: str  # e.g. "org:mycompany" or "repo:mycompany/myrepo"
    sk: str = Field(default="CONFIG", alias="SK")

    enabled: bool = True
    default_model: str = "claude-opus-4-20250514"
    auto_respond: bool = True
    allowed_tools: list[str] = Field(default_factory=list)
    max_cost_per_issue: float = 5.0
    installation_id: str | None = None

    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# 4. vswe-jobs  (PK: job_id, SK: "META")
# ---------------------------------------------------------------------------

class JobProfile(BaseModel):
    """Deterministic profiling result for an ML training job."""
    framework: str
    model_params: int
    estimated_gpu_mem_gb: float
    estimated_runtime_hours: float

    model_config = {"extra": "allow"}


class JobItem(DynamoModel):
    job_id: str
    sk: str = Field(default="META", alias="SK")

    session_id: str
    batch_job_id: str | None = None
    status: JobStatus = JobStatus.PROFILING
    instance_type: str | None = None
    spot_price: float | None = None
    script_path: str | None = None
    profile: JobProfile | None = None

    started_at: str | None = None
    completed_at: str | None = None
    total_cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# 5. vswe-checkpoints  (PK: job_id, SK: checkpoint_id)
# ---------------------------------------------------------------------------

class CheckpointItem(DynamoModel):
    job_id: str
    checkpoint_id: str

    epoch: int
    storage_tier: StorageTier = StorageTier.EFS
    efs_path: str | None = None
    s3_uri: str | None = None
    size_bytes: int = 0
    validation_metric: float | None = None
    is_best: bool = False

    created_at: str = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# 6. vswe-costs  (PK: date, SK: cost_entry_id — ULID)
# ---------------------------------------------------------------------------

class CostItem(DynamoModel):
    date: str  # ISO date string, e.g. "2026-04-03"
    cost_entry_id: str  # ULID — used as SK

    category: CostCategory
    session_id: str | None = None
    job_id: str | None = None
    amount_usd: float
    details: dict[str, Any] = Field(default_factory=dict)

    created_at: str = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# 7. vswe-users  (PK: user_id, SK: "META")
# ---------------------------------------------------------------------------

class UserItem(DynamoModel):
    user_id: str  # "github:<github_id>"
    sk: str = Field(default="META", alias="SK")

    github_id: int
    github_login: str
    name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    github_access_token: str | None = None  # encrypted in production
    orgs: list[str] = Field(default_factory=list)  # org logins
    installations: dict[str, int] = Field(default_factory=dict)  # org_or_user -> installation_id

    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)
    last_login_at: str = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Table name constants
# ---------------------------------------------------------------------------

TABLE_SESSIONS = "vswe-sessions"
TABLE_MESSAGES = "vswe-messages"
TABLE_CONFIGS = "vswe-config"
TABLE_JOBS = "vswe-jobs"
TABLE_CHECKPOINTS = "vswe-checkpoints"
TABLE_COSTS = "vswe-costs"
TABLE_USERS = "vswe-users"

TABLE_MODELS: dict[str, type[DynamoModel]] = {
    TABLE_SESSIONS: SessionItem,
    TABLE_MESSAGES: MessageItem,
    TABLE_CONFIGS: ConfigItem,
    TABLE_JOBS: JobItem,
    TABLE_CHECKPOINTS: CheckpointItem,
    TABLE_COSTS: CostItem,
    TABLE_USERS: UserItem,
}
