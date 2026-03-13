"""Unit tests for TSP algorithms."""

import pytest

from app.algorithms.nearest_neighbor import solve
from app.algorithms.simulated_annealing import solve as sa_solve
from app.algorithms.tsp_tw_utils import evaluate_route


def test_nearest_neighbor_4x4_matrix():
    """Verify NN produces expected route and cost for a known 4x4 matrix."""
    matrix = [
        [0, 10, 15, 20],
        [10, 0, 35, 25],
        [15, 35, 0, 30],
        [20, 25, 30, 0],
    ]
    route, cost = solve(matrix, start_index=0)
    # From 0: nearest is 1 (10). From 1: nearest unvisited is 3 (25).
    # From 3: only 2 left (30). Return to 0 (15).
    assert route == [0, 1, 3, 2, 0]
    assert cost == 80.0  # 10 + 25 + 30 + 15


def test_nearest_neighbor_single_node():
    """Single node: route is [0, 0], cost is 0."""
    route, cost = solve([[0]], start_index=0)
    assert route == [0, 0]
    assert cost == 0.0


def test_nearest_neighbor_single_node_with_time_windows():
    """Single node with time_windows: evaluate_route is called (not bypassed)."""
    # Window (-10, -1): we arrive at 0, but 0 > -1 -> violation.
    route, cost = solve(
        [[0]], start_index=0, time_windows=[(-10, -1)]
    )
    assert route == [0, 0]
    assert cost == 10_000  # penalty for 1 violation


def test_nearest_neighbor_empty_matrix():
    """Empty matrix returns empty route and zero cost."""
    route, cost = solve([], start_index=0)
    assert route == []
    assert cost == 0.0


def test_nearest_neighbor_start_index_out_of_range():
    """start_index out of range raises ValueError."""
    matrix = [[0, 1], [1, 0]]
    with pytest.raises(ValueError, match="start_index 2 out of range"):
        solve(matrix, start_index=2)


def test_nearest_neighbor_non_square_matrix():
    """Non-square matrix raises ValueError."""
    matrix = [[0, 1], [1, 0], [1, 1]]  # 3x2
    with pytest.raises(ValueError, match="Matrix must be square"):
        solve(matrix, start_index=0)


# --- evaluate_route (TSPTW) tests ---


def test_evaluate_route_empty():
    """Empty route returns (0, 0)."""
    cost, violations = evaluate_route([], [[0]], [(0, 100)])
    assert cost == 0.0
    assert violations == 0


def test_evaluate_route_single_node():
    """Single-node route [0, 0]: no travel, check window at time 0."""
    matrix = [[0]]
    windows = [(0, 100)]
    cost, violations = evaluate_route([0, 0], matrix, windows)
    assert cost == 0.0
    assert violations == 0


def test_evaluate_route_with_violation():
    """Route [0, 1, 2, 0] with one time window violation at location 2."""
    # 0->1: 60s, arrive 60. Window (50,150) ok.
    # 1->2: 250s, arrive 310. Window (200,300): 310 > 300 -> violation.
    # 2->0: 50s. Window (0, inf) for start - no violation on return.
    matrix = [
        [0, 60, 100],
        [60, 0, 250],
        [50, 100, 0],
    ]
    windows = [(0, float("inf")), (50, 150), (200, 300)]
    cost, violations = evaluate_route([0, 1, 2, 0], matrix, windows)
    assert violations == 1
    travel_time = 60 + 250 + 50  # 360
    assert cost == travel_time + 10_000
    assert cost == 10_360


def test_evaluate_route_no_violations():
    """Route with wait: arrive early, wait until window opens."""
    # 0->1: 10s, arrive 10. Window (50,150): wait to 50.
    # 1->2: 100s, arrive 150. Window (100,200): ok.
    # 2->0: 50s, arrive 200. Window (0, 300) for start: ok.
    matrix = [[0, 10, 50], [10, 0, 100], [50, 100, 0]]
    windows = [(0, 300), (50, 150), (100, 200)]
    cost, violations = evaluate_route([0, 1, 2, 0], matrix, windows)
    assert violations == 0
    assert cost == 10 + 100 + 50  # 160


