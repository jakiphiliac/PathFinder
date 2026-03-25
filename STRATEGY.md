# PathFinder v2 — Implementation Strategy

## Adaptive Feasibility-Guided Exploration Engine

> Replace static route optimization with a real-time feasibility dashboard and "What Next?" recommendation engine.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                             │
│  Vue 3 + Vite + Leaflet (component-based SPA)               │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌──────────────┐  │
│  │ Trip     │ │ Place    │ │ Feasibility│ │ Check-in     │  │
│  │ Setup    │ │ Selector │ │ Dashboard  │ │ Flow         │  │
│  └──────────┘ └──────────┘ └───────────┘ └──────────────┘  │
│                        │ REST + SSE                         │
├────────────────────────┼────────────────────────────────────┤
│                        │                                    │
│  ┌─────────────────────▼──────────────────────────────────┐ │
│  │              FastAPI Backend                            │ │
│  │  ┌────────────┐ ┌──────────────┐ ┌──────────────────┐  │ │
│  │  │ Trip API   │ │ Feasibility  │ │ "What Next?"     │  │ │
│  │  │ (CRUD)     │ │ Engine       │ │ Scoring Engine   │  │ │
│  │  └────────────┘ └──────────────┘ └──────────────────┘  │ │
│  │  ┌────────────┐ ┌──────────────┐ ┌──────────────────┐  │ │
│  │  │ SQLite     │ │ SSE Stream   │ │ Opening Hours    │  │ │
│  │  │ Persistence│ │ Manager      │ │ Resolver         │  │ │
│  │  └────────────┘ └──────────────┘ └──────────────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
│                        │                                    │
│  ┌─────────────────────▼──────────────────────────────────┐ │
│  │            External Services                           │ │
│  │  ┌──────────┐ ┌──────────────┐ ┌────────────────────┐  │ │
│  │  │ OSRM     │ │ Overpass API │ │ Google Places API  │  │ │
│  │  │ (Docker) │ │ (OSM hours)  │ │ (fallback hours)   │  │ │
│  │  └──────────┘ └──────────────┘ └────────────────────┘  │ │
│  │  ┌──────────┐                                          │ │
│  │  │Nominatim │                                          │ │
│  │  │(geocode) │                                          │ │
│  │  └──────────┘                                          │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## Decisions Summary

| # | Decision | Choice |
|---|---|---|
| 1 | Scope | Core only — single user, no group features |
| 2 | Location input | Browser geolocation + manual fallback |
| 3 | Place data source | OSM/Overpass + manual override |
| 4 | Duration estimation | Category defaults + user override per place |
| 5 | Feasibility coloring | Percentage-based slack + opening hours override |
| 6 | "What Next?" scoring | Opportunity cost — minimize regret |
| 7 | Persistence | SQLite + UUID URL token |
| 8 | API design | REST for mutations + SSE for live updates |
| 9 | OSRM | Self-hosted via Docker (Hungary extract) |
| 10 | Transport mode | User picks per trip, can switch mid-trip |
| 11 | Recommendation display | Top 1 + 2 alternatives with reasoning |
| 12 | Check-in mechanism | Global action buttons + place selector |
| 13 | Opening hours | OSM first, Google Places API fallback |
| 14 | Map library | Leaflet (existing) |
| 15 | URL structure | UUID token, no authentication |
| 16 | Demo region | Hungary (full country) |
| 17 | Language | English only |
| 18 | Codebase approach | Rebuild on `experimental` branch, reuse service modules |
| 19 | Testing | pytest unit tests + FastAPI integration tests |
| 20 | Development approach | Vertical slices |

---

## Tech Stack

| Layer | Technology | Status |
|---|---|---|
| Backend | Python 3.11+ / FastAPI | Keep |
| Database | SQLite (via `aiosqlite` + `databases` or raw `sqlite3`) | New |
| Routing engine | OSRM (self-hosted, Docker) | Upgrade from public demo |
| Map tiles | OpenStreetMap via Leaflet | Keep |
| Geocoding | Nominatim | Keep (`app/services/nominatim.py`) |
| Opening hours (primary) | Overpass API | Keep (`app/services/overpass.py`) |
| Opening hours (fallback) | Google Places API | New |
| Distance/time calculations | OSRM table + route services | Keep (`app/services/osrm.py`) |
| Real-time updates | Server-Sent Events | Keep (restructure) |
| Frontend framework | Vue 3 + Vite | New (replaces vanilla JS) |
| Map | Leaflet (via npm) | New (was CDN script tag) |
| Testing | pytest + FastAPI TestClient | Keep + expand |

---

## Files to Reuse (Copy to New Structure)

