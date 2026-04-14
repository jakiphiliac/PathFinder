# PathFinder — Project Overview

## What is PathFinder?

PathFinder is a **reactive journey companion** for city trip planning, built as a thesis project. Users add places they want to visit during a day trip. The "What Next?" engine recommends the optimal next destination based on closing times, priority, and proximity. Google Maps handles turn-by-turn navigation. As the user checks in to places, the map accumulates a Flighty-style trajectory of their journey.

---

## Motivation

Planning a day trip is harder than it looks. Opening hours vary, walking times between spots add up, and visiting places in the wrong order can mean arriving after closing time. Existing tools (Google Maps, etc.) don't solve the scheduling problem — they route between two fixed points but don't adapt in real time as the user moves through their day.

PathFinder takes a different approach: instead of computing a single "optimal route" up front (which breaks the moment reality diverges from the plan), it gives the user a **live feasibility dashboard** and an on-demand "What Next?" recommendation. The user stays in control; the app provides guidance.

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Backend | Python + FastAPI | Async I/O, SSE streaming support, auto-generated API docs |
| Frontend | Vue 3 + Vite | Reactive UI, fast dev cycle, Composition API |
| Maps | Leaflet.js | Open-source, no API key needed |
| Routing | OSRM (self-hosted Docker) | Free, no usage limits, real walking/driving/cycling times |
| Geocoding | Nominatim (OpenStreetMap) | Free, no API key, OSM data |
| Opening Hours | Overpass API (primary) + Google Places (fallback) | Structured OSM data with fallback |
| Database | SQLite via aiosqlite | Lightweight persistence, UUID-based trip URLs |

---

## Tooling & Linting

This project uses a small set of formatters and linters for developer ergonomics. Recent update: ESLint has been added to the frontend to provide Vue-specific static linting; Prettier remains the frontend formatter and Ruff is used for Python.

- Backend (Python)
  - Formatter & linter: `ruff`
  - Common commands:
    - `ruff format` — format files in-place
    - `ruff check app/ tests/` — report lint issues
    - `ruff check --fix app/ tests/` — auto-fix fixable issues

- Frontend (Vue / JS / CSS)
  - Formatter: `prettier`
  - Linter: `eslint` with `eslint-plugin-vue` and `eslint-config-prettier`
  - Files added:
    - `frontend/.eslintrc.cjs` — ESLint configuration
    - `frontend/.eslintignore`
    - `frontend/package.json` scripts: `format`, `lint`, `lint:fix`
  - Install & use (from `frontend/`):
    - `npm install --save-dev eslint eslint-plugin-vue eslint-config-prettier`
    - `npm run format` — Prettier (rewrites files)
    - `npm run lint` — ESLint (reports issues)
    - `npm run lint:fix` — ESLint with `--fix` (auto-fix where possible)

Recommendations:
- CI should run `ruff check`, a Python type checker (Pyright or mypy), `npm run lint`, and the test suite.

---
## Core Algorithms

### 1. Feasibility Scoring

For each unvisited place, compute a **slack** value: how much spare time would remain after visiting this place and still reaching the trip endpoint on time.

```
slack = trip_end_time − (arrival_time + visit_duration + travel_to_endpoint)
slack_ratio = slack / remaining_time
```

Each place is colored:

| Color | Meaning | Condition |
|---|---|---|
| Green | Comfortable | slack_ratio ≥ 30% and closing not imminent |
| Yellow | Tight | slack_ratio 10–30% or closing within 2 hours |
| Red | High pressure | slack_ratio < 10% or closing within 30 min |
| Gray | Impossible | slack < 0 or already closed |
| Unknown | No hours data | Time-feasible but opening hours unknown |

Trip times are always interpreted in the trip's local timezone (stored per-trip, auto-detected from browser at creation).

### 2. "What Next?" Opportunity Cost Scoring

Recommends the best next place using a weighted score:

```
score = 0.40 × opportunity_cost
      + 0.30 × proximity
      + 0.30 × priority_weight
```

- **Opportunity cost:** How many other places become unreachable if we skip this one now
- **Proximity:** Normalized travel time from current position
- **Priority:** User-assigned importance (must=1.0, want=0.5, if_time=0.2)

Returns top 3 recommendations with plain-language reasoning.

### 3. Category Duration Defaults

20 place categories with sensible default visit durations (museum=90min, cafe=30min, etc.), overridable per place by the user.

---

## Opening Hours Pipeline

1. Query **Overpass API** for the `opening_hours` OSM tag near each place (3 mirror endpoints for reliability)
2. Parse the OSM format string (e.g., `"Mo-Fr 09:00-17:00; Sa 10:00-15:00"`) into time windows
3. Handle edge cases: split periods, overnight hours, 24/7, missing data
4. Fall back to **Google Places API** if OSM data is unavailable

---

## System Architecture

```
User Browser (Vue 3 SPA)
        │
        ▼
FastAPI Backend (port 8000)
        │
   ┌────┴─────────┬──────────────┐
   │              │              │
OSRM Docker   Overpass API   Nominatim
(foot/car/    (opening       (geocoding)
 bicycle)      hours)
        │
   Google Places API
   (opening hours fallback)
        │
   SQLite DB
   (trip + place + trajectory persistence)
```

Frontend communicates via REST + **Server-Sent Events (SSE)** for real-time feasibility updates and urgency alerts.

---

## Journey Flow

1. User creates trip: city, arrive-by time, start location, transport mode, closed/open trip type
2. Adds places with priority and expected duration — map shows feasibility-colored pins
3. Taps "What Next?" — algorithm suggests best next destination based on current position
4. Taps "Go" — Google Maps opens in new tab with directions; app waits for return
5. User returns to app — "Did you arrive at [place]?" prompt for one-tap check-in
6. On arrival: trajectory segment drawn on map (road-following arc, like Flighty flight arcs)
7. Taps "Done" when finished — manually requests next recommendation when ready
8. Repeat until all places visited:
   - Closed trip: "Head back to your starting point"
   - Open trip: "Head to your final destination"
9. Trip is archived — user can view a summary page with stats, map, and place list

---

## Trajectory System

- Journey segments stored in `trajectory_segments` table (survives page refresh)
- On "arrived" check-in: OSRM route geometry fetched from last position to arrived place
- If OSRM is unavailable, the segment is **skipped** (no fake straight lines drawn)
- Frontend draws segments as semi-transparent purple polylines (encoded polyline decoding inline)

---

## Database Schema

```sql
trips               -- city, coords, times, transport_mode, timezone, status (active/archived), completed_at
places              -- name, coords, category, priority, hours, status, timestamps
distance_cache      -- cached OSRM travel times between places
trajectory_segments -- completed journey arcs (geometry, distance, duration)
```

---

## Key Design Decisions

**Why "What Next?" instead of a pre-computed route?**
A single optimal route breaks the moment reality diverges from the plan. "What Next?" adapts to where the user actually is, what time it is now, and what's still reachable.

**Why OSRM instead of Google Maps?**
OSRM is open-source, self-hostable, and has no usage limits or billing — important for a thesis project needing reproducibility and no external API dependencies.

**Why skip trajectory segments when OSRM is down?**
A straight-line arc through buildings is visually wrong and misleading. A missing segment is better than a fake one.

**Why SQLite?**
Lightweight persistence for a single-user thesis project. UUID-based trip URLs provide sharable links without authentication.

**Why monolithic Vue views?**
Home.vue, Dashboard.vue, and Summary.vue are intentionally monolithic for thesis scope. Component decomposition would add complexity without benefit at this scale.
