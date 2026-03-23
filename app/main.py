import asyncio
import datetime
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from app.algorithms.tsp_tw_utils import evaluate_route
from app.services.nominatim import geocode_place
from app.services.opening_hours_utils import (
    DEFAULT_WINDOW,
    parse_to_time_window,
    parse_user_override,
)
from app.services.osrm import get_distance_matrix, get_route_geometry
from app.services.overpass import get_opening_hours_batch
from app.services.solver import run_nn_with_progress, run_sa_with_progress

app = FastAPI(title="Travel Route Optimizer")

# Mount static files (CSS, JS) at /static
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Jinja2 templates for HTML pages
template_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(template_dir))


class GeocodeRequest(BaseModel):
    destination: str
    places: list[str]

    @field_validator("destination")
    @classmethod
    def destination_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("destination must be a non-empty string")
        return v


class GeocodeResult(BaseModel):
    name: str
    lat: float | None = None
    lon: float | None = None
    display_name: str | None = None
    error: str | None = None


class GeocodeResponse(BaseModel):
    results: list[GeocodeResult]


@app.get("/")
def index(request: Request):
    """Serve the main page with the places input form"""
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.post("/api/geocode", response_model=GeocodeResponse)
async def api_geocode(payload: GeocodeRequest) -> GeocodeResponse:
    """
    Geocode a list of place names using Nominatim.

    Body: {"destination": "London, UK", "places": ["Big Ben", "Tower of London", ...]}
    """
    results: list[GeocodeResult] = []

    for idx, raw in enumerate(payload.places):
        if not raw.strip():
            results.append(GeocodeResult(name=raw, error="Empty place name."))
            continue

        try:
            geocoded = await geocode_place(raw, destination=payload.destination)
        except Exception:
            results.append(GeocodeResult(name=raw, error="Geocoding failed."))
            if idx < len(payload.places) - 1:
                await asyncio.sleep(1.0)
            continue

        if geocoded is None:
            results.append(GeocodeResult(name=raw, error="Not found."))
        else:
            results.append(GeocodeResult(**geocoded))

        # Respect Nominatim 1 req/sec policy, but not after the last item
        if idx < len(payload.places) - 1:
            await asyncio.sleep(1.0)

    return GeocodeResponse(results=results)


class LocationInput(BaseModel):
    name: str
    lat: float
    lon: float


class TimeWindowOverride(BaseModel):
    earliest: str  # "HH:MM"
    latest: str  # "HH:MM"


class SolveRequest(BaseModel):
    coordinates: list[list[float]]
    locations: list[LocationInput] = []
    time_windows_override: dict[str, TimeWindowOverride] = {}
    day_of_week: int = -1  # -1 = today, 0=Mon … 6=Sun

    @field_validator("coordinates")
    @classmethod
    def at_least_two_coordinates(cls, v: list[list[float]]) -> list[list[float]]:
        if len(v) < 2:
            raise ValueError("Need at least 2 coordinates to solve a route")
        for coord in v:
            if len(coord) != 2:
                raise ValueError("Each coordinate must be [lon, lat]")
        return v


class RouteGeometryRequest(BaseModel):
    coordinates: list[list[float]]  # [[lon, lat], ...] in visit order


