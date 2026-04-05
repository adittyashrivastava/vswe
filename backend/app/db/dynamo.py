"""DynamoDB client helpers for the VSWE project.

All public functions are async.  Since boto3's DynamoDB resource client is
synchronous, every call is dispatched via ``asyncio.get_event_loop().run_in_executor``.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key, Attr  # noqa: F401 — re-exported for callers
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Table schemas  (name -> keys + GSIs)
# ---------------------------------------------------------------------------

_TABLE_DEFS: list[dict[str, Any]] = [
    # 1. vswe-sessions
    {
        "TableName": "vswe-sessions",
        "KeySchema": [
            {"AttributeName": "session_id", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "session_id", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
            {"AttributeName": "github_repo_full_name", "AttributeType": "S"},
            {"AttributeName": "github_issue_number", "AttributeType": "N"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "user_id-created_at-index",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "github_repo-issue-index",
                "KeySchema": [
                    {"AttributeName": "github_repo_full_name", "KeyType": "HASH"},
                    {"AttributeName": "github_issue_number", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    },
    # 2. vswe-messages
    {
        "TableName": "vswe-messages",
        "KeySchema": [
            {"AttributeName": "session_id", "KeyType": "HASH"},
            {"AttributeName": "message_id", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "session_id", "AttributeType": "S"},
            {"AttributeName": "message_id", "AttributeType": "S"},
        ],
    },
    # 3. vswe-config
    {
        "TableName": "vswe-config",
        "KeySchema": [
            {"AttributeName": "config_scope", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "config_scope", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    },
    # 4. vswe-jobs
    {
        "TableName": "vswe-jobs",
        "KeySchema": [
            {"AttributeName": "job_id", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "job_id", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "session_id", "AttributeType": "S"},
            {"AttributeName": "started_at", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "session_id-started_at-index",
                "KeySchema": [
                    {"AttributeName": "session_id", "KeyType": "HASH"},
                    {"AttributeName": "started_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    },
    # 5. vswe-checkpoints
    {
        "TableName": "vswe-checkpoints",
        "KeySchema": [
            {"AttributeName": "job_id", "KeyType": "HASH"},
            {"AttributeName": "checkpoint_id", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "job_id", "AttributeType": "S"},
            {"AttributeName": "checkpoint_id", "AttributeType": "S"},
        ],
    },
    # 6. vswe-costs
    {
        "TableName": "vswe-costs",
        "KeySchema": [
            {"AttributeName": "date", "KeyType": "HASH"},
            {"AttributeName": "cost_entry_id", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "date", "AttributeType": "S"},
            {"AttributeName": "cost_entry_id", "AttributeType": "S"},
            {"AttributeName": "category", "AttributeType": "S"},
            {"AttributeName": "session_id", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "category-date-index",
                "KeySchema": [
                    {"AttributeName": "category", "KeyType": "HASH"},
                    {"AttributeName": "date", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "session_id-created_at-index",
                "KeySchema": [
                    {"AttributeName": "session_id", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    },
    # 7. vswe-users
    {
        "TableName": "vswe-users",
        "KeySchema": [
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "github_login", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "github_login-index",
                "KeySchema": [
                    {"AttributeName": "github_login", "KeyType": "HASH"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    },
]

# Default provisioned throughput for local / dev table creation.
_DEFAULT_THROUGHPUT = {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}


# ---------------------------------------------------------------------------
# Resource factory
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_dynamodb_resource():
    """Return a shared boto3 DynamoDB *resource*.

    When ``settings.dynamodb_endpoint`` is set (local dev with DynamoDB Local
    or LocalStack), the resource is pointed at that endpoint.  Otherwise it
    uses the default AWS credential chain and region.
    """
    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.dynamodb_endpoint:
        kwargs["endpoint_url"] = settings.dynamodb_endpoint
    return boto3.resource("dynamodb", **kwargs)


def _get_table(table_name: str):
    """Return a ``Table`` object for the given table name."""
    return get_dynamodb_resource().Table(table_name)


# ---------------------------------------------------------------------------
# Async executor helper
# ---------------------------------------------------------------------------

async def _run(func, *args, **kwargs) -> Any:  # noqa: ANN401
    """Run a synchronous callable in the default thread-pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


