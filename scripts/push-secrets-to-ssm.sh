#!/usr/bin/env python3
"""
Reads backend/.env.production and pushes secrets to AWS SSM Parameter Store
as a single JSON blob at /vswe/secrets.

Usage:
    python3 scripts/push-secrets-to-ssm.sh                  # first time
    python3 scripts/push-secrets-to-ssm.sh --overwrite       # update existing

The ECS container reads this parameter at startup via the VSWE_SECRETS
env var and unpacks the JSON keys into individual environment variables.
"""

import json
import subprocess
import sys
from pathlib import Path

ENV_FILE = Path(__file__).parent.parent / "backend" / ".env.production"
PARAMETER_NAME = "/vswe/secrets"
REGION = "us-east-1"

KEYS = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GITHUB_APP_ID",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_WEBHOOK_SECRET",
    "JWT_SECRET",
    "FRONTEND_URL",
    "BACKEND_URL",
    "CORS_ORIGINS",
]


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict, handling values with special characters."""
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value
    return values


def main():
    if not ENV_FILE.exists():
        print(f"Error: {ENV_FILE} not found")
        sys.exit(1)

    env_values = parse_env_file(ENV_FILE)

    # Build the secrets JSON
    secrets = {}
    for key in KEYS:
        value = env_values.get(key, "")
        if not value:
            print(f"  Warning: {key} is empty or missing")
        else:
            secrets[key] = value
            print(f"  {key}: {'*' * min(len(value), 8)}...")

    json_blob = json.dumps(secrets)

    # Push to SSM
    overwrite = "--overwrite" in sys.argv

    cmd = [
        "aws", "ssm", "put-parameter",
        "--name", PARAMETER_NAME,
        "--type", "SecureString",
        "--value", json_blob,
        "--region", REGION,
    ]
    if overwrite:
        cmd.append("--overwrite")

    print(f"\nPushing {len(secrets)} secrets to SSM: {PARAMETER_NAME}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error: {result.stderr.strip()}")
        sys.exit(1)

    print("Done.")

    # Also push the webhook secret as a dedicated parameter for the Lambda
    # (it runs separately and doesn't use the JSON blob)
    webhook_secret = secrets.get("GITHUB_WEBHOOK_SECRET", "")
    if webhook_secret:
        lambda_cmd = [
            "aws", "ssm", "put-parameter",
            "--name", "/vswe/github-webhook-secret",
            "--type", "SecureString",
            "--value", webhook_secret,
            "--region", REGION,
        ]
        if overwrite:
            lambda_cmd.append("--overwrite")

        print(f"Pushing Lambda webhook secret to /vswe/github-webhook-secret")
        result = subprocess.run(lambda_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Warning: {result.stderr.strip()}")
        else:
            print("Done.")


if __name__ == "__main__":
    main()
