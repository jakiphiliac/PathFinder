from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
