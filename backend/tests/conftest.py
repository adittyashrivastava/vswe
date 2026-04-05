"""Shared test fixtures for VSWE backend tests."""

from __future__ import annotations

import os
import pytest

# Force local settings before anything imports app.config
os.environ.setdefault("ENV", "test")
os.environ.setdefault("DYNAMODB_ENDPOINT", "http://localhost:8000")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-key")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def dynamo_tables():
    """Create DynamoDB tables for a test, then clean up."""
    from app.db.dynamo import create_tables, get_dynamodb_resource

    await create_tables()
    yield
    # Clean up: delete all tables
    resource = get_dynamodb_resource()
    for table in resource.tables.all():
        table.delete()


@pytest.fixture
def workspace_path(tmp_path):
    """Provide a temporary workspace directory for agent tests."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return str(ws)
