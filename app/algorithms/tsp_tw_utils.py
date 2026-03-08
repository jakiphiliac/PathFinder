"""
TSP with Time Windows (TSPTW) utilities.

Penalty-based cost: violations add a large penalty so the solver prefers
routes that respect time windows when possible.
"""

VIOLATION_PENALTY = 10_000


def evaluate_route(
    route: list[int],
    matrix: list[list[float]],
    time_windows: list[tuple[float, float]],
    service_time: float = 0,
) -> tuple[float, int]:
    """
    Evaluate a route with time window constraints (penalty-based).

    Args:
        route: Ordered list of location indices (including return to start).
        matrix: Travel time matrix in seconds. matrix[i][j] = time from i to j.
        time_windows: List of (earliest, latest) in seconds from reference.
            Same length as matrix. Use (0, float('inf')) for no constraint.
        service_time: Seconds spent at each location (default 0).

    Returns:
        (total_cost, violation_count) where total_cost = travel_time + penalty * violations.
    """
    if not route:
        return (0.0, 0)

    n = len(matrix)
    if n == 0:
        return (0.0, 0)

    # Ensure we have a window for each location; pad with (0, inf) if needed
    windows = list(time_windows)
    while len(windows) < n:
        windows.append((0.0, float("inf")))

    current_time = 0.0
    travel_time = 0.0
    violations = 0

    for k in range(len(route) - 1):
        i, j = route[k], route[k + 1]
        travel_time += matrix[i][j]
        current_time += matrix[i][j]

        # Check time window for the destination (j)
        earliest, latest = windows[j]
        if current_time < earliest:
            current_time = earliest  # wait
        elif current_time > latest:
            violations += 1

        current_time += service_time

    total_cost = travel_time + VIOLATION_PENALTY * violations
    return (total_cost, violations)