| File | Reuse | Changes Needed |
|---|---|---|
| `app/services/osrm.py` | Yes | Change `OSRM_BASE_URL` to configurable (env var), support multiple profiles (foot/car/bike) |
| `app/services/overpass.py` | Yes | Minor — add batch support improvements |
| `app/services/nominatim.py` | Yes | As-is |
| `app/services/opening_hours_utils.py` | Yes | As-is |
| `app/main.py` | No | Rebuild — new endpoints, new logic |
| `app/algorithms/*` | No | Remove — SA/NN not needed for core. Optional later. |
| `app/services/solver.py` | No | Remove — replaced by feasibility + scoring engine |
| `app/static/js/map.js` | No | Delete — replaced by Vue components |
| `app/templates/*` | No | Delete — Vue handles all rendering |
| `tests/*` | No | Rebuild — new test cases for new logic |

---

## Database Schema

```sql
-- Trips table
CREATE TABLE trips (
    id TEXT PRIMARY KEY,              -- UUID
    city TEXT NOT NULL,                -- "Budapest"
    start_lat REAL NOT NULL,
    start_lon REAL NOT NULL,
    end_lat REAL NOT NULL,
    end_lon REAL NOT NULL,
    start_time TEXT NOT NULL,          -- "09:00"
    end_time TEXT NOT NULL,            -- "18:00"
    date TEXT NOT NULL,                -- "2026-04-15"
    transport_mode TEXT NOT NULL DEFAULT 'foot',  -- foot/car/bicycle
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Places in a trip
CREATE TABLE places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    category TEXT,                     -- "museum", "cafe", "temple", etc.
    priority TEXT NOT NULL DEFAULT 'want',  -- "must", "want", "if_time"
    estimated_duration_min INTEGER,    -- user override in minutes (NULL = use category default)
    opening_hours TEXT,                -- OSM format string or NULL
    opening_hours_source TEXT,         -- "osm", "google", "user", NULL
    status TEXT NOT NULL DEFAULT 'pending',  -- "pending", "visiting", "done", "skipped"
    arrived_at TEXT,                   -- ISO timestamp
    departed_at TEXT,                  -- ISO timestamp
    created_at TEXT NOT NULL
);

-- Cached distance matrix
CREATE TABLE distance_cache (
    trip_id TEXT NOT NULL REFERENCES trips(id),
    from_place_id INTEGER NOT NULL,   -- -1 for start point, -2 for end point
    to_place_id INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    PRIMARY KEY (trip_id, from_place_id, to_place_id)
);
```

---

## API Endpoints

### Trip Management (REST)

```
POST   /api/trips                    → Create a new trip, returns { id, url }
GET    /api/trips/{id}               → Get trip state (all places, statuses)
PATCH  /api/trips/{id}               → Update trip settings (transport mode, times)
DELETE /api/trips/{id}               → Delete trip
```

### Place Management (REST)

```
POST   /api/trips/{id}/places        → Add a place
PATCH  /api/trips/{id}/places/{pid}  → Update place (duration, priority, hours)
DELETE /api/trips/{id}/places/{pid}  → Remove a place
```

### Place Search (REST)

```
GET    /api/search?q=museum&lat=47.5&lon=19.0  → Search OSM for places near location
POST   /api/geocode                             → Geocode a place name (existing)
```

### Check-In (REST)

```
POST   /api/trips/{id}/checkin       → { place_id, action: "arrived"|"done"|"skipped" }
```

### Feasibility + Recommendations (REST + SSE)

```
GET    /api/trips/{id}/feasibility?lat=47.5&lon=19.0  → Current feasibility for all places
GET    /api/trips/{id}/next?lat=47.5&lon=19.0          → "What Next?" top 3 recommendations
GET    /api/trips/{id}/stream?lat=47.5&lon=19.0        → SSE stream (pushes feasibility + alerts)
```

### Page Routes

```
GET    /                              → Landing page (create trip form)
GET    /trip/{id}                     → Trip dashboard page
```

---

## Core Algorithms

### 1. Feasibility Calculation

For each remaining (pending) place, compute:

