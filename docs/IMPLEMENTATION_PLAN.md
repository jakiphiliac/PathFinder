# Travel Route Optimizer — Implementation Plan

**For thesis defense.** Each step includes: concept explanation, what to build, how pieces connect, verification, and committee Q&A prep.

---

## Honest Assessment & Suggestions

**What's solid:** Your tech stack is appropriate. FastAPI + SSE + asyncio is a good fit. OSRM public demo is fine for a thesis (mention rate limits in your defense). Keeping the database optional for MVP is smart.

**One change I'd recommend:** Add `sse-starlette` to requirements.txt. FastAPI doesn't ship SSE out of the box; this library integrates cleanly. Alternative: use `StreamingResponse` with manual SSE formatting—it works but is more error-prone. For a thesis, `sse-starlette` is easier to explain and debug.

**Risk to watch:** Steps 7–9 (SSE + algorithms) are the hardest. Budget extra time. If you fall behind, consider a "mock" SSE that yields 2–3 fake events—you can swap in real streaming later and still demo the UX.

---

# STEP 1: FastAPI Skeleton + One HTML Page

## 1. Concept Explanation

**What is FastAPI?** A Python web framework for building APIs and web apps. Unlike Flask (minimal, synchronous), FastAPI is built on ASGI (Asynchronous Server Gateway Interface), meaning it can handle many concurrent requests without blocking. When one request waits for a database or HTTP call, the server can process others. For your app, this matters when you later stream SSE while other users might be loading pages.

**Why does it exist?** Traditional frameworks like Django/Flask use WSGI—one request at a time per worker. FastAPI uses async/await, so a single worker can juggle many I/O-bound operations. You're not using that power in Step 1, but the foundation is there.

**What is Jinja2?** A templating engine. You write HTML with placeholders (e.g. `{{ title }}`), and the server fills them in before sending the page. It keeps logic out of HTML and lets you reuse layouts (e.g. a base template with nav + footer).

**Why both?** FastAPI can serve JSON APIs *or* rendered HTML. You'll use both: HTML for pages, JSON for API endpoints (e.g. geocoding, solve). Jinja2 is the standard way to render HTML in Python.

## 2. What to Build

**File: `app/main.py`**

- Import FastAPI, create an app instance.
- Mount a `StaticFiles` middleware for `/static` pointing to `app/static` (create the folder).
- Create a `Jinja2Templates` instance pointing to `app/templates`.
- Define a route: `GET /` that returns a rendered HTML template.
- Use `uvicorn` to run the app (either via `if __name__ == "__main__"` or by running `uvicorn app.main:app --reload` from the project root).

**File: `app/templates/base.html`**

- Minimal HTML5 document: `<!DOCTYPE html>`, `<html>`, `<head>`, `<body>`.
- In `<head>`: a `<title>` block (e.g. `{% block title %}Travel Route Optimizer{% endblock %}`).
- In `<body>`: a simple heading like "Travel Route Optimizer — London" and a `{% block content %}{% endblock %}`.
- No CSS or JS yet—just structure.

**File: `app/templates/index.html`**

- Extends `base.html` via `{% extends "base.html" %}`.
- Overrides `{% block content %}` with a short paragraph: "Enter places to visit. (Form coming in Step 5.)"

**Folder structure to create:**
```
app/
  main.py
  templates/
    base.html
    index.html
  static/          (empty for now)
```

**Logic for the route handler:**
- Receive the request.
- Call `templates.TemplateResponse("index.html", {"request": request})`.
- Return that response. (You can add a `title` in the context dict if your base uses it.)

**Edge cases:** None critical for Step 1. Ensure the working directory is the project root when running uvicorn, or use the correct module path.

## 3. How the Pieces Connect

- `main.py` is the entry point. All future routes (API and pages) will be added here or via routers.
- `base.html` will be extended by `index.html`, `solve.html`, and `results.html` in later steps.
- The `/static` mount will serve `map.js`, CSS, etc. later.

## 4. How to Verify

1. From project root: `uvicorn app.main:app --reload`
2. Open `http://127.0.0.1:8000/` in a browser.
3. You should see "Travel Route Optimizer — London" and the placeholder paragraph.
4. Check the browser dev tools (Network tab): the response should be `200` and `text/html`.

## 5. Thesis Committee Q&A Prep

**Q1: "Why FastAPI instead of Flask or Django?"**
- FastAPI is async-native, which supports SSE and concurrent I/O (geocoding, OSRM, Overpass) without blocking. Flask would require threading or separate workers for similar behavior.
- Automatic OpenAPI docs (Swagger) for API endpoints—useful for documenting your geocoding/solve APIs.

**Q2: "What is Jinja2 and why use templates?"**
- Jinja2 separates presentation from logic. You define a base layout once; child templates override blocks. This avoids duplicating HTML and keeps the codebase maintainable.

**Q3: "What is ASGI vs WSGI?"**
- WSGI is synchronous: one request per worker at a time. ASGI is asynchronous: a worker can start a request, yield while waiting for I/O, and handle another request. FastAPI uses ASGI, which enables non-blocking I/O and SSE streaming.

---

# STEP 2: OSRM Service — get_distance_matrix()

## 1. Concept Explanation

**What is OSRM?** Open Source Routing Machine. It computes routes on real road networks using OpenStreetMap data. The public demo at `router.project-osrm.org` lets you query travel times and distances between coordinates without hosting your own server.

**Why not Euclidean distance?** In a city, straight-line distance is misleading. A 2 km straight-line trip might be 15 minutes by road. OSRM returns *driving* (or walking) times based on actual roads, one-way streets, and traffic patterns (if available). For a travel optimizer, this is essential.

**What is a distance matrix?** A table where entry `[i][j]` is the travel time (or distance) from location `i` to location `j`. For N locations, you need N×N values. OSRM's `table` service returns this in one request (up to a limit—typically 100×100 for the public demo, but check their docs).

**API format:** OSRM expects coordinates as `longitude,latitude` (note: lon first, lat second—common in GeoJSON). You send a list of coordinates and get back a matrix of durations (seconds) and/or distances (meters).

## 2. What to Build

**File: `app/services/osrm.py`**

**Function: `get_distance_matrix(coordinates: list[tuple[float, float]]) -> list[list[float]]`**

- **Input:** A list of `(lon, lat)` tuples. Example: `[(−0.1276, 51.5074), (−0.0756, 51.5034)]` for London locations.
- **Output:** A 2D list where `result[i][j]` is the travel time in **seconds** from `coordinates[i]` to `coordinates[j]`. If OSRM cannot compute a route (e.g. unreachable), you need a fallback—see edge cases.
- **Logic:**
  1. Build the OSRM table URL. Format: `https://router.project-osrm.org/table/v1/driving/{lon1},{lat1};{lon2},{lat2};...?annotations=duration`
  2. Use `httpx` (async) or `requests` (sync) to GET the URL. For now, sync is fine; you'll use `asyncio.to_thread` later when calling from async code.
  3. Parse the JSON response. The durations are in `response["durations"]`—a list of lists.
  4. Return that matrix. Ensure the order matches the input coordinates (OSRM preserves order).

**Edge cases:**
- **Unreachable pairs:** OSRM may return `null` for some cells. Replace `null` with a large value (e.g. 999999) or the maximum duration in the matrix × 2. Document this in a comment—your thesis should mention that unreachable pairs are penalized.
- **Empty input:** If `coordinates` is empty or has one point, OSRM may error. Return `[[0]]` for one point, `[]` for empty (or raise a clear error—your choice; document it).
- **Rate limiting:** The public demo has rate limits. If you get 429, your code should raise an exception with a clear message. You can add retry logic later; for now, failing fast is fine.

