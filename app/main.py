import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="PathFinder v2")


@app.get("/health")
async def health():
    return {"status": "ok"}


FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
