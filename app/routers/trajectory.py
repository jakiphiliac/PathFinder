"""Trajectory endpoint: retrieve journey segments for a trip."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from app.db import get_db
from app.models import TrajectoryResponse, TrajectorySegment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/trips/{trip_id}/trajectory", response_model=TrajectoryResponse)
async def get_trajectory(
    trip_id: str,
    db: aiosqlite.Connection = Depends(get_db),
) -> TrajectoryResponse:
    """Return all trajectory segments for a trip, ordered chronologically."""
    cursor = await db.execute("SELECT id FROM trips WHERE id = ?", (trip_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Trip not found")

    cursor = await db.execute(
        "SELECT * FROM trajectory_segments WHERE trip_id = ? ORDER BY created_at",
        (trip_id,),
    )
    rows = await cursor.fetchall()

    segments = [
        TrajectorySegment(**{k: row[k] for k in row.keys() if k != "trip_id"})
        for row in rows
    ]
    return TrajectoryResponse(segments=segments)
