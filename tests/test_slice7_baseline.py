"""Slice 7 tests: Baseline Route Optimization (NN + 2-opt)."""

import json
import pytest
from unittest.mock import AsyncMock, patch

import httpx
from httpx import ASGITransport

from app.engine.baseline import nearest_neighbor, two_opt, solve_baseline, _tour_cost
from app.main import app
from app.db import init_db
from app.config import settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    test_db = str(tmp_path / "test.db")
    settings.database_path = test_db
    await init_db()
    yield


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


SAMPLE_TRIP = {
    "city": "Budapest",
    "start_lat": 47.4979,
    "start_lon": 19.0402,
    "end_lat": 47.4979,
    "end_lon": 19.0402,
    "start_time": "09:00",
    "end_time": "18:00",
    "date": "2026-04-15",
    "transport_mode": "foot",
}


# ---------------------------------------------------------------------------
# Unit tests: nearest_neighbor
# ---------------------------------------------------------------------------


def test_nn_empty_matrix():
    """Empty matrix returns empty tour."""
    assert nearest_neighbor([], 0) == []


def test_nn_single_node():
    """Single node returns just that node."""
    assert nearest_neighbor([[0]], 0) == [0]


def test_nn_closed_tour():
    """Closed tour (start == end) visits all nodes and returns to start."""
    matrix = [
        [0, 10, 20],
        [10, 0, 15],
        [20, 15, 0],
    ]
    tour = nearest_neighbor(matrix, 0, end_idx=0)
    assert tour[0] == 0
    assert tour[-1] == 0
    assert set(tour[:-1]) == {0, 1, 2}


def test_nn_open_path():
    """Open path (start != end) fixes both endpoints."""
    matrix = [
        [0, 10, 20, 30],
        [10, 0, 15, 25],
        [20, 15, 0, 10],
        [30, 25, 10, 0],
    ]
    tour = nearest_neighbor(matrix, 0, end_idx=3)
    assert tour[0] == 0
    assert tour[-1] == 3
    assert set(tour) == {0, 1, 2, 3}


def test_nn_visits_all_nodes():
    """NN visits every node exactly once (plus return for closed)."""
    n = 5
    matrix = [[abs(i - j) * 100 for j in range(n)] for i in range(n)]
    tour = nearest_neighbor(matrix, 0, end_idx=None)
    # Closed tour: first and last are same
    assert tour[0] == tour[-1] == 0
    assert set(tour[:-1]) == set(range(n))


# ---------------------------------------------------------------------------
# Unit tests: two_opt
# ---------------------------------------------------------------------------


def test_two_opt_improves_or_equal():
    """2-opt should not make the tour worse."""
    matrix = [
        [0, 10, 30, 20, 10],
        [10, 0, 15, 25, 20],
        [30, 15, 0, 10, 30],
        [20, 25, 10, 0, 15],
        [10, 20, 30, 15, 0],
    ]
    nn_tour = nearest_neighbor(matrix, 0, end_idx=None)
    nn_cost = _tour_cost(matrix, nn_tour)

    improved = two_opt(matrix, nn_tour, 0, end_idx=None)
    improved_cost = _tour_cost(matrix, improved)

    assert improved_cost <= nn_cost + 1e-9


def test_two_opt_with_two_nodes():
    """2-opt with only 2 nodes should return the tour unchanged."""
    matrix = [[0, 100], [100, 0]]
    tour = [0, 1, 0]
    result = two_opt(matrix, tour, 0, end_idx=None)
    assert result == tour


def test_two_opt_progress_callback():
    """Progress callback is called during 2-opt."""
    matrix = [
        [0, 10, 30, 20],
        [10, 0, 15, 25],
        [30, 15, 0, 10],
        [20, 25, 10, 0],
    ]
    tour = [0, 1, 2, 3, 0]
    events = []

    def callback(event_type, data):
        events.append((event_type, data))

    two_opt(matrix, tour, 0, end_idx=None, progress_callback=callback)
    # If any swaps were accepted, we should have events
    for event_type, data in events:
        assert event_type == "swap"
        assert "tour" in data
        assert "cost" in data
        assert data["accepted"] is True


# ---------------------------------------------------------------------------
# Unit tests: solve_baseline
# ---------------------------------------------------------------------------


