# PathFinder Development Guide

## Project Overview
Reactive journey companion for city trip planning — Flighty-style. Users add places to visit, "What Next?" suggests the optimal next destination based on closing times, priority, and proximity. Google Maps handles turn-by-turn navigation (opens in new tab). The map accumulates a trajectory of visited places as a journey record. Built as a Vue 3 SPA + FastAPI backend with self-hosted OSRM routing, SQLite persistence, and real-time SSE updates.

## Quick Start

```bash
# Terminal 1: Start OSRM and initialize database
# Prefer the modern Docker CLI: `docker compose up -d`.
# If your environment uses the legacy Docker Compose binary, use:
# `docker-compose up -d`
docker compose up -d
python3 migrate.py

# Terminal 2: Start backend
# On macOS / Linux:
source venv/bin/activate
# On Windows (Command Prompt):
venv\Scripts\activate
# On Windows (PowerShell), if execution policy allows:
# .\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload

# Terminal 3: Start frontend dev server (optional — for hot reload)
cd frontend && npm run dev
```

## Architecture

| Component | Role | Port |
|-----------|------|------|
| **OSRM Foot** | Walking routes | 5000 |
| **OSRM Car** | Driving routes | 5001 |
| **OSRM Bicycle** | Cycling routes | 5002 |
| **FastAPI** | Backend API | 8000 |
| **Vite** | Frontend dev server | 5173 |
| **SQLite** | Persistence | `./data/pathfinder.db` |
| **Google Places** | Opening hours fallback | API calls |
| **Overpass API** | Opening hours (primary) | API calls |
| **Nominatim** | Geocoding | API calls |

### Database Schema
- **trips**: User trip records (city, coords, times, transport mode, timezone)
- **places**: Location data (coordinates, name, category, priority, opening hours, status, timestamps)
- **distance_cache**: Cached OSRM results (trip_id, from/to place, duration_seconds)
- **trajectory_segments**: Completed journey legs (from/to coords, OSRM geometry, distance, duration)

### Code Structure
```
app/
  ├── config.py              # Pydantic Settings (load .env)
  ├── db.py                  # SQLite schema + init + migration
  ├── models.py              # Pydantic request/response schemas
  ├── main.py                # FastAPI app + lifespan (init DB, HTTP client)
  ├── http_client.py         # Shared httpx AsyncClient lifecycle
  ├── routers/
  │   ├── trips.py           # Trip CRUD (/api/trips)
  │   ├── places.py          # Place CRUD (/api/trips/{id}/places)
  │   ├── search.py          # POI search + geocode (/api/search, /api/geocode)
  │   ├── feasibility.py     # Feasibility endpoint + compute_feasibility + FeasibilityContext + Haversine fallback
  │   ├── next_action.py     # "What Next?" recommendations (/api/trips/{id}/next)
  │   ├── checkin.py         # Check-in state machine + trajectory recording (/api/trips/{id}/checkin)
  │   ├── trajectory.py     # Journey trajectory retrieval (/api/trips/{id}/trajectory)
  │   └── stream.py          # SSE real-time updates (/api/trips/{id}/stream)
  ├── engine/
  │   ├── feasibility.py     # Core feasibility algorithm + opening hours parser
  │   ├── scoring.py         # Opportunity-cost "What Next?" scoring
  │   └── category_defaults.py # Default visit durations per category (20 categories)
  └── services/
      ├── osrm.py            # OSRM HTTP client (distance matrix + route geometry)
      ├── overpass.py         # Opening hours via Overpass API (OSM) with caching + retries
      ├── hours.py            # Hours resolver (Overpass → Google Places fallback)
      └── google_places.py   # Google Places opening hours fallback (optional)
frontend/
  ├── src/
  │   ├── main.js            # Vue 3 app entry point
  │   ├── App.vue            # Root component (router-view)
  │   ├── router.js          # Vue Router: / and /trip/:id
  │   ├── api.js             # All API calls + SSE connection + trajectory fetch
  │   ├── style.css          # Global styles (dark/light theme, buttons)
  │   └── views/
  │       ├── Home.vue       # Landing page: trip creation form + map
  │       └── Dashboard.vue  # Trip dashboard: map, feasibility, trajectory, check-in, What Next?, Google Maps navigation
  ├── public/
  │   └── favicon.svg        # App favicon
  ├── index.html             # HTML entry point
  ├── package.json           # Vue, Leaflet, Vite, Prettier
  └── vite.config.js         # Vite config with FastAPI proxy
tests/
  ├── test_infrastructure.py # DB, OSRM, schema tests (Slice 0)
  ├── test_slice1.py         # Trip/Place CRUD endpoint tests (Slice 1)
  ├── test_slice2.py         # Feasibility engine unit + integration tests (Slice 2)
  ├── test_slice3.py         # "What Next?" scoring + /next endpoint tests (Slice 3)
  ├── test_slice4.py         # SSE urgency alerts + stream tests (Slice 4/5)
  ├── test_slice6.py         # Transport mode switching tests (Slice 6)
  ├── test_slice7.py         # Edge cases: OSRM fallback, empty states, error responses (Slice 7)
  └── test_trajectory.py    # Trajectory persistence, check-in recording, cascade delete (OSRM-resilient)
```