# ---------------------------------------------------------------------------
# Table creation (for local dev / integration tests)
# ---------------------------------------------------------------------------

async def create_tables() -> list[str]:
    """Create all VSWE DynamoDB tables if they do not already exist.

    Returns a list of table names that were created (empty if all existed).
    """
    resource = get_dynamodb_resource()
    existing = await _run(lambda: [t.name for t in resource.tables.all()])
    created: list[str] = []

    for defn in _TABLE_DEFS:
        name = defn["TableName"]
        if name in existing:
            logger.debug("Table %s already exists — skipping.", name)
            continue

        create_kwargs: dict[str, Any] = {
            "TableName": name,
            "KeySchema": defn["KeySchema"],
            "AttributeDefinitions": defn["AttributeDefinitions"],
            "BillingMode": "PAY_PER_REQUEST",
        }

        if "GlobalSecondaryIndexes" in defn:
            create_kwargs["GlobalSecondaryIndexes"] = defn["GlobalSecondaryIndexes"]

        try:
            table = await _run(resource.create_table, **create_kwargs)
            await _run(table.wait_until_exists)
            logger.info("Created table %s.", name)
            created.append(name)
        except ClientError as exc:
            # ResourceInUseException means the table was created between our
            # check and the create call — safe to ignore.
            if exc.response["Error"]["Code"] == "ResourceInUseException":
                logger.debug("Table %s created concurrently — skipping.", name)
            else:
                raise

    return created


# ---------------------------------------------------------------------------
# Generic CRUD helpers
# ---------------------------------------------------------------------------

def _sanitize_for_dynamo(value: Any) -> Any:
    """Recursively convert floats to Decimal and strip None values for DynamoDB."""
    from decimal import Decimal as D

    if isinstance(value, float):
        return D(str(value))
    if isinstance(value, dict):
        return {k: _sanitize_for_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_sanitize_for_dynamo(item) for item in value]
    return value


async def put_item(table_name: str, item: dict[str, Any]) -> dict[str, Any]:
    """Put a single item into *table_name*.

    *item* should be a dict ready for DynamoDB (e.g. from
    ``model.to_dynamo_item()``).  Floats are automatically converted to
    Decimal.  Returns the raw DynamoDB response.
    """
    table = _get_table(table_name)
    safe_item = _sanitize_for_dynamo(item)
    return await _run(table.put_item, Item=safe_item)


async def get_item(
    table_name: str,
    key: dict[str, Any],
    *,
    consistent_read: bool = False,
) -> dict[str, Any] | None:
    """Fetch a single item by its primary key.

    Returns the item dict, or ``None`` if not found.
    """
    table = _get_table(table_name)
    kwargs: dict[str, Any] = {"Key": key}
    if consistent_read:
        kwargs["ConsistentRead"] = True

    response = await _run(table.get_item, **kwargs)
    return response.get("Item")


