"""'What Next?' recommendation endpoint."""

import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Query

from app.db import get_db
from app.engine.scoring import score_next_actions
from app.models import NextRecommendation, NextResponse
from app.routers.feasibility import compute_feasibility

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/trips/{trip_id}/next", response_model=NextResponse)
async def get_next_recommendation(
    trip_id: str,
    lat: float | None = Query(None),
    lon: float | None = Query(None),
    time_override: str | None = Query(None, alias="time"),
    db: aiosqlite.Connection = Depends(get_db),
) -> NextResponse:
    """
    Get top 3 recommended next places to visit.

    Query params:
        lat, lon: Current position. Falls back to trip start.
        time: Optional ISO time override for testing (e.g. "14:30").
    """
    feas_response, ctx = await compute_feasibility(db, trip_id, lat, lon, time_override)

    if not feas_response.places:
        return NextResponse(
            recommendations=[],
            message="No pending places. Add some places or head to your endpoint.",
        )

    # Build precomputed feasibility map from the response
    precomputed_feasibility: dict[int, dict[str, Any]] = {}
    for r in feas_response.places:
        precomputed_feasibility[r.place_id] = r.model_dump()

    # Check if every pending place is infeasible
    all_infeasible: bool = all(
        f.get("color") == "gray" for f in precomputed_feasibility.values()
    )
    if all_infeasible:
        has_must: bool = any(v == "must" for v in ctx.place_priorities.values())
        if has_must:
            msg: str = (
                "No reachable places right now. Some 'must' places remain but are currently unreachable "
                "(closed or too far). Consider adjusting your schedule or transport mode."
            )
        else:
            msg = (
                "No reachable places right now — all pending places are currently infeasible "
                "(closed or too far). Consider adjusting your schedule or transport mode."
            )
        return NextResponse(recommendations=[], message=msg)

    recommendations: list[dict[str, Any]] = score_next_actions(
        places=ctx.places,
        matrix=ctx.matrix,
        current_time=ctx.current_time,
        trip_end_time=ctx.trip_end_dt,
        trip_date=ctx.trip_date,
        endpoint_idx=ctx.endpoint_idx,
        precomputed_feasibility=precomputed_feasibility,
    )

    return NextResponse(
        recommendations=[NextRecommendation(**r) for r in recommendations],
        message=None,
    )
