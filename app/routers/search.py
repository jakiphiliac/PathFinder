"""Search endpoint — POI discovery via Overpass API with Nominatim fallback."""

import logging
import re
from typing import Any

import httpx
from fastapi import APIRouter, Query

from app.http_client import client_instance
from app.services.overpass import OVERPASS_ENDPOINTS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

NOMINATIM_HEADERS: dict[str, str] = {"User-Agent": "PathFinder/2.0"}


@router.get("/geocode")
async def geocode(q: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
    """Geocode a place name using Nominatim. Returns up to 5 results."""
    client = client_instance()
    if client is not None:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 5, "addressdetails": 1},
            headers=NOMINATIM_HEADERS,
        )
    else:
        async with httpx.AsyncClient(timeout=10.0, headers=NOMINATIM_HEADERS) as tmp:
            resp = await tmp.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q, "format": "json", "limit": 5, "addressdetails": 1},
            )
    resp.raise_for_status()
    data = resp.json()

    return [
        {
            "name": item.get("display_name", ""),
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "category": item.get("type"),
            "opening_hours": None,
        }
        for item in data
    ]


@router.get("/search")
async def search_pois(
    q: str = Query(..., min_length=1, description="Search query"),
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    radius: int = Query(2000, description="Search radius in meters"),
) -> list[dict[str, Any]]:
    """Search OSM for POIs near location. Tries Overpass first, falls back to Nominatim."""
    results = await _search_overpass(q, lat, lon, radius)
    if results is not None:
        return results

    logger.warning(
        "All Overpass endpoints failed for %r — falling back to Nominatim", q
    )
    return await _search_nominatim(q, lat, lon)


async def _search_overpass(
    q: str, lat: float, lon: float, radius: int
) -> list[dict[str, Any]] | None:
    """Query Overpass for named POIs near location. Returns None if all endpoints fail."""
    safe_q: str = re.escape(q)
    query: str = (
        f"[out:json][timeout:3];"
        f"("
        f'node(around:{radius},{lat},{lon})["name"~"{safe_q}",i];'
        f'way(around:{radius},{lat},{lon})["name"~"{safe_q}",i];'
        f");"
        f"out center tags 10;"
    )

    client = client_instance()
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            if client is not None:
                resp = await client.post(endpoint, data={"data": query}, timeout=4.0)
            else:
                async with httpx.AsyncClient(timeout=4.0) as tmp:
                    resp = await tmp.post(endpoint, data={"data": query})
            if resp.status_code != 200:
                continue
            elements: list[dict[str, Any]] = resp.json().get("elements", [])
            return _format_overpass_results(elements)
        except Exception:
            logger.warning("Overpass endpoint %s failed", endpoint)
            continue

    return None


async def _search_nominatim(q: str, lat: float, lon: float) -> list[dict[str, Any]]:
    """Geocode query via Nominatim and return results in the same shape as Overpass results."""
    try:
        client = client_instance()
        nominatim_params = {
            "q": q,
            "format": "json",
            "limit": 10,
            "addressdetails": 0,
            "extratags": 1,
            "viewbox": f"{lon - 0.1},{lat + 0.1},{lon + 0.1},{lat - 0.1}",
            "bounded": 1,
        }
        if client is not None:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params=nominatim_params,
                headers=NOMINATIM_HEADERS,
                timeout=5.0,
            )
        else:
            async with httpx.AsyncClient(timeout=5.0, headers=NOMINATIM_HEADERS) as tmp:
                resp = await tmp.get(
                    "https://nominatim.openstreetmap.org/search",
                    params=nominatim_params,
                )
        resp.raise_for_status()
        data: list[dict[str, Any]] = resp.json()
    except Exception:
        logger.exception("Nominatim fallback failed for %r", q)
        return []

    results: list[dict[str, Any]] = []
    for item in data:
        extra: dict[str, Any] = item.get("extratags") or {}
        results.append(
            {
                "name": item.get("display_name", q),
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "category": item.get("type"),
                "opening_hours": extra.get("opening_hours"),
            }
        )
    return results


def _format_overpass_results(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Overpass elements to a clean response format."""
    results: list[dict[str, Any]] = []
    for el in elements:
        tags: dict[str, Any] = el.get("tags", {})
        name: str | None = tags.get("name")
        if not name:
            continue

        # Get coordinates — nodes have lat/lon directly, ways use center
        el_lat: float | None = el.get("lat") or (el.get("center", {}).get("lat"))
        el_lon: float | None = el.get("lon") or (el.get("center", {}).get("lon"))
        if el_lat is None or el_lon is None:
            continue

        # Determine category from tags
        category: str | None = None
        for key in ("tourism", "amenity", "leisure", "historic", "shop"):
            if key in tags:
                category = f"{key}:{tags[key]}"
                break

        results.append(
            {
                "name": name,
                "lat": el_lat,
                "lon": el_lon,
                "category": category,
                "opening_hours": tags.get("opening_hours"),
            }
        )
    return results
