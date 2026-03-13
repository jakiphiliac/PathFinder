"""
OSRM (Open Source Routing Machine) service for computing walking time matrices.

Uses the public demo at router.project-osrm.org with the foot profile.
Coordinates must be in (lon, lat) order.
"""

import httpx

OSRM_BASE_URL = "https://router.project-osrm.org"


async def get_distance_matrix(coordinates: list[tuple[float, float]]) -> list[list[float]]:
    """
    Fetch a travel time matrix from OSRM for the given coordinates.

    Args:
        coordinates: List of (longitude, latitude) tuples. OSRM uses lon,lat order.

    Returns:
        2D list where result[i][j] is travel time in seconds from coordinates[i] to coordinates[j].
        Unreachable pairs are replaced with 999999 (penalty value for the solver).

    Raises:
        httpx.HTTPStatusError: On 429 (rate limit) or other HTTP errors.
    """
    if not coordinates:
        return []

    if len(coordinates) == 1:
        return [[0]]

    # Build coordinate string: lon1,lat1;lon2,lat2;...
    coord_str = ";".join(f"{lon},{lat}" for lon, lat in coordinates)
    url = f"{OSRM_BASE_URL}/table/v1/foot/{coord_str}?annotations=duration"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    if data.get("code") != "Ok":
        msg = data.get("message", "Unknown OSRM error")
        raise ValueError(f"OSRM error: {msg}")

    durations = data.get("durations")
    if durations is None:
        raise ValueError("OSRM response missing 'durations' field")

    # Replace null (unreachable pairs) with a large penalty so the solver can still produce a route.
    # 999999 seconds (~11.5 days) ensures unreachable pairs are avoided when possible.
    max_duration = 0.0
    for row in durations:
        for val in row:
            if val is not None:
                max_duration = max(max_duration, val)

    penalty = max(max_duration * 2, 999999)

    result: list[list[float]] = []
    for row in durations:
        result.append([(val if val is not None else penalty) for val in row])

    return result
