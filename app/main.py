from pathlib import Path
import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from app.services.nominatim import geocode_place
from app.services.osrm import get_distance_matrix
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


class SolveRequest(BaseModel):
    coordinates: list[list[float]]

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

    Body: {"coordinates": [[lon, lat], [lon, lat], ...]}
    Events: {"type": "matrix", "size": N}
            {"type": "progress", "route": [...], "cost": float}       — NN step
            {"type": "nn_done", "route": [...], "cost": float}        — NN result
            {"type": "sa_progress", "route": [...], "cost": float}    — SA improvement
            {"type": "sa_done", "route": [...], "cost": float}        — final result
            {"type": "error", "message": "..."}
    """
    coords = [tuple(c) for c in payload.coordinates]

    async def event_generator():
        try:
            matrix = await get_distance_matrix(coords)
            yield json.dumps({"type": "matrix", "size": len(matrix)})

            nn_route = None
            nn_cost = None
            async for event in run_nn_with_progress(matrix, start_index=0):
                if event["type"] == "done":
                    nn_route = event["route"]
                    nn_cost = event["cost"]
                    yield json.dumps({"type": "nn_done", "route": nn_route, "cost": nn_cost})
                else:
                    yield json.dumps(event)

            if nn_route and len(nn_route) > 3:
                async for event in run_sa_with_progress(
                    matrix, initial_route=nn_route, max_iter=10_000
                ):
                    yield json.dumps(event)
            else:
                yield json.dumps({"type": "sa_done", "route": nn_route, "cost": nn_cost})
        except Exception as exc:
            yield json.dumps({"type": "error", "message": str(exc)})

    return EventSourceResponse(event_generator())