```python
def calculate_feasibility(place, current_position, current_time, trip_end_time,
                          end_position, transport_mode, distance_cache):
    """
    Returns: { color: "green"|"yellow"|"red"|"gray"|"unknown",
               slack_minutes: float,
               closing_urgency_minutes: float | None,
               reason: str }
    """
    travel_to_place = distance_cache.get(current_position, place)
    arrival_at_place = current_time + travel_to_place
    visit_duration = place.estimated_duration or CATEGORY_DEFAULTS[place.category]
    departure_from_place = arrival_at_place + visit_duration
    travel_to_endpoint = distance_cache.get(place, end_position)
    finish_time = departure_from_place + travel_to_endpoint

    # Basic slack: how much spare time after visiting this place and reaching endpoint
    slack = trip_end_time - finish_time
    remaining_time = trip_end_time - current_time
    slack_ratio = slack / remaining_time if remaining_time > 0 else 0

    # Opening hours urgency
    if place.opening_hours:
        closing_time = parse_closing_time(place.opening_hours, trip_date)
        closing_urgency = closing_time - arrival_at_place  # how long until it closes after you arrive
        window_remaining = closing_time - current_time      # how long until it closes from now
    else:
        closing_urgency = None
        window_remaining = None

    # Color logic
    if slack < 0:
        color = "gray"       # impossible
        reason = "Not enough time to visit and reach endpoint"
    elif place.opening_hours and arrival_at_place > closing_time:
        color = "gray"       # closed by the time you arrive
        reason = f"Closed by the time you arrive ({format_time(arrival_at_place)})"
    elif place.opening_hours and window_remaining < 30 * 60:
        color = "red"        # opening hours urgency override
        reason = f"Closes in {format_duration(window_remaining)}"
    elif slack_ratio < 0.10:
        color = "red"        # very tight on time
        reason = "Very tight schedule"
    elif place.opening_hours and window_remaining < 2 * 60 * 60:
        color = "yellow"     # opening hours getting tight
        reason = f"Closes in {format_duration(window_remaining)}"
    elif slack_ratio < 0.30:
        color = "yellow"     # moderate slack
        reason = "Feasible but limited time"
    elif not place.opening_hours:
        color = "unknown"    # no hours data — show as feasible but uncertain
        reason = "No opening hours data — time-feasible"
    else:
        color = "green"      # comfortable
        reason = "Plenty of time"

    return {
        "color": color,
        "slack_minutes": slack / 60,
        "closing_urgency_minutes": closing_urgency / 60 if closing_urgency else None,
        "reason": reason,
    }
```

### 2. Category Duration Defaults

```python
# Default visit durations in minutes
CATEGORY_DEFAULTS = {
    "museum":     90,   # 1.5 hours
    "gallery":    60,   # 1 hour
    "temple":     45,
    "church":     30,
    "castle":     90,
    "monument":   15,
    "landmark":   10,
    "park":       60,
    "garden":     45,
    "cafe":       30,
    "restaurant": 75,   # 1.25 hours
    "bar":        60,
    "shop":       40,
    "market":     60,
    "theater":    120,  # 2 hours
    "zoo":        180,  # 3 hours
    "beach":      120,
    "viewpoint":  15,
    "other":      45,   # safe default
}
```

### 3. "What Next?" — Opportunity Cost Scoring

```python
def score_next_action(candidates, current_position, current_time, trip, distance_cache):
    """
    For each candidate place, calculate:
    - opportunity_cost: how many other places become unreachable if we skip this one now
    - urgency: how soon the visit window closes
    - proximity: travel time from current position
    - priority: user's must/want/if_time setting

    Returns top 3 with reasoning.
    """
    scores = []

    for place in candidates:
        if not is_feasible(place, current_position, current_time, trip, distance_cache):
            continue

        # Opportunity cost: simulate skipping this place
        # After visiting each other candidate first, would this place still be reachable?
        cost = 0
        for other in candidates:
            if other == place:
                continue
            # Simulate: visit 'other' first, then check if 'place' is still feasible
            time_after_other = (current_time
                                + distance_cache.get(current_position, other)
                                + get_duration(other))
            pos_after_other = other.position
            if not is_feasible(place, pos_after_other, time_after_other, trip, distance_cache):
                cost += 1  # this place becomes unreachable if we visit 'other' first

        # Normalize scores to 0-1 range
        opportunity_score = cost / max(len(candidates) - 1, 1)

        travel_time = distance_cache.get(current_position, place)
        max_travel = max(distance_cache.get(current_position, c) for c in candidates)
        proximity_score = 1 - (travel_time / max_travel) if max_travel > 0 else 1

        priority_weights = {"must": 1.0, "want": 0.5, "if_time": 0.2}
        priority_score = priority_weights.get(place.priority, 0.5)

        # Combined score
        total = (
            0.40 * opportunity_score +
            0.30 * proximity_score +
            0.30 * priority_score
        )

        # Generate human-readable reason
        reasons = []
        if opportunity_score > 0.5:
            reasons.append(f"high risk of becoming unreachable ({cost} conflicts)")
        if place.priority == "must":
            reasons.append("must-visit")
        if proximity_score > 0.8:
            reasons.append(f"nearby ({int(travel_time / 60)} min)")
        if place.opening_hours:
            closing = parse_closing_time(place.opening_hours, trip.date)
            if closing and closing - current_time < 2 * 3600:
                reasons.append(f"closes in {int((closing - current_time) / 60)} min")

        scores.append({
            "place": place,
            "score": total,
            "opportunity_cost": cost,
            "travel_minutes": travel_time / 60,
            "reason": " — ".join(reasons) if reasons else "good option",
        })

    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:3]
```

