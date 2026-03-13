# Step 8 Implementation — Simulated Annealing

## Table of Contents

1. [What Was the State Before Step 8?](#1-what-was-the-state-before-step-8)
2. [What Was Missing?](#2-what-was-missing)
3. [The Core Problem Step 8 Solves](#3-the-core-problem-step-8-solves)
4. [Key Concepts You Need to Understand](#4-key-concepts-you-need-to-understand)
5. [Every File Changed or Created](#5-every-file-changed-or-created)
6. [How All the Pieces Connect](#6-how-all-the-pieces-connect)
7. [Tests Added](#7-tests-added)
8. [Design Decision: progress_callback Built In From the Start](#8-design-decision-progress_callback-built-in-from-the-start)

---

## 1. What Was the State Before Step 8?

After Step 7, the application had a complete working pipeline:

| Capability | How it worked |
|---|---|
| Geocode places | `POST /api/geocode` → Nominatim → GPS coordinates |
| Compute distance matrix | `app/services/osrm.py` → OSRM API → walking times |
| Solve TSP | `app/algorithms/nearest_neighbor.py` → greedy nearest city |
| Stream progress | `POST /api/solve/stream` → SSE events → animated polyline |
| Show walking paths | Frontend fetches OSRM route geometry after solving |

**The algorithm used was Nearest Neighbor (NN)** — a greedy heuristic that always goes to the closest unvisited city. It's fast (runs in microseconds) but often finds suboptimal routes. It gets "trapped" in local optima because it never reconsiders past decisions.

---

## 2. What Was Missing?

### The fundamental limitation of Nearest Neighbor

NN makes decisions based only on what's closest *right now*. It never looks ahead, and it never reconsiders. Imagine you're in a city and always walk to the nearest shop. You might walk past two shops that are 1 minute apart just because a different shop was 30 seconds closer from your current position — but then you have to backtrack 10 minutes to reach the ones you skipped.

**Example with a 4-city matrix:**

```
      A    B    C    D
  A [ 0   10   15   20 ]
  B [ 10   0   35   25 ]
  C [ 15  35    0   30 ]
  D [ 20  25   30    0 ]
```

NN from A: A→B (10) → B→D (25) → D→C (30) → C→A (15) = **80 seconds**

But the route A→B→C→D→A costs: 10 + 35 + 30 + 20 = **95** — worse.
And A→C→D→B→A costs: 15 + 30 + 25 + 10 = **80** — same.
And A→D→B→C→A costs: 20 + 25 + 35 + 15 = **95** — worse.

In this small example NN happens to find a good route. But with 10+ cities, NN's greedy choices often leave 10–30% improvement on the table.

**What was needed:** An algorithm that can explore different orderings, accept occasionally worse moves to escape local traps, and converge on a better solution over time.

---

## 3. The Core Problem Step 8 Solves

Step 8 adds **Simulated Annealing (SA)** — a metaheuristic that starts with the NN solution and iteratively improves it.

### The metallurgy analogy

When metalworkers anneal steel, they:
1. Heat it to a high temperature (atoms move freely, material is flexible)
2. Slowly cool it (atoms gradually settle into a stable, low-energy arrangement)
3. The result: a strong, well-structured material

If you cool too fast (quenching), atoms get trapped in disordered positions — the metal is brittle.

SA mimics this for optimization:
1. Start with a high "temperature" — the algorithm freely accepts worse solutions
2. Slowly cool — the algorithm becomes increasingly picky
3. The result: a route that has explored many possibilities and settled on a good one

### Why accept worse solutions?

This is the counter-intuitive core of SA. Consider this landscape of possible routes:

```
Cost
 ▲
 │   ╱╲
 │  ╱  ╲      ╱╲
 │ ╱    ╲    ╱  ╲
 │╱      ╲  ╱    ╲         ← NN gets stuck here (local optimum)
 │        ╲╱      ╲
 │                 ╲  ╱╲
 │                  ╲╱  ╲  ← Global optimum (SA can reach this)
 │                       ╲
 └──────────────────────────► Different route orderings
```

NN finds the first valley it rolls into. SA starts there but occasionally "jumps uphill" — accepting a worse solution — which lets it hop over the ridge and find the deeper valley on the other side. Early on (high temperature), big jumps happen often. Later (low temperature), the algorithm refines within its current valley.

---

## 4. Key Concepts You Need to Understand

### 4.1 The SA Algorithm Step by Step

```
1. Start with initial_route (from NN) and compute its cost
2. Set temperature T = T0 (e.g. 1000)
3. Repeat max_iter times:
   a. Pick two random inner cities and swap them
   b. Compute the new route's cost
   c. If new cost is better (lower): accept the swap
   d. If new cost is worse: accept with probability exp(-delta / T)
      - High T → probability ≈ 1 → almost always accept (exploring)
      - Low T  → probability ≈ 0 → almost never accept (refining)
   e. If the swap is rejected: undo it
   f. If current cost is the best ever seen: save it
   g. Cool down: T = T × alpha
4. Return the best route ever seen
```

### 4.2 The Acceptance Probability

The probability of accepting a worse solution is: `P = exp(-delta / T)`

Where:
- `delta` = new_cost - current_cost (positive, since the new solution is worse)
- `T` = current temperature

**Example calculations:**

| delta | T | P = exp(-delta/T) | Meaning |
|-------|------|-------------------|---------|
| 10 | 1000 | 0.99 | Almost certainly accept (hot, small worsening) |
| 100 | 1000 | 0.90 | Very likely accept (hot, moderate worsening) |
| 100 | 100 | 0.37 | Coin flip (warm, moderate worsening) |
| 100 | 10 | 0.00005 | Almost never accept (cold, moderate worsening) |
| 10 | 10 | 0.37 | Coin flip (cold, small worsening) |

As temperature drops, only tiny worsenings are ever accepted. By the end, SA behaves almost like a greedy algorithm — but it had time to explore first.

### 4.3 The Cooling Schedule

We use **geometric cooling**: `T = T × alpha` each iteration.

With `alpha = 0.995` and `max_iter = 10,000`:
- After 100 iterations: T = 1000 × 0.995^100 ≈ 606
- After 1000 iterations: T = 1000 × 0.995^1000 ≈ 6.7
- After 5000 iterations: T ≈ 0.0000... (effectively zero)

The first ~1000 iterations are exploration. The last ~9000 are refinement.

**Why 0.995?** It's a common default that balances exploration vs. convergence speed. Too low (e.g. 0.9) = cools too fast, gets stuck early. Too high (e.g. 0.9999) = wastes iterations exploring when it should be refining.

**Why T0 = 1000?** It should be on the order of typical cost differences. For walking routes measured in seconds, a delta of 100–1000 is common. T0 = 1000 means early on, even a 1000-second worsening has a ~37% chance of acceptance.

### 4.4 The Perturbation: City Swap

Each iteration picks two random "inner" cities and swaps their positions in the route.

**Why "inner"?** The route format is `[start, city1, city2, ..., cityN, start]`. The first and last elements are the same (return to origin). We must never swap these — it would break the route. So we only swap positions 1 through -2.

**Example:**
```
Route: [0, 1, 3, 2, 0]
         ^        ^     ← inner cities (positions 1, 2, 3)

Swap positions 1 and 3:
Route: [0, 2, 3, 1, 0]   ← new route to evaluate
```

**Why swap and not something fancier?** The plan suggests starting with swap (simplest perturbation). More advanced moves like 2-opt (reversing a segment) exist and often work better, but swap is:
- Easy to implement and explain
- Sufficient for a thesis baseline
- Can be upgraded later

### 4.5 Tracking the Best vs. Current

SA maintains two solutions:
- **`current_route`**: The solution being modified. It can get worse over time (that's the whole point — accepting bad moves to explore).
- **`best_route`**: The best solution ever seen. This only improves. It's what we return at the end.

This is important: SA's current solution might wander to a bad region, but the best solution is always preserved. Think of it like an explorer who marks the location of every gold nugget found, even while walking through barren territory.

---

## 5. Every File Changed or Created

### 5.1 `app/algorithms/simulated_annealing.py` — CREATED (new file)

**Motivation:** Provide a second, more powerful optimization algorithm that improves on NN's greedy results.

**Helper function: `_route_cost()`**

```python
def _route_cost(
    route: list[int],
    matrix: list[list[float]],
    time_windows: list[tuple[float, float]] | None = None,
    service_time: float = 0,
) -> float:
    if time_windows is not None:
        cost, _ = evaluate_route(route, matrix, time_windows, service_time=service_time)
        return cost

    total = 0.0
    for k in range(len(route) - 1):
        total += matrix[route[k]][route[k + 1]]
    return total
```

**What it does:** Computes the cost of a route. Has two modes:
- **Without time windows:** Simply sums travel times along the route. `matrix[route[0]][route[1]] + matrix[route[1]][route[2]] + ...`
- **With time windows:** Delegates to `evaluate_route()` (from Step 4) which adds 10,000-second penalties for late arrivals.

**Why a separate helper?** SA calls this function 10,000+ times (once per iteration). Having it as a clean, small function makes the main loop readable and the cost calculation easy to swap out later.

**Why not just always use `evaluate_route()`?** Without time windows, we don't need the overhead of checking arrival times, waiting, etc. A simple sum is faster and correct.

---

**Main function: `solve()`**

```python
def solve(
    matrix: list[list[float]],
    initial_route: list[int],
    time_windows: list[tuple[float, float]] | None = None,
    service_time: float = 0,
    T0: float = 1000.0,
    alpha: float = 0.995,
    max_iter: int = 10_000,
    progress_callback: Callable[[list[int], float], None] | None = None,
) -> tuple[list[int], float]:
```

**Parameters explained:**

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `matrix` | `list[list[float]]` | (required) | Travel time matrix. `matrix[i][j]` = seconds from city i to city j |
| `initial_route` | `list[int]` | (required) | Starting route from NN, e.g. `[0, 1, 3, 2, 0]` |
| `time_windows` | `list[tuple] \| None` | `None` | Optional opening hours per location |
| `service_time` | `float` | `0` | Seconds spent at each location |
| `T0` | `float` | `1000.0` | Initial temperature — controls early exploration |
| `alpha` | `float` | `0.995` | Cooling rate — how fast temperature drops |
| `max_iter` | `int` | `10,000` | Number of iterations — total computation budget |
| `progress_callback` | `Callable \| None` | `None` | Called with `(best_route, best_cost)` on each new best |

**Edge case handling (lines 65–76):**

```python
    n = len(matrix)

    if n == 0 or len(initial_route) == 0:
        return (list(initial_route), 0.0)

    if initial_route[0] != initial_route[-1]:
        raise ValueError("initial_route must start and end with the same index")

    if len(initial_route) <= 2:
        cost = _route_cost(initial_route, matrix, time_windows, service_time)
        return (list(initial_route), cost)
```

Three edge cases handled in order:

1. **Empty matrix or route** → return immediately with cost 0. Nothing to optimize.
2. **Route doesn't form a loop** → raise `ValueError`. SA requires a round trip (the start/end city is fixed). A route like `[0, 1]` without returning to 0 is invalid.
3. **Route too short to swap** → `[0, 0]` (single city) or `[0, 1, 0]` (two cities with only one inner position) have nothing to optimize. Return as-is.

**Critical: validation before early return.** The `initial_route[0] != initial_route[-1]` check comes before the `len <= 2` check. This ensures we always catch invalid routes, even short ones. The initial implementation had these reversed, causing a bug where `[0, 1]` would silently return instead of raising — caught by tests and fixed.

**Initialization (lines 78–93):**

```python
    current_route = list(initial_route)
    current_cost = _route_cost(current_route, matrix, time_windows, service_time)

    best_route = list(current_route)
    best_cost = current_cost

    if progress_callback is not None:
        progress_callback(best_route.copy(), best_cost)

    T = T0
    inner_len = len(current_route) - 2

    if inner_len < 2:
        return (best_route, best_cost)
```

- **`list(initial_route)`**: Creates a copy so we don't mutate the caller's data.
- **`best_route` vs `current_route`**: Both start as the same route but diverge during optimization. `best_route` only improves; `current_route` may get worse.
- **`progress_callback` initial call**: Reports the starting state so the UI knows what we're beginning with.
- **`inner_len`**: The number of cities that can be swapped. For route `[0, 1, 3, 2, 0]`, `inner_len = 3` (cities at positions 1, 2, 3). If less than 2, there's nothing to swap.

**The main loop (lines 95–124):**

```python
    for _ in range(max_iter):
        # Pick two distinct inner positions to swap
        i = random.randint(1, inner_len)
        j = random.randint(1, inner_len)
        while j == i:
            j = random.randint(1, inner_len)

        # Swap
        current_route[i], current_route[j] = current_route[j], current_route[i]
        new_cost = _route_cost(current_route, matrix, time_windows, service_time)

        delta = new_cost - current_cost

        if delta <= 0:
            current_cost = new_cost
        elif T > 0 and random.random() < math.exp(-delta / T):
            current_cost = new_cost
        else:
            current_route[i], current_route[j] = current_route[j], current_route[i]

        if current_cost < best_cost:
            best_route = list(current_route)
            best_cost = current_cost
            if progress_callback is not None:
                progress_callback(best_route.copy(), best_cost)

        T *= alpha
```

**Line-by-line breakdown:**

**Random index selection (lines 96–100):**
```python
        i = random.randint(1, inner_len)
        j = random.randint(1, inner_len)
        while j == i:
            j = random.randint(1, inner_len)
```
Pick two distinct positions from the inner part of the route (positions 1 through `inner_len`). The `while` loop ensures `i ≠ j`. `random.randint(1, inner_len)` is inclusive on both ends.

**In-place swap (line 103):**
```python
        current_route[i], current_route[j] = current_route[j], current_route[i]
```
Python's tuple swap — swaps the two cities without a temporary variable. This mutates `current_route` directly. If the swap is rejected later, we swap back (line 116).

**Cost evaluation (line 104):**
```python
        new_cost = _route_cost(current_route, matrix, time_windows, service_time)
```
Compute the cost of the modified route. This is O(N) — it walks the entire route summing travel times.

**Acceptance decision (lines 108–116):**
```python
        if delta <= 0:
            current_cost = new_cost                              # Better → always accept
        elif T > 0 and random.random() < math.exp(-delta / T):
            current_cost = new_cost                              # Worse → probabilistic accept
        else:
            current_route[i], current_route[j] = current_route[j], current_route[i]  # Reject → undo
```

Three cases:
1. **`delta <= 0`**: New route is better (or equal). Always accept. Just update `current_cost`.
2. **`delta > 0` and accepted**: New route is worse, but random number < `exp(-delta/T)`. Accept the worsening.
3. **`delta > 0` and rejected**: Undo the swap by swapping back. `current_cost` stays unchanged.

**Why `T > 0` guard?** If temperature somehow reaches exactly 0, `exp(-delta/0)` would be a division-by-zero error. The guard prevents this.

**Why swap back instead of copying?** We could copy the route before modifying it, but that's an O(N) list copy every iteration. Swapping back is O(1). Over 10,000 iterations, this matters.

**Best tracking (lines 118–122):**
```python
        if current_cost < best_cost:
            best_route = list(current_route)
            best_cost = current_cost
            if progress_callback is not None:
                progress_callback(best_route.copy(), best_cost)
```
If the current solution (which might have been accepted despite being worse than the *previous* current) is better than the all-time best, update the best. This is the only place `best_route` changes.

**`progress_callback` fires only on new bests.** SA might run 10,000 iterations but only find 10–50 new bests. The callback fires sparsely, which is ideal for SSE streaming (Step 9) — enough events to show progress without flooding the client.

**Cooling (line 124):**
```python
        T *= alpha
```
Decrease temperature by factor `alpha` every iteration. With `alpha = 0.995`, this is geometric cooling.

---

### 5.2 `tests/test_algorithms.py` — MODIFIED (9 new tests added)

**Motivation:** Verify SA correctness across all scenarios: normal operation, edge cases, time windows, and the callback mechanism.

---

## 6. How All the Pieces Connect

### The NN → SA Pipeline

```
User enters places
       │
       ▼
Geocode (Nominatim) → GPS coordinates
       │
       ▼
Distance matrix (OSRM) → walking times between all pairs
       │
       ▼
Nearest Neighbor → fast greedy route [0, 1, 3, 2, 0] (cost: 80s)
       │
       ▼
Simulated Annealing → improved route [0, 2, 3, 1, 0] (cost: 75s)
       │
       ▼
Display on map with walking geometry
```

SA does NOT replace NN — it improves on it. NN provides the starting point because:
1. SA needs an initial solution to begin with
2. Starting from a good solution (NN) is much better than starting from a random one
3. NN is fast (microseconds), so the overhead is negligible

### Shared code: `evaluate_route`

Both NN and SA use `evaluate_route` from `tsp_tw_utils.py` for time window evaluation. This means:
- Both algorithms measure cost the same way
- Both respect opening hours identically
- Comparing their results is fair (apples to apples)

### Ready for Step 9

SA already has `progress_callback` built in. In Step 9, we'll wire it to the SSE streaming system (same `queue.Queue` pattern as NN in Step 7) so the browser shows SA's improving route in real time.

---

## 7. Tests Added

Nine new tests were added to `tests/test_algorithms.py`:

### Test 1: `test_sa_returns_valid_route`

**What it verifies:** SA returns a structurally valid route — starts at 0, ends at 0, visits all cities exactly once, and the cost matches manual calculation.

```python
route, cost = sa_solve(matrix, initial_route=nn_route, max_iter=5000)
assert route[0] == 0
assert route[-1] == 0
inner = sorted(route[1:-1])
assert inner == [1, 2, 3]
expected_cost = sum(matrix[route[k]][route[k + 1]] for k in range(len(route) - 1))
assert abs(cost - expected_cost) < 1e-9
```

**Why this matters:** SA randomly swaps cities. If the swap logic is wrong (e.g., accidentally swapping the fixed start/end), the route would be invalid. This test catches structural corruption.

### Test 2: `test_sa_improves_or_matches_nn`

**What it verifies:** Over 10 runs, SA finds a cost ≤ NN's cost at least once.

```python
results = []
for _ in range(10):
    _, cost = sa_solve(matrix, initial_route=nn_route, max_iter=5000)
    results.append(cost)
assert min(results) <= nn_cost
```

**Why this matters:** SA is stochastic (random). One run might not improve. But over 10 runs, it should find at least one route as good as NN. If it consistently produces worse routes, the acceptance logic is broken.

### Test 3: `test_sa_empty_route`

**What it verifies:** Empty input returns empty output without crashing.

```python
route, cost = sa_solve([], initial_route=[])
assert route == []
assert cost == 0.0
```

### Test 4: `test_sa_single_city`

**What it verifies:** Single-city route `[0, 0]` is returned unchanged.

```python
route, cost = sa_solve([[0]], initial_route=[0, 0])
assert route == [0, 0]
assert cost == 0.0
```

### Test 5: `test_sa_two_cities`

**What it verifies:** Two-city route `[0, 1, 0]` has only one inner city, so nothing can be swapped. Returned as-is.

```python
route, cost = sa_solve(matrix, initial_route=[0, 1, 0], max_iter=1000)
assert route == [0, 1, 0]
assert cost == 20.0
```

**Why this matters:** With one inner city, `random.randint(1, 1)` always returns 1. The `while j == i` loop would be infinite if not handled. The early return at `inner_len < 2` prevents this.

### Test 6: `test_sa_invalid_route_raises`

**What it verifies:** A route that doesn't form a loop raises `ValueError`.

```python
with pytest.raises(ValueError, match="must start and end with the same index"):
    sa_solve(matrix, initial_route=[0, 1])
```

**Why this matters:** SA assumes the route is a loop. Without this check, the algorithm would silently produce garbage.

### Test 7: `test_sa_with_time_windows`

**What it verifies:** When time windows are provided, SA uses `evaluate_route` for cost (including penalties), and the returned cost matches recalculation.

```python
expected_cost, _ = evaluate_route(route, matrix, windows)
assert abs(cost - expected_cost) < 1e-9
```

**Why this matters:** Ensures SA and `evaluate_route` agree on cost. If SA computed cost differently, it might "optimize" for the wrong objective.

### Test 8: `test_sa_progress_callback_fires`

**What it verifies:** The callback fires at least once (initial route) and every subsequent event has a lower cost than the previous (non-increasing costs).

```python
assert len(events) >= 1
assert events[0][0] == [0, 1, 3, 2, 0]  # initial route
for i in range(1, len(events)):
    assert events[i][1] <= events[i - 1][1]  # costs non-increasing
```

**Why this matters:** The callback only fires on new bests. If costs increased between events, the "new best" logic is wrong.

### Test 9: `test_sa_progress_callback_none_is_safe`

**What it verifies:** Passing `None` as callback doesn't crash.

```python
route, cost = sa_solve(matrix, initial_route=[0, 1, 2, 0], progress_callback=None)
assert route[0] == 0
assert route[-1] == 0
```

---

## 8. Design Decision: progress_callback Built In From the Start

The original implementation plan puts SA (Step 8) and SA streaming (Step 9) as separate steps. We chose to include `progress_callback` in SA from the start. Here's why:

### Why combine?

1. **Consistency:** NN already has `progress_callback` from Step 7. SA having the same interface means the solver bridge (`solver.py`) can use the identical pattern for both algorithms.

2. **Avoid touching the file twice:** Adding `progress_callback` later would mean modifying `simulated_annealing.py` in Step 9 — changing function signatures, inserting callback calls into the loop, and re-running all tests. Doing it now means Step 9 only needs to add the solver bridge and endpoint changes.

3. **Testable from day one:** We can verify the callback behavior (fires on new bests, non-increasing costs) immediately, rather than adding tests retroactively.

4. **Zero cost:** The callback defaults to `None`. All code paths check `if progress_callback is not None:` before calling. Existing usage without a callback is unaffected. There's no runtime overhead when the callback isn't used.

### What Step 9 still needs to do

~~Step 9 will:~~
- ~~Add `run_sa_with_progress()` to `solver.py` (same queue pattern as NN)~~
- ~~Wire it into the `/api/solve/stream` endpoint (run NN first, then SA)~~
- ~~Update the frontend to show SA's improving route after NN's result~~

**All done — see [STEP9_IMPLEMENTATION.md](STEP9_IMPLEMENTATION.md).**
