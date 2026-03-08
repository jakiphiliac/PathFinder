"""
Nearest Neighbor heuristic for the Travelling Salesman Problem (TSP).

Greedy algorithm: at each step, go to the nearest unvisited city.
Fast O(N²) but not optimal. Used as a baseline for comparison with Simulated Annealing.
"""


def solve(
    matrix: list[list[float]],
    start_index: int = 0,
) -> tuple[list[int], float]:
    """
    Solve TSP using the Nearest Neighbor heuristic.

    Args:
        matrix: 2D list of travel times in seconds. matrix[i][j] = time from i to j.
        start_index: Which location to start from (default 0).

    Returns:
        (route, total_cost) where route is a list of indices in visit order
        (including return to start), and total_cost is the sum of travel times in seconds.

    Raises:
        ValueError: If matrix is empty, not square, or start_index is out of range.
    """ 
    n = len(matrix)
    if n == 0:
        return ([], 0.0)

    if start_index < 0 or start_index >= n:
        raise ValueError(f"start_index {start_index} out of range for matrix size {n}")

    for row in matrix:
        if len(row) != n:
            raise ValueError(f"Matrix must be square, got {n}x{len(row)}")

    if n == 1:
        return ([0, 0], 0.0)

    visited = {start_index}
    route = [start_index]
    current = start_index
    total_cost = 0.0

    while len(visited) < n:
        best_j = -1
        best_time = float("inf")
        for j in range(n):
            if j not in visited:
                t = matrix[current][j]
                if t < best_time:
                    best_time = t
                    best_j = j

        route.append(best_j)
        total_cost += best_time
        current = best_j
        visited.add(best_j)

    # Return leg to start
    total_cost += matrix[current][start_index]
    route.append(start_index)

    return (route, total_cost)