### 4. Opening Hours Resolution

```python
async def resolve_opening_hours(place):
    """
    Try OSM first, fall back to Google Places API.
    Returns opening_hours string and source, or (None, None).
    """
    # Step 1: Try Overpass (OSM)
    result = await overpass.get_opening_hours(place.lat, place.lon, place.name)
    if result and result.get("opening_hours"):
        return result["opening_hours"], "osm"

    # Step 2: Fall back to Google Places
    result = await google_places.get_opening_hours(place.lat, place.lon, place.name)
    if result:
        return result, "google"

    return None, None
```

---

## Pre-Slice-0 Checklist (Do This First)

Before writing any new feature code, complete these steps once:

### 1. Clean the experimental branch
```bash
# Remove old algorithms (replaced by feasibility engine)
rm -rf app/algorithms/

# Remove old solver service
rm app/services/solver.py

# Remove old frontend
rm -f app/static/js/map.js
rm -f app/templates/index.html
rm -f app/templates/base.html

# Remove old tests
rm -f tests/test_algorithms.py
rm -f tests/test_opening_hours.py
```

### 2. Strip main.py to a skeleton
```python
# app/main.py
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="PathFinder v2")

@app.get("/health")
async def health():
    return {"status": "ok"}

# Serve Vue build in production
FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
```

### 3. Set up Vue 3 + Vite
```bash
# From project root
npm create vite@latest frontend -- --template vue
cd frontend
npm install
npm install leaflet
npm install --save-dev @types/leaflet
```

### 4. Configure Vite proxy (frontend/vite.config.js)
```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
```

### 5. Update requirements.txt
Removed: `jinja2`, `python-multipart`, `black`
Added: `aiosqlite>=0.19.0`, `python-dotenv>=1.0.0`

### 6. Copy .env.example to .env and fill in values
```bash
cp .env.example .env
# Then edit .env with your Google Places API key
```

### 7. Verify
```bash
# Terminal 1: backend
uvicorn app.main:app --reload
# → http://localhost:8000/health should return {"status":"ok"}

# Terminal 2: frontend
cd frontend && npm run dev
# → http://localhost:5173 should show Vite/Vue welcome page
```

---

## Development Slices

### Slice 0: Infrastructure Setup
**Goal:** Project skeleton ready to develop on.

**Tasks:**
- [ ] Clean `experimental` branch: remove old UI, old endpoints, old algorithm files
- [ ] Keep: `osrm.py`, `overpass.py`, `nominatim.py`, `opening_hours_utils.py`
- [ ] Update `osrm.py`: make `OSRM_BASE_URL` configurable via environment variable
- [ ] Update `osrm.py`: add `profile` parameter to support foot/car/bicycle
- [ ] Set up self-hosted OSRM with Docker (Hungary extract)
  - Download Hungary OSM extract from Geofabrik
  - Pre-process for `foot` and `car` profiles
  - Docker Compose file for OSRM
- [ ] Set up SQLite database: create schema, migration script
- [ ] Set up Google Places API: create Google Cloud project, enable Places API, store key in `.env`
- [ ] Create `.env` file with: `OSRM_BASE_URL`, `GOOGLE_PLACES_API_KEY`, `DATABASE_PATH`
- [ ] Update `requirements.txt`: add `aiosqlite`, `python-dotenv`, `googlemaps` (or `httpx` for direct API calls)
- [ ] Create `app/db.py` — database connection and helper functions
- [ ] Create `app/models.py` — Pydantic models for API request/response
- [ ] Create `app/config.py` — load environment variables
- [ ] Verify: OSRM Docker responds, SQLite creates tables, Google API key works
- [ ] Write tests: DB connection, table creation, OSRM connectivity

**Deliverable:** Running server with empty endpoints, working database, OSRM responding locally.

---

### Slice 1: Create Trip + Add Places
**Goal:** User can create a trip, search for places, add them, and see them on a map.

**Backend tasks:**
- [ ] `POST /api/trips` — create trip, generate UUID, store in SQLite, return `{ id, url }`
- [ ] `GET /api/trips/{id}` — return trip with all places
- [ ] `GET /api/search?q=...&lat=...&lon=...` — search OSM via Overpass for POIs, return name/lat/lon/category/hours
- [ ] `POST /api/trips/{id}/places` — add place to trip (with category, priority, optional duration override)
- [ ] `DELETE /api/trips/{id}/places/{pid}` — remove place from trip
- [ ] `PATCH /api/trips/{id}/places/{pid}` — edit place (duration, priority, hours)
- [ ] On place add: resolve opening hours (OSM → Google fallback), store result
- [ ] On place add: compute distance from this place to all other places + start/end → cache in `distance_cache`

