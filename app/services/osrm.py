"""OSRM service — travel time matrix for foot/car/bicycle profiles."""

from typing import Any, cast

import httpx

from app.config import settings

# Prefer using a shared AsyncClient created at app startup for connection reuse.
# The shared client is optional; fall back to short-lived clients when not present.
from app.http_client import client_instance


def _base_url(profile: str) -> str:
    urls: dict[str, str] = {
        "foot": settings.osrm_foot_url,
        "car": settings.osrm_car_url,
        "bicycle": settings.osrm_bicycle_url,
    }
    if profile not in urls:
        raise ValueError(
            f"Unknown OSRM profile: {profile!r}. Must be foot, car, or bicycle."
        )
    return urls[profile]


async def get_distance_matrix(
    coordinates: list[list[float]],
    profile: str = "foot",
) -> list[list[float]]:
    """
    Fetch a travel time matrix from OSRM for the given coordinates.

    Behavior improvements:
    - Validate input coordinates and raise clear ValueError on bad input.
    - Reuse a shared AsyncClient (if initialized) to reduce connection churn.
    - Handle common httpx errors and raise ValueError for OSRM error responses.
    - Keep previous semantics for unreachable pairs (penalty substitution).

    Args:
        coordinates: Sequence of (longitude, latitude) pairs. OSRM uses lon,lat order.
        profile: Transport mode — "foot", "car", or "bicycle".

    Returns:
        2D list where result[i][j] is travel time in seconds from coordinates[i] to coordinates[j].
        Unreachable pairs are replaced with a large penalty value.

    Raises:
        ValueError: On invalid input or OSRM error responses.
    """
    # Input validation
    if coordinates is None:
        raise ValueError("coordinates must be provided")
    if not isinstance(coordinates, list):
        raise ValueError("coordinates must be a list of [lon, lat] pairs")
    if len(coordinates) == 0:
        return []
    # Validate each coordinate pair
    for idx, c in enumerate(coordinates):
        if (
            not isinstance(c, (list, tuple))
            or len(c) != 2
            or not isinstance(c[0], (int, float))
            or not isinstance(c[1], (int, float))
        ):
            raise ValueError(
                f"coordinates[{idx}] must be [lon, lat] with numeric values"
            )

    if len(coordinates) == 1:
        return [[0.0]]

    coord_str: str = ";".join(f"{float(lon)},{float(lat)}" for lon, lat in coordinates)
    url: str = (
        f"{_base_url(profile)}/table/v1/{profile}/{coord_str}?annotations=duration"
    )

    # Try to use the shared client if available (initialized at app startup).
    shared_client = client_instance()
    resp_data: dict[str, Any] | None = None

    try:
        if shared_client is not None:
            response = await shared_client.get(url)
            response.raise_for_status()
            resp_data = response.json()
        else:
            # Fallback to short-lived client
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                resp_data = response.json()
    except httpx.HTTPStatusError as exc:
        # Surface OSRM's API message when available
        data: dict[str, Any] | None = None
        try:
            data = exc.response.json()
        except Exception:
            data = None
        msg: str = (data.get("message") if isinstance(data, dict) else None) or str(exc)
        raise ValueError(f"OSRM HTTP error: {msg}") from exc
    except httpx.RequestError as exc:
        raise ValueError(f"OSRM request error: {exc}") from exc

    if not isinstance(resp_data, dict):
        raise ValueError("OSRM returned non-JSON response")

    if resp_data.get("code") != "Ok":
        msg = resp_data.get("message", "Unknown OSRM error")
        raise ValueError(f"OSRM error: {msg}")

    raw_durations: Any = resp_data.get("durations")
    if raw_durations is None:
        raise ValueError("OSRM response missing 'durations' field")

    # Normalize and compute penalty
    durations: list[list[float | None]] = cast(list[list[float | None]], raw_durations)
    max_duration: float = 0.0
    for row in durations:
        for val in row:
            if val is not None:
                try:
                    max_duration = max(max_duration, float(val))
                except Exception:
                    continue

    penalty: float = max(max_duration * 2, 999_999.0)

    result: list[list[float]] = []
    for row in durations:
        result.append([(float(val) if val is not None else penalty) for val in row])

    return result


async def get_route_geometry(
    coordinates: list[list[float]],
    profile: str = "foot",
) -> list[dict[str, Any]]:
    """Fetch OSRM route geometry for a sequence of waypoints.

    Returns a list of leg dictionaries, each with:
      - ``geometry``: encoded polyline string
      - ``distance``: leg distance in meters
      - ``duration``: leg duration in seconds

    Returns legs with empty geometry when OSRM is unreachable; callers
    should check ``geometry`` before using the result.
    """
    if len(coordinates) < 2:
        return []

    coord_str = ";".join(f"{float(lon)},{float(lat)}" for lon, lat in coordinates)
    url = (
        f"{_base_url(profile)}/route/v1/{profile}/{coord_str}"
        "?geometries=polyline&overview=full&steps=false"
    )

    shared_client = client_instance()
    try:
        if shared_client is not None:
            response = await shared_client.get(url)
            response.raise_for_status()
            data = response.json()
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

        if data.get("code") != "Ok":
            raise ValueError(data.get("message", "OSRM error"))

        route = data.get("routes", [{}])[0]
        route_geometry = route.get("geometry", "")
        legs: list[dict[str, Any]] = []
        for leg in route.get("legs", []):
            legs.append(
                {
                    "geometry": route_geometry,
                    "distance": leg.get("distance", 0),
                    "duration": leg.get("duration", 0),
                }
            )
        return legs

    except (httpx.HTTPStatusError, httpx.RequestError, ValueError, KeyError):
        # Fallback: return empty geometries — callers check for empty geometry
        legs = []
        for _ in range(len(coordinates) - 1):
            legs.append({"geometry": "", "distance": 0, "duration": 0})
        return legs
