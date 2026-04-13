"""
Overpass API service — fetches opening hours from OpenStreetMap.

This module was refactored to:
- reuse a shared HTTP client when available (to reduce connection churn),
- add simple endpoint retries with exponential backoff, and
- provide a tiny in-memory cache (TTL-based) to reduce repeated Overpass queries.

Note: this is a lightweight improvement intended to reduce rate issues and
latency for common queries. The Overpass public instances still have usage
limits; consider more robust caching or a self-hosted Overpass instance for
heavy workloads.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

import httpx

from app.http_client import client_instance

logger = logging.getLogger(__name__)

# Multiple Overpass instances for reliability (public servers can be flaky).
OVERPASS_ENDPOINTS: list[str] = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Simple in-memory cache: key -> (timestamp_seconds, value)
# Key is (lat_rounded, lon_rounded, name_normalized, radius)
# TTL is in seconds
_CACHE_TTL: int = 60 * 60  # 1 hour
_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any] | None]] = {}

# Retry configuration
_MAX_RETRIES: int = 2  # number of additional endpoints/attempts
_BACKOFF_BASE: float = 0.15  # seconds (exponential backoff base)


def _cache_key(
    lat: float, lon: float, name: str | None, radius: int
) -> tuple[Any, ...]:
    # Round coordinates to 5 decimal places to avoid overly-brittle keys
    return (round(lat, 5), round(lon, 5), (name or "").lower().strip(), int(radius))


def _fetch_from_cache(
    lat: float, lon: float, name: str | None, radius: int
) -> dict[str, Any] | None:
    key = _cache_key(lat, lon, name, radius)
    entry = _cache.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


_CACHE_MAX_SIZE: int = 500  # evict when exceeded


def _evict_expired() -> None:
    """Remove all expired entries from the cache."""
    now = time.time()
    expired = [k for k, (ts, _) in _cache.items() if now - ts > _CACHE_TTL]
    for k in expired:
        del _cache[k]


def _store_in_cache(
    lat: float, lon: float, name: str | None, radius: int, value: dict[str, Any] | None
) -> None:
    key = _cache_key(lat, lon, name, radius)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_SIZE:
        _evict_expired()


async def _post_with_retries(
    endpoint: str, data: dict[str, str], client: httpx.AsyncClient | None
) -> dict[str, Any] | None:
    """
    POST data to the given endpoint using the provided client if available.
    Implements a tiny retry/backoff around transient network issues.
    Returns parsed JSON or None on failure.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            if client is not None:
                resp = await client.post(endpoint, data=data)
            else:
                # Create a short-lived client as fallback
                async with httpx.AsyncClient(timeout=30.0) as tmp_client:
                    resp = await tmp_client.post(endpoint, data=data)
            if resp.status_code != 200:
                # Treat non-200 as failure and try next attempt/endpoint
                last_exc = RuntimeError(f"Overpass returned {resp.status_code}")
                continue
            return resp.json()
        except (
            httpx.ConnectError,
            httpx.HTTPStatusError,
            httpx.ReadTimeout,
            httpx.RequestError,
        ) as e:
            last_exc = e
            backoff: float = _BACKOFF_BASE * (2**attempt)
            await asyncio.sleep(backoff)
            continue
        except Exception as e:
            last_exc = e
            await asyncio.sleep(_BACKOFF_BASE)
            continue
    logger.debug("Overpass POST failed after retries: %s", last_exc)
    return None


async def get_opening_hours(
    lat: float,
    lon: float,
    name: str | None = None,
    radius_m: int = 200,
) -> dict[str, Any] | None:
    """
    Query Overpass for the opening_hours of a POI near (lat, lon).

    Returns:
        {"name": str, "opening_hours": str} if found, or None.
    """
    # Check cache first
    cached = _fetch_from_cache(lat, lon, name, radius_m)
    if cached is not None:
        return cached

    # Build Overpass union query (up to 5 results).
    query: str = (
        f"[out:json][timeout:10];"
        f"("
        f'node(around:{radius_m},{lat},{lon})["opening_hours"];'
        f'way(around:{radius_m},{lat},{lon})["opening_hours"];'
        f");"
        f"out tags 5;"
    )

    # Try shared client first (created at app startup). If unavailable, fall back to short-lived client.
    client = client_instance()

    for endpoint in OVERPASS_ENDPOINTS:
        data = await _post_with_retries(endpoint, {"data": query}, client)
        if not data:
            # try next endpoint
            continue
        elements: list[dict[str, Any]] = data.get("elements", [])
        if not elements:
            _store_in_cache(lat, lon, name, radius_m, None)
            return None
        result = _best_match(elements, name)
        _store_in_cache(lat, lon, name, radius_m, result)
        return result

    # All endpoints failed
    _store_in_cache(lat, lon, name, radius_m, None)
    return None