@app.post("/api/route-geometry")
async def api_route_geometry(payload: RouteGeometryRequest):
    """
    Fetch the actual road-snapped walking path for an ordered list of coordinates.

    Body: {"coordinates": [[lon, lat], ...]}
    Returns: {"latlngs": [[lat, lon], ...]} or {"error": "..."}
    """
    try:
        latlngs = await get_route_geometry(payload.coordinates)
        return {"latlngs": latlngs}
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/api/solve/stream")
async def api_solve_stream(payload: SolveRequest):
    """
    Solve TSP via Nearest Neighbor then Simulated Annealing, streaming progress as SSE.

    Body: {"coordinates": [[lon, lat], ...], "locations": [...], "time_windows_override": {...}}

    Events:
        {"type": "matrix",        "size": N}
        {"type": "status",        "message": "..."}
        {"type": "opening_hours", "windows": [[earliest, latest], ...]}
        {"type": "progress",      "route": [...], "cost": float}   — NN step
        {"type": "nn_done",       "route": [...], "cost": float}   — NN complete
        {"type": "sa_progress",   "route": [...], "cost": float}   — SA improvement
        {"type": "sa_done",       "route": [...], "cost": float}   — SA complete
        {"type": "feasibility",   "feasible": bool, "violations": int, "warning": str|null}
        {"type": "error",         "message": "..."}
    """
    async def event_generator():
        try:
            # Phase 0: Distance matrix
            matrix = await get_distance_matrix(payload.coordinates)
            yield json.dumps({"type": "matrix", "size": len(matrix)})

            # Phase 0b: Opening hours from Overpass (if locations provided)
            time_windows = None
            if payload.locations:
                if len(payload.locations) != len(payload.coordinates):
                    raise ValueError(
                        f"locations length ({len(payload.locations)}) must match "
                        f"coordinates length ({len(payload.coordinates)})"
                    )

                yield json.dumps({"type": "status", "message": "Fetching opening hours from OSM..."})

                today = datetime.date.today()
                target_date = today
                if payload.day_of_week >= 0:
                    days_ahead = (payload.day_of_week - today.weekday()) % 7
                    target_date = today + datetime.timedelta(days=days_ahead)

                locations_dicts = [loc.model_dump() for loc in payload.locations]
                oh_results = await get_opening_hours_batch(locations_dicts)

                time_windows = []
                for i, result in enumerate(oh_results):
                    override = payload.time_windows_override.get(str(i))
                    if override is not None:
                        try:
                            window = parse_user_override(override.earliest, override.latest)
                        except ValueError:
                            window = DEFAULT_WINDOW
                    elif result and result.get("opening_hours"):
                        window = (
                            parse_to_time_window(result["opening_hours"], target_date)
                            or DEFAULT_WINDOW
                        )
                    else:
                        window = DEFAULT_WINDOW
                    time_windows.append(window)

                yield json.dumps({
                    "type": "opening_hours",
                    "windows": [list(w) for w in time_windows],
                })

            # Phase 1: Nearest Neighbor with progress streaming
            nn_route = None
            nn_cost = None
            async for event in run_nn_with_progress(
                matrix, start_index=0, time_windows=time_windows
            ):
                if event["type"] == "done":
                    nn_route = event["route"]
                    nn_cost = event["cost"]
                    yield json.dumps({"type": "nn_done", "route": nn_route, "cost": nn_cost})
                else:
                    yield json.dumps(event)

            # Phase 2: Simulated Annealing (needs ≥ 2 inner cities: route length > 3)
            final_route = nn_route
            if nn_route and len(nn_route) > 3:
                async for event in run_sa_with_progress(
                    matrix,
                    initial_route=nn_route,
                    time_windows=time_windows,
                    max_iter=10_000,
                ):
                    if event["type"] == "sa_done":
                        final_route = event["route"]
                    yield json.dumps(event)
            else:
                yield json.dumps({"type": "sa_done", "route": nn_route, "cost": nn_cost})

            # Phase 3: Feasibility check
            if final_route and time_windows:
                _, violations = evaluate_route(
                    final_route, matrix, time_windows
                )
                feasible = violations == 0
                warning = (
                    None if feasible
                    else f"Could not find a route satisfying all time windows. "
                         f"{violations} violation(s). Showing best effort."
                )
                yield json.dumps({
                    "type": "feasibility",
                    "feasible": feasible,
                    "violations": violations,
                    "warning": warning,
                })

        except Exception as exc:
            yield json.dumps({"type": "error", "message": str(exc)})

    return EventSourceResponse(event_generator())