## API Endpoints

### Trip Management
```
POST   /api/trips                              → Create trip, returns { id, url }
GET    /api/trips/{id}                         → Get trip with all places
PATCH  /api/trips/{id}                         → Update settings (transport_mode, times, timezone)
DELETE /api/trips/{id}                         → Delete trip + cascade
```

### Place Management
```
POST   /api/trips/{id}/places                  → Add place (triggers hours resolution + distance caching)
PATCH  /api/trips/{id}/places/{pid}            → Update priority, duration, opening_hours
DELETE /api/trips/{id}/places/{pid}            → Remove place + clean cache
```

### Search & Geocoding
```
GET    /api/search?q=...&lat=...&lon=...       → Search OSM via Overpass, Nominatim fallback
GET    /api/geocode?q=...                      → Geocode address via Nominatim
```

### Check-In & Trajectory
```
POST   /api/trips/{id}/checkin                 → { place_id, action: "arrived"|"done"|"skipped" }
                                                  "arrived" records trajectory segment (OSRM required; skipped if unavailable)
GET    /api/trips/{id}/trajectory              → All journey segments (geometry, distance, duration)
```

### Feasibility & Recommendations
```
GET    /api/trips/{id}/feasibility?lat=&lon=   → Feasibility for all pending places
GET    /api/trips/{id}/next?lat=&lon=          → Top 3 "What Next?" recommendations
GET    /api/trips/{id}/stream?lat=&lon=        → SSE stream (feasibility_update + urgency_alert events)
```

### System
```
GET    /health                                 → { status: "ok" }
```

## Development Workflow

### 1. Code Changes
- Edit files in `app/` — uvicorn auto-reloads on save
- Edit files in `frontend/src/` — Vite hot-reloads
- Follow async/await patterns (all I/O is async)
- Add Pydantic models for new request/response types
- Use `httpx` for external API calls (not `requests`)

### 2. Testing
```bash
# Run all tests (72 tests)
pytest tests/ -v

# Run one slice
pytest tests/test_slice7.py -v

# Run one test
pytest tests/test_slice7.py::test_haversine_distance_known_pair -v
```

### 3. Linting & Formatting
```bash
# Python
ruff check app/ tests/
ruff format app/ tests/

# Frontend
cd frontend && npx prettier --write src/
```

### 4. Frontend Build
```bash
cd frontend && npm run build
# Output goes to frontend/dist/ — served by FastAPI static mount
```

### 5. Verification
```bash
# OSRM health
curl -s "http://localhost:5000/table/v1/foot/19.04,47.50;19.05,47.51" | grep -o '"code":"[^"]*"'

# Backend health
curl -s "http://localhost:8000/health"
```

### 6. Git Workflow
```bash
git status && git diff
git add app/ tests/ frontend/src/ CLAUDE.md
git commit -m "feat: add /trips POST endpoint"
git push
```

## Code Style & Conventions

### Python
- **Async everywhere**: Use `async def` for I/O, `await` for coroutines
- **Type hints**: Always annotate function params and returns (Pyright standard mode)
- **Pydantic**: All external data through Pydantic models (validation)
- **Error handling**: Raise `HTTPException` in route handlers, not generic exceptions
- **Logging**: Use Python `logging` module, not `print()`
- **Linter/Formatter**: ruff (not black)

### Frontend
- **Vue 3 Composition API** with `<script setup>`
- **No component decomposition** — Home.vue and Dashboard.vue are monolithic views
- **Leaflet** for maps (imported via npm, not CDN)
- **Formatter**: Prettier

### Imports
```python
# Good: minimal, organized
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import aiosqlite

# Bad: redundant (python-dotenv is handled by pydantic-settings)
from dotenv import load_dotenv
```

## Key Implementation Details

