"""Unit tests for TSP algorithms."""

import pytest

from app.algorithms.nearest_neighbor import solve
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
