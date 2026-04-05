"""Baseline route optimization: Nearest Neighbor + 2-opt."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def _tour_cost(matrix: list[list[float]], tour: list[int]) -> float:
    """Total travel time along a tour (sum of consecutive-pair durations)."""
    return sum(matrix[tour[i]][tour[i + 1]] for i in range(len(tour) - 1))


def nearest_neighbor(
    matrix: list[list[float]],
    start_idx: int,
    end_idx: int | None = None,
) -> list[int]:
    """Build a greedy nearest-neighbor tour.

    Args:
        matrix: NxN travel-time matrix (seconds).
        start_idx: Index of the starting node.
        end_idx: Index of the ending node (fixed). If None or equal to
                 start_idx, builds a closed tour returning to start.

    Returns:
        Ordered list of node indices representing the tour.
    """
    n = len(matrix)
    if n == 0:
        return []
    if n == 1:
        return [start_idx]

    closed = end_idx is None or end_idx == start_idx
    visited = {start_idx}
    if not closed:
        visited.add(end_idx)  # type: ignore[arg-type]

    tour = [start_idx]
    current = start_idx

    # Visit all unvisited nodes greedily
    while len(visited) < n:
        best_next = -1
        best_cost = float("inf")
        for j in range(n):
            if j not in visited and matrix[current][j] < best_cost:
                best_cost = matrix[current][j]
                best_next = j
        if best_next == -1:
            break
        tour.append(best_next)
        visited.add(best_next)
        current = best_next

    # Close the tour or append the fixed endpoint
    if closed:
        tour.append(start_idx)
    else:
        tour.append(end_idx)  # type: ignore[arg-type]

    return tour


def two_opt(
    matrix: list[list[float]],
    tour: list[int],
    start_idx: int,
    end_idx: int | None = None,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    max_iterations: int = 1000,
) -> list[int]:
    """Improve a tour using 2-opt local search.

    For open paths (start != end), only interior nodes are reversed.
    For closed tours (start == end), the full interior is eligible.

    Args:
        matrix: NxN travel-time matrix.
        tour: Initial tour (from nearest_neighbor).
        start_idx: Fixed start node.
        end_idx: Fixed end node (None or == start_idx for closed).
        progress_callback: Called with ("swap", {details}) on each accepted swap.
        max_iterations: Max number of full passes without improvement before stopping.

    Returns:
        Improved tour.
    """
    best = list(tour)
    best_cost = _tour_cost(matrix, best)
    n = len(best)

    if n <= 3:
        return best

    # For open path, don't reverse segments that include start or end
    # tour[0] is start, tour[-1] is end — both fixed
    lo = 1  # first reversible position
    hi = n - 1  # exclusive upper bound (don't touch last node)

    iterations_without_improvement = 0
    iteration = 0

    while iterations_without_improvement < max_iterations:
        improved = False
        iteration += 1

        for i in range(lo, hi - 1):
            for j in range(i + 1, hi):
                # Reverse segment [i..j]
                candidate = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                candidate_cost = _tour_cost(matrix, candidate)

                if candidate_cost < best_cost - 1e-9:
                    if progress_callback:
                        progress_callback(
                            "swap",
                            {
                                "i": i,
                                "j": j,
                                "tour": candidate,
                                "cost": candidate_cost,
                                "accepted": True,
                                "iteration": iteration,
                            },
                        )
                    best = candidate
                    best_cost = candidate_cost
                    improved = True
                    break  # restart inner loops
            if improved:
                break

        if not improved:
            iterations_without_improvement += 1
        else:
            iterations_without_improvement = 0

    return best


def solve_baseline(
    matrix: list[list[float]],
    start_idx: int,
    end_idx: int | None = None,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> list[int]:
    """Run full baseline optimization: NN then 2-opt.

    Args:
        matrix: NxN travel-time matrix.
        start_idx: Starting node index.
        end_idx: Ending node index (None for closed tour).
        progress_callback: Called with events during optimization.

    Returns:
        Optimized tour as list of node indices.
    """
    nn_tour = nearest_neighbor(matrix, start_idx, end_idx)
    nn_cost = _tour_cost(matrix, nn_tour)

    if progress_callback:
        progress_callback("nn", {"tour": nn_tour, "cost": nn_cost})

    optimized = two_opt(matrix, nn_tour, start_idx, end_idx, progress_callback)
    return optimized