**Dependency:** Add `httpx` to `requirements.txt` if not already there.

## 3. How the Pieces Connect

- `osrm.py` is a standalone service. Nothing calls it yet.
- In Step 3, `nearest_neighbor.py` will receive a distance matrix (from this service) and compute a route.
- In Step 6, the frontend will send coordinates (from Nominatim geocoding) to the backend; the backend will call OSRM to get the matrix before solving.

## 4. How to Verify

**Option A — Python script:**
Create a small test script (or use a Python REPL) that:
1. Imports `get_distance_matrix` from `app.services.osrm`
2. Calls it with two London coordinates, e.g. `[(−0.1276, 51.5074), (−0.0756, 51.5034)]` (Big Ben and Tower of London)
3. Prints the result. You should get a 2×2 matrix. The diagonal might be 0; off-diagonal should be a few hundred to a few thousand seconds (minutes).

**Option B — Temporary route in main.py:**
Add a debug route `GET /test-osrm` that calls `get_distance_matrix` with two hardcoded London coords and returns the matrix as JSON. Visit `/test-osrm` and check the response. Remove before Step 5.

## 5. Thesis Committee Q&A Prep

**Q1: "Why OSRM instead of Google Maps API?"**
- OSRM is free and open-source. No API key, no usage limits (within the public demo's fair-use). For a thesis, it avoids billing and ToS concerns. Google's Distance Matrix API is more accurate in some regions but requires a key and has costs.

**Q2: "What if two locations are unreachable by car?"**
- OSRM returns `null` for that pair. We replace it with a large penalty value so the solver can still produce a route (it will avoid that pair if possible). We document this and could show a warning to the user if any pair is unreachable.

**Q3: "Why store the matrix instead of computing distances on demand?"**
- The solver (NN, SA) needs to evaluate many route permutations. Computing each distance via HTTP would be far too slow. One matrix request up front gives us O(1) lookup during solving.

---

# STEP 3: Nearest Neighbor (Plain TSP, No Time Windows Yet)

## 1. Concept Explanation

**What is the Travelling Salesman Problem (TSP)?** Given N cities and distances between them, find the shortest route that visits each city exactly once and returns to the start. It's NP-hard—no known polynomial-time algorithm for the optimal solution.

**What is Nearest Neighbor (NN)?** A greedy heuristic. Start at a city (often the first). At each step, go to the nearest unvisited city. Repeat until all are visited, then return to the start. It's fast (O(N²)) but not optimal. For 10 cities, it might be 10–20% worse than optimal; for 50, it can be much worse. For a thesis, it's a baseline to compare against Simulated Annealing.

**Why implement it?** It's simple, deterministic (same input → same output), and gives you a working solver quickly. You'll later add time-window penalties and compare NN vs SA.

**Input/output:** You receive a distance matrix and (optionally) a start index. You return an ordered list of indices representing the visit order, and the total cost (sum of travel times for that order).

## 2. What to Build

**File: `app/algorithms/nearest_neighbor.py`**

**Function: `solve(matrix: list[list[float]], start_index: int = 0) -> tuple[list[int], float]`**

- **Input:**
  - `matrix`: 2D list of travel times in seconds. `matrix[i][j]` = time from i to j.
  - `start_index`: Which location to start from (default 0).
- **Output:** `(route, total_cost)` where:
  - `route`: List of indices in visit order, e.g. `[0, 3, 1, 2, 0]` (start and end at 0).
  - `total_cost`: Sum of travel times along the route, in seconds.
- **Logic:**
  1. Let `n = len(matrix)`. If `n == 0`, return `([], 0)` or raise.
  2. Initialize `visited = {start_index}`. `route = [start_index]`. `current = start_index`. `total_cost = 0`.
  3. While `len(visited) < n`:
     - Among all `j` not in `visited`, find `j` with minimum `matrix[current][j]`.
     - Append `j` to `route`, add `matrix[current][j]` to `total_cost`, set `current = j`, add `j` to `visited`.
  4. Add the return leg: `total_cost += matrix[current][start_index]`, append `start_index` to `route`.
  5. Return `(route, total_cost)`.

**Edge cases:**
- `n == 1`: Route is `[0, 0]`, cost is 0.
- `matrix` not square or `start_index` out of range: raise `ValueError` with a clear message.

## 3. How the Pieces Connect

- `nearest_neighbor.solve` is called with the matrix from `osrm.get_distance_matrix`.
- In Step 4, you'll add a wrapper that computes a *penalized* cost (travel time + time-window violations). For now, the matrix is the only cost.
- In Step 7, the solver service will call NN and stream progress via SSE.

## 4. How to Verify

1. Create a small test matrix, e.g. 4×4 with known values:
   ```
   [[0, 10, 15, 20],
    [10, 0, 35, 25],
    [15, 35, 0, 30],
    [20, 25, 30, 0]]
   ```
2. Call `solve(matrix, 0)`. Manually trace: from 0, nearest is 1 (10). From 1, nearest unvisited is 3 (25). From 3, only 2 left (30). Return to 0 (20). Route: `[0, 1, 3, 2, 0]`, cost = 10+25+30+20 = 85.
3. Write a unit test in `tests/test_algorithms.py` that asserts this. Run `pytest tests/test_algorithms.py -v`.

## 5. Thesis Committee Q&A Prep

**Q1: "Why Nearest Neighbor? What are its limitations?"**
- NN is a greedy heuristic: it makes locally optimal choices (nearest city) but doesn't consider global structure. It can get "trapped" in bad regions. For example, if the nearest city leads you away from a cluster of other cities, you might have a long return leg. Its approximation ratio can be arbitrarily bad in theory, but in practice for small instances it's often acceptable.

**Q2: "What is the time complexity of NN?"**
- O(N²): for each of N steps, we scan up to N cities to find the nearest. With a priority queue you could do O(N² log N) for sparse graphs, but for a dense matrix O(N²) is standard.

**Q3: "Is the result deterministic?"**
- Yes. Same matrix and start_index always produce the same route. This makes it reproducible for testing and comparison.

---

# STEP 4: Add Penalty-Based Cost for TSPTW

## 1. Concept Explanation

**What is TSPTW?** Travelling Salesman Problem with Time Windows. Each location has an earliest and latest arrival time. You must visit each place within its window. This models real-world constraints: museums open 9–17, restaurants 12–14 for lunch, etc.

**Why a penalty instead of hard constraints?** Hard constraints (reject any route that violates a window) can make the problem infeasible. With 10 places and tight windows, there might be *no* valid route. A penalty approach: allow violations but add a large cost (e.g. 10,000 seconds per violation). The solver minimizes total cost, so it will try to avoid violations. You get a "best effort" route even when strict feasibility is impossible.

**Your cost function:** `cost = total_travel_time + (10_000 × number_of_violations)`. The factor 10,000 ensures one violation is "worse" than many extra minutes of travel. Tune it if needed; document your choice in the thesis.

**Feasibility check:** For each location, you need `earliest` and `latest` (in seconds from midnight or from a reference time). When evaluating a route, you simulate visiting in order: arrival time = previous departure + travel time. If arrival < earliest, you wait. If arrival > latest, that's a violation. Count violations, add penalty.

## 2. What to Build

**File: `app/algorithms/tsp_tw_utils.py`** (or add to `nearest_neighbor.py`—your choice; a separate utils file keeps algorithms clean)

**Function: `evaluate_route(route: list[int], matrix: list[list[float]], time_windows: list[tuple[float, float]], service_time: float = 0) -> tuple[float, int]`**

- **Input:**
  - `route`: Ordered list of location indices (including return to start if your format does).
  - `matrix`: Travel time matrix.
  - `time_windows`: List of `(earliest, latest)` in seconds from a reference (e.g. midnight). Same length as matrix. If a location has no window, use `(0, float('inf'))`.
  - `service_time`: Minutes (or seconds—be consistent!) spent at each location. Often 0 for simplicity; you can add 30 min later.
- **Output:** `(total_cost, violation_count)` where:
  - `total_cost` = total travel time + (10_000 × violation_count).
  - `violation_count` = number of time windows violated.
- **Logic:**
  1. Initialize `current_time = 0` (or your reference), `travel_time = 0`, `violations = 0`.
  2. For each consecutive pair `(i, j)` in the route (i.e. from `route[k]` to `route[k+1]`):
     - Add `matrix[route[k]][route[k+1]]` to `current_time` and `travel_time`.
     - If visiting `route[k+1]`: get `(earliest, latest)` for that index. If `current_time < earliest`, set `current_time = earliest` (wait). If `current_time > latest`, increment `violations`.
     - Add `service_time` to `current_time` (time spent at the location).
  3. `total_cost = travel_time + 10_000 * violations`.
  4. Return `(total_cost, violations)`.

**Update `nearest_neighbor.py`:**

- Add an optional parameter: `time_windows: list[tuple[float, float]] | None = None`.
- If `time_windows` is None, use the old logic (pure TSP).
- If provided, you have two options:
  - **Option A:** Use the same greedy rule but when choosing the "nearest" city, use a *penalized* distance: `matrix[i][j]` plus an estimate of whether going to j will cause a violation. This is tricky.
  - **Option B (simpler):** Keep NN as pure TSP (ignore time windows during the greedy step). After building the route, call `evaluate_route` to get the penalized cost. Return both the route and the evaluated cost. NN doesn't optimize for time windows; it just reports the cost. SA (Step 8) will actually optimize the penalized cost.
- I recommend **Option B** for clarity. NN remains a baseline; SA will do the real TSPTW optimization.

**Function: `evaluate_route` — edge cases:**
- Empty route: return `(0, 0)`.
- Single-node route: no travel, check if that node's window is satisfied at time 0.

## 3. How the Pieces Connect

- `evaluate_route` is used by both NN (to report cost) and SA (to compare solutions).
- `time_windows` will come from Overpass + `opening_hours` library (Step 10) and user override.
- In Step 11, you'll add strict feasibility check first; if infeasible, fall back to best-effort (penalty) mode.

## 4. How to Verify

1. Create a 3-node route, matrix, and time windows. Manually compute: travel times, arrival times, violations.
2. Call `evaluate_route` and assert the result matches.
3. Example: Route `[0, 1, 2, 0]`, matrix with known values, windows `[(0, 100), (50, 150), (200, 300)]`. If travel 0→1 is 60, arrival at 1 is 60, within [50,150]. If 1→2 is 100, arrival at 2 is 160, but window is [200,300]—violation. Cost = travel_time + 10000.
4. Add a test in `tests/test_algorithms.py`.

## 5. Thesis Committee Q&A Prep

**Q1: "Why 10,000 for the penalty? How did you choose it?"**
- The penalty must dominate travel time so the solver prefers avoiding violations over saving a few minutes. With typical travel times in the hundreds or low thousands of seconds, 10,000 per violation ensures that. I could tune it via experiments; for the thesis, document the choice and note that it's a hyperparameter.

**Q2: "What if all routes are infeasible?"**
- In best-effort mode, we still return the route with the fewest violations and lowest travel time. The user sees a warning. In Step 11 we'll try strict mode first; if no feasible route exists, we fall back to best-effort.

**Q3: "Do you consider service time (e.g. 30 min at each place)?"**
- Yes, via the `service_time` parameter. It's added to the current time after arriving at each location. For MVP we can use 0; for realism we can add a default (e.g. 30 min) or let the user specify per location.

---

# STEP 5: Basic Form + Leaflet Map

## 1. Concept Explanation

**What is Leaflet?** A JavaScript library for interactive maps. It uses OpenStreetMap tiles by default (free, no API key). You create a map, add markers, draw polylines. It's lightweight and widely used.

**Why a form?** Users need to input places they want to visit. A text input (or multiple inputs) plus a map to visualize them is the core UX. The form will submit to your backend (or trigger client-side geocoding first—Step 6).

**Why Bootstrap?** Quick, responsive layout. Buttons, inputs, cards. You're using it via CDN—no build step. For a thesis, it's pragmatic; you can mention that a production app might use a component library or custom CSS.

**Form flow (for now):** User types place names (comma-separated or one per input). A "Solve" button. For Step 5, the button can just show an alert or do nothing—the form and map are the goal.

## 2. What to Build

**File: `app/templates/index.html`**

- Extend `base.html`.
- Add a form:
  - A textarea or multiple text inputs for "Places to visit" (e.g. "Big Ben, Tower of London, British Museum").
  - A "Solve" button.
- Add a `<div id="map" style="height: 400px;">` for the Leaflet map.
- In `{% block content %}`, include the form and map div.

**File: `app/templates/base.html`**

- Add in `<head>`:
  - Bootstrap 5 CSS: `https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css`
  - Leaflet CSS: `https://unpkg.com/leaflet@1.9.4/dist/leaflet.css`
- Add before `</body>`:
  - Bootstrap JS: `https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js`
  - Leaflet JS: `https://unpkg.com/leaflet@1.9.4/dist/leaflet.js`
  - Your script: `<script src="/static/js/map.js"></script>` (or inline for now)

**File: `app/static/js/map.js`**

- Initialize a Leaflet map centered on London (e.g. `[51.5074, -0.1278]`).
- Use `L.tileLayer` with OpenStreetMap tiles: `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`.
- Add the tile layer to the map.
- Optionally add a default marker at the center.
- Export or expose nothing special—just run on load. Use `DOMContentLoaded` if the script runs in the head.

**Form behavior (minimal):**
- Give the form an `id` (e.g. `places-form`).
- Give the textarea/input an `id` (e.g. `places-input`).
- In `map.js`, add a listener for the form `submit` event. `preventDefault()`. Get the value of the input, split by comma, trim each. For now, `console.log` the list. Or show an alert with "You entered: X places."

**Edge cases:**
- Empty input: show a message "Please enter at least one place."
- Leaflet needs the map div to have a defined height; 400px works.

## 3. How the Pieces Connect

- `index.html` is the main page. The form will later POST to a solve endpoint or trigger an API call.
- `map.js` will grow: in Step 6, add markers for geocoded places; in Step 7, add a polyline for the route; in Step 9, update the route as SA progresses.
- The form data will be sent to Nominatim (Step 6) for geocoding, then to your backend for solving.

## 4. How to Verify

1. Run the app, visit `/`.
2. See the form and map. The map should show London, draggable and zoomable.
3. Type "Big Ben, Tower of London" in the input, click Solve. Check console or alert for the parsed list.
4. Resize the browser—Bootstrap should keep the layout reasonable.

## 5. Thesis Committee Q&A Prep

**Q1: "Why Leaflet instead of Google Maps?"**
- Leaflet is open-source, no API key, uses OSM data. Consistent with OSRM and Nominatim (also OSM-based). Avoids Google's ToS and billing.

**Q2: "How does the form relate to the backend?"**
- The form collects place names. The frontend will send them to a geocoding API (Nominatim) to get coordinates, then send coordinates to our backend. The backend fetches OSRM matrix, runs the solver, returns the route. The form is the entry point for user intent.

**Q3: "Why Bootstrap? Isn't it generic-looking?"**
- For an MVP and thesis, Bootstrap provides responsive layout and components quickly. The focus is on the algorithms and UX flow, not custom design. A production app might use Tailwind or a design system.

---

# STEP 6: Nominatim Geocoding with Progress Feedback

## 1. Concept Explanation

**What is Geocoding?** Converting place names (e.g. "Big Ben, London") into coordinates (latitude, longitude). The reverse—coordinates to names—is reverse geocoding.

**What is Nominatim?** The geocoding service for OpenStreetMap. Free, no API key, but rate-limited (1 request per second for the public instance). You send a query, get back JSON with `lat`, `lon`, `display_name`, etc.

**Why progress feedback?** If the user enters 5 places, you need 5 Nominatim requests. At 1 req/sec, that's 5 seconds. The user might think the app is frozen. Showing "Geocoding 2/5..." keeps them informed.

**Flow:** User submits form → frontend splits input into place names → for each place, call Nominatim API → collect results → show on map with markers → enable "Solve" or auto-proceed to solve.

## 2. What to Build

**Backend: `app/main.py`**

**New route: `POST /api/geocode`**

- **Input:** JSON body `{"places": ["Big Ben", "Tower of London", ...]}`.
- **Output:** JSON `{"results": [{"name": "...", "lat": 51.5, "lon": -0.12, "display_name": "..."}, ...]}`. If a place fails, include `{"name": "...", "error": "Not found"}`.
- **Logic:**
  1. Parse the JSON body.
  2. For each place, call Nominatim. Use `httpx` (async). URL: `https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1`. Add header `User-Agent: TravelRouteOptimizerThesis` (Nominatim requires a descriptive User-Agent).
  3. Respect rate limit: `await asyncio.sleep(1)` between requests (or use a semaphore).
  4. Extract `lat`, `lon`, `display_name` from the first result. If no results, add an error entry.
  5. Return the list.

**Alternative: client-side geocoding.** You could call Nominatim from the frontend (JavaScript). Pros: no backend load, parallel requests (but still rate-limited). Cons: CORS might require a proxy for some setups; you'd need to handle it. For a thesis, backend geocoding is simpler to explain and control.

**File: `app/services/nominatim.py`**

**Function: `geocode(place: str) -> dict | None`**

- **Input:** Place name string.
- **Output:** `{"lat": float, "lon": float, "display_name": str}` or `None` if not found.
- **Logic:** GET Nominatim search URL, parse JSON, return first result or None. Use `httpx` sync; the route handler can use `asyncio.to_thread` if you want to keep the handler async, or use `httpx.AsyncClient` and call async.

**Frontend: `app/static/js/map.js`**

- On form submit: prevent default, get place names, disable the button.
- Call `POST /api/geocode` with `fetch`, body `JSON.stringify({places: placeNames})`.
- While waiting, show "Geocoding..." (or use SSE for progress—see below).
- **Progress feedback option A:** Backend returns only when done. Frontend shows "Geocoding..." until response. Simple.
- **Progress feedback option B:** Use SSE for geocoding progress. Backend streams events: `{"type": "geocode_progress", "current": 2, "total": 5, "result": {...}}`. Frontend updates UI. More work but better UX.
- For Step 6, **Option A** is enough. Add a loading spinner or text. When response arrives, add markers to the map for each result, and store the coordinates for the solve step.

**Edge cases:**
- Empty place name: skip or return error.
- Nominatim returns empty: show "Place X not found" to user.
- Rate limit (429): retry after a delay or show error. Document in thesis.

## 3. How the Pieces Connect

- Form submit → `/api/geocode` → Nominatim → results → frontend adds markers to map.
- The coordinates from geocoding are what get sent to the solve endpoint (Step 7). The frontend will need to store them (e.g. in a variable or data attribute) after geocoding.
- OSRM (Step 2) receives these coordinates to build the matrix.

## 4. How to Verify

1. Enter "Big Ben, Tower of London, British Museum" in the form. Submit.
2. See "Geocoding..." (or spinner). After a few seconds, see markers on the map at the correct locations.
3. Try an invalid place ("asdfghjkl12345"). See "Place not found" or similar.
4. Check Network tab: POST to `/api/geocode`, response with lat/lon for each.

## 5. Thesis Committee Q&A Prep

**Q1: "Why Nominatim? What about Google Geocoding?"**
- Nominatim is free, OSM-based, no API key. Consistent with our OSRM and Leaflet stack. Google requires a key and has costs. For London, Nominatim is accurate enough.

**Q2: "How do you handle rate limits?"**
- Nominatim allows 1 request per second. We add a 1-second delay between requests. For 10 places, that's 10 seconds. We could use a paid Nominatim instance or batch geocoding for production.

**Q3: "What if a place is ambiguous (e.g. 'London')?"**
- Nominatim returns the best match. "London" typically gives London, UK. We take the first result. For a thesis, we document this; a production app might let the user disambiguate.

---

# STEP 7: SSE Streaming for NN

## 1. Concept Explanation

**What is Server-Sent Events (SSE)?** A one-way stream from server to client. The server sends text chunks in a specific format (`data: {...}\n\n`). The client uses `EventSource` or `fetch` with a reader to receive them. Unlike WebSockets, SSE is HTTP-based and one-way—perfect for progress updates.

**Why SSE for the solver?** The solver (NN, SA) can take seconds. Without streaming, the user waits with a blank screen. With SSE, you send events like "Starting NN", "Visited city 3", "Done. Route: [...]". The frontend updates the map in real time—the route appears step by step. This is a strong thesis demo.

**How it works:**
- Client: `fetch(url, {method: 'POST', body: ...})` with `Accept: text/event-stream`, then read the body as a stream.
- Server: Return a `StreamingResponse` with `Content-Type: text/event-stream`. Write lines like `data: {"step": 1}\n\n`. Keep the connection open until done.
- Each `\n\n` is one event. The client parses the JSON and updates the UI.

**asyncio.to_thread:** Your solver (NN) is CPU-bound and synchronous. If you run it in the main async loop, it blocks everything—no SSE can be sent. `asyncio.to_thread(solve, ...)` runs the solver in a thread pool. The main thread stays free to stream events. But wait—the solver runs in one go; it doesn't yield. So how do you stream progress?

**Key insight:** You need the solver to *yield* or *callback* during execution. Two approaches:
- **A) Generator/callback:** Modify NN to accept a callback or yield after each step. When it visits a new city, it calls `callback(route_so_far)` or yields. The SSE handler runs NN in a thread, and when it gets a callback, it sends an event. This requires passing a queue or callback from the async side to the thread.
- **B) Run NN in thread, poll a shared structure:** NN writes to a shared list/dict. The async handler polls it and sends events. Simpler but hacky.
- **C) Make NN a generator:** `def solve_nn(...): ...; yield (route, cost); ...` — but NN is synchronous. You'd run it in a thread and have it put results on a queue; the async side reads the queue and sends SSE. The "progress" is: after each city added, put the partial route on the queue.