**Frontend tasks:**
- [ ] Landing page (`/`): trip creation form (city, date, times, start/end location, transport mode)
- [ ] On submit: POST to API, redirect to `/trip/{id}`
- [ ] Dashboard page (`/trip/{id}`): Leaflet map + place search bar + category buttons
- [ ] Search: call `/api/search`, show results as pins on map with "Add" button
- [ ] Place list panel: show all added places with name, category, priority selector, duration, hours, remove button
- [ ] Duration: show category default, editable field for user override
- [ ] Opening hours: show if available, "unknown" badge if not, editable field for manual entry
- [ ] Map: show all added places as markers, start/end point markers in different style

**Tests:**
- [ ] Create trip → verify UUID returned, trip in DB
- [ ] Add place → verify stored with correct fields
- [ ] Delete place → verify removed
- [ ] Search endpoint → verify returns results from Overpass
- [ ] Opening hours resolution → verify OSM-first, Google fallback

**Deliverable:** User can create a trip, search and add places, see them on a map with metadata.

---

### Slice 2: Feasibility Engine + Map Coloring
**Goal:** Map shows color-coded feasibility for every place.

**Backend tasks:**
- [ ] Create `app/engine/feasibility.py` — the feasibility calculation function (see algorithm above)
- [ ] Create `app/engine/category_defaults.py` — duration defaults table
- [ ] `GET /api/trips/{id}/feasibility?lat=...&lon=...` endpoint
  - Accept current position (from geolocation or manual)
  - If no position provided, use trip start location
  - Compute current time from server clock (or accept `&time=` param for testing)
  - For each pending place: calculate feasibility color + reason
  - Return: `{ current_time, remaining_minutes, places: [{ id, color, reason, slack_minutes, ... }] }`
- [ ] Handle distance cache: if current position is new, fetch OSRM distances to all places + endpoint, cache them

**Frontend tasks:**
- [ ] On dashboard load: request browser geolocation
  - If granted: use coordinates
  - If denied: show "tap your location on map" or use trip start point
- [ ] Call `/api/trips/{id}/feasibility` with position
- [ ] Color-code map markers: green/yellow/red/gray/blue(unknown)
- [ ] Time budget bar at top: visual progress bar showing time used vs remaining
- [ ] Stats line: "Visited: X · Remaining: Y · Reachable: Z"
- [ ] Place list: add color dot + reason text next to each place
- [ ] Auto-refresh feasibility when page becomes visible (Page Visibility API)

**Tests:**
- [ ] Feasibility with all places open and plenty of time → all green
- [ ] Feasibility with one place closing in 20 min → red
- [ ] Feasibility with place too far to reach → gray
- [ ] Feasibility with no opening hours → unknown (blue)
- [ ] Feasibility with tight total schedule → yellow/red based on percentage
- [ ] Opening hours override: percent-based says green, but closing in 25 min → red

**Deliverable:** User sees a live color-coded feasibility map that updates with their position.

---

### Slice 3: "What Next?" Recommendation Engine
**Goal:** User taps "What Next?" and gets the top recommendation + 2 alternatives.

**Backend tasks:**
- [ ] Create `app/engine/scoring.py` — opportunity cost scoring function (see algorithm above)
- [ ] `GET /api/trips/{id}/next?lat=...&lon=...` endpoint
  - Compute feasibility for all pending places
  - Filter to feasible only (not gray)
  - Score each using opportunity cost algorithm
  - Return top 3: `{ recommendations: [{ place, score, travel_minutes, reason }, ...] }`
- [ ] Edge cases:
  - 0 feasible places → return `{ recommendations: [], message: "No more places reachable. Head to your endpoint." }`
  - 1 feasible place → return just that one
  - All "must visit" are done → deprioritize remaining, suggest heading to endpoint if time is tight

**Frontend tasks:**
- [ ] "What Next?" button (prominent, always visible on dashboard)
- [ ] On tap: call `/api/trips/{id}/next` with current position
- [ ] Display result card:
  ```
  ┌─────────────────────────────────────┐
  │ → Market E                          │
  │   12 min walk                       │
  │   "nearby — closes in 2 hours"      │
  │                    [Navigate] [Skip] │
  ├─────────────────────────────────────┤
  │ Also good:                          │
  │ · Café B — 8 min, no time pressure  │
  │ · Museum C — 25 min, must-visit     │
  └─────────────────────────────────────┘
  ```
- [ ] "Navigate" button: open Google Maps / Apple Maps directions in new tab
- [ ] "Skip" button: skip this recommendation, show next
- [ ] Highlight recommended place on map (pulse animation or different marker style)

