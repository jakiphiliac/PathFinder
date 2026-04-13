"""Feasibility endpoint."""

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import get_db
from app.engine.feasibility import calculate_feasibility
from app.models import FeasibilityResponse, FeasibilityResult
from app.services.osrm import get_distance_matrix

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@dataclass
class FeasibilityContext:
    """Intermediate data produced during feasibility computation.

    Exposed so callers (e.g. /next endpoint) can reuse the trip lookup,
    parsed times, place list, and OSRM matrix without re-fetching.
    """

    places: list[dict[str, Any]]
    matrix: list[list[float]]
    current_time: datetime
    trip_end_dt: datetime
    trip_date: date
    endpoint_idx: int
    place_names: dict[int, str] = field(default_factory=dict)
    place_priorities: dict[int, str] = field(default_factory=dict)


_SPEED_MPS: dict[str, float] = {
    "foot": 1.4,  # ~5 km/h walking
    "bicycle": 4.2,  # ~15 km/h cycling
    "car": 8.3,  # ~30 km/h urban driving
}


def _haversine_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in meters between two lon/lat points."""
    r = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _haversine_matrix(coords: list[list[float]], profile: str) -> list[list[float]]:
    """Build a travel-time matrix (seconds) from straight-line distances."""
    speed = _SPEED_MPS.get(profile, 1.4)
    detour = 1.4  # straight-line to road-network multiplier
    n = len(coords)
    matrix: list[list[float]] = []
    for i in range(n):
        row: list[float] = []
        for j in range(n):
            if i == j:
                row.append(0.0)
            else:
                d = _haversine_distance_m(
                    coords[i][0], coords[i][1], coords[j][0], coords[j][1]
                )
                row.append(d * detour / speed)
        matrix.append(row)
    return matrix


async def compute_feasibility(
    db: aiosqlite.Connection,
    trip_id: str,
    lat: float | None = None,
    lon: float | None = None,
    time_override: str | None = None,
) -> tuple[FeasibilityResponse, FeasibilityContext]:
    """Shared feasibility computation.

    Returns:
        (FeasibilityResponse, FeasibilityContext)
    """
    cursor = await db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
    trip_row = await cursor.fetchone()
    if not trip_row:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip: dict[str, Any] = dict(trip_row)  # type: ignore[arg-type]

    cur_lat: float = lat if lat is not None else trip["start_lat"]
    cur_lon: float = lon if lon is not None else trip["start_lon"]

    trip_date: date = date.fromisoformat(trip["date"])
    trip_tz_name: str = trip.get("timezone") or "UTC"
    try:
        trip_tz: ZoneInfo | timezone = ZoneInfo(trip_tz_name)
    except (KeyError, Exception):
        trip_tz = timezone.utc

    # Interpret stored times in the trip's local timezone, then convert to UTC
    end_h, end_m = trip["end_time"].split(":")
    trip_end_dt: datetime = datetime.combine(
        trip_date, time(int(end_h), int(end_m)), tzinfo=trip_tz
    ).astimezone(timezone.utc)

    if time_override:
        t_h, t_m = time_override.split(":")
        current_time: datetime = datetime.combine(
            trip_date, time(int(t_h), int(t_m)), tzinfo=trip_tz
        ).astimezone(timezone.utc)
    else:
        current_time = datetime.now(timezone.utc)

    remaining_minutes: float = max(0, (trip_end_dt - current_time).total_seconds() / 60)

    cursor = await db.execute(
        "SELECT * FROM places WHERE trip_id = ? AND status = 'pending' ORDER BY id",
        (trip_id,),
    )
    rows = await cursor.fetchall()
    places: list[dict[str, Any]] = [dict(r) for r in rows]  # type: ignore[arg-type]

    place_names: dict[int, str] = {p["id"]: p["name"] for p in places}
    place_priorities: dict[int, str] = {
        p["id"]: p.get("priority", "want") for p in places
    }

    if not places:
        ctx = FeasibilityContext(
            places=places,
            matrix=[],
            current_time=current_time,
            trip_end_dt=trip_end_dt,
            trip_date=trip_date,
            endpoint_idx=0,
            place_names=place_names,
            place_priorities=place_priorities,
        )
        return (
            FeasibilityResponse(
                current_time=current_time.isoformat(),
                remaining_minutes=round(remaining_minutes, 1),
                places=[],
            ),
            ctx,
        )

    coords: list[list[float]] = [[cur_lon, cur_lat]]
    for p in places:
        coords.append([p["lon"], p["lat"]])
    coords.append([trip["end_lon"], trip["end_lat"]])

    try:
        matrix: list[list[float]] = await get_distance_matrix(
            coords, trip["transport_mode"]
        )
    except (ValueError, Exception):
        logger.warning(
            "OSRM unreachable for trip %s — falling back to straight-line estimates",
            trip_id,
        )
        matrix = _haversine_matrix(coords, trip["transport_mode"])

    endpoint_idx: int = len(places) + 1

    results: list[FeasibilityResult] = []
    for i, place in enumerate(places):
        place_idx: int = i + 1
        travel_to_place: float = matrix[0][place_idx]
        travel_to_endpoint: float = matrix[place_idx][endpoint_idx]

        result: dict[str, Any] = calculate_feasibility(
            place=place,
            travel_to_place_seconds=travel_to_place,
            travel_to_endpoint_seconds=travel_to_endpoint,
            current_time=current_time,
            trip_end_time=trip_end_dt,
            trip_date=trip_date,
            trip_timezone=trip.get("timezone"),
        )
        results.append(FeasibilityResult(**result))

    ctx = FeasibilityContext(
        places=places,
        matrix=matrix,
        current_time=current_time,
        trip_end_dt=trip_end_dt,
        trip_date=trip_date,
        endpoint_idx=endpoint_idx,
        place_names=place_names,
        place_priorities=place_priorities,
    )
    return (
        FeasibilityResponse(
            current_time=current_time.isoformat(),
            remaining_minutes=round(remaining_minutes, 1),
            places=results,
        ),
        ctx,
    )


@router.get("/trips/{trip_id}/feasibility", response_model=FeasibilityResponse)
async def get_feasibility(
    trip_id: str,
    lat: float | None = Query(None),
    lon: float | None = Query(None),
    time_override: str | None = Query(None, alias="time"),
    db: aiosqlite.Connection = Depends(get_db),
) -> FeasibilityResponse:
    """
    Compute feasibility for all pending places in a trip.

    Query params:
        lat, lon: Current position. If not provided, uses trip start location.
        time: Optional ISO time override for testing (e.g. "14:30").
    """
    response, _ = await compute_feasibility(db, trip_id, lat, lon, time_override)
    return response