**Recommended:** Modify NN to accept an optional `progress_callback: Callable[[list[int], float], None]`. Each time it adds a city to the route, it calls `progress_callback(current_route, current_cost)`. The SSE handler runs NN in a thread with a callback that puts `(route, cost)` on an `asyncio.Queue`. The async handler reads from the queue and sends SSE events. When NN finishes, it puts a "done" sentinel. Clean and testable.

## 2. What to Build

**File: `app/algorithms/nearest_neighbor.py`**

- Add optional parameter `progress_callback: Callable[[list[int], float], None] | None = None`.
- Inside the main loop, each time you add a city to the route, call `progress_callback(route.copy(), total_cost)` if callback is not None.
- After the return leg, call `progress_callback(route, total_cost)` once more (final state).

**File: `app/services/solver.py`**

**Function: `run_nn_with_progress(matrix: list[list[float]], start_index: int) -> AsyncGenerator[dict, None]`**

- **Input:** Matrix and start index.
- **Output:** Async generator that yields dicts like `{"type": "progress", "route": [0,1,2], "cost": 123}` and finally `{"type": "done", "route": [...], "cost": ...}`.
- **Logic:**
  1. Create an `asyncio.Queue`.
  2. Define a callback that puts `(route, cost)` on the queue.
  3. Start a task: `asyncio.to_thread(nn.solve, matrix, start_index, progress_callback=callback)`.
  4. In a loop, `await queue.get()`. If it's the final result (you need a sentinel—e.g. `None` or a special dict), break. Otherwise yield `{"type": "progress", "route": ..., "cost": ...}`.
  5. The tricky part: the thread will put many items. You need to know when it's done. Option: the callback is called N times (once per city) plus once at the end. Have the callback put `("progress", route, cost)` and at the very end put `("done", route, cost)`. The generator reads until it gets `("done", ...)`.
  6. Actually, simpler: run NN in thread. The callback puts on the queue. The main coroutine reads from the queue. But the thread blocks until NN finishes—so the callback runs during NN execution. Good. When NN returns, the thread ends. You need the callback to be called from within the thread. So: `asyncio.to_thread` runs `nn.solve(matrix, start_index, callback)`. The callback runs in the thread. It must put on the queue in a way the async side can read. `queue.put_nowait` is not async-safe from a thread... Actually, `asyncio.Queue.put` from a thread might block. Use `queue.put_nowait`—it's safe from a thread as long as the queue isn't full. Or use `asyncio.run_coroutine_threadsafe(queue.put(...), loop)` to schedule a put from the thread. This gets messy.
  7. **Simpler approach:** Use a `queue.Queue` (thread-safe) instead of `asyncio.Queue`. The callback does `thread_queue.put((route, cost))`. The async side runs a loop: `while True: item = await asyncio.to_thread(thread_queue.get)`. When NN finishes, the callback puts a sentinel like `("done", route, cost)`. The async side yields until it gets "done". Clean.