**Tests:**
- [ ] With must-visit at risk → must-visit scores highest
- [ ] With nearby easy place and far urgent place → verify scoring balance
- [ ] Opportunity cost: place A only reachable now → A scores highest
- [ ] All places equivalent → closest wins (proximity tiebreak)
- [ ] No feasible places → correct empty response

**Deliverable:** Working "What Next?" with opportunity-cost scoring and human-readable reasoning.

---

### Slice 4: Check-In Flow
**Goal:** User can mark places as arrived/done/skipped, and everything recalculates.

**Backend tasks:**
- [ ] `POST /api/trips/{id}/checkin` — `{ place_id, action: "arrived"|"done"|"skipped" }`
  - `arrived`: set `status = "visiting"`, `arrived_at = now()`
  - `done`: set `status = "done"`, `departed_at = now()`
  - `skipped`: set `status = "skipped"`
- [ ] On any check-in: update distance cache from new current position (the place they just arrived at / left)
- [ ] Validation: can't "done" a place that isn't "visiting", can't "arrive" at a place already "done"

**Frontend tasks:**
- [ ] Two persistent buttons at bottom of dashboard:
  - `[ I arrived somewhere ]`
  - `[ I'm leaving / Done ]`
- [ ] "I arrived" → opens place picker:
  - If geolocation available: show places sorted by distance from current position (nearest first)
  - Otherwise: show full list sorted by name
  - User taps a place → sends `checkin(arrived)`
- [ ] "I'm leaving" → shows currently visiting place with one-tap "Done" confirmation
- [ ] "Skip" button available on each place in the place list
- [ ] After any check-in action:
  - Re-fetch feasibility → map colors update
  - Time budget bar updates
  - Place list updates (move to done/skipped section)
- [ ] Place list sections: "Now visiting" (if any) → "Remaining" → "Completed" → "Skipped"

**Tests:**
- [ ] Arrive → place status is "visiting"
- [ ] Done → place status is "done", departed_at set
- [ ] Skip → place status is "skipped"
- [ ] Feasibility recalculates after check-in with updated time
- [ ] Can't arrive at already-done place (400 error)
- [ ] Arrived_at and departed_at timestamps are correct

**Deliverable:** Full check-in loop working. User arrives, visits, leaves, and the system adapts.

---

### Slice 5: SSE Urgency Alerts
**Goal:** Dashboard receives real-time alerts when feasibility changes significantly.

**Backend tasks:**
- [ ] `GET /api/trips/{id}/stream?lat=...&lon=...` — SSE endpoint
  - On connect: send current feasibility snapshot
  - Every 60 seconds (server-side timer): recompute feasibility, compare with last state
  - If any place changed color (e.g., yellow → red): push alert event
  - If a must-visit is about to become unreachable: push urgent alert
  - Event types:
    - `feasibility_update`: full feasibility data (same as REST endpoint)
    - `urgency_alert`: `{ place_id, place_name, message: "closes in 30 min", severity: "warning"|"critical" }`
- [ ] Close stream cleanly when client disconnects

**Frontend tasks:**
- [ ] On dashboard load: connect to SSE stream
- [ ] On `feasibility_update`: refresh map colors and place list (same rendering as Slice 2)
- [ ] On `urgency_alert`: show notification banner at top of dashboard
  - Warning (yellow): "Museum C closes in 1 hour — consider going soon"
  - Critical (red): "Museum C closes in 30 min — leave now or you'll miss it"
  - Auto-dismiss after 30 seconds, or user taps to dismiss
- [ ] Reconnect on stream disconnect (EventSource handles this automatically)

**Tests:**
- [ ] SSE connection established → receives initial feasibility
- [ ] Simulated time advance → place goes yellow → red → alert pushed
- [ ] Must-visit about to expire → critical alert
- [ ] Client disconnect → server cleans up

**Deliverable:** Live updating dashboard with proactive urgency alerts.

---

### Slice 6: Transport Mode Switching
**Goal:** User can change transport mode mid-trip and everything recalculates.

**Backend tasks:**
- [ ] `PATCH /api/trips/{id}` — accepts `{ transport_mode: "car" }`
- [ ] On mode change: invalidate distance cache for this trip
- [ ] Recompute all distances using new OSRM profile
- [ ] Update `osrm.py`: accept `profile` parameter (`foot`, `car`, `bicycle`)

**Frontend tasks:**
- [ ] Transport mode selector on dashboard (dropdown or toggle: Walk / Drive / Bike)
- [ ] On change: call PATCH, then re-fetch feasibility
- [ ] Show current mode indicator on dashboard

