"""SSE endpoint for baseline route optimization (NN + 2-opt) with live progress."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, Query
from sse_starlette.sse import EventSourceResponse

from app.db import get_db
from app.engine.baseline import solve_baseline
from app.models import (
    BaselineDone,
    BaselineNNResult,
    BaselineRoadSegment,
    BaselineSwapEvent,
)
from app.routers.feasibility import compute_feasibility
from app.services.osrm import get_route_geometry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_executor = ThreadPoolExecutor(max_workers=2)


def _coords_from_tour(
    tour: list[int], all_coords: list[list[float]]
) -> list[list[float]]:
    """Map tour indices back to [lon, lat] coordinate pairs."""
    return [all_coords[i] for i in tour]


async def _run_baseline(
    matrix: list[list[float]],
    start_idx: int,
    end_idx: int | None,
    all_coords: list[list[float]],
    place_id_map: dict[int, int],
    transport_mode: str,
    event_queue: asyncio.Queue[dict[str, Any] | None],
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Run NN + 2-opt in a thread, bridging progress events to the async queue."""

    def progress_callback(event_type: str, data: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(event_queue.put_nowait, {"type": event_type, **data})

    def run_solver() -> list[int]:
        return solve_baseline(matrix, start_idx, end_idx, progress_callback)

    # Run solver in thread pool
    final_tour = await loop.run_in_executor(_executor, run_solver)

    # Emit NN result if it wasn't already sent via callback
    # (it was — but we also send road segments for the final tour)

    # Fetch OSRM road geometry for each leg of the final tour
    for i in range(len(final_tour) - 1):
        from_node = final_tour[i]
        to_node = final_tour[i + 1]
        leg_coords = [all_coords[from_node], all_coords[to_node]]
        try:
            legs = await get_route_geometry(leg_coords, transport_mode)
            geometry = legs[0]["geometry"] if legs else ""
        except Exception:
            geometry = ""

        segment = BaselineRoadSegment(
            from_idx=place_id_map.get(from_node, from_node),
            to_idx=place_id_map.get(to_node, to_node),
            geometry=geometry,
        )
        event_queue.put_nowait(
            {"type": "road_segment", "data": segment.model_dump_json()}
        )

    # Final done event
    final_cost = sum(
        matrix[final_tour[i]][final_tour[i + 1]] for i in range(len(final_tour) - 1)
    )
    done = BaselineDone(
        tour=[place_id_map.get(i, i) for i in final_tour],
        cost=final_cost,
    )
    event_queue.put_nowait({"type": "done", "data": done.model_dump_json()})
    event_queue.put_nowait(None)  # sentinel


@router.get("/trips/{trip_id}/baseline/stream")
async def baseline_stream(
    trip_id: str,
    lat: float | None = Query(None),
    lon: float | None = Query(None),
    db: aiosqlite.Connection = Depends(get_db),
) -> EventSourceResponse:
    """SSE stream that runs NN + 2-opt and streams progress events."""

    response, ctx = await compute_feasibility(db, trip_id, lat, lon)

    if len(ctx.places) < 2:

        async def empty_gen() -> Any:
            done = BaselineDone(tour=[], cost=0)
            yield {"event": "done", "data": done.model_dump_json()}

        return EventSourceResponse(empty_gen())  # type: ignore[arg-type]

    # Build coordinate list: [current_pos, place1, place2, ..., endpoint]
    # This matches the matrix layout from compute_feasibility
    all_coords: list[list[float]] = []
    cur_lat = lat if lat is not None else ctx.places[0]["lat"]
    cur_lon = lon if lon is not None else ctx.places[0]["lon"]
    all_coords.append([cur_lon, cur_lat])
    for p in ctx.places:
        all_coords.append([p["lon"], p["lat"]])
    # Endpoint is at ctx.endpoint_idx
    # We need to reconstruct end coords from the trip
    cursor = await db.execute(
        "SELECT end_lon, end_lat FROM trips WHERE id = ?", (trip_id,)
    )
    trip_row = await cursor.fetchone()
    if trip_row:
        all_coords.append([trip_row[0], trip_row[1]])
    else:
        all_coords.append(all_coords[0])  # fallback to start

    start_idx = 0
    end_idx: int | None = len(all_coords) - 1
    # Check if closed tour (start == end)
    if (
        abs(all_coords[0][0] - all_coords[-1][0]) < 1e-6
        and abs(all_coords[0][1] - all_coords[-1][1]) < 1e-6
    ):
        end_idx = None  # closed tour

    # Map matrix indices -> place IDs for frontend
    place_id_map: dict[int, int] = {0: 0}  # start node
    for i, p in enumerate(ctx.places):
        place_id_map[i + 1] = p["id"]
    place_id_map[len(ctx.places) + 1] = -1  # endpoint sentinel

    # Get transport mode from trip
    cursor2 = await db.execute(
        "SELECT transport_mode FROM trips WHERE id = ?", (trip_id,)
    )
    mode_row = await cursor2.fetchone()
    transport_mode = mode_row[0] if mode_row else "foot"

    loop = asyncio.get_event_loop()
    event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    # Launch the solver as a background task
    task = asyncio.create_task(
        _run_baseline(
            ctx.matrix,
            start_idx,
            end_idx,
            all_coords,
            place_id_map,
            transport_mode,
            event_queue,
            loop,
        )
    )

    async def event_generator() -> Any:
        try:
            while True:
                item = await event_queue.get()
                if item is None:
                    break

                event_type = item.get("type", "")

                if event_type == "nn":
                    tour = item["tour"]
                    coords = _coords_from_tour(tour, all_coords)
                    nn_result = BaselineNNResult(
                        tour=[place_id_map.get(i, i) for i in tour],
                        cost=item["cost"],
                        coords=coords,
                    )
                    yield {"event": "nn_result", "data": nn_result.model_dump_json()}

                elif event_type == "swap":
                    tour = item["tour"]
                    coords = _coords_from_tour(tour, all_coords)
                    swap_event = BaselineSwapEvent(
                        i=item["i"],
                        j=item["j"],
                        tour=[place_id_map.get(idx, idx) for idx in tour],
                        cost=item["cost"],
                        accepted=item["accepted"],
                        coords=coords,
                    )
                    event_name = "swap_accept" if item["accepted"] else "swap_eval"
                    yield {"event": event_name, "data": swap_event.model_dump_json()}

                elif event_type == "road_segment":
                    yield {"event": "road_segment", "data": item["data"]}

                elif event_type == "done":
                    yield {"event": "done", "data": item["data"]}

        except asyncio.CancelledError:
            task.cancel()

    return EventSourceResponse(event_generator())  # type: ignore[arg-type]
