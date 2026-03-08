"""Unit tests for TSP algorithms."""

import pytest

from app.algorithms.nearest_neighbor import solve


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
