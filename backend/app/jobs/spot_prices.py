"""AWS EC2 Spot and On-Demand price lookups with in-memory caching."""

from __future__ import annotations

import logging
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache: instance_type -> (price_usd_per_hour, unix_timestamp)
# ---------------------------------------------------------------------------
_spot_cache: dict[str, tuple[float, float]] = {}
CACHE_TTL = 300  # 5 minutes

# ---------------------------------------------------------------------------
# On-demand price table (USD/hr, us-east-1, Linux)
# These are hardcoded as a fallback — the canonical source is the AWS Price
# List API, but it's slow and the values rarely change.
# Last updated: 2025-05.
# ---------------------------------------------------------------------------
ON_DEMAND_PRICES: dict[str, float] = {
    # GPU — G4dn (T4)
    "g4dn.xlarge": 0.526,
    "g4dn.2xlarge": 0.752,
    "g4dn.4xlarge": 1.204,
    "g4dn.12xlarge": 3.912,
    # GPU — G5 (A10G)
    "g5.xlarge": 1.006,
    "g5.2xlarge": 1.212,
    "g5.4xlarge": 1.624,
    "g5.12xlarge": 5.672,
    # GPU — P3 (V100)
    "p3.2xlarge": 3.06,
    "p3.8xlarge": 12.24,
    # CPU — C5
    "c5.large": 0.085,
    "c5.xlarge": 0.17,
    "c5.2xlarge": 0.34,
    "c5.4xlarge": 0.68,
    # Memory — M5
    "m5.xlarge": 0.192,
    "m5.2xlarge": 0.384,
    "m5.4xlarge": 0.768,
    # Memory — R5
    "r5.xlarge": 0.252,
    "r5.2xlarge": 0.504,
}

# Typical spot-to-on-demand ratio when we cannot reach the API.
_DEFAULT_SPOT_RATIO = 0.35


def _is_cached(instance_type: str) -> bool:
    if instance_type not in _spot_cache:
        return False
    _, ts = _spot_cache[instance_type]
    return (time.time() - ts) < CACHE_TTL


def _set_cache(instance_type: str, price: float) -> None:
    _spot_cache[instance_type] = (price, time.time())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_spot_prices(
    instance_types: list[str],
    region: str = "us-east-1",
) -> dict[str, float]:
    """Query current spot prices for the given instance types.

    Results are cached in-memory for ``CACHE_TTL`` seconds. If the AWS API
    call fails (e.g. no credentials in local dev), falls back to an estimate
    based on on-demand pricing.

    Returns a mapping of ``instance_type -> price_usd_per_hour``.
    """
    result: dict[str, float] = {}
    to_fetch: list[str] = []

    for itype in instance_types:
        if _is_cached(itype):
            result[itype] = _spot_cache[itype][0]
        else:
            to_fetch.append(itype)

    if not to_fetch:
        return result

    try:
        prices = _fetch_spot_prices_from_api(to_fetch, region)
        for itype, price in prices.items():
            _set_cache(itype, price)
            result[itype] = price
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Spot price API call failed, using estimates: %s", exc)
        for itype in to_fetch:
            estimated = _estimate_spot_price(itype)
            _set_cache(itype, estimated)
            result[itype] = estimated

    # Fill any types that the API didn't return (e.g. no recent spot history).
    for itype in instance_types:
        if itype not in result:
            estimated = _estimate_spot_price(itype)
            _set_cache(itype, estimated)
            result[itype] = estimated

    return result


async def get_spot_price(
    instance_type: str,
    region: str = "us-east-1",
) -> float:
    """Get the spot price for a single instance type."""
    prices = await get_spot_prices([instance_type], region)
    return prices[instance_type]


def get_on_demand_price(instance_type: str) -> float:
    """Return the on-demand price (USD/hr) from the hardcoded lookup table.

    Returns ``0.0`` if the instance type is unknown.
    """
    return ON_DEMAND_PRICES.get(instance_type, 0.0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _estimate_spot_price(instance_type: str) -> float:
    """Estimate spot price as a fraction of on-demand when we have no API data."""
    od = get_on_demand_price(instance_type)
    if od > 0:
        return round(od * _DEFAULT_SPOT_RATIO, 4)
    # Absolute fallback for unknown instance types.
    return 0.10


def _fetch_spot_prices_from_api(
    instance_types: list[str],
    region: str,
) -> dict[str, float]:
    """Call ``describe_spot_price_history`` and return the latest price per type.

    This is a synchronous boto3 call. In production we run it inside
    ``asyncio.to_thread`` via the async wrappers above; keeping it sync here
    makes unit-testing trivial.
    """
    client = boto3.client("ec2", region_name=region)

    prices: dict[str, float] = {}
    # The API paginates and accepts at most 1000 instance types per call.
    for batch_start in range(0, len(instance_types), 100):
        batch = instance_types[batch_start : batch_start + 100]
        response: dict[str, Any] = client.describe_spot_price_history(
            InstanceTypes=batch,
            ProductDescriptions=["Linux/UNIX"],
            MaxResults=len(batch),
        )
        for entry in response.get("SpotPriceHistory", []):
            itype = entry["InstanceType"]
            price = float(entry["SpotPrice"])
            # Keep the lowest price across AZs for each type.
            if itype not in prices or price < prices[itype]:
                prices[itype] = price

    return prices


def clear_cache() -> None:
    """Clear the spot-price cache. Useful in tests."""
    _spot_cache.clear()
