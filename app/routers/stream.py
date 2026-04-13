"""SSE stream endpoint for real-time feasibility updates and urgency alerts."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette.sse import EventSourceResponse
import aiosqlite

from app.db import get_db
from app.models import UrgencyAlert
from app.routers.feasibility import compute_feasibility

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Color severity ordering for detecting degradation
_COLOR_RANK: dict[str, int] = {
    "green": 0,
    "unknown": 1,
    "yellow": 2,
    "red": 3,
    "gray": 4,
}

STREAM_INTERVAL_SECONDS = 60


def _detect_alerts(
    results: list[dict[str, Any]],
    last_colors: dict[int, str],
    place_names: dict[int, str],
    place_priorities: dict[int, str],
) -> list[UrgencyAlert]:
    """Compare current feasibility with previous state and generate alerts."""
    alerts: list[UrgencyAlert] = []

    for r in results:
        pid: int = r["place_id"]
        color: str = r["color"]
        old_color: str | None = last_colors.get(pid)
        pid_name: str | None = place_names.get(pid)
        name: str = pid_name if pid_name is not None else f"Place {pid}"
        closing: float | None = r.get("closing_urgency_minutes")

        # Color degradation alert
        if old_color and _COLOR_RANK.get(color, 0) > _COLOR_RANK.get(old_color, 0):
            if color == "gray":
                alerts.append(
                    UrgencyAlert(
                        place_id=pid,
                        place_name=name,
                        message="no longer reachable in time",
                        severity="critical",
                    )
                )
            elif color == "red":
                msg = (
                    f"closes in {int(closing)} min — leave now or you'll miss it"
                    if closing
                    else "very tight on time"
                )
                alerts.append(
                    UrgencyAlert(
                        place_id=pid,
                        place_name=name,
                        message=msg,
                        severity="critical",
                    )
                )
            elif color == "yellow":
                msg = (
                    f"closes in {int(closing)} min — consider going soon"
                    if closing
                    else "limited time remaining"
                )
                alerts.append(
                    UrgencyAlert(
                        place_id=pid,
                        place_name=name,
                        message=msg,
                        severity="warning",
                    )
                )

        # Must-visit urgency (independent of color change)
        if place_priorities.get(pid) == "must" and closing is not None:
            if closing < 30 and color != "gray":
                alert = UrgencyAlert(
                    place_id=pid,
                    place_name=name,
                    message=f"must-visit closes in {int(closing)} min — go now!",
                    severity="critical",
                )
                if alert not in alerts:
                    alerts.append(alert)
            elif closing < 60 and color != "gray":
                alert = UrgencyAlert(
                    place_id=pid,
                    place_name=name,
                    message=f"must-visit closes in {int(closing)} min — plan accordingly",
                    severity="warning",
                )
                if alert not in alerts:
                    alerts.append(alert)

    return alerts


@router.get("/trips/{trip_id}/stream")
async def trip_stream(
    request: Request,
    trip_id: str,
    lat: float | None = Query(None),
    lon: float | None = Query(None),
    db: aiosqlite.Connection = Depends(get_db),
) -> EventSourceResponse:
    """SSE stream that pushes feasibility updates and urgency alerts."""

    async def event_generator() -> Any:
        last_colors: dict[int, str] = {}

        while True:
            if await request.is_disconnected():
                logger.debug("SSE client disconnected for trip %s", trip_id)
                break

            try:
                response, ctx = await compute_feasibility(
                    db,
                    trip_id,
                    lat,
                    lon,
                )

                # Send feasibility update
                yield {
                    "event": "feasibility_update",
                    "data": response.model_dump_json(),
                }

                # Detect and send urgency alerts
                alerts: list[UrgencyAlert] = _detect_alerts(
                    [r.model_dump() for r in response.places],
                    last_colors,
                    ctx.place_names,
                    ctx.place_priorities,
                )
                for alert in alerts:
                    yield {
                        "event": "urgency_alert",
                        "data": alert.model_dump_json(),
                    }

                # Update last known colors
                for r in response.places:
                    last_colors[r.place_id] = r.color

            except Exception:
                logger.exception(
                    "Error computing feasibility in SSE stream for trip %s", trip_id
                )

            try:
                await asyncio.sleep(STREAM_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    return EventSourceResponse(event_generator())  # type: ignore[arg-type]