async def get_opening_hours_batch(
    locations: list[dict[str, Any]],
    radius_m: int = 200,
) -> list[dict[str, Any] | None]:
    """
    Fetch opening hours for multiple locations in a single Overpass query.

    Args:
        locations: List of {"lat": float, "lon": float, "name": str}.
        radius_m: Search radius in meters.

    Returns:
        List of {"name": str, "opening_hours": str} or None, one per location.
    """
    if not locations:
        return []

    # First try to satisfy from cache
    results: list[dict[str, Any] | None] = []
    to_query: list[dict[str, Any]] = []
    to_query_indices: list[int] = []
    for i, loc in enumerate(locations):
        cached = _fetch_from_cache(loc["lat"], loc["lon"], loc.get("name"), radius_m)
        if cached is not None:
            results.append(cached)
        else:
            # Placeholder; will fill after query
            results.append(None)
            to_query.append(loc)
            to_query_indices.append(i)

    if not to_query:
        return results

    # Build union query for the remaining locations
    union_parts: list[str] = []
    for loc in to_query:
        lat, lon = loc["lat"], loc["lon"]
        union_parts.append(f'node(around:{radius_m},{lat},{lon})["opening_hours"];')
        union_parts.append(f'way(around:{radius_m},{lat},{lon})["opening_hours"];')

    query: str = f"[out:json][timeout:25];({''.join(union_parts)});out tags;"

    client = client_instance()
    elements: list[dict[str, Any]] | None = None
    # Try endpoints with retries
    for endpoint in OVERPASS_ENDPOINTS:
        data = await _post_with_retries(endpoint, {"data": query}, client)
        if data is None:
            continue
        elements = data.get("elements", [])
        break

    if elements is None:
        # mark all missing entries as None in cache and return
        for i in to_query_indices:
            _store_in_cache(
                locations[i]["lat"],
                locations[i]["lon"],
                locations[i].get("name"),
                radius_m,
                None,
            )
        return results

    # Match elements back to requested locations
    for idx, loc in zip(to_query_indices, to_query):
        nearby = _find_nearby_elements(
            elements, loc["lat"], loc["lon"], radius_m, loc.get("name")
        )
        results[idx] = nearby
        _store_in_cache(loc["lat"], loc["lon"], loc.get("name"), radius_m, nearby)

    return results


def _best_match(
    elements: list[dict[str, Any]], name: str | None
) -> dict[str, Any] | None:
    """Pick the best matching element from Overpass results."""
    if not elements:
        return None

    if name:
        name_lower: str = name.lower()
        for el in elements:
            tags: dict[str, Any] = el.get("tags", {})
            el_name: str = tags.get("name", "")
            if el_name and name_lower in el_name.lower():
                return {
                    "name": el_name,
                    "opening_hours": tags["opening_hours"],
                }

    # No name match — return the first result
    tags = elements[0].get("tags", {})
    return {
        "name": tags.get("name", "Unknown"),
        "opening_hours": tags["opening_hours"],
    }


def _find_nearby_elements(
    elements: list[dict[str, Any]],
    lat: float,
    lon: float,
    radius_m: int,
    name: str | None,
) -> dict[str, Any] | None:
    """Find the best matching element near (lat, lon) from a list."""
    # Filter to elements that have coordinates and are within rough radius.
    # Overpass nodes have lat/lon directly; ways don't (we requested tags only).
    # For ways without coordinates, we include them but can't distance-filter.
    candidates: list[tuple[float, dict[str, Any]]] = []
    for el in elements:
        tags: dict[str, Any] = el.get("tags", {})
        if "opening_hours" not in tags:
            continue

        el_lat: float | None = el.get("lat")
        el_lon: float | None = el.get("lon")
        if el_lat is not None and el_lon is not None:
            # Rough distance check (1 degree ≈ 111km)
            dlat: float = abs(el_lat - lat) * 111_000
            dlon: float = abs(el_lon - lon) * 111_000 * math.cos(math.radians(lat))
            dist: float = (dlat**2 + dlon**2) ** 0.5
            if dist <= radius_m * 1.5:  # generous margin
                candidates.append((dist, el))
        else:
            # Way without coords — include with large distance
            candidates.append((radius_m, el))

    if not candidates:
        return None

    # Sort by distance
    candidates.sort(key=lambda x: x[0])

    # Try name matching first
    if name:
        name_lower = name.lower()
        for _, el in candidates:
            el_name = el.get("tags", {}).get("name", "")
            if el_name and name_lower in el_name.lower():
                return {
                    "name": el_name,
                    "opening_hours": el["tags"]["opening_hours"],
                }

    # Return closest
    _, el = candidates[0]
    tags = el.get("tags", {})
    return {
        "name": tags.get("name", "Unknown"),
        "opening_hours": tags["opening_hours"],
    }
