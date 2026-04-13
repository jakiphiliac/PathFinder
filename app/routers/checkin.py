"""Check-in endpoint: arrived / done / skipped."""

import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.db import get_db
from app.http_client import client_instance
from app.models import CheckinRequest, CheckinResponse, TrajectorySegment
from app.services.osrm import get_route_geometry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Valid state transitions: current_status -> allowed actions
VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"arrived", "skipped"},
    "visiting": {"done", "skipped"},
}


@router.post("/trips/{trip_id}/checkin", response_model=CheckinResponse)
async def checkin(
    trip_id: str,
    body: CheckinRequest,
    db: aiosqlite.Connection = Depends(get_db),
) -> CheckinResponse:
    """
    Check in at a place.

    Actions:
        arrived: Mark place as currently visiting (sets arrived_at).
        done: Mark place as done (sets departed_at). Must be visiting first.
        skipped: Mark place as skipped. Can be pending or visiting.
    """
    # Verify trip exists
    cursor = await db.execute("SELECT id FROM trips WHERE id = ?", (trip_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Trip not found")

    # Get place
    cursor = await db.execute(
        "SELECT * FROM places WHERE id = ? AND trip_id = ?",
        (body.place_id, trip_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Place not found")

    place: dict[str, Any] = dict(row)  # type: ignore[arg-type]
    current_status: str = place["status"]
    action: str = body.action

    # Validate transition
    allowed: set[str] = VALID_TRANSITIONS.get(current_status, set())
    if action not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot '{action}' a place that is '{current_status}'",
        )

    now: str = datetime.now(timezone.utc).isoformat()
    trajectory_segment: TrajectorySegment | None = None

    if action == "arrived":
        _ = await db.execute(
            "UPDATE places SET status = 'visiting', arrived_at = ? WHERE id = ?",
            (now, body.place_id),
        )
        message: str = f"Arrived at {place['name']}"

        # Record trajectory segment from last position to this place
        trajectory_segment = await _record_trajectory(db, trip_id, place, now)

    elif action == "done":
        _ = await db.execute(
            "UPDATE places SET status = 'done', departed_at = ? WHERE id = ?",
            (now, body.place_id),
        )
        message = f"Finished visiting {place['name']}"

    elif action == "skipped":
        _ = await db.execute(
            "UPDATE places SET status = 'skipped' WHERE id = ?",
            (body.place_id,),
        )
        message = f"Skipped {place['name']}"

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    await db.commit()

    # Re-read updated place
    cursor = await db.execute("SELECT * FROM places WHERE id = ?", (body.place_id,))
    updated: dict[str, Any] = dict(await cursor.fetchone())  # type: ignore[arg-type]

    return CheckinResponse(
        place_id=updated["id"],
        status=updated["status"],
        arrived_at=updated["arrived_at"],
        departed_at=updated["departed_at"],
        message=message,
        trajectory_segment=trajectory_segment,
    )


async def _record_trajectory(
    db: aiosqlite.Connection,
    trip_id: str,
    place: dict[str, Any],
    now: str,
) -> TrajectorySegment | None:
    """Compute and store trajectory segment from last position to the arrived place."""
    try:
        # Get trip info (start coords + transport mode) in one query
        cursor = await db.execute(
            "SELECT start_lat, start_lon, transport_mode FROM trips WHERE id = ?",
            (trip_id,),
        )
        trip_row = await cursor.fetchone()
        if not trip_row:
            return None
        transport_mode = trip_row["transport_mode"]

        # Determine last position
        cursor = await db.execute(
            "SELECT to_lat, to_lon FROM trajectory_segments "
            "WHERE trip_id = ? ORDER BY created_at DESC LIMIT 1",
            (trip_id,),
        )
        last_pos = await cursor.fetchone()

        if last_pos:
            from_lat, from_lon = last_pos["to_lat"], last_pos["to_lon"]
        else:
            from_lat, from_lon = trip_row["start_lat"], trip_row["start_lon"]

        to_lat, to_lon = place["lat"], place["lon"]

        # Check OSRM health endpoint for the chosen profile; skip if not healthy.
        try:
            osrm_base = {
                "foot": settings.osrm_foot_url,
                "car": settings.osrm_car_url,
                "bicycle": settings.osrm_bicycle_url,
            }.get(transport_mode, settings.osrm_foot_url)
            health_url = f"{osrm_base}/health"
            client = client_instance()
            if client is not None:
                resp = await client.get(health_url, timeout=1.0)
            else:
                async with httpx.AsyncClient(timeout=1.0) as tmp:
                    resp = await tmp.get(health_url)
            if resp.status_code != 200:
                logger.warning(
                    "OSRM health check failed (%s) — skipping trajectory segment",
                    health_url,
                )
                return None
        except Exception:
            logger.warning("OSRM health check error — skipping trajectory segment")
            return None

        # Fetch OSRM route geometry
        legs = await get_route_geometry(
            [[from_lon, from_lat], [to_lon, to_lat]], transport_mode
        )
        if not legs or not legs[0].get("geometry"):
            logger.warning(
                "OSRM unavailable for trip %s — skipping trajectory segment", trip_id
            )
            return None

        geometry = legs[0]["geometry"]
        distance = legs[0]["distance"]
        duration = legs[0]["duration"]

        # Insert trajectory segment
        cursor = await db.execute(
            "INSERT INTO trajectory_segments "
            "(trip_id, from_lat, from_lon, to_lat, to_lon, place_id, "
            "geometry, distance_meters, duration_seconds, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trip_id,
                from_lat,
                from_lon,
                to_lat,
                to_lon,
                place["id"],
                geometry,
                distance,
                duration,
                now,
            ),
        )

        return TrajectorySegment(
            id=cursor.lastrowid or 0,
            from_lat=from_lat,
            from_lon=from_lon,
            to_lat=to_lat,
            to_lon=to_lon,
            place_id=place["id"],
            geometry=geometry,
            distance_meters=distance,
            duration_seconds=duration,
            created_at=now,
        )
    except Exception:
        logger.exception("Failed to record trajectory segment")
        return None
