"""Opening hours resolution — OSM first, Google Places fallback."""

import logging

from app.services import google_places, overpass

logger = logging.getLogger(__name__)


async def resolve_opening_hours(
    lat: float, lon: float, name: str | None
) -> tuple[str | None, str | None]:
    """
    Try Overpass (OSM) first, then Google Places as fallback.

    Returns:
        (opening_hours_string, source) or (None, None).
    """
    try:
        result = await overpass.get_opening_hours(lat, lon, name)
        # Ensure result is a mapping and opening_hours is a non-empty string
        if isinstance(result, dict):
            opening = result.get("opening_hours")
            if isinstance(opening, str) and opening:
                return opening, "osm"
    except Exception:
        # Use repr-style logging so None appears clearly in logs
        logger.exception("Overpass lookup failed for %r", name)

    try:
        # Google Places expects a string name; if caller provided None, supply an empty string.
        hours = await google_places.get_opening_hours(lat, lon, name or "")
        if hours:
            return hours, "google"
    except Exception:
        logger.exception("Google Places lookup failed for %r", name)

    return None, None