**File: `app/main.py`**

**New route: `POST /api/solve/stream`**

- **Input:** JSON body `{"coordinates": [[lon, lat], ...]}`.
- **Output:** SSE stream.
- **Logic:**
  1. Parse coordinates.
  2. Call `osrm.get_distance_matrix(coordinates)`. Use `asyncio.to_thread` because OSRM is sync.
  3. Create an async generator that calls `solver.run_nn_with_progress(matrix, 0)` and yields each event as SSE.
  4. Return `StreamingResponse(gen(), media_type="text/event-stream")`. Set headers: `Cache-Control: no-cache`, `Connection: keep-alive`.
  5. Format each event: `data: {json}\n\n`.

**SSE format:** Each event is `data: {"type": "progress", "route": [0,1,2], "cost": 123}\n\n`. The client parses the JSON.

**Frontend: `app/static/js/map.js`**

- When user clicks Solve (after geocoding), call `POST /api/solve/stream` with the coordinates.
- Use `fetch` with `body: JSON.stringify({coordinates: coords})`. Read the response body with `response.body.getReader()`. Decode chunks, split by `\n\n`, parse each `data: {...}` line.
- For each "progress" event: update the map—draw a polyline through the route (use the coordinates corresponding to the route indices). Update a status div: "Step 3 of 5".
- For "done" event: show final route, enable any "Results" button.