async def query_items(
    table_name: str,
    key_condition,  # boto3 Key condition expression
    *,
    index_name: str | None = None,
    filter_expression=None,
    scan_forward: bool = True,
    limit: int | None = None,
    exclusive_start_key: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Query items from *table_name* (or a GSI).

    Returns the full DynamoDB response dict containing ``Items``,
    ``Count``, ``ScannedCount``, and optionally ``LastEvaluatedKey``.
    """
    table = _get_table(table_name)
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": key_condition,
        "ScanIndexForward": scan_forward,
    }
    if index_name:
        kwargs["IndexName"] = index_name
    if filter_expression is not None:
        kwargs["FilterExpression"] = filter_expression
    if limit is not None:
        kwargs["Limit"] = limit
    if exclusive_start_key is not None:
        kwargs["ExclusiveStartKey"] = exclusive_start_key

    return await _run(table.query, **kwargs)


async def query_all_items(
    table_name: str,
    key_condition,
    *,
    index_name: str | None = None,
    filter_expression=None,
    scan_forward: bool = True,
) -> list[dict[str, Any]]:
    """Paginated query that returns **all** matching items.

    Use with caution on large result sets.
    """
    items: list[dict[str, Any]] = []
    last_key: dict[str, Any] | None = None

    while True:
        response = await query_items(
            table_name,
            key_condition,
            index_name=index_name,
            filter_expression=filter_expression,
            scan_forward=scan_forward,
            exclusive_start_key=last_key,
        )
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if last_key is None:
            break

    return items


async def update_item(
    table_name: str,
    key: dict[str, Any],
    update_expression: str,
    expression_attribute_values: dict[str, Any] | None = None,
    expression_attribute_names: dict[str, str] | None = None,
    *,
    condition_expression=None,
    return_values: str = "ALL_NEW",
) -> dict[str, Any]:
    """Update an item and return the (new) attributes.

    Wraps ``table.update_item`` with commonly used parameters.
    """
    table = _get_table(table_name)
    kwargs: dict[str, Any] = {
        "Key": key,
        "UpdateExpression": update_expression,
        "ReturnValues": return_values,
    }
    if expression_attribute_values:
        kwargs["ExpressionAttributeValues"] = _sanitize_for_dynamo(expression_attribute_values)
    if expression_attribute_names:
        kwargs["ExpressionAttributeNames"] = expression_attribute_names
    if condition_expression is not None:
        kwargs["ConditionExpression"] = condition_expression

    response = await _run(table.update_item, **kwargs)
    return response.get("Attributes", {})


async def delete_item(
    table_name: str,
    key: dict[str, Any],
    *,
    condition_expression=None,
) -> dict[str, Any]:
    """Delete an item by primary key.

    Returns the raw DynamoDB response.
    """
    table = _get_table(table_name)
    kwargs: dict[str, Any] = {"Key": key}
    if condition_expression is not None:
        kwargs["ConditionExpression"] = condition_expression

    return await _run(table.delete_item, **kwargs)


async def query_by_gsi(
    table_name: str,
    index_name: str,
    key_name: str,
    key_value: str,
    *,
    limit: int | None = None,
    scan_forward: bool = False,
) -> list[dict[str, Any]]:
    """Convenience: query a GSI by a single hash key.

    Returns a flat list of items (no pagination metadata).
    """
    response = await query_items(
        table_name,
        Key(key_name).eq(key_value),
        index_name=index_name,
        limit=limit,
        scan_forward=scan_forward,
    )
    return response.get("Items", [])


async def query_by_partition(
    table_name: str,
    key_name: str,
    key_value: str,
    *,
    limit: int | None = None,
    last_key: str | None = None,
    scan_forward: bool = True,
) -> tuple[list[dict[str, Any]], str | None]:
    """Convenience: query by partition key with simple string-based pagination.

    Returns ``(items, next_last_key)`` where *next_last_key* is a JSON-encoded
    string of the DynamoDB ``LastEvaluatedKey``, or ``None`` if no more pages.
    """
    import json as _json

    exclusive_start = _json.loads(last_key) if last_key else None
    response = await query_items(
        table_name,
        Key(key_name).eq(key_value),
        limit=limit,
        exclusive_start_key=exclusive_start,
        scan_forward=scan_forward,
    )
    items = response.get("Items", [])
    raw_last = response.get("LastEvaluatedKey")
    next_key = _json.dumps(raw_last) if raw_last else None
    return items, next_key


async def scan_table(
    table_name: str,
    *,
    filter_expression: str | None = None,
    expression_values: dict[str, Any] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Full table scan with optional filter.  Use sparingly.

    *filter_expression* is a string filter (e.g. ``"#d BETWEEN :s AND :e"``).
    """
    from boto3.dynamodb.conditions import Attr  # noqa: F811

    table = _get_table(table_name)

    kwargs: dict[str, Any] = {}
    if filter_expression and expression_values:
        kwargs["FilterExpression"] = filter_expression
        kwargs["ExpressionAttributeValues"] = expression_values
    if limit:
        kwargs["Limit"] = limit

    items: list[dict[str, Any]] = []
    last_key: dict[str, Any] | None = None

    while True:
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = await _run(table.scan, **kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if last_key is None:
            break

    return items


async def batch_write_items(
    table_name: str,
    items: list[dict[str, Any]],
) -> None:
    """Batch-write up to 25 items at a time using ``batch_writer``.

    Items should be dicts ready for DynamoDB.
    """
    table = _get_table(table_name)

    def _write():
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)

    await _run(_write)
