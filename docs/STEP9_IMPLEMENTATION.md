# Step 9 Implementation — SA SSE Streaming

## Table of Contents

1. [What Was the State Before Step 9?](#1-what-was-the-state-before-step-9)
2. [What Was Missing?](#2-what-was-missing)
3. [The Core Problem Step 9 Solves](#3-the-core-problem-step-9-solves)
4. [Every File Changed or Created](#4-every-file-changed-or-created)
5. [The Two-Phase SSE Flow](#5-the-two-phase-sse-flow)
6. [The Four-Color Animation Pipeline](#6-the-four-color-animation-pipeline)
7. [Tests Added](#7-tests-added)
8. [Bug Fix: Browser Cache and Event Type Mismatch](#8-bug-fix-browser-cache-and-event-type-mismatch)
9. [Enhancement: Animated Walking Geometry](#9-enhancement-animated-walking-geometry)
10. [Known Limitations](#10-known-limitations)

---

## 1. What Was the State Before Step 9?

After Step 8, the application had:

| Capability | How it worked |
|---|---|
| Geocode places | `POST /api/geocode` -> Nominatim -> GPS coordinates |
| Distance matrix | `app/services/osrm.py` -> OSRM `/table/v1/foot/` -> walking times |
| Nearest Neighbor | `app/algorithms/nearest_neighbor.py` -> greedy route with progress_callback |
| Simulated Annealing | `app/algorithms/simulated_annealing.py` -> improves NN route with progress_callback |
| NN SSE streaming | `solver.py` + `/api/solve/stream` -> animated NN progress on the map |
| Walking geometry | Frontend fetches OSRM route geometry for final route |

**The problem:** SA existed as an algorithm but was NOT wired into the web interface. The endpoint only ran NN. SA was only usable through Python code or tests.

---

## 2. What Was Missing?

### SA had no path to the browser

The SA algorithm was complete with `progress_callback` (built in during Step 8), but:

1. **No async bridge** — `solver.py` only had `run_nn_with_progress()`. SA needed its own `run_sa_with_progress()` using the same queue pattern.
2. **No endpoint integration** — `/api/solve/stream` only ran NN and streamed `progress`/`done` events. SA events needed to flow through it too.
3. **No frontend handling** — `map.js` only understood `progress` and `done` event types. It needed to handle SA-specific events.

### The user experience gap

Without Step 9:
- User clicks "Solve" -> sees NN animation -> gets NN result
- SA improvement is invisible (it literally doesn't run)

After Step 9:
- User clicks "Solve" -> sees NN animation -> sees SA improvements in real time -> gets the best route

---

## 3. The Core Problem Step 9 Solves

Step 9 answers: **How do you stream a two-phase optimization (NN then SA) through a single SSE connection so the user sees the route progressively improve?**

This requires:
- Distinct event types so the frontend knows which phase is active
- The NN result feeding directly into SA as its initial solution
- A smooth visual transition between phases

---

## 4. Every File Changed or Created

### 4.1 `app/services/solver.py` — MODIFIED (added `run_sa_with_progress()`)

**What was added:**

```python
async def run_sa_with_progress(
    matrix, initial_route, time_windows=None, service_time=0,
    T0=1000.0, alpha=0.995, max_iter=10_000,
) -> AsyncGenerator[dict[str, Any], None]:
```

**How it works:** Identical pattern to `run_nn_with_progress()`:

1. Create a thread-safe `queue.Queue`
2. Define a `callback` that puts `("sa_progress", route, cost)` on the queue
3. Define a `run()` function that calls `sa.solve()` with the callback, wrapped in `try/finally` to ensure the sentinel is always placed (prevents deadlock)
4. Launch `run()` in a thread via `run_in_executor`
5. Async loop reads from the queue, yielding `{"type": "sa_progress", ...}` dicts
6. After sentinel arrives, `await task` to get the final result
7. Yield `{"type": "sa_done", ...}` as the last event

**Why the same pattern?** Code reuse through consistent architecture. Both algorithms are CPU-bound (run in threads), both need to communicate progress to an async SSE handler, and both use the queue bridge. The only differences are the event type names and which algorithm function is called.

---

### 4.2 `app/main.py` — MODIFIED (two-phase endpoint)

**Before (Step 7):**
```
/api/solve/stream:
  1. Fetch OSRM matrix
  2. Run NN with progress -> stream "progress" and "done" events
```

**After (Step 9):**
```
/api/solve/stream:
  1. Fetch OSRM matrix -> emit "matrix" event
  2. Run NN with progress -> stream "progress" events
  3. NN finishes -> emit "nn_done" event (not "done")
  4. Feed NN route into SA -> stream "sa_progress" events
  5. SA finishes -> emit "sa_done" event
```

**Key change: event type renaming.** The old `"done"` event became `"nn_done"` to distinguish it from `"sa_done"`. This is the change that caused the browser cache bug (see Section 8).

**SA skip condition:**
```python
if nn_route and len(nn_route) > 3:
    # Run SA — enough cities to swap
    async for event in run_sa_with_progress(matrix, initial_route=nn_route, max_iter=10_000):
        yield json.dumps(event)
else:
    # 2 or fewer cities — nothing to optimize, emit sa_done with NN result
    yield json.dumps({"type": "sa_done", "route": nn_route, "cost": nn_cost})
```

**Why `len > 3`?** A route like `[0, 1, 0]` has length 3 but only 1 inner city — SA can't swap anything. SA needs at least 2 inner cities (route length > 3) to do useful work.

---

### 4.3 `app/static/js/map.js` — MODIFIED (SA event handling + walking animation)

**New SSE event handlers added:**

| Event type | Color | Status message | Purpose |
|---|---|---|---|
| `progress` | Gray (#6c757d) | "Nearest Neighbor: visited X of Y" | NN building route city by city |
| `nn_done` | Gray | "NN done (X min). Improving with SA..." | NN complete, SA about to start |
| `sa_progress` | Orange (#fd7e14) | "SA: improvement #N - X min" | SA found a better route |
| `sa_done` | Blue (#0d6efd) | "Route optimized! X min (N SA improvements)" | Final result |

**Walking geometry animation (new):**

Previously, walking geometry appeared all at once after the route was found. Now it animates segment by segment:

```javascript
const walkingLatLngs = await fetchWalkingGeometry(orderedCoords, async (partialPath, segIdx) => {
    drawRawPolyline(partialPath, "#198754");  // Green while loading
    setStatus(`Loading walking path... segment ${segIdx + 1} of ${totalSegments}`);
});
drawRawPolyline(walkingLatLngs, "#0d6efd");  // Blue when complete
```

The `fetchWalkingGeometry` function now accepts an optional `onSegment` callback that fires after each OSRM segment is fetched. The path grows on the map in green, then turns blue when all segments are loaded.

---

### 4.4 `app/templates/base.html` — MODIFIED (cache busting)

```html
<!-- Before -->
<script src="/static/js/map.js"></script>

<!-- After -->
<script src="/static/js/map.js?v=10"></script>
```

**Why?** Browsers cache static files aggressively. When `map.js` changes, the browser might serve the old version. The `?v=10` query parameter forces the browser to fetch the new file. Bump the number each time `map.js` changes.

---

### 4.5 `tests/test_algorithms.py` — MODIFIED (1 new async test)

Added `test_run_sa_with_progress_yields_events` — verifies:
- The async generator yields at least one `sa_progress` event
- The last event is `sa_done`
- The final route is structurally valid
- Progress costs are non-increasing (each event is a new best)

---

## 5. The Two-Phase SSE Flow

### Complete event sequence for a 4-place route

```
Client                           Server
  |                                |
  |  POST /api/solve/stream        |
  |  {"coordinates": [...]}        |
  |------------------------------->|
  |                                | Fetch OSRM matrix
  |  data: {"type":"matrix",       |
  |         "size": 4}             |
  |<-------------------------------|
  |                                | Start NN in thread
  |  data: {"type":"progress",     |
  |         "route":[0]}           |
  |<-------------------------------|
  |  data: {"type":"progress",     |
  |         "route":[0,1]}         |
  |<-------------------------------|
  |  data: {"type":"progress",     |
  |         "route":[0,1,3]}       |
  |<-------------------------------|
  |  data: {"type":"progress",     |
  |         "route":[0,1,3,2]}     |
  |<-------------------------------|
  |  data: {"type":"progress",     |
  |         "route":[0,1,3,2,0]}   |
  |<-------------------------------|
  |  data: {"type":"nn_done",      |  NN complete
  |         "route":[0,1,3,2,0],   |
  |         "cost": 4800}          |
  |<-------------------------------|
  |                                | Start SA with NN route
  |  data: {"type":"sa_progress",  |
  |         "route":[0,1,3,2,0],   |  SA initial (same as NN)
  |         "cost": 4800}          |
  |<-------------------------------|
  |  data: {"type":"sa_progress",  |
  |         "route":[0,2,3,1,0],   |  SA found improvement!
  |         "cost": 4500}          |
  |<-------------------------------|
  |  data: {"type":"sa_done",      |  SA complete
  |         "route":[0,2,3,1,0],   |
  |         "cost": 4500}          |
  |<-------------------------------|
  |                                |
  | (stream ends)                  |
```

**Key points:**
- One HTTP connection, one SSE stream
- NN events come first (fast, microseconds)
- SA events follow (10,000 iterations, but only fires on new bests)
- The frontend uses event types to know which phase is active

---

## 6. The Four-Color Animation Pipeline

The user sees four distinct visual phases:

```
Phase 1: NN Building          Phase 2: NN Complete
Gray polyline grows            Gray polyline, status updates
city by city                   "NN done. Improving with SA..."
  ┌─────────┐                    ┌─────────┐
  │  ·─·    │                    │  ·─·─·  │
  │    ·    │                    │  ·   ·  │
  │         │                    │  ·───·  │
  └─────────┘                    └─────────┘

Phase 3: SA Improving          Phase 4: Walking Geometry
Orange polyline updates         Green grows segment by segment
on each new best                then turns blue when complete
  ┌─────────┐                    ┌─────────┐
  │  ·───·  │                    │  ·~~~·  │
  │  │   │  │                    │  ~   ~  │
  │  ·───·  │                    │  ·~~~·  │
  └─────────┘                    └─────────┘

Colors:
  Gray (#6c757d)    = NN progress
  Orange (#fd7e14)  = SA improvements
  Green (#198754)   = Walking geometry loading
  Blue (#0d6efd)    = Final result
```

---

## 7. Tests Added

### `test_run_sa_with_progress_yields_events`

```python
@pytest.mark.asyncio
async def test_run_sa_with_progress_yields_events():
    matrix = [
        [0, 10, 15, 20],
        [10, 0, 35, 25],
        [15, 35, 0, 30],
        [20, 25, 30, 0],
    ]
    nn_route = [0, 1, 3, 2, 0]
    events = []
    async for event in run_sa_with_progress(matrix, initial_route=nn_route, max_iter=5000):
        events.append(event)

    assert events[-1]["type"] == "sa_done"
    final_route = events[-1]["route"]
    assert final_route[0] == 0
    assert final_route[-1] == 0
    assert sorted(final_route[1:-1]) == [1, 2, 3]

    sa_progress = [e for e in events if e["type"] == "sa_progress"]
    assert len(sa_progress) >= 1

    for i in range(1, len(sa_progress)):
        assert sa_progress[i]["cost"] <= sa_progress[i - 1]["cost"]
```

**What it verifies:**
- The async generator yields events with correct types
- Final route is structurally valid (starts/ends at 0, visits all cities)
- At least one `sa_progress` event fires (the initial best)
- Progress costs are non-increasing

**Total test count: 24** (6 NN + 5 evaluate_route + 2 NN callback + 9 SA + 1 NN async + 1 SA async)

---

## 8. Bug Fix: Browser Cache and Event Type Mismatch

### The bug

After deploying Step 9, the user saw: **"Route solving ended without a result."**

### Root cause

The backend changed `"done"` to `"nn_done"` and added `"sa_done"`. But the browser was serving the **old** `map.js` from cache. The old JS only set `finalRoute` on `event.type === "done"` — which the backend no longer sends.

```
Backend sends:  "nn_done", "sa_progress", "sa_done"
Old JS expects: "done"
Result:         finalRoute stays null -> "ended without a result"
```

### The fix

1. Added `?v=10` cache buster to the `<script>` tag in `base.html`
2. Server restart + hard refresh (Ctrl+Shift+R) in the browser

### Lesson

When changing SSE event types, always bump the cache buster on the frontend. The backend and frontend must agree on event type strings — a mismatch silently fails because unrecognized events are ignored.

---

## 9. Enhancement: Animated Walking Geometry

### Before Step 9

Walking geometry appeared all at once after the route was found. The user saw:
1. Route found! -> blue straight lines
2. (pause while OSRM segments load)
3. Suddenly: blue walking paths replace straight lines

### After Step 9

Walking geometry animates segment by segment:
1. Route found! -> blue straight lines
2. Green path grows segment by segment ("Loading walking path... segment 2 of 5")
3. All segments loaded -> green turns blue

### Implementation

`fetchWalkingGeometry()` now accepts an optional `onSegment` callback:

```javascript
async function fetchWalkingGeometry(orderedCoords, onSegment = null) {
    // ... for each segment:
    if (onSegment) await onSegment([...allLatLngs], i);
    // ...
}
```

The caller passes a callback that redraws the polyline after each segment:

```javascript
await fetchWalkingGeometry(orderedCoords, async (partialPath, segIdx) => {
    drawRawPolyline(partialPath, "#198754");  // Green
    setStatus(`Loading walking path... segment ${segIdx + 1} of ${totalSegments}`);
});
```

---

## 10. Known Limitations

### OSRM public server walking quirks

The OSRM public demo server (`router.project-osrm.org`) sometimes routes pedestrians around one-way streets, even though the `/foot/` profile should allow bidirectional walking. This is a known limitation of the public server's data. The fix would be self-hosting OSRM with custom walking data — significant infrastructure work, beyond thesis scope.

### Starting point is implicit

The first place entered by the user becomes the TSP depot (index 0). The route departs from and returns to this location. There is no explicit "starting point" selector in the UI. This could be added as a future enhancement.

### SA parameters are hardcoded

`T0=1000`, `alpha=0.995`, `max_iter=10_000` are fixed in the endpoint. Future steps could expose these as user-configurable options or add auto-tuning.