def test_nearest_neighbor_with_time_windows_returns_penalized_cost():
    """NN with time_windows returns evaluate_route cost, not raw travel time."""
    matrix = [
        [0, 10, 15],
        [10, 0, 35],
        [15, 35, 0],
    ]
    # Tight window on 1: (0, 12). NN route [0,1,2,0]: 0->1=10 ok, 1->2=35, arrive 45.
    # Window for 2: (0, 100). 45 ok. Return 15. Total travel 60. No violations.
    windows = [(0, 100), (0, 12), (0, 100)]
    route, cost = solve(matrix, start_index=0, time_windows=windows)
    # NN gives [0, 1, 2, 0] (nearest from 0 is 1, from 1 is 2). Arrive 1 at 10, ok.
    # Leave 1 at 10, arrive 2 at 45. Ok. Return 0 at 60. Cost = 60, no violations.
    assert cost == 60.0
    assert route == [0, 1, 2, 0]


# --- progress_callback tests ---


def test_nearest_neighbor_progress_callback_fires():
    """progress_callback is called for each step and the final route."""
    matrix = [
        [0, 10, 15, 20],
        [10, 0, 35, 25],
        [15, 35, 0, 30],
        [20, 25, 30, 0],
    ]
    events = []
    solve(matrix, start_index=0, progress_callback=lambda r, c: events.append((r, c)))

    # Expect: initial [0], then [0,1], [0,1,3], [0,1,3,2], then final [0,1,3,2,0]
    assert len(events) == 5
    assert events[0][0] == [0]
    assert events[1][0] == [0, 1]
    assert events[2][0] == [0, 1, 3]
    assert events[3][0] == [0, 1, 3, 2]
    assert events[4][0] == [0, 1, 3, 2, 0]


def test_nearest_neighbor_progress_callback_none_is_safe():
    """Passing progress_callback=None should not change behavior."""
    matrix = [[0, 10], [10, 0]]
    route, cost = solve(matrix, start_index=0, progress_callback=None)
    assert route == [0, 1, 0]
    assert cost == 20.0


# --- solver service tests ---


# --- Simulated Annealing tests ---


def test_sa_returns_valid_route():
    """SA returns a valid route that visits all cities exactly once."""
    matrix = [
        [0, 10, 15, 20],
        [10, 0, 35, 25],
        [15, 35, 0, 30],
        [20, 25, 30, 0],
    ]
    nn_route = [0, 1, 3, 2, 0]
    route, cost = sa_solve(matrix, initial_route=nn_route, max_iter=5000)

    # Route must start and end at 0
    assert route[0] == 0
    assert route[-1] == 0
    # All cities visited exactly once
    inner = sorted(route[1:-1])
    assert inner == [1, 2, 3]
    # Cost must match manual evaluation
    expected_cost = sum(matrix[route[k]][route[k + 1]] for k in range(len(route) - 1))
    assert abs(cost - expected_cost) < 1e-9


def test_sa_improves_or_matches_nn():
    """SA should find a cost <= NN cost (or equal) on a known matrix."""
    matrix = [
        [0, 10, 15, 20],
        [10, 0, 35, 25],
        [15, 35, 0, 30],
        [20, 25, 30, 0],
    ]
    nn_route = [0, 1, 3, 2, 0]
    nn_cost = 80.0  # 10 + 25 + 30 + 15

    # Run SA multiple times — it should never be worse on average
    results = []
    for _ in range(10):
        _, cost = sa_solve(matrix, initial_route=nn_route, max_iter=5000)
        results.append(cost)

    assert min(results) <= nn_cost


def test_sa_empty_route():
    """Empty initial_route returns empty route and zero cost."""
    route, cost = sa_solve([], initial_route=[])
    assert route == []
    assert cost == 0.0


