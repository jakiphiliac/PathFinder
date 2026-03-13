"""
Solver service — bridges sync TSP algorithms with async SSE streaming.

Uses a thread-safe queue so the algorithm (running in a thread) can push
progress events that the async generator yields to the SSE response.
"""

import asyncio
import queue
from collections.abc import AsyncGenerator
from typing import Any

from app.algorithms import nearest_neighbor as nn
from app.algorithms import simulated_annealing as sa


_SENTINEL = object()


async def run_nn_with_progress(
    matrix: list[list[float]],
    start_index: int = 0,
    time_windows: list[tuple[float, float]] | None = None,
    service_time: float = 0,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Run Nearest Neighbor in a thread, yielding SSE-ready dicts as it progresses.

    Yields:
        {"type": "progress", "route": [...], "cost": float}  — after each city
        {"type": "done", "route": [...], "cost": float}       — final result
    """
    q: queue.Queue[tuple[str, list[int], float] | object] = queue.Queue()

    def callback(route: list[int], cost: float) -> None:
        q.put(("progress", route, cost))

    def run() -> tuple[list[int], float]:
        try:
            result = nn.solve(
                matrix,
                start_index=start_index,
                time_windows=time_windows,
                service_time=service_time,
                progress_callback=callback,
            )
            return result
        finally:
            q.put(_SENTINEL)

    task = asyncio.get_event_loop().run_in_executor(None, run)

    while True:
        item = await asyncio.to_thread(q.get)
        if item is _SENTINEL:
            break
        _, route, cost = item
        yield {"type": "progress", "route": route, "cost": cost}

    route, cost = await task
    yield {"type": "done", "route": route, "cost": cost}


async def run_sa_with_progress(
    matrix: list[list[float]],
    initial_route: list[int],
    time_windows: list[tuple[float, float]] | None = None,
    service_time: float = 0,
    T0: float = 1000.0,
    alpha: float = 0.995,
    max_iter: int = 10_000,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Run Simulated Annealing in a thread, yielding SSE-ready dicts as it improves.

    Yields:
        {"type": "sa_progress", "route": [...], "cost": float}  — each new best
        {"type": "sa_done", "route": [...], "cost": float}       — final result
    """
    q: queue.Queue[tuple[str, list[int], float] | object] = queue.Queue()

    def callback(route: list[int], cost: float) -> None:
        q.put(("sa_progress", route, cost))

    def run() -> tuple[list[int], float]:
        try:
            result = sa.solve(
                matrix,
                initial_route=initial_route,
                time_windows=time_windows,
                service_time=service_time,
                T0=T0,
                alpha=alpha,
                max_iter=max_iter,
                progress_callback=callback,
            )
            return result
        finally:
            q.put(_SENTINEL)

    task = asyncio.get_event_loop().run_in_executor(None, run)

    while True:
        item = await asyncio.to_thread(q.get)
        if item is _SENTINEL:
            break
        _, route, cost = item
        yield {"type": "sa_progress", "route": route, "cost": cost}

    route, cost = await task
    yield {"type": "sa_done", "route": route, "cost": cost}
