# PathFinder — Architecture & Design Reference

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (Vue 3)                      │
│  ┌──────────┐ ┌──────────────┐ ┌───────────┐ ┌───────────┐  │
│  │ Home.vue │ │Dashboard.vue │ │ api.js    │ │ router.js │  │
│  │(trip form)│ │(trip UI)    │ │(all calls)│ │           │  │
│  └──────────┘ └──────────────┘ └───────────┘ └───────────┘  │
│                        │ REST + SSE                          │
├────────────────────────┼─────────────────────────────────────┤
│  FastAPI Backend        │                                    │
│  ┌────────────┐ ┌──────┴───────┐ ┌──────────────────────┐   │
│  │ trips.py   │ │ feasibility  │ │ next_action.py       │   │
│  │ places.py  │ │ .py          │ │ (What Next? scoring) │   │
│  │ checkin.py │ └──────────────┘ └──────────────────────┘   │
│  │ trajectory │ ┌──────────────┐ ┌──────────────────────┐   │
│  │ .py        │ │ stream.py    │ │ search.py            │   │
│  └────────────┘ │ (SSE)        │ └──────────────────────┘   │
│                 └──────────────┘                             │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Engine: feasibility.py · scoring.py · category_defaults │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Services: osrm.py · overpass.py · hours.py ·            │ │
│  │           google_places.py · http_client.py             │ │
│  └─────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  External Services                                           │
│  ┌──────────┐ ┌──────────────┐ ┌────────────────────────┐   │
│  │ OSRM     │ │ Overpass API │ │ Google Places API      │   │
│  │ (Docker) │ │ (OSM hours)  │ │ (fallback hours)       │   │
│  │foot/car/ │ └──────────────┘ └────────────────────────┘   │
│  │bicycle   │ ┌──────────────┐                              │
│  └──────────┘ │ Nominatim    │                              │
│               │ (geocoding)  │                              │
│               └──────────────┘                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Scope | Single user, no auth | Thesis scope — UUID URL is sufficient |
| 2 | Trip type | Closed (return to start) or Open (different endpoint) | Covers both day-trip patterns |
| 3 | Location input | Search (Nominatim) + "Pick on map" | Two clear options per fieldset |
| 4 | Time input | Arrive-by time primary; depart-at optional (defaults to now) | Users think in terms of "must be back by" |
| 5 | Place data | Overpass/OSM + manual override | Free, no usage limits |
| 6 | Duration estimation | Category defaults + user override per place | 20 categories; users can adjust |
| 7 | Feasibility coloring | Percentage-based slack + opening hours urgency | Visual at-a-glance status |
| 8 | "What Next?" | Opportunity cost scoring (minimize regret) | Adapts to where user actually is |
| 9 | Navigation | Google Maps in new tab | No re-implementation of turn-by-turn |
| 10 | Trajectory | Recorded retrospectively on "arrived" check-in | Flighty-style visual record |
| 11 | OSRM unavailable | Feasibility falls back to Haversine; trajectory skips segment | Straight-line trajectory arcs are misleading |
| 12 | Persistence | SQLite + UUID trip URL | Lightweight, shareable, no login |
| 13 | API design | REST for mutations + SSE for live feasibility updates | SSE is simpler than WebSockets for one-way push |
| 14 | Transport mode | User picks per trip, can switch mid-trip | Invalidates distance cache, recomputes |
| 15 | Timezone | Stored per trip, auto-detected from browser | Opening hours interpreted in trip's local time |
| 16 | Frontend structure | Two monolithic views (Home.vue, Dashboard.vue) | Acceptable for thesis scope |
| 17 | OSRM hosting | Self-hosted Docker (Hungary extract) | No API costs, reproducible |
| 18 | Testing | pytest unit + integration tests, OSRM-resilient | 72 tests covering all slices |

---

## Database Schema

```sql
CREATE TABLE trips (
    id TEXT PRIMARY KEY,              -- UUID
    city TEXT NOT NULL,
    start_lat REAL NOT NULL,
    start_lon REAL NOT NULL,
    end_lat REAL NOT NULL,            -- same as start for closed trips
    end_lon REAL NOT NULL,
    start_time TEXT NOT NULL,         -- "09:00" (defaults to current time)
    end_time TEXT NOT NULL,           -- "18:00" (arrive-by time)
    date TEXT NOT NULL,               -- "2026-04-15" (defaults to today)
    transport_mode TEXT NOT NULL DEFAULT 'foot',  -- foot/car/bicycle
    timezone TEXT NOT NULL DEFAULT 'UTC',         -- e.g. "Europe/Budapest"
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    category TEXT,
    priority TEXT NOT NULL DEFAULT 'want',        -- must/want/if_time
    estimated_duration_min INTEGER,               -- NULL = use category default
    opening_hours TEXT,                           -- OSM format or NULL
    opening_hours_source TEXT,                    -- "osm"/"google"/"user"/NULL
    status TEXT NOT NULL DEFAULT 'pending',       -- pending/visiting/done/skipped
    arrived_at TEXT,
    departed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE distance_cache (
    trip_id TEXT NOT NULL REFERENCES trips(id),
    from_place_id INTEGER NOT NULL,
    to_place_id INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    PRIMARY KEY (trip_id, from_place_id, to_place_id)
);

CREATE TABLE trajectory_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    from_lat REAL NOT NULL,
    from_lon REAL NOT NULL,
    to_lat REAL NOT NULL,
    to_lon REAL NOT NULL,
    place_id INTEGER,                             -- destination place
    geometry TEXT NOT NULL DEFAULT '',            -- encoded polyline from OSRM
    distance_meters REAL NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
```