def test_sa_single_city():
    """Single-city route [0, 0] is returned unchanged."""
    route, cost = sa_solve([[0]], initial_route=[0, 0])
    assert route == [0, 0]
    assert cost == 0.0


def test_sa_two_cities():
    """Two-city route has only one inner city — nothing to swap, returned as-is."""
    matrix = [[0, 10], [10, 0]]
    route, cost = sa_solve(matrix, initial_route=[0, 1, 0], max_iter=1000)
    assert route == [0, 1, 0]
    assert cost == 20.0


def test_sa_invalid_route_raises():
    """initial_route not starting and ending at same index raises ValueError."""
    matrix = [[0, 10], [10, 0]]
    with pytest.raises(ValueError, match="must start and end with the same index"):
        sa_solve(matrix, initial_route=[0, 1])


def test_sa_with_time_windows():
    """SA uses evaluate_route for cost when time_windows are provided."""
    matrix = [
        [0, 10, 15],
        [10, 0, 35],
        [15, 35, 0],
    ]
    # Window that causes a violation on certain orderings
    windows = [(0, 100), (0, 5), (0, 100)]  # arriving at 1 after t=5 is a violation
    route, cost = sa_solve(
        matrix, initial_route=[0, 1, 2, 0], time_windows=windows, max_iter=1000
    )
    # Cost should include penalty if violation exists, or be pure travel time if not
    expected_cost, _ = evaluate_route(route, matrix, windows)
    assert abs(cost - expected_cost) < 1e-9


def test_sa_progress_callback_fires():
    """progress_callback fires at start and on each new best."""
    matrix = [
        [0, 10, 15, 20],
        [10, 0, 35, 25],
        [15, 35, 0, 30],
        [20, 25, 30, 0],
    ]
    events = []
    sa_solve(
        matrix,
        initial_route=[0, 1, 3, 2, 0],
        max_iter=5000,
        progress_callback=lambda r, c: events.append((r, c)),
    )
    # At minimum: one initial event
    assert len(events) >= 1
    # First event should be the initial route
    assert events[0][0] == [0, 1, 3, 2, 0]
    # Costs should be non-increasing (each event is a new best)
    for i in range(1, len(events)):
        assert events[i][1] <= events[i - 1][1]


def test_sa_progress_callback_none_is_safe():
    """Passing progress_callback=None should not change behavior."""
    matrix = [[0, 10, 15], [10, 0, 35], [15, 35, 0]]
    route, cost = sa_solve(
        matrix, initial_route=[0, 1, 2, 0], progress_callback=None
    )
    assert route[0] == 0
    assert route[-1] == 0


# --- solver service tests ---


@pytest.mark.asyncio
async def test_run_nn_with_progress_yields_events():
    """run_nn_with_progress yields progress and done events."""
    from app.services.solver import run_nn_with_progress

    matrix = [
        [0, 10, 15],
        [10, 0, 35],
        [15, 35, 0],
    ]
    events = []
    async for event in run_nn_with_progress(matrix, start_index=0):
        events.append(event)

    # Should have multiple progress events and one done event
    assert events[-1]["type"] == "done"
    assert events[-1]["route"] == [0, 1, 2, 0]
    progress_events = [e for e in events if e["type"] == "progress"]
    assert len(progress_events) >= 1


@pytest.mark.asyncio
async def test_run_sa_with_progress_yields_events():
    """run_sa_with_progress yields sa_progress and sa_done events."""
    from app.services.solver import run_sa_with_progress

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

    # Must end with sa_done
    assert events[-1]["type"] == "sa_done"
    final_route = events[-1]["route"]
    assert final_route[0] == 0
    assert final_route[-1] == 0
    assert sorted(final_route[1:-1]) == [1, 2, 3]

    # Should have at least one sa_progress (the initial best)
    sa_progress = [e for e in events if e["type"] == "sa_progress"]
    assert len(sa_progress) >= 1

    # sa_progress costs should be non-increasing
    for i in range(1, len(sa_progress)):
        assert sa_progress[i]["cost"] <= sa_progress[i - 1]["cost"]