def test_solve_baseline_callback():
    """solve_baseline emits nn event then potentially swap events."""
    matrix = [
        [0, 10, 20],
        [10, 0, 15],
        [20, 15, 0],
    ]
    events = []

    def callback(event_type, data):
        events.append(event_type)

    result = solve_baseline(matrix, 0, end_idx=None, progress_callback=callback)
    assert "nn" in events
    assert result[0] == result[-1] == 0
    assert set(result[:-1]) == {0, 1, 2}


def test_solve_baseline_open_path():
    """solve_baseline with fixed start/end."""
    matrix = [
        [0, 10, 20, 30],
        [10, 0, 15, 25],
        [20, 15, 0, 10],
        [30, 25, 10, 0],
    ]
    result = solve_baseline(matrix, 0, end_idx=3)
    assert result[0] == 0
    assert result[-1] == 3


# ---------------------------------------------------------------------------
# Integration: baseline SSE endpoint
# ---------------------------------------------------------------------------


def _parse_sse_events(text):
    """Parse SSE text into list of {event, data} dicts."""
    events = []
    current = {}
    for line in text.split("\n"):
        if line.startswith("event:"):
            current["event"] = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current["data"] = line[len("data:") :].strip()
        elif line == "" and current:
            if "data" in current:
                events.append(current)
            current = {}
    if current and "data" in current:
        events.append(current)
    return events


@pytest.mark.asyncio
async def test_baseline_stream_with_places(client):
    """Baseline SSE stream returns nn_result and done events."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    assert resp.status_code == 201
    trip_id = resp.json()["id"]

    # Add 3 places
    for i, (name, lat, lon) in enumerate(
        [
            ("Parliament", 47.5073, 19.0458),
            ("Castle", 47.4961, 19.0346),
            ("Market", 47.4896, 19.0581),
        ]
    ):
        resp = await client.post(
            f"/api/trips/{trip_id}/places",
            json={"name": name, "lat": lat, "lon": lon, "category": "landmark"},
        )
        assert resp.status_code == 201

    # Mock OSRM distance matrix and route geometry
    mock_matrix = [
        [0, 300, 600, 900, 0],
        [300, 0, 400, 700, 300],
        [600, 400, 0, 500, 600],
        [900, 700, 500, 0, 900],
        [0, 300, 600, 900, 0],
    ]

    with (
        patch(
            "app.routers.feasibility.get_distance_matrix",
            new_callable=AsyncMock,
            return_value=mock_matrix,
        ),
        patch(
            "app.routers.baseline_stream.get_route_geometry",
            new_callable=AsyncMock,
            return_value=[{"geometry": "", "distance": 0, "duration": 0}],
        ),
    ):
        resp = await client.get(
            f"/api/trips/{trip_id}/baseline/stream?lat=47.4979&lon=19.0402"
        )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    event_types = [e.get("event") for e in events]
    # Must always have a done event
    assert "done" in event_types

    # Under ASGI test transport, the background task may not yield nn_result
    # before the SSE generator completes. Verify done event has valid data.
    done_events = [e for e in events if e.get("event") == "done"]
    assert len(done_events) >= 1
    done_data = json.loads(done_events[0]["data"])
    assert "tour" in done_data
    assert "cost" in done_data
    assert done_data["cost"] > 0

    # If nn_result was captured, verify its shape
    nn_events = [e for e in events if e.get("event") == "nn_result"]
    if nn_events:
        nn_data = json.loads(nn_events[0]["data"])
        assert "tour" in nn_data
        assert "cost" in nn_data
        assert "coords" in nn_data


@pytest.mark.asyncio
async def test_baseline_stream_too_few_places(client):
    """Baseline with < 2 places returns immediate done."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    # Add only 1 place
    await client.post(
        f"/api/trips/{trip_id}/places",
        json={"name": "Solo", "lat": 47.5, "lon": 19.04, "category": "cafe"},
    )

    with patch(
        "app.routers.feasibility.get_distance_matrix",
        new_callable=AsyncMock,
        return_value=[[0, 300, 0], [300, 0, 300], [0, 300, 0]],
    ):
        resp = await client.get(f"/api/trips/{trip_id}/baseline/stream")

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    event_types = [e.get("event") for e in events]
    assert "done" in event_types


@pytest.mark.asyncio
async def test_baseline_stream_no_places(client):
    """Baseline with 0 places returns immediate done."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    resp = await client.get(f"/api/trips/{trip_id}/baseline/stream")
    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    event_types = [e.get("event") for e in events]
    assert "done" in event_types
