"""Google Places API service — fetches opening hours via Text Search.

This is an *optional* fallback used only when Overpass (OSM) returns no
opening hours data. It requires GOOGLE_PLACES_API_KEY to be set in .env;
if the key is absent the function returns None immediately and the app
continues without it.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings
from app.http_client import client_instance

logger = logging.getLogger(__name__)


async def get_opening_hours(lat: float, lon: float, name: str) -> str | None:
    """
    Use Google Places Text Search (New) to find opening hours for a place.

    Returns the opening_hours weekday_text joined as a string, or None.

    Notes:
    - This function uses the shared HTTP client when available to reduce
      connection overhead.
    - It implements conservative retries with exponential backoff for rate
      limiting (429). If Google returns 403 (forbidden), we log and return
      None immediately (likely an API key issue).
    - All failures return None so callers gracefully fall back to other sources.
    """
    if not settings.google_places_api_key:
        return None

    url: str = "https://places.googleapis.com/v1/places:searchText"
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.google_places_api_key,
        "X-Goog-FieldMask": "places.currentOpeningHours,places.regularOpeningHours",
    }
    body: dict[str, Any] = {
        "textQuery": name,
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": 500.0,
            }
        },
        "maxResultCount": 1,
    }

    max_attempts: int = 3
    backoff_base: float = 0.5

    client = client_instance()

    for attempt in range(1, max_attempts + 1):
        try:
            if client is not None:
                resp = await client.post(url, json=body, headers=headers)
            else:
                # Short-lived client fallback (rare when lifespan init not used)
                async with httpx.AsyncClient(timeout=10.0) as tmp:
                    resp = await tmp.post(url, json=body, headers=headers)

            # Handle common status codes explicitly
            if resp.status_code == 403:
                logger.warning(
                    "Google Places returned 403 Forbidden for %r; check API key", name
                )
                return None
            if resp.status_code == 429:
                # Rate limited — backoff and retry up to max_attempts
                if attempt < max_attempts:
                    wait: float = backoff_base * (2 ** (attempt - 1))
                    logger.warning(
                        "Google Places rate-limited (429) for %r — retrying in %.1fs (attempt %d/%d)",
                        name,
                        wait,
                        attempt,
                        max_attempts,
                    )
                    await asyncio.sleep(wait)
                    continue
                else:
                    logger.error(
                        "Google Places rate-limited (429) and exhausted retries for %r",
                        name,
                    )
                    return None

            # Raise for other 4xx/5xx so we hit request-exception handler
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

            places: list[dict[str, Any]] = data.get("places", [])
            if not places:
                return None

            place: dict[str, Any] = places[0]
            hours: dict[str, Any] | None = place.get(
                "regularOpeningHours"
            ) or place.get("currentOpeningHours")
            if not hours:
                return None

            # API uses different keys in different payloads; accept both
            weekday_text: list[str] | None = hours.get(
                "weekdayDescriptions"
            ) or hours.get("weekday_text")
            if weekday_text:
                return "; ".join(weekday_text)

            return None

        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning(
                "Google Places request error for %r: %s (attempt %d/%d)",
                name,
                e,
                attempt,
                max_attempts,
            )
            if attempt < max_attempts:
                await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            return None
        except Exception:
            logger.exception("Google Places API unexpected error for %r", name)
            return None

    return None