**Edge cases:**
- Empty coordinates: return 400 before streaming.
- OSRM failure: return 500 or stream an error event.
- Client disconnects: the generator may get a broken pipe. Catch and exit gracefully.

## 3. How the Pieces Connect

- Form → Geocode (Step 6) → coordinates stored → Solve button → `/api/solve/stream` → OSRM matrix → `solver.run_nn_with_progress` → NN with callback → SSE events → frontend updates map.
- The solver service is the orchestrator: it calls OSRM, runs the algorithm, and streams progress.

## 4. How to Verify

1. Enter 4–5 places, geocode, click Solve.
2. Watch the map: a polyline should appear and extend as each city is "visited".
3. Check Network tab: the request to `/api/solve/stream` should show "pending" for several seconds, then "200". The response type should be "eventsource" or "stream".
4. Console log the events to ensure they're parsed correctly.

## 5. Thesis Committee Q&A Prep

**Q1: "Why SSE instead of WebSockets?"**
- SSE is simpler: one-way, HTTP-based. We only need server→client updates. WebSockets are bidirectional and require a different protocol. SSE fits our use case and is easier to implement and debug.

**Q2: "Why run the solver in a thread? Doesn't that defeat async?"**
- The solver is CPU-bound (loops, arithmetic). Async doesn't help with CPU work—it only helps with I/O. If we ran the solver in the main thread, it would block the event loop and we couldn't send SSE. `asyncio.to_thread` runs it in a thread pool so the main loop stays responsive.

