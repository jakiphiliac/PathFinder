"""
Nominatim (OpenStreetMap) geocoding service.

Uses the public Nominatim instance. Respect the usage policy:
- Identify the application with a custom User-Agent.
- Do not exceed 1 request per second (handled by the caller).
"""

from collections.abc import Mapping
from typing import Any

import httpx

NOMINATIM_BASE_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "TravelRouteOptimizerThesis/1.0 (your-email-or-url)"

    
async def geocode_place(
    place: str,
    destination: str | None = None,
) -> dict[str, Any] | None:
    """
    Geocode a single place name with Nominatim.

    Args:
        place: Free-text place name, e.g. "Big Ben".
        destination: Optional destination context (city/country),
            e.g. "London, UK" or "Sydney, Australia".

    Returns:
        Dict with keys: name, lat, lon, display_name; or None if not found.
    """
    if not place.strip():
        return None

    # Bias results to the destination by appending it to the query.
    query = place if not destination or not destination.strip() else f"{place}, {destination}"

    async with httpx.AsyncClient(
        timeout=20.0,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(
            NOMINATIM_BASE_URL,
            params={"q": query, "format": "json", "limit": 1},
        )
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list) or not data:
        return None

    first: Mapping[str, Any] = data[0]
    try:
        lat = float(first["lat"])
        lon = float(first["lon"])
    except (KeyError, TypeError, ValueError):
        return None

    return {
        "name": place,
        "lat": lat,
        "lon": lon,
        "display_name": str(first.get("display_name", place)),
    }