**OSRM setup:**
- [ ] Pre-process Hungary extract for `foot` profile
- [ ] Pre-process Hungary extract for `car` profile
- [ ] Pre-process Hungary extract for `bicycle` profile
- [ ] Docker Compose: run separate OSRM containers per profile, or single instance with profile switching

**Tests:**
- [ ] Switch foot → car: travel times decrease, more places become green
- [ ] Switch car → foot: travel times increase, some places go yellow/red/gray
- [ ] Distance cache invalidated on mode switch

**Deliverable:** User can switch transport mode and see feasibility update accordingly.

---

### Slice 7: Polish + Edge Cases
**Goal:** Handle all edge cases, improve UX, prepare for thesis demo.

**Tasks:**
- [ ] Edge case: trip with 0 places → show helpful empty state
- [ ] Edge case: all places done → show summary ("You visited 8/12 places!")
- [ ] Edge case: trip end time passed → show "Trip ended" state with summary
- [ ] Edge case: no internet → show last cached state with "offline" indicator
- [ ] Edge case: OSRM unreachable → graceful error, show straight-line distance estimates
- [ ] Edge case: Google Places API fails → skip gracefully, mark hours as unknown
- [ ] Mobile responsive: test and fix dashboard on phone-sized screens
- [ ] Loading states: skeleton UI while fetching feasibility
- [ ] Error toasts: user-friendly error messages for API failures
- [ ] Favicon and page title: "PathFinder — {trip city}"
- [ ] Share: copy trip URL button

**Tests:**
- [ ] All edge case scenarios covered
- [ ] API error responses have correct status codes and messages

**Deliverable:** Production-quality UX, no crashes, graceful degradation.

---

## OSRM Docker Setup (Hungary)

```bash
# 1. Create directory for OSRM data
mkdir -p osrm-data && cd osrm-data

# 2. Download Hungary extract from Geofabrik
wget https://download.geofabrik.de/europe/hungary-latest.osm.pbf

# 3. Pre-process for foot profile
docker run -t -v $(pwd):/data osrm/osrm-backend osrm-extract -p /opt/foot.lua /data/hungary-latest.osm.pbf
docker run -t -v $(pwd):/data osrm/osrm-backend osrm-partition /data/hungary-latest.osrm
docker run -t -v $(pwd):/data osrm/osrm-backend osrm-customize /data/hungary-latest.osrm

# 4. Run OSRM server
docker run -t -p 5000:5000 -v $(pwd):/data osrm/osrm-backend osrm-routed --algorithm mld /data/hungary-latest.osrm

# 5. Test
curl "http://localhost:5000/table/v1/foot/19.0402,47.4979;19.0515,47.5007?annotations=duration"
```

For multiple profiles, repeat extract step with `-p /opt/car.lua` and `-p /opt/bicycle.lua`,
store in separate directories, run on different ports (5000, 5001, 5002).

---

## Docker Compose (Full Stack)

```yaml
version: "3.8"

services:
  osrm-foot:
    image: osrm/osrm-backend
    command: osrm-routed --algorithm mld /data/hungary-latest.osrm
    volumes:
      - ./osrm-data/foot:/data
    ports:
      - "5000:5000"

  osrm-car:
    image: osrm/osrm-backend
    command: osrm-routed --algorithm mld /data/hungary-latest.osrm
    volumes:
      - ./osrm-data/car:/data
    ports:
      - "5001:5000"

  osrm-bicycle:
    image: osrm/osrm-backend
    command: osrm-routed --algorithm mld /data/hungary-latest.osrm
    volumes:
      - ./osrm-data/bicycle:/data
    ports:
      - "5002:5000"

  pathfinder:
    build: .
    ports:
      - "8000:8000"
    environment:
      - OSRM_FOOT_URL=http://osrm-foot:5000
      - OSRM_CAR_URL=http://osrm-car:5000
      - OSRM_BICYCLE_URL=http://osrm-bicycle:5000
      - GOOGLE_PLACES_API_KEY=${GOOGLE_PLACES_API_KEY}
      - DATABASE_PATH=/data/pathfinder.db
    volumes:
      - ./data:/data
    depends_on:
      - osrm-foot
      - osrm-car
      - osrm-bicycle
```

---

## Environment Variables

```env
# .env
OSRM_FOOT_URL=http://localhost:5000
OSRM_CAR_URL=http://localhost:5001
OSRM_BICYCLE_URL=http://localhost:5002
GOOGLE_PLACES_API_KEY=your_key_here
DATABASE_PATH=./data/pathfinder.db
```

---

## File Structure (Final)

