from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.osrm import get_distance_matrix

app = FastAPI(title="Travel Route Optimizer")

# Mount static files (CSS, JS) at /static
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Jinja2 templates for HTML pages
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/")
def index(request: Request):
    """Serve the main page with the places input form"""
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/test-osrm")
def test_osrm():
    """Debug route to verify OSRM distance matrix. Remove before Step 5."""
    # Big Ben and Tower of London
    coords = [(-0.1276, 51.5074), (-0.0756, 51.5034)]
    matrix = get_distance_matrix(coords)
    return {"coordinates": coords, "durations_seconds": matrix}