**Q3: "What if the user closes the tab mid-solve?"**
- The SSE connection drops. The server may get a write error. We should catch it and stop the generator. The solver thread will complete in the background (we can't easily cancel it without more work). For a thesis, documenting this is enough.

---

# STEP 8: Simulated Annealing

## 1. Concept Explanation

**What is Simulated Annealing (SA)?** A metaheuristic inspired by metallurgy. When metal cools slowly (annealing), atoms settle into a low-energy state. SA mimics this: start with a random solution and a high "temperature". Randomly perturb the solution (e.g. swap two cities). If the new solution is better, accept it. If worse, accept with probability `exp(-Δcost / T)`. So bad moves are sometimes accepted—this helps escape local optima. Over time, lower the temperature. Eventually, bad moves are rarely accepted, and the solution converges.

**Why SA for TSP?** SA can improve on greedy solutions. It explores the solution space and can find better routes than NN. It's not guaranteed optimal but often gets close. The temperature schedule (how fast you cool) affects quality and speed.

**Temperature schedule:** Start with `T = T0` (e.g. 1000 or a fraction of the initial cost). Each iteration: `T = T * alpha` (e.g. alpha=0.995). Or: `T = T0 / (1 + iteration)`. Or geometric: `T = T0 * alpha^iteration`. Document your choice.

**Perturbation:** For TSP, common moves: (1) swap two cities, (2) reverse a segment (2-opt), (3) move a city to another position. Start with swap—simplest.

**Stopping:** After a fixed number of iterations (e.g. 10,000) or when T drops below a threshold, or when no improvement for N iterations.

## 2. What to Build

**File: `app/algorithms/simulated_annealing.py`**

**Function: `solve(matrix: list[list[float]], initial_route: list[int], time_windows: list[tuple[float, float]] | None = None, T0: float = 1000, alpha: float = 0.995, max_iter: int = 10000) -> tuple[list[int], float]`**

- **Input:**
  - `matrix`: Travel time matrix.
  - `initial_route`: Starting route (from NN or random). Format: e.g. `[0, 1, 2, 3, 0]`.
  - `time_windows`: Optional. If provided, use `evaluate_route` for cost.
  - `T0`, `alpha`, `max_iter`: SA parameters.
- **Output:** `(best_route, best_cost)`.
- **Logic:**
  1. `current_route = initial_route`, `current_cost = evaluate(current_route)`.
  2. `best_route = current_route`, `best_cost = current_cost`.
  3. `T = T0`.
  4. For `iter` in 0..max_iter:
     - Pick two random indices i, j (i < j). Swap `current_route[i]` and `current_route[j]` (but not the start/end if it's the same node—e.g. if route is [0,1,2,3,0], swap among 1,2,3).
     - `new_cost = evaluate(new_route)`.
     - `delta = new_cost - current_cost`.
     - If delta <= 0: accept. `current_route = new_route`, `current_cost = new_cost`. If `new_cost < best_cost`, update best.
     - Else: accept with prob `exp(-delta / T)`. If accepted, same update. If not, revert.
     - `T = T * alpha`.
  5. Return `(best_route, best_cost)`.

**Evaluate:** Use `evaluate_route` from Step 4 if time_windows given; else sum of travel times along the route.

**Edge cases:**
- `initial_route` length 0 or 1: return as-is.
- Ensure swap doesn't break the route format (e.g. if you have start=end, don't swap that).

## 3. How the Pieces Connect

- SA takes the NN route as initial solution. So: NN runs first (or in parallel for comparison), then SA improves it.
- In Step 9, SA will also stream progress—each time a new best is found, send an event.
- `evaluate_route` is shared between NN (reporting) and SA (optimization).

## 4. How to Verify

1. Use the same 4×4 test matrix as NN. Run SA with `initial_route` from NN.
2. SA should often find a cost <= NN's cost. Run 10 times with different random seeds; document variance.
3. Unit test: assert that SA returns a valid route (all cities visited once) and cost matches manual evaluation.

## 5. Thesis Committee Q&A Prep

**Q1: "Why accept worse solutions? Doesn't that slow convergence?"**
- Accepting worse solutions helps escape local optima. A greedy algorithm gets stuck. SA can "jump" to a different region of the solution space. As T decreases, bad moves become rare, so we converge.

**Q2: "How did you choose the temperature schedule?"**
- T0 should be on the order of typical cost differences. Alpha controls cooling speed. 0.995 is common. I could tune via experiments; for the thesis I document the choice and note it as a hyperparameter.

**Q3: "What is the time complexity of SA?"**
- Per iteration: O(1) for swap, O(N) for evaluate. Total: O(max_iter * N). Unlike NN, we don't guarantee a good solution, but we often get better results with more iterations.

---

# STEP 9: SA SSE Streaming

## 1. Concept Explanation

Same as Step 7, but for SA. The challenge: SA has many iterations (e.g. 10,000). You can't send an event every iteration—that would flood the client. Send events only when a *new best* is found. That might be 10–50 events for a typical run. Enough to show progress without overwhelming.

## 2. What to Build

**File: `app/algorithms/simulated_annealing.py`**

- Add optional `progress_callback: Callable[[list[int], float], None] | None = None`.
- Each time `best_cost` is updated, call `progress_callback(best_route, best_cost)`.
- Optionally call at the start with the initial route.

**File: `app/services/solver.py`**

**Function: `run_sa_with_progress(matrix, initial_route, time_windows=None, **sa_params) -> AsyncGenerator[dict, None]`**

- Same pattern as `run_nn_with_progress`: thread queue, callback, async generator.
- Run SA in `asyncio.to_thread` with the callback.
- Yield SSE events for each progress update.

**File: `app/main.py`**

**Update `/api/solve/stream` or add `/api/solve/stream/sa`:**

- Option A: One endpoint that accepts `{"coordinates": [...], "algorithm": "nn" | "sa"}`. If "sa", run NN first (sync, fast), then SA with NN's route as initial. Stream both: NN progress, then SA progress.
- Option B: Separate endpoints. For SA, the client must send coordinates; the backend runs NN internally to get the initial route, then SA. Stream SA progress.
- I'd recommend Option A: one endpoint, one flow. User picks algorithm or we run both and show comparison (Step 12).

**Frontend:**
- Same as Step 7, but events may be less frequent. Update the polyline when a new best is found. Show "SA: new best found (iteration X)".

## 3. How the Pieces Connect

- Solver service runs NN (optional) then SA. Streams SA progress.
- Step 12 will show NN vs SA side-by-side, so you may run both and stream the one the user selected, or run both and show two result panels.

## 4. How to Verify

1. Solve with SA. Watch the map: the route should update when SA finds a better solution.
2. Compare final cost to NN. SA should often be better or equal.

## 5. Thesis Committee Q&A Prep

**Q1: "Why stream only on improvement? Why not every iteration?"**
- 10,000 iterations would mean 10,000 SSE events. That's too many for the client and the network. Streaming on improvement gives meaningful feedback without overhead.

**Q2: "What if SA never improves on NN?"**
- It can happen with easy instances or bad parameters. We still return the best we have. The user sees the route. We could add a note: "SA did not improve on NN for this instance."

**Q3: "How do you ensure the stream doesn't block the solver?"**
- Same as Step 7: solver runs in a thread, callback puts on a thread-safe queue, async handler reads and sends. The solver never blocks on I/O.

---

# STEP 10: Overpass + opening_hours Library + User Override

## 1. Concept Explanation

**What is Overpass?** An API to query OpenStreetMap data. You write a query in Overpass QL (a query language) to get nodes/ways with certain tags (e.g. `amenity=restaurant`, `opening_hours=*`). You send the query to `https://overpass-api.de/api/interpreter` (or another instance).

**What is opening_hours?** A format for expressing business hours, e.g. `"Mo-Fr 09:00-17:00"`. The `osm-humanized-opening-hours` Python library parses this string and can tell you if a place is open at a given time. Essential for TSPTW.

**User override:** Overpass might not have opening hours for every place, or the data might be wrong. Let the user manually set hours (e.g. "9:00–17:00") for any location. Store this in the frontend (or in the request) and merge with Overpass data.

**Flow:** For each geocoded place, we have (lat, lon). Query Overpass for nearby OSM nodes with `opening_hours` tag. Parse with `opening_hours` library. Convert to (earliest, latest) in seconds for the user's chosen day (e.g. today). If no data, use user override or (0, 86400) (whole day).

## 2. What to Build

**File: `app/services/overpass.py`**

**Function: `get_opening_hours(lat: float, lon: float, radius_m: int = 50) -> str | None`**

- **Input:** Coordinates and search radius.
- **Output:** The raw `opening_hours` string from OSM, or None if not found.
- **Logic:**
  1. Build Overpass query: search for nodes within radius of (lat, lon) that have `opening_hours` tag. Overpass QL example: `[out:json]; node(around:50, lat, lon)["opening_hours"]; out;`
  2. GET the Overpass API. Parse JSON. Extract `opening_hours` from the first result if any.
  3. Return the string or None.

**File: `app/services/opening_hours_utils.py`** (or in overpass.py)

**Function: `parse_to_time_window(opening_hours_str: str, day_of_week: int = 0) -> tuple[float, float] | None`**

- **Input:** `opening_hours_str` (e.g. "Mo-Fr 09:00-17:00"), `day_of_week` (0=Monday, 6=Sunday).
- **Output:** `(earliest_seconds, latest_seconds)` from midnight, or None if closed that day.
- **Logic:** Use the `opening_hours` library. Check the library's API—it typically has a method to get intervals for a day. Convert hours to seconds (e.g. 9:00 = 9*3600).
- **Dependency:** Add `osm-humanized-opening-hours` to requirements.txt. This is the most established OSM opening_hours parser. It provides `OHParser` with methods like `is_open()` and `next_change()`. Check its API for extracting time intervals for a given day.

**Integration in solver flow:**
- When the frontend sends coordinates for solve, it can also send `time_windows_override: {index: {earliest: "09:00", latest: "17:00"}}` for user overrides.
- Backend: for each location, call `overpass.get_opening_hours(lat, lon)`. If found, parse with `parse_to_time_window`. If user override for that index, use override instead. If neither, use (0, 86400).
- Pass the list of (earliest, latest) to `evaluate_route` and to the solver.

**API change:** `POST /api/solve/stream` now accepts `{"coordinates": [...], "time_windows_override": {...}, "day_of_week": 0}`. The backend fetches Overpass data for each coordinate, merges with override, builds the time_windows list.

**Edge cases:**
- Overpass timeout or error: fall back to (0, 86400) or user override.
- `opening_hours` string unparseable: same fallback.
- Multiple OSM nodes in radius: take the closest or first. Document.

## 3. How the Pieces Connect

- Geocoding gives coordinates. Before solving, we fetch opening hours for each. The solve request includes coordinates and optional overrides.
- The time_windows list is passed to `evaluate_route` and to SA (and NN for cost reporting).

## 4. How to Verify

1. Use a known place with opening hours (e.g. British Museum). Call `get_opening_hours` with its coordinates. Check the returned string.
2. Call `parse_to_time_window` with that string. Verify (earliest, latest) for a weekday.
3. In the UI, add optional override fields per place. Solve and check that the route respects (or penalizes) violations.

## 5. Thesis Committee Q&A Prep

**Q1: "Why Overpass instead of a different source?"**
- Overpass is the standard way to query OSM. Our stack is OSM-based (Nominatim, OSRM, Leaflet). Consistency and no API keys.

**Q2: "What if opening hours are missing or wrong?"**
- We allow user override. If no data, we assume the whole day (0–24h). We document this and could show a warning to the user.

**Q3: "How do you handle time zones?"**
- For London, we use local time. The `osm-humanized-opening-hours` library works with local time. If we expand to other cities, we'd need timezone handling. Document for thesis scope.

---

# STEP 11: Infeasibility — Strict + Best-Effort Fallback

## 1. Concept Explanation

**Strict TSPTW:** Only accept routes where every time window is satisfied. If no such route exists, the problem is infeasible.

**Best-effort:** Use the penalty cost. Return the route with the fewest violations and lowest travel time. Always return something.

**Strategy:** Try strict first. If the solver returns a route with violations > 0, treat it as infeasible. Run again in best-effort mode (minimize penalized cost). Return the best-effort result with a warning: "Could not find a route that satisfies all time windows. Showing best effort."

**How to "try strict":** In strict mode, reject any route with violations. For NN: after building the route, check violations. If > 0, NN "failed" in strict mode. For SA: only accept moves that result in 0 violations. But SA with hard constraints is tricky—you might never find a feasible solution. Simpler: run the solver (NN or SA) with the penalty. If the result has 0 violations, great. If not, we already have the best-effort result. So we don't need two runs—we run once with the penalty, and if violations > 0, we show a warning. That's effectively "best-effort". For "strict", we could run a feasibility check: is there any route with 0 violations? That's as hard as the full problem. **Practical approach:** Run the solver with the penalty. If the best solution has 0 violations, we're good. If not, show the solution with a warning. We don't need a separate "strict" solver—we just report feasibility based on the result.

**Alternative:** Have a "strict" mode where we use a very high penalty (e.g. 1e9) so the solver will almost never accept a violation. If it still can't find a feasible route, we fall back to the normal penalty and show best-effort. This is a bit redundant. **Simpler:** One run. Check result. If violations > 0, show warning. Done.

## 2. What to Build

**File: `app/services/solver.py`**

- After getting the final route from NN or SA, call `evaluate_route` to get `(cost, violations)`.
- If `violations == 0`: return `{"feasible": True, "route": ..., "cost": ..., "warning": None}`.
- If `violations > 0`: return `{"feasible": False, "route": ..., "cost": ..., "violations": violations, "warning": "Could not find a route satisfying all time windows. Showing best effort."}`.

**Frontend:**
- When displaying the result, if `feasible` is False, show the warning in a Bootstrap alert (yellow or orange).
- Still display the route on the map.

**Edge case:** If time_windows are all (0, inf), every route is feasible. Violations will always be 0.

## 3. How the Pieces Connect

- Solver already uses `evaluate_route`. Now we explicitly check violations and set the `feasible` flag.
- The SSE "done" event and the results page (Step 12) include this flag and warning.

## 4. How to Verify

1. Create a scenario with tight, conflicting time windows (e.g. two places with non-overlapping hours). Solve. You should see the warning and a best-effort route.
2. Create a scenario with loose windows. Solve. No warning.

## 5. Thesis Committee Q&A Prep

**Q1: "Why not always use strict constraints?"**
- Strict constraints can make the problem infeasible. With 10 places and tight windows, there might be no valid route. Best-effort always gives the user something useful.

**Q2: "How do you define 'best effort'?"**
- We minimize the penalized cost: travel time + 10,000 per violation. So we prefer fewer violations and less travel. The route we return minimizes that combined cost.

**Q3: "Could you use a different algorithm for strict feasibility?"**
- Constraint programming (CP) or mixed-integer programming (MIP) can enforce hard constraints. For a thesis, our penalty approach is simpler and still produces useful results. We document the limitation.

---

# STEP 12: Algorithm Results Page (NN vs SA Side-by-Side)

## 1. Concept Explanation

**Purpose:** Let the user (and the thesis committee) compare NN and SA directly. Same input, two algorithms, side-by-side results. Shows route, cost, violations, and optionally a map for each.

**Layout:** Two columns (or two cards). Left: NN result. Right: SA result. Each shows: route order, total cost, violations, and a small map or shared map with both routes in different colors.

## 2. What to Build

**File: `app/templates/results.html`**

- Extends `base.html`.
- Two columns (Bootstrap grid): `col-md-6` for NN, `col-md-6` for SA.
- Each column: heading "Nearest Neighbor" / "Simulated Annealing", a div for route list (e.g. "1. Big Ben, 2. Tower of London, ..."), cost in minutes, violations count, feasibility warning if any.
- A shared map or two small maps showing the routes. Use different colors (e.g. blue for NN, red for SA).

**Backend:** 
- Option A: One request that runs both algorithms and returns both results. `POST /api/solve/compare` with coordinates. Returns `{"nn": {...}, "sa": {...}}`.
- Option B: Two separate requests. Frontend calls solve for NN, then for SA. Simpler but two round-trips.
- Option A is better for a clean demo.

**File: `app/main.py`**

**Route: `GET /results`** — Renders `results.html`. Pass `nn_result` and `sa_result` in the context. These come from... you need to get them from somewhere. The flow: user solves from index page, gets redirected to results with the data. But results are computed asynchronously via SSE. So the flow is:
- User on index, enters places, geocodes, clicks "Solve".
- Frontend streams from `/api/solve/stream`. When done, we have NN and SA results. We need to run both. So the stream could send both, or we have a "compare" endpoint that runs both and streams the combined progress, then returns a redirect URL with result IDs... This gets complex if we don't have a DB.
- **Simpler flow:** The solve stream runs one algorithm (user selects NN or SA). For "compare" mode, we add a "Compare" button that runs both. The frontend: (1) Call `/api/solve/compare` with coordinates. This runs NN and SA (NN is fast; SA takes longer). Stream progress from SA (and maybe NN). When both done, the response includes both results. (2) Frontend stores both in JS, then navigates to `/results` with the data in the URL (as base64 or JSON in a fragment) or in sessionStorage. (3) `/results` reads from sessionStorage or from a query param. If the data is in the URL, it can be huge. Better: store in sessionStorage, redirect to `/results`, and the results page reads from sessionStorage. No backend state needed.
- **Implementation:** `POST /api/solve/compare` returns JSON (not stream) with both results. It runs NN (fast), then SA with NN's route. Total time = OSRM + NN + SA. For 5–10 places, maybe 15–30 seconds. The frontend shows "Computing..." and then displays the results. We could stream progress from SA during that time—the endpoint would need to be SSE. So: `POST /api/solve/compare/stream` that streams events for both NN and SA progress, and ends with a final event `{"type": "done", "nn": {...}, "sa": {...}}`. The frontend updates the UI as it goes, and when done, shows the side-by-side. The results page could be the same as index but with a "results" section that appears when done. Or a separate `/results` page that expects data in sessionStorage. When the stream is done, the frontend does `sessionStorage.setItem("solveResults", JSON.stringify(data))` and `window.location.href = "/results"`. The results page loads, reads from sessionStorage, renders the comparison. Clean.

**File: `app/main.py`**

**Route: `GET /results`**
- Renders `results.html`. The template can use a small inline script that reads `sessionStorage.getItem("solveResults")` and renders the comparison. So the backend doesn't need to pass the data—the frontend does it all. The backend just serves the HTML. The template has `{% block scripts %}` with JS that checks sessionStorage and builds the DOM. If no data, show "No results. Go back and solve."

**File: `app/services/solver.py`**

**Function: `run_compare(matrix, time_windows, start_index=0) -> tuple[dict, dict]`**
- Run NN, get `(nn_route, nn_cost)`.
- Run SA with `nn_route` as initial. Get `(sa_route, sa_cost)`.
- Evaluate both for violations.
- Return `{"route": nn_route, "cost": nn_cost, "violations": nn_violations}, {"route": sa_route, "cost": sa_cost, "violations": sa_violations}`.

**Streaming version:** `run_compare_stream` — yields NN progress, then SA progress, then `{"type": "done", "nn": {...}, "sa": {...}}`.

## 3. How the Pieces Connect

- Index page: user solves, chooses "Compare". Frontend calls `/api/solve/compare/stream`, gets SSE, updates map with both routes (or the better one), stores final result in sessionStorage, redirects to `/results`.
- Results page: reads sessionStorage, renders NN vs SA side-by-side.

## 4. How to Verify

1. Solve with compare mode. See both algorithms run. Redirect to results.
2. Results page shows NN and SA with costs. SA should often be better or equal.
3. Check that violations and warnings are correct for each.

## 5. Thesis Committee Q&A Prep

**Q1: "Why compare NN and SA?"**
- NN is a baseline—simple and fast. SA is a metaheuristic that can improve on it. The comparison demonstrates that SA often finds better solutions and justifies its added complexity.

**Q2: "How do you visualize the difference?"**
- We show both routes on the map (different colors) and the cost/violations for each. The user can see which route is shorter and which respects time windows better.

**Q3: "What if SA is worse than NN?"**
- It can happen (rarely). We document it. SA is stochastic; with different seeds or parameters, results vary. We show both and let the user decide.

---

# STEP 13: Deploy to Railway (PostgreSQL, Env Vars)

## 1. Concept Explanation

**What is Railway?** A platform for deploying web apps. You connect a GitHub repo, Railway builds and runs your app. It supports PostgreSQL, Redis, and env vars. Free tier available.

**Why PostgreSQL for production?** SQLite is fine for dev but doesn't scale well for concurrent writes and isn't ideal for production. PostgreSQL is robust and widely used. Railway offers managed Postgres.

**Env vars:** Secrets (API keys, DB URL) and config (debug mode, base URL) should not be in code. Use environment variables. Railway lets you set them in the dashboard.

**Database for MVP:** You said DB is optional. If you deploy without persistence, you can skip DB setup for now. But if you want to store solve history or user sessions later, you'll need it. For Step 13, the minimal deploy is: FastAPI app, static files, templates. No DB required. Add PostgreSQL when you add persistence.

**What to deploy:** The FastAPI app. Railway needs a `Procfile` or it will detect `uvicorn` and run it. You need `requirements.txt`. Set `PORT` env var (Railway provides it) and bind uvicorn to `0.0.0.0:PORT`.

## 2. What to Build

**File: `Procfile`** (or `railway.json` / `nixpacks.toml` depending on Railway's setup)
- `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Railway sets `PORT` automatically.

**File: `app/main.py`**
- Read `PORT` from env: `port = int(os.getenv("PORT", 8000))`. When running locally, use 8000. On Railway, use `PORT`.
- In `if __name__ == "__main__"`: `uvicorn.run(app, host="0.0.0.0", port=port)`.

**File: `runtime.txt`** (optional)
- `python-3.11` to pin the Python version.

**Railway setup:**
- Connect GitHub repo.
- Add a new project, deploy from repo.
- Set env vars if needed: `ENVIRONMENT=production`, any API keys (you might not have any).
- For PostgreSQL (when you add it): Add Postgres service, get `DATABASE_URL`, set it in env. Use it in SQLAlchemy. For MVP without DB, skip.

**CORS:** If your frontend is on the same domain, no CORS needed. If you later have a separate frontend, add `CORSMiddleware` to FastAPI with the frontend origin.

**Static files:** Ensure `StaticFiles` is mounted correctly. In production, you might serve static files via a CDN, but for a thesis, serving from the app is fine.

## 3. How the Pieces Connect

- Railway builds from `requirements.txt`, runs the Procfile command.
- The app listens on `0.0.0.0` so it accepts external connections.
- Env vars are read at startup.

## 4. How to Verify

1. Push to GitHub. Railway auto-deploys.
2. Visit the Railway-provided URL. You should see your app.
3. Test: enter places, geocode, solve. OSRM and Nominatim are public APIs, so they should work from Railway's servers.
4. Check logs for errors.

## 5. Thesis Committee Q&A Prep

**Q1: "Why Railway instead of Heroku or AWS?"**
- Railway has a simple workflow, good free tier, and built-in Postgres. Heroku reduced free tier. AWS is more complex. Railway is a good balance for a thesis project.

**Q2: "How do you handle secrets?"**
- Environment variables. Never commit API keys. Railway's dashboard stores them securely. The app reads them at runtime.

**Q3: "What about rate limits on OSRM and Nominatim from a deployed server?"**
- The public instances may rate-limit by IP. Railway's IP might hit limits under heavy use. For a thesis demo, it's usually fine. I'd document this and mention that production would use dedicated instances or paid APIs.

---

# Common Beginner Mistakes (By Step)

| Step | Mistake | What to watch |
|------|---------|---------------|
| 1 | Wrong working directory when running uvicorn | Run from project root; use `uvicorn app.main:app` |
| 2 | Lon/lat order wrong for OSRM | OSRM uses lon,lat (GeoJSON order) |
| 3 | Forgetting the return leg in NN | Cost must include travel back to start |
| 4 | Inconsistent time units (min vs sec) | Pick one (seconds) and stick to it |
| 5 | Leaflet map has zero height | Give the map div an explicit height in CSS |
| 6 | Nominatim rate limit (429) | 1 req/sec; add sleep between requests |
| 7 | Blocking the event loop with the solver | Use asyncio.to_thread for CPU-bound work |
| 8 | SA never converging | T0 too low or alpha too small; tune |
| 9 | Too many SSE events | Stream only on improvement, not every iteration |
| 10 | osm-humanized-opening-hours API | Check OHParser docs for extracting intervals per day |
| 11 | Confusing feasible vs best-effort | One run, check result, set flag |
| 12 | Passing large result data in URL | Use sessionStorage for client-side redirect |
| 13 | Binding to localhost | Use 0.0.0.0 and $PORT for Railway |

---

# Suggested Additions to requirements.txt

```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
jinja2>=3.1.0
httpx>=0.26.0
numpy>=1.26.0
python-multipart>=0.0.6
sse-starlette>=1.6.0    # Optional: cleaner SSE than manual StreamingResponse
osm-humanized-opening-hours>=0.6.0  # OSM opening_hours parser
```

---

*End of Implementation Plan. Good luck with your thesis defense.*
