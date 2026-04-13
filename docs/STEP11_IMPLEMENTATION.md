# Step 11 Implementation — Infeasibility: Strict + Best-Effort Fallback

## Table of Contents

1. [What Was the State Before Step 11?](#1-what-was-the-state-before-step-11)
2. [What Was Missing?](#2-what-was-missing)
3. [The Core Problem Step 11 Solves](#3-the-core-problem-step-11-solves)
4. [Key Concepts You Need to Understand](#4-key-concepts-you-need-to-understand)
5. [Every File Changed or Created](#5-every-file-changed-or-created)
6. [The Complete SSE Event Flow (With Feasibility)](#6-the-complete-sse-event-flow-with-feasibility)
7. [Tests Added](#7-tests-added)
8. [How to Test Manually](#8-how-to-test-manually)
9. [Design Decisions and Alternatives](#9-design-decisions-and-alternatives)
10. [Thesis Committee Q&A Prep](#10-thesis-committee-qa-prep)

---

## 1. What Was the State Before Step 11?

After completing Steps 1–10, the application could do the following:

| Capability | How it worked |
|---|---|
| Geocode places | `POST /api/geocode` → Nominatim → GPS coordinates |
| Distance matrix | `app/services/osrm.py` → OSRM `/table/v1/foot/` → walking times |
| Nearest Neighbor | `app/algorithms/nearest_neighbor.py` → greedy route with progress_callback |
| Simulated Annealing | `app/algorithms/simulated_annealing.py` → improves NN route with progress_callback |
| SSE streaming | `solver.py` + `/api/solve/stream` → animated NN + SA progress on the map |
| Walking geometry | Frontend fetches OSRM route geometry for final route |
| Opening hours | `overpass.py` → Overpass API → OSM opening hours per location |
| Time window parsing | `opening_hours_utils.py` → converts "Mo-Fr 09:00-17:00" to (seconds, seconds) |
| User override | Frontend sends custom Opens/Closes times; backend merges with OSM data |
| Penalty-based cost | `tsp_tw_utils.py` → `evaluate_route()` returns `(cost, violations)` where cost = travel_time + 10,000 × violations |

**The critical gap:** The solver already computed a penalized cost that accounted for time window violations. The `evaluate_route` function already returned a violation count. But **nobody checked the violation count after solving**. The user received a route and a travel time, with no indication of whether the route actually respected all the opening hours they configured.

In other words: the system *knew* when it failed to satisfy constraints, but it kept that information to itself.

---

## 2. What Was Missing?

### Missing piece 1: No feasibility reporting

After the solver finished (NN → SA), the backend sent an `sa_done` event with the final route and cost. That cost *included* penalties for violations (10,000 seconds per violation), but the raw cost number is meaningless to a user. If the cost is `11,240`, is that 11,240 seconds of walking, or 1,240 seconds of walking plus one violation? The user cannot tell.

### Missing piece 2: No user-visible warning

Even if the backend knew about violations, the frontend had no mechanism to display a warning. There was no UI element for "something went wrong but here's the best we could do."

### What the user experienced

**Before Step 11:**
- User sets tight time windows → Clicks Solve → Gets a route → Sees "Route optimized! Total walking time: 187.3 min"
- The 187.3 min includes a hidden 166.7-minute penalty (10,000 seconds). The actual walking is ~20 min. The user has no idea one of their time windows was violated.

**After Step 11:**
- User sets tight time windows → Clicks Solve → Gets a route → Sees a **yellow warning banner**: "Could not find a route satisfying all time windows. 1 violation(s). Showing best effort."
- The route is still shown (it's the best the solver could find), but the user knows it's imperfect.

---

## 3. The Core Problem Step 11 Solves

**How do you tell the user when the optimizer couldn't find a perfect solution?**

This is a fundamental UX problem in optimization applications. There are three strategies:

| Strategy | Description | Downside |
|---|---|---|
| **Fail hard** | Return an error if any constraint is violated | User gets nothing — useless |
| **Silently succeed** | Return the best route, ignore violations | User doesn't know constraints were broken |
| **Best-effort with warning** | Return the best route AND tell the user about violations | User gets a useful result AND honest feedback |

Step 11 implements **best-effort with warning**: always return a route, but explicitly flag when it's imperfect.

---

## 4. Key Concepts You Need to Understand

### 4.1 Strict vs. Best-Effort TSPTW

**Strict TSPTW:** A route is valid only if every location is visited within its time window. If no such route exists, the problem is declared infeasible — no solution returned.

**Best-effort TSPTW:** Use a penalty cost (travel time + 10,000 × violations). The solver minimizes this combined cost, naturally preferring routes with fewer violations. If violations remain in the final solution, we know the problem is (likely) infeasible under strict rules, but we still have a useful route.

### 4.2 Why Not Two Separate Solver Runs?

The implementation plan considered running the solver twice: once in strict mode (reject all violations), then again in best-effort mode if strict failed. This was rejected because:

1. **It's redundant.** The penalty-based solver already produces the best-effort result. If that result has 0 violations, it's also the strict result. One run gives us both answers.
2. **Strict SA is unreliable.** SA with hard constraints (reject any move that creates a violation) may fail to explore the search space effectively. The penalty approach is more robust.
3. **Simpler code.** One run, one check. No conditional logic for "which mode are we in?"

### 4.3 The Feasibility Check

After the solver finishes, we call `evaluate_route(final_route, matrix, time_windows)` one more time on the final route. This returns `(total_cost, violation_count)`. If `violation_count == 0`, the route is feasible. If `violation_count > 0`, we show the warning.

This is a **post-hoc check**, not a constraint during solving. The solver has already done its best to minimize violations through the penalty cost. We're just reporting the outcome.

### 4.4 When the Feasibility Check is Skipped

The check only runs when `time_windows` is not `None`. If the user didn't provide any locations for opening hours lookup (no time windows at all), there's nothing to check — every route trivially satisfies "no constraints."

---

## 5. Every File Changed or Created

### 5.1 `app/main.py` — MODIFIED (feasibility check + new SSE event)

**What changed:**

1. **New import** at the top of the file:

```python
from app.algorithms.tsp_tw_utils import evaluate_route
```

2. **Track the final route** after SA completes. Before Step 11, the SA events were yielded directly without the endpoint remembering the final route. Now we capture it:

```python
final_route = nn_route
if nn_route and len(nn_route) > 3:
    async for event in run_sa_with_progress(...):
        if event["type"] == "sa_done":
            final_route = event["route"]
        yield json.dumps(event)
else:
    yield json.dumps({"type": "sa_done", "route": nn_route, "cost": nn_cost})
```

3. **Phase 3: Feasibility check** — new code after Phase 2:

```python
# Phase 3: Feasibility check
if final_route and time_windows:
    _, violations = evaluate_route(
        final_route, matrix, time_windows
    )
    feasible = violations == 0
    warning = (
        None if feasible
        else f"Could not find a route satisfying all time windows. "
             f"{violations} violation(s). Showing best effort."
    )
    yield json.dumps({
        "type": "feasibility",
        "feasible": feasible,
        "violations": violations,
        "warning": warning,
    })
```

**How it works:**

- `evaluate_route` simulates walking the final route, checking each arrival time against its time window.
- If `violations == 0`: emit `{"feasible": true, "violations": 0, "warning": null}`.
- If `violations > 0`: emit `{"feasible": false, "violations": N, "warning": "..."}`.
- The event is emitted **after** `sa_done`, so the frontend already has the route drawn before the warning appears.

**New SSE event type documented in the endpoint docstring:**

```
{"type": "feasibility", "feasible": bool, "violations": int, "warning": str|null}
```

---

### 5.2 `app/static/js/map.js` — MODIFIED (warning display)

**What was added:**

1. **`showWarning(message)` function** (lines 8–15):

```javascript
function showWarning(message) {
  const container = document.getElementById("warning-container");
  if (!container) return;
  container.innerHTML = `<div class="alert alert-warning alert-dismissible fade show" role="alert">
    <strong>Warning:</strong> ${message}
    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
  </div>`;
}
```

Creates a Bootstrap 5 dismissible alert (yellow/orange). The user can close it by clicking the × button. Uses `innerHTML` because the alert contains structured HTML (strong tag, close button).

2. **`clearWarning()` function** (lines 18–21):

```javascript
function clearWarning() {
  const container = document.getElementById("warning-container");
  if (container) container.innerHTML = "";
}
```

Removes any existing warning. Called at the start of `solveRoute()` so old warnings don't persist from a previous solve.

3. **`feasibility` event handler** in the SSE stream callback:

```javascript
} else if (event.type === "feasibility") {
    if (!event.feasible && event.warning) {
        showWarning(event.warning);
    }
}
```

Only shows the warning when `feasible` is `false`. If the route is feasible, nothing happens (no success banner needed — the status line already says "Route optimized!").

4. **`clearWarning()` call** at the start of `solveRoute()`:

```javascript
async function solveRoute(coords, locations, timeWindowsOverride) {
    const coordinates = coords.map((c) => [c.lon, c.lat]);
    clearWarning();  // ← NEW: remove warning from previous solve
    setStatus("Fetching walking times from OSRM...");
```

---

### 5.3 `app/templates/index.html` — MODIFIED (warning container)

**What was added:**

```html
<div id="warning-container" class="mb-2"></div>
```

Placed between the places table and the status div. This is an empty container that gets populated by `showWarning()` when a feasibility violation is detected.

**Layout order:**

```
[Geocode form]
[Places table with time windows]
[warning-container]  ← NEW: appears here when violations exist
[status message]
[map]
```

The warning sits above the status line and below the solve button, making it immediately visible after solving.

---

### 5.4 `tests/test_algorithms.py` — MODIFIED (2 new tests)

**Test 1: `test_evaluate_route_feasible_with_loose_windows`**

```python
def test_evaluate_route_feasible_with_loose_windows():
    """Loose time windows produce 0 violations (feasible)."""
    matrix = [[0, 10, 15], [10, 0, 35], [15, 35, 0]]
    windows = [(0, 86400), (0, 86400), (0, 86400)]
    cost, violations = evaluate_route([0, 1, 2, 0], matrix, windows)
    assert violations == 0
```

Verifies that when all time windows span the full day (0 to 86400 seconds = midnight to midnight), no violations occur regardless of route order. This is the "no constraints" baseline.

**Test 2: `test_evaluate_route_infeasible_with_tight_windows`**

```python
def test_evaluate_route_infeasible_with_tight_windows():
    """Conflicting time windows produce violations > 0 (infeasible)."""
    matrix = [[0, 100, 200], [100, 0, 100], [200, 100, 0]]
    windows = [(0, 86400), (0, 50), (0, 50)]
    cost, violations = evaluate_route([0, 1, 2, 0], matrix, windows)
    assert violations == 2
    assert cost == (100 + 100 + 200) + 10_000 * 2
```

Verifies that when time windows are impossible to satisfy (must arrive at locations 1 and 2 by second 50, but travel time from start is 100s and 200s), the violations are counted correctly and the penalty cost is computed as expected:
- Travel time: 100 + 100 + 200 = 400 seconds
- Penalty: 10,000 × 2 = 20,000
- Total: 20,400

---

## 6. The Complete SSE Event Flow (With Feasibility)

### Event sequence for a 4-place route with time windows

```
Client                                 Server
  |                                      |
  |  POST /api/solve/stream              |
  |  {"coordinates": [...],              |
  |   "locations": [...],                |
  |   "time_windows_override": {...}}    |
  |------------------------------------->|
  |                                      | Fetch OSRM matrix
  |  data: {"type":"matrix",             |
  |         "size": 4}                   |
  |<-------------------------------------|
  |                                      | Fetch Overpass opening hours
  |  data: {"type":"status",             |
  |         "message":"Fetching..."}     |
  |<-------------------------------------|
  |  data: {"type":"opening_hours",      |
  |         "windows":[[...], ...]}      |
  |<-------------------------------------|
  |                                      | Start NN in thread
  |  data: {"type":"progress", ...}      |  (multiple events)
  |<-------------------------------------|
  |  data: {"type":"nn_done", ...}       |
  |<-------------------------------------|
  |                                      | Start SA in thread
  |  data: {"type":"sa_progress", ...}   |  (multiple events)
  |<-------------------------------------|
  |  data: {"type":"sa_done", ...}       |
  |<-------------------------------------|
  |                                      | evaluate_route() ← NEW
  |  data: {"type":"feasibility",        |
  |         "feasible": false,           |  ← NEW EVENT
  |         "violations": 1,             |
  |         "warning": "Could not..."}   |
  |<-------------------------------------|
  |                                      |
  | [stream ends]                        |
  |                                      |
  | GET /api/route-geometry (per leg)    |  ← Walking path fetching
  |------------------------------------->|
```

**Key addition:** The `feasibility` event is the **last event** in the stream (before the stream closes). This ensures:
1. The route is fully computed and drawn before the warning appears.
2. The frontend has the final route to display walking geometry regardless of feasibility.

---

## 7. Tests Added

| Test | File | What it verifies |
|---|---|---|
| `test_evaluate_route_feasible_with_loose_windows` | `tests/test_algorithms.py` | Full-day windows → 0 violations |
| `test_evaluate_route_infeasible_with_tight_windows` | `tests/test_algorithms.py` | Impossible windows → correct violation count and penalty cost |

Run all tests:

```bash
venv/bin/python -m pytest tests/ -v
```

Expected: **29 passed** (27 existing + 2 new).

---

## 8. How to Test Manually

### Test Case 1: Infeasible route (warning should appear)

1. Start the app: `venv/bin/python -m uvicorn app.main:app --reload`
2. Open `http://localhost:8000`
3. Enter destination: `Tokyo, Japan`
4. Enter places: `Tokyo Tower, Senso-ji, Shinjuku Gyoen`
5. Click **Geocode Places**
6. In the time window table, set deliberately impossible windows:
   - Tokyo Tower: Opens `06:00`, Closes `06:01`
   - Senso-ji: Opens `06:00`, Closes `06:01`
   - Shinjuku Gyoen: Opens `06:00`, Closes `06:01`
7. Click **Solve Route**
8. **Expected:** A yellow warning banner appears:
   > **Warning:** Could not find a route satisfying all time windows. X violation(s). Showing best effort.
9. The route is still drawn on the map (best-effort result).

### Test Case 2: Feasible route (no warning)

1. Same steps 1–5 as above.
2. Leave all time windows **blank** (or set wide windows like 00:00–23:59).
3. Click **Solve Route**
4. **Expected:** No warning appears. Status shows "Route optimized! Total walking time: X min".

### Test Case 3: Warning clears on re-solve

1. Run Test Case 1 (warning appears).
2. Without refreshing, change the time windows to be wide (or clear them).
3. Click **Solve Route** again.
4. **Expected:** The old warning disappears when the new solve starts. If the new route is feasible, no warning appears.

---

## 9. Design Decisions and Alternatives

### Decision 1: One solver run, post-hoc check

**Chosen:** Run the solver once with the penalty cost. After it finishes, check the violation count.

**Alternative considered:** Run twice — first with a very high penalty (1e9) for "strict" mode, then with normal penalty (10,000) for best-effort if strict fails.

**Why we chose the simpler approach:**
- The penalty-based solver already minimizes violations. If the result has 0 violations, we've found the strict solution. If not, we already have the best-effort result. A second run would almost certainly produce the same route.
- Simpler code, easier to explain in a thesis defense.
- One run means half the computation time.

### Decision 2: Separate `feasibility` event (not embedded in `sa_done`)

**Chosen:** Emit a separate `{"type": "feasibility"}` event after `sa_done`.

**Alternative:** Add `feasible`, `violations`, and `warning` fields to the `sa_done` event.

**Why a separate event:**
- **Separation of concerns.** The `sa_done` event reports the algorithm's result. The `feasibility` event reports a higher-level business decision.
- **Backward compatibility.** Frontend code that doesn't know about feasibility still works — it simply ignores an unknown event type.
- **Timing.** The feasibility check runs after the route is drawn, so the warning appears as a separate visual step.

### Decision 3: Skip feasibility check when no time windows

**Chosen:** Only emit the `feasibility` event when `time_windows` is not `None`.

**Why:** If there are no time windows, every route is trivially feasible. Emitting `{"feasible": true}` every time would be noise.

---

## 10. Thesis Committee Q&A Prep

**Q1: "Why not use hard constraints (reject all infeasible moves) instead of penalties?"**

Hard constraints can make the search space empty. With 10 locations and tight windows, there might be no route where every arrival is on time. If SA rejects all infeasible moves, it might get stuck at the initial (also infeasible) solution and never improve. The penalty approach guides the solver *toward* feasibility while still allowing it to explore infeasible intermediate states. This is a well-known technique in constraint optimization literature, sometimes called a "soft constraint" or "Lagrangian relaxation" approach.

**Q2: "How do you define 'best effort'?"**

Best effort minimizes the penalized cost: `travel_time + 10,000 × violations`. The solver (SA) explores many route permutations and keeps the one with the lowest penalized cost. This naturally prioritizes: (1) fewer violations, then (2) less travel time. A route with 0 violations and 30 min walking beats a route with 1 violation and 5 min walking (cost: 1,800 vs 10,300).

**Q3: "What's the 10,000 penalty? Why that number?"**

10,000 seconds ≈ 2.8 hours. This is large enough to dominate any realistic walking time (most city walks are under 2 hours) but small enough that the solver can still distinguish between routes with different travel times when violations are equal. It's a tuning parameter — too low and the solver ignores constraints; too high and it can't differentiate routes. 10,000 works well empirically for walking routes in a city.

**Q4: "Could you use a different algorithm for strict feasibility checking?"**

Yes. Constraint Programming (CP) solvers like Google OR-Tools can enforce hard time window constraints and prove infeasibility. Mixed-Integer Programming (MIP) can also model TSPTW exactly. For this thesis, the penalty approach is simpler, produces useful results in all cases, and doesn't require additional solver dependencies. We document the limitation: our "infeasible" diagnosis is based on SA's best attempt, not a mathematical proof of infeasibility.

**Q5: "If the solver says 'infeasible,' can you be sure no feasible route exists?"**

No. SA is a metaheuristic — it finds good solutions but doesn't guarantee optimality. It's possible (though unlikely for small instances) that a feasible route exists but SA didn't find it. For a definitive answer, you'd need an exact solver (e.g., branch-and-bound). Our warning says "Could not find a route satisfying all time windows" rather than "No feasible route exists" — this is intentionally honest about the solver's limitations.
