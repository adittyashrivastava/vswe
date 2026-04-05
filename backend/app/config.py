from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    env: str = "local"

    # AWS
    aws_region: str = "us-east-1"
    dynamodb_endpoint: str | None = None  # None = use real AWS, set for local

    # LLM
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    default_model: str = "claude-opus-4-20250514"

    # GitHub App
    github_app_id: str = ""
    github_client_id: str = ""  # Same as app_id for GitHub Apps
    github_client_secret: str = ""
    github_app_private_key: str = ""
    github_webhook_secret: str = ""

    # JWT
    jwt_secret: str = "vswe-local-dev-secret-change-in-production"
    jwt_expiry_hours: int = 24

    # Storage
    efs_mount_path: str = ""  # Set in .env; defaults to ./workspaces locally
    s3_bucket: str = "vswe-artifacts"

    @property
    def workspace_root(self) -> str:
        """Root directory for agent workspaces."""
        import os
        if self.efs_mount_path:
            return os.path.join(self.efs_mount_path, "workspaces")
        # Local dev: use a directory inside the project
        return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "workspaces")

    # Budget
    budget_limit_usd: float = 5.0

    # SQS
    sqs_issue_queue_url: str = ""

    # URLs
    frontend_url: str = "http://localhost:3000"  # e.g. "https://app.vswe.example.com"
    backend_url: str = "http://localhost:8080"  # e.g. "https://api.vswe.example.com"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"  # comma-separated

    # Ngrok (local dev only — public URL for GitHub webhooks)
    ngrok_url: str = ""  # e.g. "https://abc123.ngrok-free.app"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}


settings = Settings()