### Feasibility Engine
- **Colors**: green (>30% slack), yellow (10-30%), red (<10% or closing <30min), gray (impossible), unknown (no hours)
- **Scoring weights**: 40% opportunity cost + 30% proximity + 30% priority
- **OSRM fallback**: When OSRM is unreachable, falls back to Haversine straight-line estimates with detour factor
- **Opening hours**: Overpass API (primary) → Google Places (fallback) → unknown

### Journey Flow (Flighty-style)
1. User creates trip (city, arrive-by time, start location, transport mode, closed/open path)
   - Browser auto-detects timezone via `Intl.DateTimeFormat`
   - Frontend validates end_time > start_time before submission
   - Start/end locations set via search or "Pick on map" button
2. Adds places with priority and expected duration — map shows feasibility-colored pins
   - Search bar and results clear after adding a place
   - Place names display as short name (text before first comma) in cards
3. Taps "What Next?" — algorithm suggests best next destination
4. Taps "Go" — Google Maps opens in new tab, What Next? card dismisses, place stored as pending arrival
5. User navigates to destination, returns to app — sees "Did you arrive at [place]?" with one-tap confirm
   - Can also tap "I went somewhere else" to pick from full list
6. Trajectory segment is drawn on the map (retrospective, like Flighty flight arcs)
   - Only recorded when OSRM is available; skipped (not drawn) when OSRM is down
7. Taps "Done" at current place — user manually taps "What Next?" when ready
8. Repeat until all places visited:
   - What Next? and check-in sections auto-hide
   - Closed trip: "Head back to your starting point" with Google Maps link
   - Open trip: "Head to your final destination" with Google Maps link

### Trajectory System
- Segments stored in `trajectory_segments` table (survives page refresh/tab kill)
- On "arrived" check-in: OSRM route geometry fetched from last position → arrived place
- If OSRM is unreachable, segment is **not** recorded (no straight-line fallback for trajectory)
- Last position = most recent trajectory segment's destination, or trip start if first visit
- Frontend draws segments as semi-transparent polylines (purple #6366f1)
- Google Maps navigation URL: `maps/dir/?api=1&origin=...&destination=...&travelmode=...`

### SSE Stream
- Pushes `feasibility_update` and `urgency_alert` events every 60 seconds
- Alerts trigger on color degradation (green→yellow, yellow→red, any→gray)
- Must-visit places get extra alerts when closing within 30/60 minutes

### Check-In State Machine
- `pending` → `arrived` (visiting) or `skipped`
- `visiting` → `done` or `skipped`
- Invalid transitions return 400
- "arrived" action records trajectory segment when OSRM is available (skipped otherwise)

### Transport Mode Switching
- PATCH trip with new mode → invalidates distance_cache → background recompute via OSRM

## Important Rules

### ✅ DO
- Run `docker compose up -d` before dev work
- Activate venv before running code: `source venv/bin/activate`
- Run `pytest tests/ -v` before committing
- Run `ruff check app/ tests/` before committing
- Use `await` when calling async functions (OSRM, SQLite, httpx)
- Load config from `app.config.settings` (reads .env)

### ❌ DON'T
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

## Known Limitations

| Issue | Impact | Mitigation |
|-------|--------|-----------|
| Static mount at `/` may shadow `/health` | Health check may fail if matched by static handler | `/health` registered before static mount |
| OSRM containers have no health checks | Startup race conditions | Manually verify OSRM with curl before testing |
| Nominatim has no rate-limit guard | Heavy usage may trigger 429s | Cache geocode results or self-host |
| Timezone stored per trip (default UTC) | Feasibility uses trip timezone for opening hours | Set timezone when creating trip |
| OSRM fallback uses straight-line estimates | Feasibility less accurate when OSRM is down | Haversine + 1.4x detour factor + profile-specific speeds |
| Trajectory requires OSRM | No trajectory segments recorded when OSRM is down | Segments resume when OSRM comes back; missing gaps are acceptable |
| Frontend is two monolithic views | Harder to maintain at scale | Acceptable for thesis scope |

## Debugging

### OSRM not responding?
```bash
docker compose ps
docker logs pathfinder-osrm-foot
docker compose restart
```

### Database errors?
```bash
python3 migrate.py
sqlite3 ./data/pathfinder.db ".tables"
sqlite3 ./data/pathfinder.db ".schema trips"
```

### Tests failing?
```bash
pytest tests/ -vv -s
pytest tests/test_slice7.py::test_name -s
```

## Resources

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Pydantic v2](https://docs.pydantic.dev/latest/)
- [aiosqlite](https://github.com/omnilib/aiosqlite)
- [OSRM HTTP API](https://router.project-osrm.org/docs/v5.5.1/api/overview)
- [Google Places API](https://developers.google.com/maps/documentation/places/web-service/overview)
