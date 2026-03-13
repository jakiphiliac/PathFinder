"""
Simulated Annealing (SA) metaheuristic for the Travelling Salesman Problem.

Starts from an initial solution (e.g. from Nearest Neighbor) and iteratively
improves it by randomly swapping cities. Worse solutions are accepted with
a probability that decreases over time (cooling schedule), allowing the
algorithm to escape local optima.
"""

import math
import random
from collections.abc import Callable

from app.algorithms.tsp_tw_utils import evaluate_route


def _route_cost(
    route: list[int],
    matrix: list[list[float]],
    time_windows: list[tuple[float, float]] | None = None,
    service_time: float = 0,
) -> float:
    """Compute the cost of a route: raw travel time or penalized if time windows given."""
    if time_windows is not None:
        cost, _ = evaluate_route(route, matrix, time_windows, service_time=service_time)
        return cost

    total = 0.0
    for k in range(len(route) - 1):
        total += matrix[route[k]][route[k + 1]]
    return total


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
    """
    Improve a TSP route using Simulated Annealing.

    Args:
        matrix: 2D travel time matrix in seconds.
        initial_route: Starting route from NN, e.g. [0, 1, 3, 2, 0].
            Must start and end with the same index.
        time_windows: Optional (earliest, latest) per location for TSPTW.
        service_time: Seconds spent at each location.
        T0: Initial temperature.
        alpha: Cooling factor (T *= alpha each iteration).
        max_iter: Maximum number of iterations.
        progress_callback: Called with (best_route, best_cost) each time
            a new best solution is found. Also called once at the start.

    Returns:
        (best_route, best_cost).

    Raises:
        ValueError: If initial_route is invalid.
    """
    n = len(matrix)

    # Edge cases
    if n == 0 or len(initial_route) == 0:
        return (list(initial_route), 0.0)

    if initial_route[0] != initial_route[-1]:
        raise ValueError("initial_route must start and end with the same index")

    if len(initial_route) <= 2:
        cost = _route_cost(initial_route, matrix, time_windows, service_time)
        return (list(initial_route), cost)

    # The "inner" cities are indices 1..-2 (exclude fixed start/end)
    current_route = list(initial_route)
    current_cost = _route_cost(current_route, matrix, time_windows, service_time)

    best_route = list(current_route)
    best_cost = current_cost

    if progress_callback is not None:
        progress_callback(best_route.copy(), best_cost)

    T = T0
    inner_len = len(current_route) - 2  # number of swappable positions

    if inner_len < 2:
        # Only one inner city — nothing to swap
        return (best_route, best_cost)

    for _ in range(max_iter):
        # Pick two distinct inner positions to swap (indices 1..n-1 in route)
        i = random.randint(1, inner_len)
        j = random.randint(1, inner_len)
        while j == i:
            j = random.randint(1, inner_len)

        # Swap
        current_route[i], current_route[j] = current_route[j], current_route[i]
        new_cost = _route_cost(current_route, matrix, time_windows, service_time)

        delta = new_cost - current_cost

        if delta <= 0:
            # Better or equal — accept
            current_cost = new_cost
        elif T > 0 and random.random() < math.exp(-delta / T):
            # Worse but accepted probabilistically
            current_cost = new_cost
        else:
            # Reject — revert the swap
            current_route[i], current_route[j] = current_route[j], current_route[i]

        if current_cost < best_cost:
            best_route = list(current_route)
            best_cost = current_cost
            if progress_callback is not None:
                progress_callback(best_route.copy(), best_cost)

        T *= alpha

    return (best_route, best_cost)
