# PathFinder — Project Overview

## What is PathFinder?

PathFinder is a **travel route optimizer** built as a thesis project. Given a list of places a user wants to visit in a day, it computes the most efficient visiting order while respecting each place's **opening hours** and the user's available time window.

The project has evolved through two generations:

- **v1 — Classical TSP Solver:** Takes a list of places, computes an optimal route using combinatorial optimization, and streams results in real-time.
- **v2 — Feasibility-Guided Exploration Engine (current):** Instead of outputting a single "best" route, it gives the user a live dashboard showing which places are reachable right now, which are borderline, and which are effectively impossible — along with a "What Next?" recommendation engine.

---

## Motivation

Planning a day trip is harder than it looks. Opening hours vary, walking times between spots add up, and visiting places in the wrong order can mean arriving after closing time. Existing tools (Google Maps, etc.) don't optimize for this — they route between two fixed points but don't solve the scheduling problem.

PathFinder frames this as a **Travelling Salesman Problem with Time Windows (TSPTW)**: find the shortest-distance tour that visits all locations within their allowed time windows.

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Backend | Python + FastAPI | Async I/O, auto-generated API docs, SSE streaming support |
| Frontend | Vue 3 + Vite | Reactive UI, fast dev cycle |
| Maps | Leaflet.js | Open-source, no API key needed |
| Routing | OSRM (Open Source Routing Machine) | Free, no usage limits, real walking/driving times |
| Geocoding | Nominatim (OpenStreetMap) | Free, no API key, OSM data |
| Opening Hours | Overpass API (OpenStreetMap) | Structured OSM tag queries |
| Parsing | `humanized_opening_hours` library | Handles complex OSM opening_hours strings |
| Database (v2) | SQLite via aiosqlite | Lightweight persistence, UUID-based trip URLs |

---

## Algorithms and Mathematical Formulations

### 1. Nearest Neighbor Heuristic (Greedy TSP)

The simplest baseline. Starting from the user's current location, always travel to the nearest unvisited place.

- **Complexity:** O(N²) for N locations
- **Quality:** Typically 10–25% worse than optimal
- **Role:** Fast initial solution; used as a starting point for more sophisticated solvers

### 2. Travelling Salesman Problem with Time Windows (TSPTW)

Each location has a **time window** `[earliest, latest]` (e.g., a museum open 09:00–17:00). The goal is to find a tour that minimizes travel time while visiting each place within its window.

**Time representation:** All times are stored as seconds from midnight.
- 09:00 → 32,400 s
- 17:00 → 61,200 s

**Constraint handling — Penalty Method:**

Rather than enforcing hard constraints (which can make the problem infeasible with no solution), violations are penalized:

```
total_cost = travel_time + 10,000 × violation_count
```

This allows the solver to always return *some* route — either fully feasible, or the least-bad option if perfect scheduling is impossible.

### 3. Simulated Annealing (v1 metaheuristic)

Used in v1 to improve routes beyond the greedy baseline. Randomly swaps pairs of locations in the tour, accepting worse solutions with a probability that decreases over time ("cooling"), to escape local optima.

- **Temperature schedule:** Exponential cooling
- **Acceptance:** Metropolis criterion — accept worse solution with probability e^(−ΔCost/T)

### 4. Feasibility Scoring (v2)

For each unvisited location, compute a **slack** value: how much spare time would remain after visiting this place and still reaching the trip endpoint on time.

```
slack = trip_end_time − (arrival_time + visit_duration + travel_to_endpoint)
slack_ratio = slack / remaining_time
```

Each location is colored:

| Color | Meaning | Condition |
|---|---|---|
| Green | Comfortable visit | slack_ratio ≥ 30% and closes in > 2 hours |
| Yellow | Feasible but tight | slack_ratio ≥ 10% or closes within 2 hours |
| Red | High pressure | slack_ratio < 10% or closes within 30 min |
| Gray | Impossible | slack < 0 or already closed |
| Unknown | No hours data | Time is feasible, but no opening hours info |

### 5. "What Next?" Opportunity Cost Scoring (v2)

Recommends the best next place to visit using a weighted score:

```
score = 0.40 × opportunity_cost
      + 0.30 × proximity
      + 0.30 × priority_weight
```

- **Opportunity cost:** How many other places become unreachable if we skip this one now
- **Proximity:** Normalized travel time from current position
- **Priority:** User-assigned importance (must=1.0, want=0.5, if_time=0.2)

Returns the top 3 recommendations with plain-language reasoning.

---

## Opening Hours Pipeline

1. Query **Overpass API** for the `opening_hours` OSM tag at each location (3 mirror endpoints for reliability)
2. Parse the OSM format string (e.g., `"Mo-Fr 09:00-17:00; Sa 10:00-15:00"`) into time windows using the `humanized_opening_hours` library
3. Handle edge cases: split periods, overnight hours, 24/7, missing data
4. Fall back to **Google Places API** if OSM data is unavailable (v2 roadmap)

---

## System Architecture

```
User Browser (Vue 3 SPA)
        │
        ▼
FastAPI Backend
        │
   ┌────┴────┐
   │         │
Nominatim   OSRM
(geocoding) (routing)
        │
   Overpass API
   (opening hours)
        │
   SQLite DB (v2)
   (trip persistence)
```

The frontend communicates with the backend via REST endpoints and **Server-Sent Events (SSE)** for real-time streaming of solver progress.

---

## Key Design Decisions

**Why OSRM instead of Google Maps?**
OSRM is open-source, self-hostable, and has no usage limits or billing — important for a thesis project that needs reproducibility and no external dependencies.

**Why penalty-based constraints instead of hard constraints?**
Hard constraints can make the problem infeasible (no solution exists). The penalty approach always produces a usable result — either fully valid or "best effort with warnings" — which is more useful in practice.

**Why shift from v1 (solver) to v2 (feasibility dashboard)?**
A single "optimal route" output assumes perfect information and a rigid plan. Real travel is dynamic — places get skipped, time runs short. v2 gives the user actionable real-time guidance rather than a plan that breaks the moment reality diverges from it.

---

## Project Structure

```
PathFinder/
├── app/
│   ├── main.py                    # FastAPI entry point
│   └── services/
│       ├── nominatim.py           # Geocoding service
│       ├── osrm.py                # Distance matrix (OSRM)
│       ├── overpass.py            # Opening hours (OSM)
│       └── opening_hours_utils.py # OSM string parser
├── frontend/
│   └── src/
│       ├── App.vue                # Root Vue component
│       └── components/
├── tests/
│   ├── test_algorithms.py         # NN + TSPTW unit tests
│   └── test_opening_hours.py      # Opening hours parser tests
├── STRATEGY.md                    # v2 architecture design
└── IMPLEMENTATION_PLAN.md         # Detailed thesis defense guide
```
