import asyncio
import datetime
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from app.services.nominatim import geocode_place
from app.services.opening_hours_utils import (
    DEFAULT_WINDOW,
    parse_to_time_window,
    parse_user_override,
)
from app.services.osrm import get_distance_matrix
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
            # Record a per-item error instead of failing the whole batch.
            results.append(GeocodeResult(name=raw, error="Geocoding failed."))
            # Still respect rate limiting before continuing to next item.
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


class TimeWindowOverride(BaseModel):
    earliest: str  # "HH:MM"
    latest: str    # "HH:MM"


class SolveRequest(BaseModel):
    coordinates: list[list[float]]
    names: list[str] | None = None
    time_windows_override: dict[str, TimeWindowOverride] | None = None  # index -> override
    visit_date: str | None = None  # "YYYY-MM-DD", defaults to today

    @field_validator("coordinates")
    @classmethod
    def at_least_two_coordinates(cls, v: list[list[float]]) -> list[list[float]]:
        if len(v) < 2:
            raise ValueError("Need at least 2 coordinates to solve a route")
        for coord in v:
            if len(coord) != 2:
                raise ValueError("Each coordinate must be [lon, lat]")
        return v


@app.post("/api/solve/stream")
async def api_solve_stream(payload: SolveRequest):
    """
    Solve TSP via Nearest Neighbor then improve with Simulated Annealing,
    streaming progress for both phases as Server-Sent Events.
    Optionally fetches opening hours from Overpass and applies time windows.

    Body: {"coordinates": [[lon, lat], ...], "names": [...],
           "time_windows_override": {"0": {"earliest": "09:00", "latest": "17:00"}},
           "visit_date": "2026-03-13"}
    """
    coords = [tuple(c) for c in payload.coordinates]
    names = payload.names or [None] * len(coords)

    # Parse visit date
    target_date = None
    if payload.visit_date:
        try:
            target_date = datetime.date.fromisoformat(payload.visit_date)
        except ValueError:
            pass

    async def event_generator():
        try:
            # --- Fetch opening hours from Overpass ---
            locations = [
                {"lat": c[1], "lon": c[0], "name": names[i] if i < len(names) else None}
                for i, c in enumerate(coords)
            ]
            yield json.dumps({"type": "status", "message": "Fetching opening hours..."})

            overpass_results = await get_opening_hours_batch(locations)

            # Build time_windows list
            time_windows: list[tuple[float, float]] | None = None
            tw_info: list[dict] = []  # For sending to frontend
            has_any_window = False

            for i in range(len(coords)):
                override = None
                if payload.time_windows_override and str(i) in payload.time_windows_override:
                    ov = payload.time_windows_override[str(i)]
                    try:
                        override = parse_user_override(ov.earliest, ov.latest)
                    except ValueError:
                        pass

                if override:
                    window = override
                    source = "user"
                    has_any_window = True
                elif overpass_results[i]:
                    oh_str = overpass_results[i]["opening_hours"]
                    parsed = parse_to_time_window(oh_str, target_date)
                    if parsed:
                        window = parsed
                        source = "overpass"
                        has_any_window = True
                    else:
                        window = DEFAULT_WINDOW
                        source = "default (closed or unparseable)"
                else:
                    window = DEFAULT_WINDOW
                    source = "default"

                tw_info.append({
                    "index": i,
                    "window": [window[0], window[1]],
                    "source": source,
                    "overpass_name": overpass_results[i]["name"] if overpass_results[i] else None,
                    "overpass_hours": overpass_results[i]["opening_hours"] if overpass_results[i] else None,
                })

            # Only pass time_windows to solver if we found any real constraints
            if has_any_window:
                time_windows = [
                    (info["window"][0], info["window"][1]) for info in tw_info
                ]

            yield json.dumps({"type": "time_windows", "windows": tw_info})

            # --- Distance matrix ---
            matrix = await get_distance_matrix(coords)
            yield json.dumps({"type": "matrix", "size": len(matrix)})

            # --- NN phase ---
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

            # --- SA phase ---
            if nn_route and len(nn_route) > 3:
                async for event in run_sa_with_progress(
                    matrix, initial_route=nn_route,
                    time_windows=time_windows, max_iter=10_000,
                ):
                    yield json.dumps(event)
            else:
                yield json.dumps({"type": "sa_done", "route": nn_route, "cost": nn_cost})
        except Exception as exc:
            yield json.dumps({"type": "error", "message": str(exc)})

    return EventSourceResponse(event_generator())