```
PathFinder/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, page routes
│   ├── config.py                # Environment variables
│   ├── db.py                    # SQLite connection, helpers
│   ├── models.py                # Pydantic request/response models
│   ├── api/
│   │   ├── __init__.py
│   │   ├── trips.py             # Trip CRUD endpoints
│   │   ├── places.py            # Place management endpoints
│   │   ├── search.py            # Place search endpoint
│   │   ├── checkin.py           # Check-in endpoint
│   │   ├── feasibility.py       # Feasibility endpoint
│   │   ├── next_action.py       # "What Next?" endpoint
│   │   └── stream.py            # SSE endpoint
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── feasibility.py       # Feasibility calculation logic
│   │   ├── scoring.py           # Opportunity cost scoring logic
│   │   └── category_defaults.py # Duration defaults table
│   ├── services/
│   │   ├── __init__.py
│   │   ├── osrm.py              # OSRM client (reused, updated)
│   │   ├── overpass.py           # Overpass API client (reused)
│   │   ├── nominatim.py          # Nominatim geocoding (reused)
│   │   ├── opening_hours_utils.py # Opening hours parser (reused)
│   │   └── google_places.py     # Google Places API client (new)
│   └── static/                  # Only serves Vue build output in production
├── frontend/                    # Vue 3 + Vite SPA
│   ├── src/
│   │   ├── main.js              # Vue app entry point
│   │   ├── App.vue              # Root component + router-view
│   │   ├── components/
│   │   │   ├── TripForm.vue     # Landing page: create trip form
│   │   │   ├── PlaceSearch.vue  # Search bar + category buttons
│   │   │   ├── PlaceCard.vue    # Single place row with status/color
│   │   │   ├── PlaceList.vue    # List of all places (pending/done/skipped)
│   │   │   ├── MapView.vue      # Leaflet map wrapper component
│   │   │   ├── FeasibilityBar.vue # Time budget progress bar
│   │   │   ├── NextAction.vue   # "What Next?" card with alternatives
│   │   │   ├── CheckinModal.vue # Arrived/Done/Skip picker
│   │   │   └── UrgencyAlert.vue # SSE urgency notification banner
│   │   ├── views/
│   │   │   ├── HomeView.vue     # Landing page (uses TripForm)
│   │   │   └── DashboardView.vue # Trip dashboard (uses all components)
│   │   ├── composables/
│   │   │   ├── useTrip.js       # Trip state + API calls
│   │   │   ├── useFeasibility.js # Feasibility fetch + state
│   │   │   ├── useSSE.js        # SSE connection management
│   │   │   └── useGeolocation.js # Browser geolocation wrapper
│   │   └── router/
│   │       └── index.js         # Vue Router: / and /trip/:id
│   ├── package.json
│   └── vite.config.js           # Vite config with FastAPI proxy
├── tests/
│   ├── __init__.py
│   ├── test_feasibility.py      # Feasibility engine tests
│   ├── test_scoring.py          # Opportunity cost scoring tests
│   ├── test_api_trips.py        # Trip CRUD API tests
│   ├── test_api_checkin.py      # Check-in API tests
│   ├── test_api_feasibility.py  # Feasibility API integration tests
│   └── test_api_next.py         # "What Next?" API integration tests
├── osrm-data/                   # OSRM preprocessed data (gitignored)
│   ├── foot/
│   ├── car/
│   └── bicycle/
├── data/                        # SQLite database (gitignored)
│   └── pathfinder.db
├── docker-compose.yml
├── Dockerfile
├── .env                         # Environment variables (gitignored)
├── .env.example
├── requirements.txt
├── pyproject.toml
├── STRATEGY.md                  # This file
└── README.md
```

---

## Estimated Slice Sizes

| Slice | Description | Relative Size |
|---|---|---|
| 0 | Infrastructure setup | Small |
| 1 | Create trip + add places | Large (most UI work) |
| 2 | Feasibility engine + map coloring | Medium |
| 3 | "What Next?" scoring engine | Medium |
| 4 | Check-in flow | Medium |
| 5 | SSE urgency alerts | Small |
| 6 | Transport mode switching | Small |
| 7 | Polish + edge cases | Medium |

**Recommended order:** 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7

Each slice is independently demo-able. If you complete through Slice 4, you have a fully functional thesis demo. Slices 5-7 are enhancements.

---

## Minimum Viable Thesis Demo (Slices 0-4)

After completing slices 0 through 4, you can demonstrate:

1. "I create a trip to Budapest, 9 AM to 6 PM"
2. "I search for places and add 10 to my list, marking 3 as must-visit"
3. "The map shows all places color-coded by feasibility"
4. "I tap What Next — it recommends the Hungarian National Museum because it has the highest opportunity cost"
5. "I arrive at the museum, check in, spend 2 hours"
6. "I check out — the map updates, one place turned gray (no longer reachable), another turned red (closing soon)"
7. "I tap What Next again — different recommendation, adapted to my new situation"

This is a complete, compelling thesis demo.
