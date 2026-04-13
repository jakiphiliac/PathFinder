# Step 7 Implementation — SSE Streaming for Nearest Neighbor

## Table of Contents

1. [What Was the State Before Step 7?](#1-what-was-the-state-before-step-7)
2. [What Was Missing?](#2-what-was-missing)
3. [The Core Problem Step 7 Solves](#3-the-core-problem-step-7-solves)
4. [Key Concepts You Need to Understand](#4-key-concepts-you-need-to-understand)
5. [Every File Changed or Created](#5-every-file-changed-or-created)
6. [How All the Pieces Connect (End-to-End Flow)](#6-how-all-the-pieces-connect-end-to-end-flow)
7. [Tests Added](#7-tests-added)
8. [What the User Sees](#8-what-the-user-sees)
9. [Bugs Found and Fixed After Initial Implementation](#9-bugs-found-and-fixed-after-initial-implementation)

---

## 1. What Was the State Before Step 7?

After completing Steps 1–6, the application could do the following:

| Capability | How it worked |
|---|---|
| Serve a web page | `GET /` returns `index.html` (FastAPI + Jinja2) |
| Geocode places | `POST /api/geocode` takes place names, calls Nominatim, returns GPS coordinates |
| Show markers on a map | JavaScript receives coordinates, adds Leaflet markers |
| Compute a distance matrix | `app/services/osrm.py` calls the OSRM API to get walking times between all coordinate pairs |
| Solve TSP | `app/algorithms/nearest_neighbor.py` takes a distance matrix and returns the shortest route |
| Evaluate time windows | `app/algorithms/tsp_tw_utils.py` checks if a route respects opening hours and adds penalties |

**The critical gap:** The OSRM service and the NN algorithm existed as standalone Python modules, but **nothing connected them to the user interface**. A user could type in places and see pins on a map, but there was no way to actually solve the route and see the result. The algorithm had no pathway to the browser.

In other words: the "brain" (algorithm) and the "eyes" (map) existed separately but could not talk to each other.

---

## 2. What Was Missing?

Three specific things were missing:

### Missing piece 1: The algorithm couldn't report its progress

The Nearest Neighbor `solve()` function ran silently — it accepted a matrix, crunched numbers internally, and returned the final answer. There was no mechanism to see what was happening *during* execution. It was like asking someone to solve a puzzle in a locked room — you only see the result when they come out.

**Why this matters:** If you have 20 places to visit, the algorithm takes multiple steps (visiting one city at a time). Without progress reporting, the user stares at a blank screen with no feedback. Is it working? Did it crash? They cannot tell.

### Missing piece 2: No server endpoint to trigger route solving

The backend had `POST /api/geocode` for geocoding, but no endpoint that said "here are my coordinates, solve the route." There was no URL the browser could call to start the optimization.

### Missing piece 3: No frontend code to request solving and display the route

The JavaScript knew how to show markers (dots on the map) but had no code to:
- Send coordinates to the server for solving
- Receive results back
- Draw a line (polyline) connecting the places in the optimized order
- Show the actual walking path along real roads

---

## 3. The Core Problem Step 7 Solves

### The problem with a normal HTTP request

A normal HTTP request works like a phone call: you ask a question, wait, get one answer, done.

```
Browser: "Solve this route" ──────────────────────► Server
                                                     │
                                        (server works for 5 seconds)
                                                     │
Browser: ◄──────────── "Here's the complete answer" ─┘
```

During those 5 seconds, the user sees nothing. The browser is just... waiting.

### The SSE (Server-Sent Events) solution

SSE is like a phone call where the other person gives you live updates instead of making you wait for the final answer:

```
Browser: "Solve this route" ──────────────────────► Server
                                                     │
Browser: ◄─── "Started! Visiting city 1..."          │
Browser: ◄─── "Now visiting city 2..."               │
Browser: ◄─── "Now visiting city 3..."               │
Browser: ◄─── "Done! Here's the complete route."   ──┘
```

The user sees the route being built step by step on the map. This is more informative, more engaging, and proves to the user that the system is actively working.

### Why not WebSockets?

WebSockets allow two-way communication (browser ↔ server). We only need one-way (server → browser). SSE is:
- Simpler to implement (it's just HTTP with a special content type)
- Supported by all browsers
- Easier to explain in a thesis defense
- Built on regular HTTP, so proxies and firewalls handle it well

---

## 4. Key Concepts You Need to Understand

### 4.1 The Threading Problem

Python's FastAPI is **asynchronous** — it uses `async/await` to handle many requests concurrently without blocking. Think of a restaurant waiter who serves multiple tables by going to whichever table needs attention instead of standing at one table until they finish eating.

The NN algorithm is **synchronous and CPU-bound** — it runs a loop doing math. If we ran it directly in the async event loop, it would be like the waiter standing at one table doing math homework — no other tables get served.

**Solution:** Run the algorithm in a **separate thread**. The waiter (event loop) tells a kitchen helper (thread) to do the math, and goes back to serving tables. When the helper has updates, they pass notes through a window (the queue).

### 4.2 The Queue Bridge

The algorithm runs in Thread A. The SSE sender runs in Thread B (the async event loop). They need to communicate. A **thread-safe queue** (`queue.Queue` from Python's standard library) is like a mailbox that both threads can safely use:

- The algorithm **puts** progress updates into the mailbox
- The SSE sender **reads** from the mailbox and sends each update to the browser

This is the `queue.Queue` pattern used in `solver.py`.

### 4.3 The progress_callback Pattern

Instead of modifying the algorithm to "know about" SSE, queues, or networking (which would mix concerns), we use a **callback**: a function that gets passed into the algorithm and called at key moments.

The algorithm doesn't know or care what the callback does — it just calls it with the current route and cost. The callback could:
- Put data on a queue (what we do in production)
- Append to a list (what we do in tests)
- Print to console (for debugging)
- Do nothing (when `None` is passed)

This keeps the algorithm pure and testable.

### 4.4 SSE Wire Format

SSE has a specific text format that browsers understand:

```
data: {"type": "progress", "route": [0, 1], "cost": 10}\n\n
data: {"type": "progress", "route": [0, 1, 3], "cost": 35}\n\n
data: {"type": "done", "route": [0, 1, 3, 2, 0], "cost": 80}\n\n
```

Each event starts with `data: `, followed by the JSON payload, ending with two newlines (`\n\n`). The `sse-starlette` library handles this formatting automatically on the server side. On the client side, our JavaScript parses lines starting with `data:`.

### 4.5 The Browser Repaint Problem

The NN algorithm completes in microseconds for typical inputs (5–20 places). This means the server sends all SSE events almost instantly — they arrive in the browser as a single TCP chunk. The JavaScript processes them in a tight synchronous loop, and the browser never gets a chance to repaint the map between events. The user sees nothing until the last event.

**Solution:** The `onEvent` callback is made async, and after each progress event we insert a small `setTimeout` delay (300ms). This yields control back to the browser's rendering loop so Leaflet can actually draw each intermediate polyline on screen. Without this, the "step-by-step animation" would be invisible.

### 4.6 Straight Lines vs. Real Walking Paths

The algorithm works with a distance matrix — it knows the *time* between places but not the *shape of the road*. Drawing straight lines between stops is fast but looks unrealistic. To show actual walking paths, we query the OSRM route endpoint for each consecutive pair of stops after the algorithm finishes. OSRM returns a GeoJSON geometry that follows real roads, sidewalks, and pedestrian paths.

This is done only once (for the final route), not during progress animation, to avoid flooding OSRM with requests.

---

## 5. Every File Changed or Created

### 5.1 `app/algorithms/nearest_neighbor.py` — MODIFIED

**Motivation:** The algorithm needed to report what it was doing step-by-step, not just return a final answer.

**What changed:** Added one new parameter: `progress_callback`

```python
def solve(
    matrix: list[list[float]],
    start_index: int = 0,
    time_windows: list[tuple[float, float]] | None = None,
    service_time: float = 0,
    progress_callback: Callable[[list[int], float], None] | None = None,  # ← NEW
) -> tuple[list[int], float]:
```

**Where the callback fires** (3 places):

1. **After initialization** (line 54–55): Reports the starting point `[0]` with cost `0.0`. This tells the UI "we've started."

```python
    visited = {start_index}
    route = [start_index]
    current = start_index
    total_cost = 0.0

    if progress_callback is not None:
        progress_callback(route.copy(), total_cost)      # "Starting at city 0"
```

2. **After each city is added** (line 72–73): Inside the `while` loop, after the nearest unvisited city is found and added to the route.

```python
        route.append(best_j)
        total_cost += best_time
        current = best_j
        visited.add(best_j)

        if progress_callback is not None:
            progress_callback(route.copy(), total_cost)   # "Now visited city X"
```

3. **After the return leg** (line 84–85): When the route is complete (returned to start). This is the final state.

```python
    route.append(start_index)
    # ... time window evaluation ...

    if progress_callback is not None:
        progress_callback(route.copy(), total_cost)       # "Route complete"
```

**Why `route.copy()`?** Because lists in Python are mutable. If we passed the original `route` list, the callback would receive a reference to it. As the algorithm continues modifying `route`, all previously-sent references would change too. `.copy()` creates an independent snapshot at each step.

**Why is this safe for existing code?** The parameter defaults to `None`. All existing calls that don't pass `progress_callback` work exactly as before — the `if progress_callback is not None` checks are simply skipped.

**For a 4-city route `[0, 1, 3, 2, 0]`, the callback fires 5 times:**

| Call # | Route so far | Cost | What happened |
|--------|-------------|------|---------------|
| 1 | `[0]` | 0.0 | Started at city 0 |
| 2 | `[0, 1]` | 10.0 | Visited nearest city (1) |
| 3 | `[0, 1, 3]` | 35.0 | Visited nearest unvisited (3) |
| 4 | `[0, 1, 3, 2]` | 65.0 | Visited last city (2) |
| 5 | `[0, 1, 3, 2, 0]` | 80.0 | Returned to start |

---

### 5.2 `app/services/solver.py` — CREATED (new file)

**Motivation:** We need a bridge between the synchronous algorithm (which blocks a thread) and the asynchronous SSE response (which streams events over HTTP). This bridge must handle two fundamentally different execution models talking to each other.

**Why a separate file?** Separation of concerns:
- `nearest_neighbor.py` knows about math and routes — nothing about networking
- `main.py` knows about HTTP requests — nothing about threading queues
- `solver.py` sits in between, translating algorithm progress into network events

**Full code with explanation:**

```python
"""
Solver service — bridges sync TSP algorithms with async SSE streaming.
"""

import asyncio
import queue
from collections.abc import AsyncGenerator
from typing import Any

from app.algorithms import nearest_neighbor as nn

_SENTINEL = object()
```

**`_SENTINEL`**: A unique object used to signal "the algorithm is done." We can't use `None` or any regular value because those could theoretically appear as data. `object()` creates a one-of-a-kind object — if we see it in the queue, we know with certainty it means "stop reading."

```python
async def run_nn_with_progress(
    matrix: list[list[float]],
    start_index: int = 0,
    time_windows: list[tuple[float, float]] | None = None,
    service_time: float = 0,
) -> AsyncGenerator[dict[str, Any], None]:
```

**`AsyncGenerator`**: This function is an async generator — it produces a sequence of values over time (like a conveyor belt), not all at once. Each `yield` sends one event to whoever is consuming the generator. The SSE endpoint consumes it, converting each yielded dict into an SSE text event sent to the browser.

```python
    q: queue.Queue[...] = queue.Queue()
```

**`queue.Queue`**: Python's thread-safe queue from the standard library. Both the algorithm thread and the async event loop can safely put/get items without race conditions. This is the "mailbox" between the two worlds.

**Why not `asyncio.Queue`?** `asyncio.Queue` is designed for async code only. The algorithm callback runs in a regular thread (not the async event loop). Calling `asyncio.Queue.put()` from a non-async thread is unsafe and could cause subtle bugs. `queue.Queue` is thread-safe by design.

```python
    def callback(route: list[int], cost: float) -> None:
        q.put(("progress", route, cost))
```

**The callback**: This function gets passed to `nn.solve()`. Every time the algorithm visits a new city, it calls this function. The callback doesn't process the data — it just drops it into the queue for the async side to pick up. The tuple `("progress", route, cost)` tags the data so we know what type of event it is.

```python
    def run() -> tuple[list[int], float]:
        try:
            result = nn.solve(
                matrix,
                start_index=start_index,
                time_windows=time_windows,
                service_time=service_time,
                progress_callback=callback,
            )
            return result
        finally:
            q.put(_SENTINEL)
```

**The `run()` function**: This is what actually executes in the thread. It:
1. Runs the NN algorithm (which calls `callback` multiple times during execution)
2. Returns the final result

**Critical: `try/finally` for the sentinel.** The `q.put(_SENTINEL)` is inside a `finally` block. This guarantees the sentinel is placed on the queue **even if `nn.solve()` raises an exception**. Without `finally`, an exception would skip the sentinel, and the async reader loop would block forever waiting for data that will never arrive — a deadlock. The `finally` block is the safety net that prevents this. The exception itself still propagates via `await task` on the async side, where the caller can handle it.

```python
    task = asyncio.get_event_loop().run_in_executor(None, run)
```

**`run_in_executor`**: This starts `run()` in a thread from the thread pool (`None` = default pool). It returns a future that we can `await` later to get the final result. The key insight: the algorithm runs in the background thread while the lines below continue executing in the async event loop.

```python
    while True:
        item = await asyncio.to_thread(q.get)
        if item is _SENTINEL:
            break
        _, route, cost = item
        yield {"type": "progress", "route": route, "cost": cost}
```

**The reading loop**: On the async side, we read from the queue. `asyncio.to_thread(q.get)` runs `q.get()` (which blocks until something is available) in a thread, but `await` it so the event loop stays free. When we get a progress tuple, we yield it as a dict. When we see the sentinel, we break.

**Why `asyncio.to_thread(q.get)` instead of just `q.get()`?** `q.get()` is a blocking call — it waits until data is available. If we called it directly in async code, it would freeze the entire event loop (no other requests could be handled). `asyncio.to_thread()` moves the blocking wait to a separate thread, keeping the event loop free.

```python
    route, cost = await task
    yield {"type": "done", "route": route, "cost": cost}
```

**Final event**: After the loop ends, we `await` the task to get the definitive final result (with time window penalties applied, etc.) and yield one last "done" event.

**Data flow through solver.py:**

```
Thread (algorithm)                Queue                  Async (event loop)
──────────────────               ──────                  ──────────────────
nn.solve() starts
  callback([0], 0) ──────► put(progress,[0],0) ──► get() → yield {progress}
  callback([0,1], 10) ───► put(progress,[0,1],10) ► get() → yield {progress}
  callback([0,1,3], 35) ─► put(progress,...) ──────► get() → yield {progress}
  ...
  nn.solve() returns (or raises)
  finally: put(_SENTINEL) ────────────────────────► get() → break
                                                    await task → yield {done}
                                                    (or re-raise exception)
```

---

### 5.3 `app/main.py` — MODIFIED

**Motivation:** The backend needed a new URL endpoint that the browser could call to trigger route solving and receive live updates.

**New imports added:**

```python
import json
from sse_starlette.sse import EventSourceResponse
from app.services.osrm import get_distance_matrix
from app.services.solver import run_nn_with_progress
```

- **`json`**: To convert Python dicts to JSON strings for SSE events
- **`EventSourceResponse`**: From `sse-starlette` library. It takes an async generator and converts each yielded string into a properly-formatted SSE event (adding `data: ` prefix, `\n\n` suffix, and setting `Content-Type: text/event-stream`)
- **`get_distance_matrix`**: The OSRM service (already existed, now wired in)
- **`run_nn_with_progress`**: The new solver bridge

**New request model:**

```python
class SolveRequest(BaseModel):
    coordinates: list[list[float]]

    @field_validator("coordinates")
    @classmethod
    def at_least_two_coordinates(cls, v: list[list[float]]) -> list[list[float]]:
        if len(v) < 2:
            raise ValueError("Need at least 2 coordinates to solve a route")
        for coord in v:
            if len(coord) != 2:
                raise ValueError("Each coordinate must be [lon, lat]")
        return v
```

**Why validate?** Without validation, sending `{"coordinates": []}` or `{"coordinates": [[1]]}` would cause cryptic errors deep in OSRM or the algorithm. Pydantic validators catch bad input early and return clear error messages. The validator ensures:
- At least 2 coordinates (can't make a route with one point)
- Each coordinate is exactly `[longitude, latitude]`

**The new endpoint:**

```python
@app.post("/api/solve/stream")
async def api_solve_stream(payload: SolveRequest):
    coords = [tuple(c) for c in payload.coordinates]

    async def event_generator():
        try:
            matrix = await get_distance_matrix(coords)
            yield json.dumps({"type": "matrix", "size": len(matrix)})

            async for event in run_nn_with_progress(matrix, start_index=0):
                yield json.dumps(event)
        except Exception as exc:
            yield json.dumps({"type": "error", "message": str(exc)})

    return EventSourceResponse(event_generator())
```

**How it works, step by step:**

1. **`coords = [tuple(c) for c in payload.coordinates]`**: Converts the JSON lists to tuples (OSRM expects tuples).

2. **`event_generator()`**: An async generator function (defined inside the endpoint). It is NOT executed when defined — it only runs when `EventSourceResponse` starts consuming it.

3. **`matrix = await get_distance_matrix(coords)`**: Calls OSRM to compute the walking time matrix. This is the first slow step — it makes an HTTP request to an external API. The `await` ensures the event loop isn't blocked while waiting for OSRM's response.

4. **`yield json.dumps({"type": "matrix", "size": len(matrix)})`**: The first SSE event. It tells the browser "I got the distance matrix, it has N places." This is useful feedback because OSRM can take a few seconds.

5. **`async for event in run_nn_with_progress(matrix):`**: Consumes the async generator from `solver.py`. Each progress/done dict is JSON-serialized and yielded as an SSE event.

6. **`except Exception`**: If OSRM fails or the algorithm crashes, we catch the error and send it as an SSE event instead of crashing the connection silently. The browser can then display the error message.

7. **`return EventSourceResponse(event_generator())`**: FastAPI returns this response object. The `sse-starlette` library keeps the HTTP connection open and writes each yielded string as a `data: ...\n\n` SSE event. The connection closes when the generator is exhausted.

**Event types the endpoint sends:**

| Event type | When | Data |
|---|---|---|
| `matrix` | After OSRM returns | `{"type": "matrix", "size": 5}` |
| `progress` | After each city visited | `{"type": "progress", "route": [0,1,3], "cost": 35}` |
| `done` | Algorithm complete | `{"type": "done", "route": [0,1,3,2,0], "cost": 80}` |
| `error` | Something went wrong | `{"type": "error", "message": "OSRM error: ..."}` |

---

### 5.4 `app/static/js/map.js` — MODIFIED (major rewrite)

**Motivation:** The JavaScript was only doing geocoding + showing markers. It now needs to:
1. After geocoding, automatically trigger route solving
2. Read SSE events from a streaming response
3. Animate the route building step-by-step on the map in real time
4. After solving, fetch and display actual walking paths from OSRM

**Function: `readSSEStream(response, onEvent)`**

```javascript
async function readSSEStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("data:")) {
        const jsonStr = trimmed.slice(5).trim();
        if (jsonStr) {
          try {
            await onEvent(JSON.parse(jsonStr));
          } catch (e) {
            console.warn("SSE parse error:", e, jsonStr);
          }
        }
      }
    }
  }
}
```

**Why not use the browser's built-in `EventSource` API?** `EventSource` only supports GET requests. Our endpoint is POST (we need to send coordinates in the body). So we use `fetch()` + manual stream reading.

**Critical detail: `await onEvent(...)`.** The `onEvent` callback is `await`ed, meaning it can be an async function. This is essential for the animation fix (see section 9.1). If `onEvent` were called synchronously, all events from the same TCP chunk would be processed in a tight loop with no browser repaint in between — the user would never see intermediate progress.

**How it works:**

1. **`response.body.getReader()`**: Gets a readable stream from the fetch response. Instead of waiting for the entire response to download, we read it chunk by chunk as the server sends data.

2. **`buffer`**: Network data arrives in arbitrary chunks — a chunk might contain half an event, or two-and-a-half events. The buffer accumulates data until we find complete lines.

3. **Line splitting**: We split on `\n` and keep the last part (which might be an incomplete line) in the buffer. Complete lines are processed immediately.

4. **`data:` parsing**: SSE format requires each event line to start with `data:`. We strip that prefix, parse the remaining JSON, and call `onEvent()` with the parsed object.

5. **Error handling**: If a line can't be parsed as JSON, we log a warning but continue. One bad event shouldn't break the whole stream.

---

**Function: `fetchWalkingGeometry(orderedCoords)`**

```javascript
async function fetchWalkingGeometry(orderedCoords) {
  const allLatLngs = [];

  for (let i = 0; i < orderedCoords.length - 1; i++) {
    const from = orderedCoords[i];
    const to = orderedCoords[i + 1];
    const url =
      `https://router.project-osrm.org/route/v1/foot/` +
      `${from.lon},${from.lat};${to.lon},${to.lat}` +
      `?overview=full&geometries=geojson`;

    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        console.warn(`OSRM route failed for segment ${i}: HTTP ${resp.status}`);
        allLatLngs.push([from.lat, from.lon]);
        continue;
      }
      const data = await resp.json();
      if (data.code === "Ok" && data.routes && data.routes[0]) {
        const coords = data.routes[0].geometry.coordinates;
        for (const [lon, lat] of coords) {
          allLatLngs.push([lat, lon]);
        }
      } else {
        allLatLngs.push([from.lat, from.lon]);
      }
    } catch (err) {
      console.warn(`OSRM route error for segment ${i}:`, err);
      allLatLngs.push([from.lat, from.lon]);
    }
  }

  if (orderedCoords.length > 0) {
    const last = orderedCoords[orderedCoords.length - 1];
    allLatLngs.push([last.lat, last.lon]);
  }

  return allLatLngs;
}
```

**What it does:** After the algorithm determines the optimal visit order, this function fetches the actual walking geometry from OSRM for each consecutive pair of stops.

**How it works:**

1. For each pair of consecutive stops (A→B, B→C, C→D, D→A), make a GET request to OSRM's route endpoint: `GET /route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson`

2. OSRM returns a GeoJSON geometry — an array of `[lon, lat]` coordinate pairs tracing the actual walking path along roads and sidewalks.

3. Convert each `[lon, lat]` to `[lat, lon]` (Leaflet uses lat-first order) and append to a single continuous array.

4. Chain all segments together into one seamless polyline.

**Why per-pair instead of one big request?** OSRM's route endpoint accepts multiple waypoints, but the demo server has URL length limits. For 14+ stops, the URL can exceed limits. Querying each pair individually is more reliable and each request is small.

**Fallback behavior:** If any OSRM request fails (network error, rate limit, etc.), that segment falls back to a straight line between the two stops. The rest of the route still shows real geometry. This ensures the app never breaks completely — degraded display is better than no display.

**Why only on the "done" event?** Fetching walking geometry for every progress event would mean N×(N-1)/2 requests during animation (one per step per segment). For 14 places, that's ~91 OSRM requests in rapid succession. This would flood the public API, get rate-limited, and slow the animation to a crawl. By fetching geometry only once (for the final route), we make exactly N requests total.

---

**Function: `drawRoute(routeIndices, geocodedCoords, color)` — for progress animation**

```javascript
function drawRoute(routeIndices, geocodedCoords, color = "#0d6efd") {
    clearRoute();
    const latlngs = routeIndices
      .filter((i) => i < geocodedCoords.length)
      .map((i) => [geocodedCoords[i].lat, geocodedCoords[i].lon]);

    if (latlngs.length < 2) return;

    routePolyline = L.polyline(latlngs, {
      color: color,
      weight: 4,
      opacity: 0.8,
    }).addTo(map);
}
```

**What it does:** Takes route indices (e.g., `[0, 2, 1, 3, 0]`) and the array of geocoded coordinates, then draws a straight-line polyline connecting those points on the map.

**Why `routeIndices` instead of direct coordinates?** The algorithm works with indices (0, 1, 2, 3) referring to positions in the coordinate array. The index-to-coordinate translation happens here in the UI layer.

**Why `clearRoute()` before drawing?** Each progress event shows the route *so far*. We erase the old line and draw a new, longer one. This creates the visual effect of the route "growing" on the map.

**This function is used for progress animation only.** It draws straight lines because it's called many times rapidly during the solving animation. Real walking geometry is fetched separately and drawn by `drawRawPolyline()` after solving completes.

---

**Function: `drawRawPolyline(latlngs, color)` — for final walking geometry**

```javascript
function drawRawPolyline(latlngs, color = "#0d6efd") {
    clearRoute();
    if (latlngs.length < 2) return;

    routePolyline = L.polyline(latlngs, {
      color: color,
      weight: 4,
      opacity: 0.8,
    }).addTo(map);
}
```

**What it does:** Draws a polyline from raw `[lat, lon]` coordinate arrays. Unlike `drawRoute()` which takes algorithm indices, this accepts pre-computed coordinate arrays (from OSRM walking geometry).

**Why a separate function?** `drawRoute()` translates indices to coordinates. `drawRawPolyline()` takes coordinates directly. They serve different stages of the visualization pipeline.

---

**Function: `animationDelay(ms)`**

```javascript
function animationDelay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
```

**What it does:** Returns a Promise that resolves after `ms` milliseconds. When `await`ed, this pauses JavaScript execution and yields control back to the browser's rendering loop.

**Why this exists:** The browser can only repaint (update what you see on screen) between JavaScript tasks. If you run a tight loop updating the DOM 100 times, the user sees only the last update. By inserting a small delay (300ms), we give the browser a chance to paint each intermediate state of the polyline.

---

**Function: `solveRoute(geocodedCoords)` — the main solving orchestrator**

```javascript
async function solveRoute(geocodedCoords) {
    const coordinates = geocodedCoords.map((c) => [c.lon, c.lat]);

    setStatus("Fetching walking times from OSRM...");

    const resp = await fetch("/api/solve/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ coordinates }),
    });

    if (!resp.ok) {
      setStatus(`Route solving failed: HTTP ${resp.status}`);
      return;
    }

    let finalRoute = null;
    let finalCost = null;
    const totalPlaces = geocodedCoords.length;

    await readSSEStream(resp, async (event) => {
      if (event.type === "matrix") {
        setStatus(`Distance matrix ready (${event.size} places). Solving route...`);
      } else if (event.type === "progress") {
        const visited = event.route.length - 1;
        drawRoute(event.route, geocodedCoords, "#6c757d");
        setStatus(`Solving: visited ${visited} of ${totalPlaces} place(s)...`);
        await animationDelay(300);
      } else if (event.type === "done") {
        finalRoute = event.route;
        finalCost = event.cost;
        drawRoute(event.route, geocodedCoords, "#0d6efd");
        const minutes = (event.cost / 60).toFixed(1);
        setStatus(`Route found! Total walking time: ${minutes} min. Loading walking path...`);
      } else if (event.type === "error") {
        setStatus(`Solve error: ${event.message}`);
      }
    });

    if (!finalRoute) {
      setStatus("Route solving ended without a result.");
      return;
    }

    // Fetch real walking geometry from OSRM for the final route
    try {
      const orderedCoords = finalRoute
        .filter((i) => i < geocodedCoords.length)
        .map((i) => geocodedCoords[i]);

      const walkingLatLngs = await fetchWalkingGeometry(orderedCoords);
      drawRawPolyline(walkingLatLngs, "#0d6efd");
      const minutes = (finalCost / 60).toFixed(1);
      setStatus(`Route found! Total walking time: ${minutes} min`);
    } catch (err) {
      console.warn("Failed to fetch walking geometry, keeping straight lines:", err);
    }
}
```

**Important detail: coordinate order flip.** Geocoding returns `{lat, lon}` but OSRM expects `[lon, lat]`. The line `geocodedCoords.map((c) => [c.lon, c.lat])` does this conversion. Getting this wrong would place markers in the middle of the ocean.

**The `async (event) =>` callback.** The event handler is declared `async` so it can `await animationDelay(300)` inside the progress handler. Because `readSSEStream` `await`s each `onEvent` call, this delay actually pauses the SSE processing loop — giving the browser time to repaint the map before the next event is processed.

**Three-phase visualization:**

1. **Progress animation (gray straight lines):** As SSE progress events arrive, gray straight lines grow on the map with 300ms between each step. The user sees the algorithm "thinking."

2. **Immediate done (blue straight lines):** When the "done" event arrives, straight lines turn blue. Status shows walking time + "Loading walking path..."

3. **Walking geometry (blue road-following lines):** After SSE finishes, `fetchWalkingGeometry()` queries OSRM for the real walking path of each segment. The blue straight lines are replaced with blue curves following actual roads.

**Event handling summary:**

| Event | Visual action | Status message |
|---|---|---|
| `matrix` | (none) | "Distance matrix ready (5 places). Solving route..." |
| `progress` | Draw gray straight-line polyline, wait 300ms | "Solving: visited 3 of 5 place(s)..." |
| `done` | Draw blue straight-line polyline | "Route found! Total walking time: 15.3 min. Loading walking path..." |
| (after SSE) | Replace with blue OSRM walking geometry | "Route found! Total walking time: 15.3 min" |
| `error` | (none) | "Solve error: ..." |

---

**Color convention:**

| Color | Meaning | When |
|---|---|---|
| `#6c757d` (gray) | Route is still being built | Progress animation |
| `#0d6efd` (Bootstrap blue) | Final completed route | Done event + walking geometry |

---

**Modified form submission flow:**

The form submit handler was restructured into three sequential phases:

```
Phase 1 — Geocode (from Step 6)
├── Send places to /api/geocode
├── Add markers to map
└── Store coordinates in geocodedCoords array

Phase 2 — Solve with SSE animation (Step 7)
├── Check we have ≥ 2 geocoded places
├── POST /api/solve/stream with coordinates
├── Read SSE stream
├── On each progress event: draw gray line, wait 300ms for browser repaint
└── On done event: draw blue straight-line route

Phase 3 — Fetch real walking geometry (Step 7)
├── Convert route indices to ordered coordinates
├── For each consecutive pair: GET OSRM route endpoint
├── Extract and chain GeoJSON geometry
└── Replace straight lines with road-following polyline
```

The `geocodedCoords` array bridges Phase 1 and Phase 2. The `finalRoute` index array bridges Phase 2 and Phase 3.

---

## 6. How All the Pieces Connect (End-to-End Flow)

Here is the complete journey of a user request, from clicking the button to seeing the route on the map:

```
┌─────────── BROWSER (JavaScript) ───────────┐      ┌─────── SERVER (Python) ───────┐
│                                             │      │                               │
│  User clicks "Solve"                        │      │                               │
│       │                                     │      │                               │
│       ▼                                     │      │                               │
│  parsePlaces("Big Ben, Tower, Museum")      │      │                               │
│  → ["Big Ben", "Tower", "Museum"]           │      │                               │
│       │                                     │      │                               │
│       ▼                                     │      │                               │
│  fetch POST /api/geocode ──────────────────────────► api_geocode()                 │
│  {destination: "London",                    │      │   │                           │
│   places: ["Big Ben", ...]}                 │      │   ▼                           │
│                                             │      │  geocode_place("Big Ben") ────────► Nominatim API
│                                             │      │  sleep(1)                     │      (OpenStreetMap)
│                                             │      │  geocode_place("Tower") ──────────►
│                                             │      │  sleep(1)                     │
│                                             │      │  geocode_place("Museum") ─────────►
│                                             │      │   │                           │
│  ◄──────────────────────────────────────────────── {results: [{lat,lon}, ...]}     │
│  Add markers to map                         │      │                               │
│  Store geocodedCoords                       │      │                               │
│       │                                     │      │                               │
│       ▼                                     │      │                               │
│  fetch POST /api/solve/stream ─────────────────────► api_solve_stream()            │
│  {coordinates: [[lon,lat], ...]}            │      │   │                           │
│                                             │      │   ▼                           │
│                                             │      │  get_distance_matrix() ───────────► OSRM Table API
│                                             │      │   │                           │    (walking times)
│  ◄──── SSE: {type:"matrix", size:3} ──────────────│   │                           │
│  status: "Distance matrix ready..."         │      │   ▼                           │
│                                             │      │  run_nn_with_progress()        │
│                                             │      │   │                           │
│                                             │      │   ├─ Thread: nn.solve()       │
│                                             │      │   │    callback([0], 0)       │
│  ◄──── SSE: {type:"progress", route:[0]}───────── │   │     ↓ via queue            │
│  draw gray line [start]                     │      │   │                           │
│  await 300ms (browser repaints)             │      │   │    callback([0,1], 10)    │
│  ◄──── SSE: {type:"progress", route:[0,1]} ────── │   │     ↓ via queue            │
│  draw gray line [start → city1]             │      │   │                           │
│  await 300ms (browser repaints)             │      │   │    callback([0,1,2], 45)  │
│  ◄──── SSE: {type:"progress", ...} ───────────── │   │     ↓ via queue            │
│  draw gray line [start → c1 → c2]          │      │   │                           │
│  await 300ms (browser repaints)             │      │   │    callback([0,1,2,0],60) │
│  ◄──── SSE: {type:"progress", ...} ───────────── │   │     ↓ via queue            │
│                                             │      │   │    finally: put(SENTINEL) │
│  ◄──── SSE: {type:"done", route, cost} ────────── │   └─ Thread done              │
│  draw BLUE straight lines [final route]     │      │                               │
│  status: "Route found! ... Loading path..." │      │                               │
│       │                                     │      │                               │
│       ▼                                     │      │                               │
│  fetchWalkingGeometry()                     │      │                               │
│  ├─ GET OSRM /route/v1/foot/A;B ──────────────────────────────────► OSRM Route API
│  ├─ GET OSRM /route/v1/foot/B;C ──────────────────────────────────►  (per-segment
│  ├─ GET OSRM /route/v1/foot/C;A ──────────────────────────────────►   geometry)
│  │                                          │      │                               │
│  ◄──── GeoJSON walking geometry ────────────────────────────────────                │
│  Replace straight lines with road paths     │      │                               │
│  status: "Route found! 15.3 min"            │      │                               │
│                                             │      │                               │
│  Re-enable Solve button                     │      │                               │
└─────────────────────────────────────────────┘      └───────────────────────────────┘
```

---

## 7. Tests Added

Three new tests were added to `tests/test_algorithms.py`:

### Test 1: `test_nearest_neighbor_progress_callback_fires`

**What it verifies:** The callback is called exactly the right number of times with the right data.

```python
events = []
solve(matrix, start_index=0, progress_callback=lambda r, c: events.append((r, c)))
assert len(events) == 5  # start + 3 cities + final
assert events[0][0] == [0]               # starting point
assert events[4][0] == [0, 1, 3, 2, 0]   # complete route
```

**Why this matters:** If the callback fires too few times, the user misses progress updates. If it fires with the wrong data (e.g., without `.copy()`), the UI would show garbage.

### Test 2: `test_nearest_neighbor_progress_callback_none_is_safe`

**What it verifies:** Passing `progress_callback=None` (the default) doesn't break anything.

```python
route, cost = solve(matrix, start_index=0, progress_callback=None)
assert route == [0, 1, 0]
assert cost == 20.0
```

**Why this matters:** Backwards compatibility. All the existing tests don't pass `progress_callback`. If `None` caused an error, every existing test would break.

### Test 3: `test_run_nn_with_progress_yields_events`

**What it verifies:** The full async solver pipeline works — algorithm runs in a thread, events flow through the queue, the async generator yields them correctly.

```python
events = []
async for event in run_nn_with_progress(matrix, start_index=0):
    events.append(event)
assert events[-1]["type"] == "done"
assert events[-1]["route"] == [0, 1, 2, 0]
```

**Why this matters:** This is an integration test of the threading + queue + async generator pipeline. It catches issues like deadlocks (the reader waiting forever) or the sentinel not being sent (infinite loop).

---

## 8. What the User Sees

### Before Step 7

1. Type "London, UK" and "Big Ben, Tower of London, British Museum"
2. Click Solve
3. See "Geocoding 3 places..."
4. See 3 markers appear on the map
5. See "Geocoded all 3 places."
6. **That's it.** No route. No optimization. Just dots on a map.

### After Step 7

1. Type "London, UK" and "Big Ben, Tower of London, British Museum"
2. Click Solve
3. See "Geocoding 3 places..."
4. See 3 markers appear on the map
5. See "Fetching walking times from OSRM..."
6. See "Distance matrix ready (3 places). Solving route..."
7. See a **gray straight line** appear connecting the first city
8. 300ms later, the gray line extends to the next city
9. 300ms later, it extends again — the route "grows" step by step
10. Gray line turns **blue** (straight lines) — the algorithm is done
11. See "Route found! Total walking time: 15.3 min. Loading walking path..."
12. Blue straight lines morph into **blue curves following real roads and sidewalks**
13. See **"Route found! Total walking time: 15.3 min"**

The user goes from "dots on a map" to "an animated route building sequence followed by realistic walking paths." The progressive animation demonstrates that the algorithm is working in real time, and the road-following geometry makes the result look professional and trustworthy — particularly impactful for a thesis defense demo.

---

## 9. Bugs Found and Fixed After Initial Implementation

### 9.1 Bug: No visible SSE progress animation

**Symptom:** The user saw "Geocoding 14 places..." and then the final route appeared all at once. No intermediate gray lines were visible despite the SSE progress events being sent correctly.

**Root cause:** The NN algorithm completes in microseconds. All SSE events arrived at the browser in a single TCP chunk. The original `readSSEStream` called `onEvent` synchronously in a `for` loop — all events were processed in one JavaScript microtask. The browser never got a chance to repaint the map between events, so only the last drawn polyline was ever visible.

**Fix:** Two changes:
1. Made `readSSEStream` `await` the `onEvent` callback (changed `onEvent(...)` to `await onEvent(...)`)
2. Made the progress handler async with a 300ms delay: `await animationDelay(300)`

This yields control to the browser between each event, allowing Leaflet to paint each intermediate polyline on screen.

### 9.2 Bug: Solver deadlock on algorithm exception

**Symptom:** If `nn.solve()` raised an exception (e.g., invalid matrix), the SSE stream would hang indefinitely instead of returning an error.

**Root cause:** In `solver.py`, the `run()` function placed the sentinel on the queue *after* calling `nn.solve()`:

```python
def run():
    result = nn.solve(...)   # If this raises...
    q.put(_SENTINEL)         # ...this never runs
    return result
```

If `nn.solve()` raised, the sentinel was never placed on the queue. The async reader loop (`while True: item = await asyncio.to_thread(q.get)`) blocked forever waiting for an item that would never arrive — a deadlock.

**Fix:** Wrapped in `try/finally`:

```python
def run():
    try:
        result = nn.solve(...)
        return result
    finally:
        q.put(_SENTINEL)    # Always runs, even on exception
```

The `finally` block guarantees the sentinel is placed regardless of success or failure. The exception itself still propagates when the async side `await`s the task.

### 9.3 Bug: `model_dump()` called on a plain dict

**Symptom:** `POST /api/geocode` returned HTTP 500 with `AttributeError: 'dict' object has no attribute 'model_dump'`.

**Root cause:** `geocode_place()` returns a plain Python dict, not a Pydantic model. The code called `geocoded.model_dump()` as if it were a Pydantic object.

**Fix:** Changed `GeocodeResult(**geocoded.model_dump())` to `GeocodeResult(**geocoded)`. Plain dicts can be unpacked with `**` directly.

### 9.4 Enhancement: Straight lines replaced with real walking paths

**Symptom:** The final route was drawn as straight lines between stops. While correct for showing the visit order, this looked unrealistic — lines cut through buildings, rivers, and parks.

**Solution:** After the algorithm finishes and the "done" event is received, the frontend fetches actual walking geometry from OSRM's route endpoint for each consecutive pair of stops. The GeoJSON geometry returned by OSRM follows real roads and sidewalks. All segment geometries are chained into one continuous polyline that replaces the straight-line version on the map. If any segment request fails, that segment falls back to a straight line.
