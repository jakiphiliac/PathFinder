"""Trip CRUD endpoints."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.config import settings
from app.db import get_db
from app.models import (
    PlaceResponse,
    TripCreate,
    TripCreatedResponse,
    TripDetailResponse,
    TripResponse,
    TripUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.post("/trips", response_model=TripCreatedResponse, status_code=201)
async def create_trip(
    body: TripCreate, db: aiosqlite.Connection = Depends(get_db)
) -> TripCreatedResponse:
    trip_id = str(uuid.uuid4())
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    # Apply defaults for optional fields
    start_time = body.start_time or now_dt.strftime("%H:%M")
    date = body.date or now_dt.strftime("%Y-%m-%d")
    tz = body.timezone or "UTC"

    # Validate end_time > start_time on the same day
    if body.end_time <= start_time:
        raise HTTPException(
            status_code=400,
            detail=f"End time ({body.end_time}) must be after start time ({start_time})",
        )
    _ = await db.execute(
        """INSERT INTO trips
           (id, city, start_lat, start_lon, end_lat, end_lon,
            start_time, end_time, date, transport_mode, timezone, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trip_id,
            body.city,
            body.start_lat,
            body.start_lon,
            body.end_lat,
            body.end_lon,
            start_time,
            body.end_time,
            date,
            body.transport_mode,
            tz,
            now,
            now,
        ),
    )
    await db.commit()
    return TripCreatedResponse(id=trip_id, url=f"/api/trips/{trip_id}")


@router.get("/trips/{trip_id}", response_model=TripDetailResponse)
async def get_trip(
    trip_id: str, db: aiosqlite.Connection = Depends(get_db)
) -> TripDetailResponse:
    cursor = await db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trip not found")

    trip = TripResponse(**dict(row))  # type: ignore[arg-type]

    cursor = await db.execute(
        "SELECT * FROM places WHERE trip_id = ? ORDER BY id", (trip_id,)
    )
    place_rows = await cursor.fetchall()
    places = [PlaceResponse(**dict(r)) for r in place_rows]  # type: ignore[misc]

    return TripDetailResponse(**trip.model_dump(), places=places)


async def _recompute_distances_background(
    trip_id: str, profile: str, db_path: str
) -> None:
    """Recompute full distance matrix for all places in a trip after mode change."""
    import aiosqlite as _aiosqlite

    from app.services.osrm import get_distance_matrix

    try:
        async with _aiosqlite.connect(db_path) as db:
            db.row_factory = _aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, lat, lon FROM places WHERE trip_id = ?", (trip_id,)
            )
            rows = await cursor.fetchall()
            all_places: list[dict[str, Any]] = [dict(r) for r in rows]  # type: ignore[arg-type]
            if len(all_places) < 2:
                return

            coords: list[list[float]] = [[p["lon"], p["lat"]] for p in all_places]
            matrix: list[list[float]] = await get_distance_matrix(coords, profile)

            for i, pi in enumerate(all_places):
                for j, pj in enumerate(all_places):
                    if i == j:
                        continue
                    await db.execute(
                        """INSERT OR REPLACE INTO distance_cache
                           (trip_id, from_place_id, to_place_id, duration_seconds)
                           VALUES (?, ?, ?, ?)""",
                        (trip_id, pi["id"], pj["id"], matrix[i][j]),
                    )
            await db.commit()
            logger.info(
                "Recomputed distance cache for trip %s with profile %s",
                trip_id,
                profile,
            )
    except Exception:
        logger.exception("Failed to recompute distances for trip %s", trip_id)


@router.patch("/trips/{trip_id}", response_model=TripResponse)
async def update_trip(
    trip_id: str,
    body: TripUpdate,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
) -> TripResponse:
    cursor = await db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trip not found")

    ALLOWED_COLUMNS: set[str] = {"start_time", "end_time", "transport_mode", "timezone"}
    updates: dict[str, Any] = {
        k: v
        for k, v in body.model_dump(exclude_none=True).items()
        if k in ALLOWED_COLUMNS
    }
    if not updates:
        return TripResponse(**dict(row))  # type: ignore[arg-type]

    now: str = datetime.now(timezone.utc).isoformat()
    updates["updated_at"] = now
    set_clause: str = ", ".join(f"{k} = ?" for k in updates)
    values: list[Any] = list(updates.values()) + [trip_id]
    _ = await db.execute(f"UPDATE trips SET {set_clause} WHERE id = ?", values)
    await db.commit()

    # Invalidate distance cache when transport mode changes
    if "transport_mode" in updates:
        _ = await db.execute("DELETE FROM distance_cache WHERE trip_id = ?", (trip_id,))
        await db.commit()
        background_tasks.add_task(
            _recompute_distances_background,
            trip_id,
            updates["transport_mode"],
            settings.database_path,
        )

    cursor = await db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,))
    updated = await cursor.fetchone()
    if updated is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return TripResponse(**dict(updated))  # type: ignore[arg-type]


@router.delete("/trips/{trip_id}", status_code=204)
async def delete_trip(trip_id: str, db: aiosqlite.Connection = Depends(get_db)) -> None:
    cursor = await db.execute("SELECT id FROM trips WHERE id = ?", (trip_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Trip not found")

    _ = await db.execute(
        "DELETE FROM trajectory_segments WHERE trip_id = ?", (trip_id,)
    )
    _ = await db.execute("DELETE FROM distance_cache WHERE trip_id = ?", (trip_id,))
    _ = await db.execute("DELETE FROM places WHERE trip_id = ?", (trip_id,))
    _ = await db.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
    await db.commit()
