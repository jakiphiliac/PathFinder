"""Slice 3 tests: 'What Next?' Scoring Engine."""

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import httpx
from httpx import ASGITransport

from app.engine.scoring import score_next_actions
from app.main import app
from app.db import init_db
from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRIP_DATE = date(2026, 4, 15)  # Wednesday
TRIP_END = datetime(2026, 4, 15, 18, 0)


def make_place(id=1, **overrides):
    defaults = {
        "id": id,
        "name": f"Place {id}",
        "category": "viewpoint",
        "estimated_duration_min": None,
        "opening_hours": "Mo-Su 09:00-18:00",
        "priority": "want",
        "status": "pending",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Unit tests: score_next_actions
# ---------------------------------------------------------------------------


def test_empty_places():
    """No places -> empty recommendations."""
    result = score_next_actions(
        places=[],
        matrix=[[0]],
        current_time=datetime(2026, 4, 15, 9, 0),
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
        endpoint_idx=1,
    )
    assert result == []


def test_all_gray_returns_empty():
    """All places infeasible -> empty recommendations."""
    places = [make_place(id=1, category="museum")]  # 90 min visit
    # Matrix: [current, place1, endpoint]
    matrix = [
        [0, 3600, 0],  # 1 hour to place
        [3600, 0, 3600],  # 1 hour back
        [0, 3600, 0],
    ]
    # current=17:00, end=18:00. travel(60)+visit(90)+travel(60) = 210 min > 60 min remaining
    result = score_next_actions(
        places=places,
        matrix=matrix,
        current_time=datetime(2026, 4, 15, 17, 0),
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
        endpoint_idx=2,
    )
    assert result == []


def test_must_visit_scores_highest():
    """Must-visit place should score higher than want/if_time at same distance."""
    places = [
        make_place(id=1, priority="must"),
        make_place(id=2, priority="if_time"),
    ]
    # Both equidistant, same travel times
    matrix = [
        [0, 300, 300, 0],
        [300, 0, 300, 300],
        [300, 300, 0, 300],
        [0, 300, 300, 0],
    ]
    result = score_next_actions(
        places=places,
        matrix=matrix,
        current_time=datetime(2026, 4, 15, 9, 0),
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
        endpoint_idx=3,
    )
    assert len(result) >= 2
    assert result[0]["place_id"] == 1  # must-visit wins


def test_opportunity_cost_place_only_reachable_now():
    """Place A only reachable now (closes soon) should score highest."""
    places = [
        make_place(id=1, opening_hours="Mo-Su 09:00-09:30"),  # closes in 30 min
        make_place(id=2, opening_hours="Mo-Su 09:00-18:00"),  # open all day
    ]
    # Both nearby
    matrix = [
        [0, 300, 300, 0],
        [300, 0, 600, 300],
        [300, 600, 0, 300],
        [0, 300, 300, 0],
    ]
    result = score_next_actions(
        places=places,
        matrix=matrix,
        current_time=datetime(2026, 4, 15, 9, 0),
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
        endpoint_idx=3,
    )
    assert len(result) >= 1
    # Place 1 has high opportunity cost (if we visit place 2 first, place 1 closes)
    assert result[0]["place_id"] == 1


def test_closest_wins_when_equivalent():
    """All else equal, closer place should score higher."""
    places = [
        make_place(id=1, priority="want"),
        make_place(id=2, priority="want"),
    ]
    # Place 1 is much closer
    matrix = [
        [0, 60, 1800, 0],  # 1 min vs 30 min
        [60, 0, 1800, 60],
        [1800, 1800, 0, 1800],
        [0, 60, 1800, 0],
    ]
    result = score_next_actions(
        places=places,
        matrix=matrix,
        current_time=datetime(2026, 4, 15, 9, 0),
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
        endpoint_idx=3,
    )
    assert len(result) == 2
    assert result[0]["place_id"] == 1  # closer wins


def test_returns_max_3():
    """Should return at most 3 recommendations."""
    places = [make_place(id=i) for i in range(1, 6)]
    n = len(places)
    size = n + 2  # current + places + endpoint
    matrix = [[300] * size for _ in range(size)]
    for i in range(size):
        matrix[i][i] = 0
    result = score_next_actions(
        places=places,
        matrix=matrix,
        current_time=datetime(2026, 4, 15, 9, 0),
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
        endpoint_idx=size - 1,
    )
    assert len(result) <= 3


def test_recommendation_has_required_fields():
    """Each recommendation should have all required fields."""
    places = [make_place(id=1)]
    matrix = [
        [0, 300, 0],
        [300, 0, 300],
        [0, 300, 0],
    ]
    result = score_next_actions(
        places=places,
        matrix=matrix,
        current_time=datetime(2026, 4, 15, 9, 0),
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
        endpoint_idx=2,
    )
    assert len(result) == 1
    rec = result[0]
    assert "place_id" in rec
    assert "place_name" in rec
    assert "score" in rec
    assert "opportunity_cost" in rec
    assert "travel_minutes" in rec
    assert "reason" in rec


# ---------------------------------------------------------------------------
# Integration test: /next endpoint
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


@pytest.mark.asyncio
async def test_next_endpoint_with_places(client):
    """Create trip + places, call /next, verify response shape."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    assert resp.status_code == 201
    trip_id = resp.json()["id"]

    # Add two places
    for name in ["Parliament", "Castle"]:
        resp = await client.post(
            f"/api/trips/{trip_id}/places",
            json={
                "name": name,
                "lat": 47.5073,
                "lon": 19.0458,
                "category": "landmark",
                "priority": "want",
            },
        )
        assert resp.status_code == 201

    mock_matrix = [
        [0, 300, 600, 0],
        [300, 0, 300, 300],
        [600, 300, 0, 600],
        [0, 300, 600, 0],
    ]

    with patch(
        "app.routers.feasibility.get_distance_matrix",
        new_callable=AsyncMock,
        return_value=mock_matrix,
    ):
        resp = await client.get(f"/api/trips/{trip_id}/next?time=09:00")

    assert resp.status_code == 200
    data = resp.json()
    assert "recommendations" in data
    assert isinstance(data["recommendations"], list)
    assert len(data["recommendations"]) <= 3
    if data["recommendations"]:
        rec = data["recommendations"][0]
        assert "place_id" in rec
        assert "score" in rec
        assert "reason" in rec
        assert "travel_minutes" in rec


@pytest.mark.asyncio
async def test_next_endpoint_no_places(client):
    """Empty trip returns message, no recommendations."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    resp = await client.get(f"/api/trips/{trip_id}/next?time=09:00")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommendations"] == []
    assert data["message"] is not None


@pytest.mark.asyncio
async def test_next_endpoint_404(client):
    """Non-existent trip returns 404."""
    resp = await client.get("/api/trips/nonexistent/next?time=09:00")
    assert resp.status_code == 404
