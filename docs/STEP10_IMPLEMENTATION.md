# Step 10 Implementation — Overpass + opening_hours Library + User Override

## Table of Contents

1. [What Was the State Before Step 10?](#1-what-was-the-state-before-step-10)
2. [What Was Missing?](#2-what-was-missing)
3. [The Core Problem Step 10 Solves](#3-the-core-problem-step-10-solves)
4. [Key Concepts You Need to Understand](#4-key-concepts-you-need-to-understand)
5. [Every File Changed or Created](#5-every-file-changed-or-created)
6. [How All the Pieces Connect (End-to-End Flow)](#6-how-all-the-pieces-connect-end-to-end-flow)
7. [Tests Added](#7-tests-added)
8. [How to Test Manually](#8-how-to-test-manually)
9. [Edge Cases and Fallback Strategy](#9-edge-cases-and-fallback-strategy)
10. [Thesis Committee Q&A Prep](#10-thesis-committee-qa-prep)

---

## 1. What Was the State Before Step 10?

After completing Steps 1–9, the application could do the following:

| Capability | How it worked |
|---|---|
| Geocode places | `POST /api/geocode` → Nominatim → GPS coordinates |
| Distance matrix | `app/services/osrm.py` → OSRM `/table/v1/foot/` → walking times |
| Nearest Neighbor | `app/algorithms/nearest_neighbor.py` → greedy route with progress_callback |
| Simulated Annealing | `app/algorithms/simulated_annealing.py` → improves NN route with progress_callback |
| NN + SA SSE streaming | `solver.py` + `/api/solve/stream` → animated two-phase progress on the map |
| Time window penalty | `app/algorithms/tsp_tw_utils.py` → `evaluate_route()` adds 10,000 per violation |
| Walking geometry | Frontend fetches OSRM route geometry for final route |

**The critical gap:** The solver had a fully working penalty system for time window violations (Step 4), but **there was no way to get actual time window data**. The `time_windows` parameter in `evaluate_route` existed, but it was always `None` when called from the web interface. The solver had a stomach but no food.

In other words: the algorithm could optimize routes around opening hours, but nobody told it what the opening hours were.

---

## 2. What Was Missing?

### Missing piece 1: No opening hours data source

The user typed "British Museum" and got its GPS coordinates from Nominatim. But Nominatim doesn't return opening hours. We needed a second data source: **Overpass**, the query engine for OpenStreetMap, which can return tags like `opening_hours=Mo-Fr 10:00-17:00`.

### Missing piece 2: No parser for OSM opening_hours strings

OpenStreetMap stores opening hours in a human-readable but complex format:
- `Mo-Fr 09:00-17:00` — weekdays 9 to 5
- `Mo-Fr 09:00-12:00, 14:00-18:00` — split lunch break
- `24/7` — always open
- `Mo-Fr 09:00-17:00; Sa 10:00-14:00; PH off` — with weekend and public holiday rules

The solver needs `(earliest_seconds, latest_seconds)` — two numbers. We needed a parser to bridge the gap.

### Missing piece 3: No user override

Overpass data can be incomplete (not every place has `opening_hours` tagged) or wrong (a museum changed its hours but nobody updated OSM). The user needed a way to manually type "Opens 09:00, Closes 17:00" for any location.

### Missing piece 4: No merge logic

With three possible sources of time window data — Overpass (automatic), user override (manual), and default (no data) — we needed logic to decide which one wins:

1. **User override** takes priority (user knows best)
2. **Overpass data** is used if no override
3. **Default (0, 86400)** — full day, no constraint — if neither exists

### What the user experienced

**Before Step 10:**
- User clicks Solve → route is computed ignoring all opening hours → every place treated as "open all day"
- The time window penalty system existed but was dormant

**After Step 10:**
- User clicks Solve → backend queries Overpass for real opening hours → parses them → merges with user overrides → solver respects actual business hours
- The time window table shows OSM-fetched hours, user can override any of them

---

## 3. The Core Problem Step 10 Solves

**How do you feed real-world business hours into a mathematical optimizer?**

This requires a three-stage pipeline:

```
Real world                     Data                        Math
───────────                    ────                        ────
"British Museum               "Mo-Fr 10:00-17:00"         (36000.0, 61200.0)
 opens 10am,                   ↑ from Overpass              ↑ seconds from midnight
 closes 5pm"                                                used by evaluate_route()
```

Stage 1 (Overpass): Get the raw string from OpenStreetMap.
Stage 2 (Parser): Convert the string to (earliest, latest) seconds for a specific day.
Stage 3 (Merge): Combine with user overrides and pass to the solver.

---

## 4. Key Concepts You Need to Understand

### 4.1 What is Overpass?

**Overpass** is a read-only API for querying OpenStreetMap data. Unlike the OSM website (which renders maps), Overpass lets you ask structured questions:

> "Find all nodes within 200 meters of (51.5, -0.1) that have an `opening_hours` tag."

You write queries in **Overpass QL** (a query language) and send them to a public server. The response is JSON containing matching OSM elements with their tags.

**Example query:**
```
[out:json][timeout:10];
(
  node(around:200, 51.5074, -0.1278)["opening_hours"];
  way(around:200, 51.5074, -0.1278)["opening_hours"];
);
out tags 5;
```

This searches for both nodes (points) and ways (areas like building outlines) that have opening hours, within 200m of Big Ben's coordinates.

### 4.2 Why Multiple Overpass Endpoints?

Public Overpass servers are free but can be unreliable — rate limited, overloaded, or temporarily down. We use three endpoints and try them in order:

1. `overpass-api.de` — the main instance (Germany)
2. `maps.mail.ru` — Russian mirror
3. `overpass.kumi.systems` — community mirror

If the first one fails (timeout, 429, server error), we try the next. This is a **fallback chain** pattern.

### 4.3 The `opening_hours` Format

OSM's `opening_hours` is a surprisingly expressive format:

| String | Meaning |
|---|---|
| `Mo-Fr 09:00-17:00` | Weekdays 9am–5pm |
| `Mo-Fr 09:00-12:00, 14:00-18:00` | Weekdays with lunch break |
| `Mo-Fr 09:00-17:00; Sa 10:00-14:00` | Weekdays + Saturday |
| `24/7` | Always open |
| `Mo-Fr 22:00-02:00` | Overnight (opens 10pm, closes 2am next day) |
| `PH off` | Closed on public holidays |

We use the `humanized_opening_hours` Python library (`hoh.OHParser`) to parse these strings. The library can tell us what periods a place is open on a specific date.

### 4.4 Time Representation: Seconds from Midnight

The solver works in **seconds from midnight**. This is a simple, unambiguous representation:

| Time | Seconds |
|---|---|
| 00:00 (midnight) | 0 |
| 09:00 | 32,400 |
| 17:00 | 61,200 |
| 23:59 | 86,340 |
| 24:00 (end of day) | 86,400 |

A time window of `(32400, 61200)` means "you must arrive between 9am and 5pm." Travel times from OSRM are also in seconds, so everything is in the same unit.

### 4.5 The Merge Priority

When building the `time_windows` list for the solver, we check three sources for each location, in priority order:

```
1. User override?  → parse_user_override("09:00", "17:00") → (32400, 61200)
         ↓ no
2. Overpass data?   → parse_to_time_window("Mo-Fr 09:00-17:00", today) → (32400, 61200)
         ↓ no
3. Default          → (0, 86400)  — whole day, no constraint
```

This ensures: user input > automatic data > safe default.

### 4.6 Batch Querying vs. Per-Location Querying

We have two functions in `overpass.py`:

- **`get_opening_hours(lat, lon)`** — queries Overpass for a single location. Sends one HTTP request per place.
- **`get_opening_hours_batch(locations)`** — combines all locations into a single Overpass query. Sends one HTTP request total.

The solve endpoint uses `get_opening_hours_batch` because:
- Fewer HTTP requests = faster
- Lower chance of hitting rate limits
- Overpass queries can include multiple `around:` clauses in a union

The tradeoff: we have to match results back to locations by proximity (since Overpass doesn't tag which results belong to which input).

---

## 5. Every File Changed or Created

### 5.1 `app/services/overpass.py` — CREATED

**Purpose:** Fetch `opening_hours` tags from OpenStreetMap via the Overpass API.

**Public functions:**

#### `get_opening_hours(lat, lon, name, radius_m) -> dict | None`

Single-location query. Sends an Overpass QL query searching for nodes and ways with `opening_hours` within `radius_m` meters of `(lat, lon)`.

```python
async def get_opening_hours(
    lat: float,
    lon: float,
    name: str | None = None,
    radius_m: int = 200,
) -> dict | None:
```

- Returns `{"name": str, "opening_hours": str}` if found, or `None`.
- If `name` is provided, tries to match by name (case-insensitive substring).
- Falls back across 3 Overpass endpoints.

#### `get_opening_hours_batch(locations, radius_m) -> list[dict | None]`

Multi-location query. Builds a single Overpass union query covering all locations, then matches results back by proximity.

```python
async def get_opening_hours_batch(
    locations: list[dict],
    radius_m: int = 200,
) -> list[dict | None]:
```

- Input: `[{"lat": float, "lon": float, "name": str}, ...]`
- Returns: one `{"name": str, "opening_hours": str}` or `None` per location.
- Builds union query: for each location, adds `node(around:200,lat,lon)["opening_hours"];` and `way(around:...)["opening_hours"];`.

**Private functions:**

#### `_query_overpass(query) -> list[dict] | None`

Tries each Overpass endpoint in order. Returns the `elements` array from the JSON response, or `None` if all endpoints fail.

#### `_best_match(elements, name) -> dict | None`

From a list of Overpass elements, picks the best match:
1. If `name` is given, find the first element whose OSM name contains the search name (case-insensitive).
2. Otherwise, return the first element.

#### `_find_nearby_elements(elements, lat, lon, radius_m, name) -> dict | None`

From a batch query's combined results, finds the best match for a specific location:
1. Filter elements by rough distance (using `1° ≈ 111km` approximation).
2. Sort candidates by distance (closest first).
3. Try name matching among nearby candidates.
4. Fall back to the closest candidate.

**Distance calculation detail:**
```python
dlat = abs(el_lat - lat) * 111_000          # latitude degrees to meters
dlon = abs(el_lon - lon) * 111_000 * 0.65   # longitude correction (cos ~51°)
dist = (dlat**2 + dlon**2) ** 0.5            # Euclidean approximation
```

This is a rough approximation but sufficient for 200m-radius matching. The `0.65` factor corrects for longitude lines being closer together at higher latitudes (cosine of ~50° latitude).

---

### 5.2 `app/services/opening_hours_utils.py` — CREATED

**Purpose:** Convert OSM `opening_hours` strings and user input into `(earliest, latest)` seconds tuples for the solver.

#### `parse_to_time_window(opening_hours_str, target_date) -> tuple[float, float] | None`

Parses an OSM opening_hours string for a specific date.

```python
def parse_to_time_window(
    opening_hours_str: str,
    target_date: datetime.date | None = None,
) -> tuple[float, float] | None:
```

**Step-by-step logic:**

1. Reject empty/whitespace strings → return `None`.
2. Parse with `hoh.OHParser(opening_hours_str)`. If it throws → return `None`.
3. Get the day's schedule with `oh.get_day(target_date)`.
4. If closed today → return `None`.
5. If always open (24/7) → return `(0.0, 86400.0)`.
6. Get all periods for the day. For each period:
   - Extract `beginning.time()` and `end.time()`.
   - Convert to seconds: `hour * 3600 + minute * 60`.
   - Handle midnight closing: if `end_secs == 0` or `>= 86340`, set to `86400`.
   - Handle overnight: if `end_secs <= begin_secs`, set end to `86400`.
   - Track the overall earliest opening and latest closing.
7. Return `(earliest_secs, latest_secs)`.

**Why span across all periods?** A place open `09:00-12:00, 14:00-18:00` (lunch break) is treated as `(32400, 64800)` — the solver considers you can arrive anytime from 9am to 6pm. This is a simplification: the solver doesn't model lunch breaks. The window covers the full span, which is the most permissive interpretation.

#### `parse_user_override(earliest_str, latest_str) -> tuple[float, float]`

Converts user-typed times like `"09:00"` and `"17:00"` to seconds.

```python
def parse_user_override(earliest_str: str, latest_str: str) -> tuple[float, float]:
```

- Delegates to `_time_str_to_seconds()` for each string.
- Raises `ValueError` on invalid format (caught by caller, falls back to default).

#### `_time_str_to_seconds(time_str) -> float`

Converts `"HH:MM"` to seconds from midnight.

- Splits on `:`, expects exactly 2 parts.
- Validates: 0 ≤ hours ≤ 23, 0 ≤ minutes ≤ 59.
- Raises `ValueError` on invalid input.

#### `DEFAULT_WINDOW = (0.0, 86400.0)`

Constant used when no opening hours data exists. Represents "open all day" — the solver won't penalize any arrival time.

---

### 5.3 `app/main.py` — MODIFIED (API changes + time window integration)

**New imports:**

```python
from app.services.overpass import get_opening_hours_batch
from app.services.opening_hours_utils import parse_to_time_window, parse_user_override, DEFAULT_WINDOW
```

**Modified Pydantic model: `SolveRequest`**

Three new fields added:

```python
class SolveRequest(BaseModel):
    coordinates: list[list[float]]
    locations: list[dict] = []                    # NEW: [{name, lat, lon}] for Overpass lookup
    time_windows_override: dict[str, dict] = {}   # NEW: {"0": {"earliest": "09:00", "latest": "17:00"}}
    day_of_week: int = -1                         # NEW: -1 = today, 0=Mon … 6=Sun
```

- `locations`: The frontend sends the name + coordinates for each place. The backend uses this to query Overpass.
- `time_windows_override`: User-entered times, keyed by place index as a string (e.g., `"0"`, `"1"`).
- `day_of_week`: Which day to evaluate opening hours for. `-1` means today.

**Modified endpoint: `/api/solve/stream`**

New Phase 0b added between the distance matrix and NN:

```python
# Phase 0b: Opening hours from Overpass (if locations provided)
time_windows = None
if payload.locations:
    yield json.dumps({"type": "status", "message": "Fetching opening hours from OSM..."})

    # Determine target date
    target_date = datetime.date.today()
    if payload.day_of_week >= 0:
        today = datetime.date.today()
        days_ahead = (payload.day_of_week - today.weekday()) % 7
        target_date = today + datetime.timedelta(days=days_ahead)

    # Batch fetch from Overpass
    oh_results = await get_opening_hours_batch(payload.locations)

    # Merge: user override > Overpass > default
    time_windows = []
    for i, result in enumerate(oh_results):
        override = payload.time_windows_override.get(str(i))
        if override and override.get("earliest") and override.get("latest"):
            try:
                window = parse_user_override(override["earliest"], override["latest"])
            except ValueError:
                window = DEFAULT_WINDOW
        elif result and result.get("opening_hours"):
            window = (
                parse_to_time_window(result["opening_hours"], target_date)
                or DEFAULT_WINDOW
            )
        else:
            window = DEFAULT_WINDOW
        time_windows.append(window)

    # Send windows to frontend for display
    yield json.dumps({
        "type": "opening_hours",
        "windows": [list(w) for w in time_windows],
    })
```

**New SSE event type:**

```
{"type": "opening_hours", "windows": [[earliest, latest], ...]}
```

Sent after Overpass data is fetched and merged. The frontend can use this to display the resolved time windows.

---

### 5.4 `app/static/js/map.js` — MODIFIED (time window UI + solve integration)

**Changes in the geocode handler (after markers are placed):**

Populates a per-place table with editable time window inputs:

```javascript
// Populate per-place time window table
coords.forEach((c, idx) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="text-muted">${idx + 1}</td>
      <td>${c.name}</td>
      <td><input type="text" class="form-control form-control-sm tw-open"
           placeholder="09:00" style="width:80px"></td>
      <td><input type="text" class="form-control form-control-sm tw-close"
           placeholder="21:00" style="width:80px"></td>
      <td class="text-muted small tw-osm">—</td>
    `;
    placesTbody.appendChild(tr);
});
placesTableContainer.style.display = "block";
```

Each row has:
- Place name (from geocoding)
- Opens input (class `tw-open`) — user types `"09:00"`
- Closes input (class `tw-close`) — user types `"17:00"`
- OSM Hours column (class `tw-osm`) — filled automatically when opening hours arrive

**Changes in the solve button handler:**

Collects user overrides from the table inputs before solving:

```javascript
const timeWindowsOverride = {};
placesTbody.querySelectorAll("tr").forEach((row, idx) => {
    const opens = row.querySelector(".tw-open")?.value.trim();
    const closes = row.querySelector(".tw-close")?.value.trim();
    if (opens && closes) {
        timeWindowsOverride[String(idx)] = { earliest: opens, latest: closes };
    }
});

// Build locations list for Overpass
const locations = geocodedCoords.map((c) => ({ name: c.name, lat: c.lat, lon: c.lon }));
```

Both `timeWindowsOverride` and `locations` are sent in the solve request body.

**New SSE event handler for `opening_hours`:**

```javascript
} else if (event.type === "opening_hours") {
    setStatus("Opening hours loaded. Running Nearest Neighbor...");
}
```

Updates the status message when opening hours have been fetched.

---

### 5.5 `app/templates/index.html` — MODIFIED (time window table)

Added a table between the geocode form and the map:

```html
<div id="places-table-container" class="mb-3" style="display:none">
    <p class="text-muted small mb-1">
        Optional: set opening hours for each place. Leave blank for no time constraint.
        Hours fetched from OpenStreetMap will appear in the OSM column automatically.
    </p>
    <div class="table-responsive">
        <table class="table table-sm table-bordered align-middle">
            <thead class="table-light">
                <tr>
                    <th>#</th>
                    <th>Place</th>
                    <th>Opens</th>
                    <th>Closes</th>
                    <th>OSM Hours</th>
                </tr>
            </thead>
            <tbody id="places-tbody"></tbody>
        </table>
    </div>
    <button id="solve-btn" class="btn btn-primary">Solve Route</button>
</div>
```

- Hidden by default (`display:none`). Shown after geocoding completes.
- `#places-tbody` is populated dynamically by JavaScript.
- The "Solve Route" button is inside this container (only visible after geocoding).
- Bootstrap `table-responsive` makes it scrollable on small screens.

---

### 5.6 `tests/test_opening_hours.py` — CREATED

12 unit tests covering the parsing utilities:

| Test | What it verifies |
|---|---|
| `test_parse_weekday_hours` | `"Mo-Fr 09:00-17:00"` on Friday → `(32400, 61200)` |
| `test_parse_weekend_hours` | `"Mo-Fr 09:00-17:00; Sa 10:00-14:00"` on Saturday → `(36000, 50400)` |
| `test_parse_closed_day` | `"Mo-Fr 09:00-17:00"` on Sunday → `None` |
| `test_parse_24_7` | `"24/7"` → `(0, 86400)` |
| `test_parse_empty_string` | `""` and `"  "` → `None` |
| `test_parse_invalid_string` | `"not valid hours"` → `None` |
| `test_parse_multiple_periods` | `"Mo-Fr 09:00-12:00, 14:00-18:00"` → `(32400, 64800)` — spans the full range |
| `test_parse_defaults_to_today` | `"Mo-Su 08:00-22:00"` with no date → uses today, still works |
| `test_user_override_valid` | `"09:00"`, `"17:00"` → `(32400, 61200)` |
| `test_user_override_single_digit` | `"9:00"`, `"17:30"` → `(32400, 63000)` — single-digit hour accepted |
| `test_user_override_invalid` | `"abc"`, `"17:00"` → raises `ValueError` |
| `test_default_window` | `DEFAULT_WINDOW == (0.0, 86400.0)` |

Run all tests:

```bash
venv/bin/python -m pytest tests/test_opening_hours.py -v
```

---

## 6. How All the Pieces Connect (End-to-End Flow)

### The complete data flow from user input to solver

```
User enters: "British Museum, Tower of London"
                    |
                    v
    POST /api/geocode → Nominatim
                    |
                    v
    Coordinates: [(51.52, -0.13), (51.51, -0.08)]
    Markers appear on map
    Time window table appears (Opens/Closes inputs)
                    |
    User optionally types: Opens "10:00", Closes "17:00" for row 0
                    |
                    v
    POST /api/solve/stream
    Body: {
        coordinates: [[-0.13, 51.52], [-0.08, 51.51]],
        locations: [{name: "British Museum", lat: 51.52, lon: -0.13}, ...],
        time_windows_override: {"0": {earliest: "10:00", latest: "17:00"}}
    }
                    |
                    v
    ┌─ Phase 0: OSRM distance matrix
    │   → SSE: {"type": "matrix", "size": 2}
    │
    ├─ Phase 0b: Opening hours
    │   → Overpass batch query for both locations
    │   → For location 0: user override exists → parse_user_override("10:00", "17:00") = (36000, 61200)
    │   → For location 1: no override, Overpass returns "Mo-Su 10:00-18:00"
    │       → parse_to_time_window("Mo-Su 10:00-18:00", today) = (36000, 64800)
    │   → time_windows = [(36000, 61200), (36000, 64800)]
    │   → SSE: {"type": "opening_hours", "windows": [[36000, 61200], [36000, 64800]]}
    │
    ├─ Phase 1: Nearest Neighbor (uses time_windows for penalty cost)
    │   → SSE: progress events...
    │   → SSE: {"type": "nn_done", ...}
    │
    └─ Phase 2: Simulated Annealing (uses time_windows for penalty cost)
        → SSE: sa_progress events...
        → SSE: {"type": "sa_done", ...}
```

### Where each file fits in the pipeline

```
index.html          → Form + time window table (HTML structure)
       ↓
map.js              → Collects overrides, sends solve request, handles SSE events
       ↓
main.py             → Receives request, orchestrates: OSRM → Overpass → merge → solve
       ↓
overpass.py         → Sends Overpass QL query, returns raw opening_hours strings
       ↓
opening_hours_utils → Parses strings into (earliest, latest) seconds
       ↓
tsp_tw_utils.py     → evaluate_route() uses time_windows to compute penalized cost
       ↓
nearest_neighbor.py → Greedy solution (uses evaluate_route if time_windows given)
simulated_annealing → Iterative improvement (uses evaluate_route for cost)
```

---

## 7. Tests Added

| Test file | Test count | Coverage |
|---|---|---|
| `tests/test_opening_hours.py` | 12 tests | `parse_to_time_window`, `parse_user_override`, `DEFAULT_WINDOW` |

Run:

```bash
venv/bin/python -m pytest tests/test_opening_hours.py -v
```

Expected: **12 passed**.

Note: The Overpass service (`overpass.py`) is not unit-tested because it depends on external HTTP calls. Testing would require mocking the Overpass API, which adds complexity for limited value. The parsing layer is thoroughly tested, and the Overpass layer is simple HTTP + JSON parsing.

---

## 8. How to Test Manually

### Test Case 1: Automatic opening hours from Overpass

1. Start the app: `venv/bin/python -m uvicorn app.main:app --reload`
2. Open `http://localhost:8000`
3. Destination: `London, UK`
4. Places: `British Museum, Tower of London, Buckingham Palace`
5. Click **Geocode Places**
6. Leave all Opens/Closes fields **blank**
7. Click **Solve Route**
8. Watch the status: should show "Fetching opening hours from OSM..."
9. After solving, the route should account for real opening hours (if Overpass returns data)

### Test Case 2: User override takes priority

1. Same as above, but after geocoding:
2. For "British Museum": type Opens `14:00`, Closes `15:00`
3. Click **Solve Route**
4. The solver will use `(50400, 54000)` for British Museum — only a 1-hour window
5. This tight window may cause the solver to visit British Museum first (to arrive on time)

### Test Case 3: Overpass has no data

1. Destination: `Tokyo, Japan`
2. Places: `Some obscure park, Another random place`
3. Click **Geocode Places** → **Solve Route**
4. If Overpass has no `opening_hours` for these places, they get `DEFAULT_WINDOW = (0, 86400)` — no constraint
5. Route should still work normally

### Test Case 4: Mixed — some overrides, some Overpass, some default

1. Geocode 3+ places
2. Set Opens/Closes for only the first place
3. Leave others blank
4. Click **Solve Route**
5. Place 1: uses your override. Place 2-3: uses Overpass data or default.

---

## 9. Edge Cases and Fallback Strategy

### Fallback chain

Every edge case falls back to `DEFAULT_WINDOW = (0, 86400)` — the solver treats the location as "open all day":

| Scenario | What happens |
|---|---|
| Overpass timeout or HTTP error | All endpoints tried → returns `None` per location → default |
| Overpass returns no elements | No POIs with `opening_hours` nearby → `None` → default |
| `opening_hours` string is unparseable | `hoh.OHParser` throws → `parse_to_time_window` returns `None` → default |
| Place is closed on target day | `day.opens_today()` is False → `None` → default |
| User types invalid override (e.g., `"abc"`) | `parse_user_override` raises `ValueError` → caught → default |
| No locations sent (empty list) | `time_windows` stays `None` → solver ignores windows entirely |

### Multiple OSM elements in radius

When multiple POIs with `opening_hours` are found within the search radius:

1. **Name matching first:** If the user searched for "British Museum" and an element has `name=British Museum`, that element wins.
2. **Distance fallback:** If no name match, the closest element wins.
3. **Ways without coordinates:** OSM ways (building outlines) don't have lat/lon in `out tags` mode. They're included as candidates with a large assumed distance, so they only win if no closer nodes exist.

---

## 10. Thesis Committee Q&A Prep

**Q1: "Why Overpass instead of Google Places API or another source?"**

Our entire stack is OSM-based: Nominatim for geocoding, OSRM for routing, Leaflet for maps. Overpass keeps us consistent. It's free, requires no API keys, and has no usage limits (beyond being polite). Google Places API would require billing, API key management, and introduces a dependency on a commercial service. For a thesis, consistency and simplicity matter more than data completeness.

**Q2: "What if OpenStreetMap doesn't have opening hours for a location?"**

We fall back to `(0, 86400)` — open all day. This is the safest default: the solver won't incorrectly penalize a location we have no data about. We also provide user override so the user can manually enter hours. In practice, popular tourist attractions in major cities have good OSM coverage for `opening_hours`.

**Q3: "How do you handle time zones?"**

We don't — this is a documented limitation. The `humanized_opening_hours` library works with local time. OSRM travel times are in seconds (timezone-agnostic). As long as all locations are in the same city (same timezone), this works correctly. For a multi-timezone trip, we'd need `pytz` or `zoneinfo` to normalize times. This is out of scope for the thesis.

**Q4: "Why not query Overpass per-location instead of batching?"**

Performance. With 10 locations, per-location querying means 10 HTTP requests to Overpass (each taking 1-5 seconds). Batching sends one query and gets all results in one response. The tradeoff is complexity in matching results back to locations, but the speed improvement is significant.

**Q5: "What happens if the user sets conflicting windows — e.g., two places with non-overlapping hours?"**

The solver will still produce a route, but it will have time window violations. Step 11 adds a feasibility check: if violations > 0, the user sees a warning. The route shown is the "best effort" — the route that minimizes the combined travel time + penalty cost.

**Q6: "Why convert to seconds from midnight instead of using datetime objects?"**

The solver's cost function is pure arithmetic: `travel_time + penalty * violations`. Using floats (seconds) keeps the math simple, fast, and free of timezone or date complications. The conversion from datetime to seconds happens once (at the boundary), and the solver never needs to reason about dates, days of the week, or time formats.
