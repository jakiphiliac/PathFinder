from pathlib import Path
import asyncio

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from app.services.nominatim import geocode_place

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
            results.append(GeocodeResult(**geocoded.model_dump()))

        # Respect Nominatim 1 req/sec policy, but not after the last item
        if idx < len(payload.places) - 1:
            await asyncio.sleep(1.0)

    return GeocodeResponse(results=results)
