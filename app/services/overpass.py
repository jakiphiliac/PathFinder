"""
Overpass API service — fetches opening hours from OpenStreetMap.

Queries the Overpass API to find POIs near given coordinates that have
an `opening_hours` tag. Falls back across multiple Overpass instances
for reliability.
"""

import httpx

# Multiple Overpass instances for reliability (public servers can be flaky).
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


async def get_opening_hours(
    lat: float,
    lon: float,
    name: str | None = None,
    radius_m: int = 200,
) -> dict | None:
    """
    Query Overpass for the opening_hours of a POI near (lat, lon).

    Args:
        lat: Latitude.
        lon: Longitude.
        name: Optional place name for better matching.
        radius_m: Search radius in meters.

    Returns:
        {"name": str, "opening_hours": str} if found, or None.
    """
    # Search for nodes and ways with opening_hours within radius.
    # Request up to 5 results so we can pick the best name match.
    query = (
        f"[out:json][timeout:10];"
        f"("
        f'node(around:{radius_m},{lat},{lon})["opening_hours"];'
        f'way(around:{radius_m},{lat},{lon})["opening_hours"];'
        f");"
        f"out tags 5;"
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                resp = await client.post(endpoint, data={"data": query})
                if resp.status_code != 200:
                    continue
                data = resp.json()
                elements = data.get("elements", [])
                if not elements:
                    return None
                return _best_match(elements, name)
            except Exception:
                continue

    # All endpoints failed
    return None


async def get_opening_hours_batch(
    locations: list[dict],
    radius_m: int = 200,
) -> list[dict | None]:
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

    # Build a union query: for each location, search nearby nodes/ways.
    # We tag each sub-query result by using the location index.
    # Unfortunately Overpass doesn't support grouping by input, so we
    # query all at once and then match results back to locations by proximity.
    union_parts = []
    for loc in locations:
        lat, lon = loc["lat"], loc["lon"]
        union_parts.append(f'node(around:{radius_m},{lat},{lon})["opening_hours"];')
        union_parts.append(f'way(around:{radius_m},{lat},{lon})["opening_hours"];')

    query = f"[out:json][timeout:25];({''.join(union_parts)});out tags;"

    elements = await _query_overpass(query)
    if elements is None:
        return [None] * len(locations)

    # Match elements back to locations by proximity
    results: list[dict | None] = []
    for loc in locations:
        nearby = _find_nearby_elements(
            elements, loc["lat"], loc["lon"], radius_m, loc.get("name")
        )
        results.append(nearby)

    return results


async def _query_overpass(query: str) -> list[dict] | None:
    """Try each Overpass endpoint, return elements list or None."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                resp = await client.post(endpoint, data={"data": query})
                if resp.status_code != 200:
                    continue
                data = resp.json()
                return data.get("elements", [])
            except Exception:
                continue
    return None


def _best_match(elements: list[dict], name: str | None) -> dict | None:
    """Pick the best matching element from Overpass results."""
    if not elements:
        return None

    if name:
        name_lower = name.lower()
        for el in elements:
            tags = el.get("tags", {})
            el_name = tags.get("name", "")
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
    elements: list[dict],
    lat: float,
    lon: float,
    radius_m: int,
    name: str | None,
) -> dict | None:
    """Find the best matching element near (lat, lon) from a list."""
    # Filter to elements that have coordinates and are within rough radius.
    # Overpass nodes have lat/lon directly; ways don't (we requested tags only).
    # For ways without coordinates, we include them but can't distance-filter.
    candidates = []
    for el in elements:
        tags = el.get("tags", {})
        if "opening_hours" not in tags:
            continue

        el_lat = el.get("lat")
        el_lon = el.get("lon")
        if el_lat is not None and el_lon is not None:
            # Rough distance check (1 degree ≈ 111km)
            dlat = abs(el_lat - lat) * 111_000
            dlon = abs(el_lon - lon) * 111_000 * 0.65  # cos(51°) ≈ 0.63
            dist = (dlat**2 + dlon**2) ** 0.5
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
