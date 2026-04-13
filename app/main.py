"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.http_client import close_http_client, init_http_client
from app.routers import (
    checkin,
    feasibility,
    next_action,
    places,
    search,
    stream,
    trajectory,
    trips,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database and shared HTTP client on startup
    await init_db()
    await init_http_client()
    try:
        yield
    finally:
        # Close shared HTTP client on shutdown
        await close_http_client()


app = FastAPI(title="PathFinder v2", lifespan=lifespan)

app.include_router(trips.router)
app.include_router(places.router)
app.include_router(search.router)
app.include_router(feasibility.router)
app.include_router(next_action.router)
app.include_router(checkin.router)
app.include_router(stream.router)
app.include_router(trajectory.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(FRONTEND_DIST):
    # Catch-all for Vue Router paths (e.g. /trip/:id) — serve index.html
    @app.get("/trip/{path:path}")
    async def spa_trip(path: str):
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))

    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
