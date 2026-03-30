# PathFinder Development Guide

## Project Overview
Feasibility-guided exploration engine for trip planning with self-hosted OSRM routing, SQLite persistence, and Google Places API integration.

## Quick Start

```bash
# Terminal 1: Start OSRM and initialize database
docker compose up -d
python3 migrate.py

# Terminal 2: Start backend
source venv/bin/activate
uvicorn app.main:app --reload
```

## Architecture

| Component | Role | Port |
|-----------|------|------|
| **OSRM Foot** | Walking routes | 5000 |
| **OSRM Car** | Driving routes | 5001 |
| **OSRM Bicycle** | Cycling routes | 5002 |
| **FastAPI** | Backend API | 8000 |
| **SQLite** | Persistence | `./data/pathfinder.db` |
| **Google Places** | Place search & info | API calls |

### Database Schema
- **trips**: User trip records with metadata
- **places**: Location data (coordinates, name, opening hours)
- **distance_cache**: Cached OSRM results (trip_id, from/to place, duration)

### Code Structure
```
app/
  ├── config.py          # Pydantic Settings (load .env)
  ├── db.py              # SQLite schema + init
  ├── models.py          # Pydantic request/response schemas
  ├── main.py            # FastAPI app + lifespan
  └── services/
      ├── osrm.py        # OSRM HTTP client (foot/car/bicycle)
      └── nominatim.py   # (v1 carryover, status TBD)
tests/
  └── test_infrastructure.py  # DB, OSRM, schema tests
```

## Development Workflow

### 1. Code Changes
- Edit files in `app/` — uvicorn auto-reloads on save
- Follow async/await patterns (all I/O is async)
- Add Pydantic models for new request/response types
- Use `httpx` for external API calls (not `requests`)

### 2. Testing
```bash
# Run all tests
pytest tests/ -v

# Run one test file
pytest tests/test_infrastructure.py -v

# Run one test
pytest tests/test_infrastructure.py::test_osrm_foot_connectivity -v
```

### 3. Verification
```bash
# OSRM health
curl -s "http://localhost:5000/table/v1/foot/19.04,47.50;19.05,47.51" | grep -o '"code":"[^"]*"'

# Backend health
curl -s "http://localhost:8000/health"

# SQLite tables
python3 migrate.py
```

### 4. Git Workflow
```bash
# Check what changed
git status
git diff

# Stage files (never osrm-data/ or .env)
git add app/ tests/ CLAUDE.md

# Commit with clear message
git commit -m "feat: add /trips POST endpoint"
git push
```

## Code Style & Conventions

### Python
- **Async everywhere**: Use `async def` for I/O, `await` for coroutines
- **Type hints**: Always annotate function params and returns
- **Pydantic**: All external data through Pydantic models (validation)
- **Error handling**: Raise `HTTPException` in route handlers, not generic exceptions
- **Logging**: Use Python `logging` module, not `print()`

### Imports
```python
# Good: minimal, organized
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import aiosqlite

# Bad: redundant (python-dotenv is handled by pydantic-settings)
from dotenv import load_dotenv
```

### Dependencies
- Check `requirements.txt` before adding a new package
- If it's not actively used, it shouldn't be there
- Document why pre-emptive dependencies exist (e.g., `sse-starlette` for Slice 1 SSE)

## Important Rules

### ✅ DO
- Run `docker compose up -d` before dev work
- Activate venv before running code: `source venv/bin/activate`
- Test with `pytest` before committing
- Use `await` when calling async functions (OSRM, SQLite, httpx)
- Load config from `app.config.settings` (reads .env)

### ❌ DON'T
-
- Commit `osrm-data/` (3GB+ binary files)
- Commit `.env` (use `.env.example` as template)
- Use `print()` for debugging (use `logging`)
- Call `db.execute()` without `await`
- Mix sync and async code (no `requests`, use `httpx`)
- Edit `docker-compose.yml` without testing all three profiles

## Environment Variables

See `.env.example` for all required keys:

```bash
# OSRM endpoints (self-hosted)
OSRM_FOOT_URL=http://localhost:5000
OSRM_CAR_URL=http://localhost:5001
OSRM_BICYCLE_URL=http://localhost:5002

# Google Places API key (from Google Cloud Console)
GOOGLE_PLACES_API_KEY=AIzaSy...

# Database path
DATABASE_PATH=./data/pathfinder.db
```

## Known Limitations (Slice 0)

| Issue | Impact | Mitigation |
|-------|--------|-----------|
| `get_db()` is bare connection | Will leak resources in Slice 1 routes | Will refactor to async generator dependency |
| Static mount at `/` may shadow `/health` | Health check may fail once frontend is built | Will move frontend to `/app` path |
| OSRM containers have no health checks | Startup race conditions if app and OSRM in same compose | Manually verify OSRM with curl before testing |

## Debugging

### OSRM not responding?
```bash
docker compose ps
docker logs pathfinder-osrm-foot  # Check for errors
docker compose restart             # Restart all containers
```

### Database errors?
```bash
# Reinitialize (safe — uses CREATE TABLE IF NOT EXISTS)
python3 migrate.py

# Inspect schema
sqlite3 ./data/pathfinder.db ".tables"
sqlite3 ./data/pathfinder.db ".schema trips"
```

### Tests failing?
```bash
# Run with verbose output
pytest tests/ -vv -s

# Run one test with print statements visible
pytest tests/test_infrastructure.py::test_name -s
```

## Resources

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Pydantic v2](https://docs.pydantic.dev/latest/)
- [aiosqlite](https://github.com/omnilib/aiosqlite)
- [OSRM HTTP API](https://router.project-osrm.org/docs/v5.5.1/api/overview)
- [Google Places API](https://developers.google.com/maps/documentation/places/web-service/overview)