---

## API Endpoints

### Trip Management
```
POST   /api/trips                              → Create trip (validates end_time > start_time)
GET    /api/trips/{id}                         → Get trip with all places
PATCH  /api/trips/{id}                         → Update transport_mode / times / timezone
DELETE /api/trips/{id}                         → Delete trip + cascade all child records
```

### Place Management
```
POST   /api/trips/{id}/places                  → Add place (triggers hours resolution + distance caching)
PATCH  /api/trips/{id}/places/{pid}            → Update priority, duration, opening_hours
DELETE /api/trips/{id}/places/{pid}            → Remove place + clean distance cache
```

### Search & Geocoding
```
GET    /api/search?q=...&lat=...&lon=...       → OSM search via Overpass, Nominatim fallback
GET    /api/geocode?q=...                      → Geocode address via Nominatim
```

### Check-In & Trajectory
```
POST   /api/trips/{id}/checkin                 → { place_id, action: "arrived"|"done"|"skipped" }
                                                  "arrived": records trajectory segment if OSRM available
GET    /api/trips/{id}/trajectory              → All segments ordered by created_at
```

### Feasibility & Recommendations
```
GET    /api/trips/{id}/feasibility?lat=&lon=   → Feasibility for all pending places
GET    /api/trips/{id}/next?lat=&lon=          → Top 3 "What Next?" recommendations
GET    /api/trips/{id}/stream?lat=&lon=        → SSE: feasibility_update + urgency_alert (every 60s)
```

### System
```
GET    /health                                 → { status: "ok" }
```

---

## Core Algorithms

### Feasibility Calculation

```python
# For each pending place:
travel_to_place    = distance_cache(current_pos → place)         # OSRM or Haversine fallback
arrival_at_place   = current_time + travel_to_place
visit_duration     = place.estimated_duration or CATEGORY_DEFAULTS[place.category]
departure          = arrival_at_place + visit_duration
travel_to_endpoint = distance_cache(place → end_pos)
finish_time        = departure + travel_to_endpoint

slack              = trip_end_time - finish_time
slack_ratio        = slack / remaining_time

# Color logic (opening hours urgency can override):
# gray    → slack < 0 or arrival after closing
# red     → slack_ratio < 10% or closing within 30 min
# yellow  → slack_ratio 10-30% or closing within 2 hours
# green   → slack_ratio ≥ 30%
# unknown → time-feasible but no opening hours data
```

All times interpreted in the trip's stored timezone (ZoneInfo).

### "What Next?" Scoring

```python
score = 0.40 × opportunity_cost   # how many places become unreachable if skipped
      + 0.30 × proximity_score    # 1 - (travel_time / max_travel_time)
      + 0.30 × priority_score     # must=1.0, want=0.5, if_time=0.2
```

Returns top 3 with plain-language reasoning (e.g., "closes soon", "nearby", "must-visit").

### OSRM Fallback (Haversine)

When OSRM is unreachable, feasibility uses straight-line Haversine estimates:
- Detour factor: 1.4× (accounts for road geometry)
- Profile speeds: foot=5 km/h, bicycle=15 km/h, car=40 km/h

Trajectory segments are **not** recorded when OSRM is down — no fake straight lines.

---

## Check-In State Machine

```
pending ──→ arrived (visiting)
        └─→ skipped

visiting ──→ done
         └─→ skipped
```

Invalid transitions return HTTP 400. "arrived" triggers trajectory recording if OSRM available.

---

## Frontend Key Behaviors

- **Place names** displayed as text before first comma (short name only)
- **Search** clears query + results after adding a place
- **What Next? card** dismisses immediately when "Go" is tapped
- **Pending arrival** remembers which place "Go" was tapped on; shown as default check-in prompt
- **What Next? + check-in sections** hidden when all places are done
- **Closed trip completion**: "Head back to your starting point" banner + Google Maps link
- **Open trip completion**: "Head to your final destination" banner + Google Maps link
- **Frontend validation**: end_time must be after start_time; shown before API call
- **SSE alerts**: urgency banners auto-dismiss after 30 seconds, max 5 visible

---

## SSE Event Format

```json
// feasibility_update
{ "places": [{ "place_id": 1, "color": "yellow", "reason": "Closes in 45 min", ... }] }

// urgency_alert
{ "place_id": 1, "place_name": "Museum", "message": "Closes in 25 minutes!", "severity": "critical" }
```

Alert triggers: color degradation (green→yellow, yellow→red, any→gray) or must-visit closing within 30/60 min.
