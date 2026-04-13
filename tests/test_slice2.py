"""Slice 2 tests: Feasibility Engine."""

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import httpx
from httpx import ASGITransport

from app.engine.feasibility import calculate_feasibility
from app.engine.category_defaults import get_duration_minutes
from app.main import app
from app.db import init_db
from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRIP_DATE = date(2026, 4, 15)  # Wednesday
TRIP_END = datetime(2026, 4, 15, 18, 0)


def make_place(**overrides):
    """Helper to create a place dict for testing."""
    defaults = {
        "id": 1,
        "category": "museum",
        "estimated_duration_min": None,
        "opening_hours": "Mo-Su 09:00-18:00",
        "status": "pending",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Unit tests: calculate_feasibility
# ---------------------------------------------------------------------------


def test_feasibility_green():
    """Place with plenty of time and open hours -> green."""
    current = datetime(2026, 4, 15, 9, 0)
    result = calculate_feasibility(
        place=make_place(),
        travel_to_place_seconds=600,  # 10 min
        travel_to_endpoint_seconds=600,  # 10 min
        current_time=current,
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
    )
    assert result["color"] == "green"
    assert result["slack_minutes"] > 0
    assert result["reason"] == "Plenty of time"


def test_feasibility_red_closing_soon():
    """Place closing in < 30 min -> red (window_remaining < 30 min)."""
    current = datetime(2026, 4, 15, 17, 42)
    # Use viewpoint (15 min visit) so there's enough time to actually visit.
    # A museum (90 min) would be gray (impossible) at this point.
    result = calculate_feasibility(
        place=make_place(category="viewpoint", opening_hours="Mo-Su 09:00-18:00"),
        travel_to_place_seconds=60,  # 1 min
        travel_to_endpoint_seconds=60,  # 1 min
        current_time=current,
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
    )
    # viewpoint = 15 min. arrival=17:43, depart=17:58, finish=17:59
    # slack = 18:00 - 17:59 = 1 min >= 0 (not gray)
    # window_remaining = 18:00 - 17:42 = 18 min < 30 min -> red
    assert result["color"] == "red"
    assert "Closes in" in result["reason"]


def test_feasibility_gray_impossible():
    """Place too far to reach and return -> gray."""
    current = datetime(2026, 4, 15, 17, 0)
    result = calculate_feasibility(
        place=make_place(),
        travel_to_place_seconds=3600,  # 1 hour to get there
        travel_to_endpoint_seconds=3600,  # 1 hour back
        current_time=current,
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
    )
    # travel(60) + visit(90) + travel(60) = 210 min, remaining = 60 min -> slack < 0
    assert result["color"] == "gray"
    assert "Not enough time" in result["reason"]
    assert result["slack_minutes"] < 0


def test_feasibility_gray_closed():
    """Place closed by arrival time -> gray."""
    current = datetime(2026, 4, 15, 16, 0)
    result = calculate_feasibility(
        place=make_place(opening_hours="Mo-Su 09:00-15:00"),
        travel_to_place_seconds=600,
        travel_to_endpoint_seconds=600,
        current_time=current,
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
    )
    # Closes at 15:00, arrival at 16:10 -> closed
    # But slack must be >= 0 for this branch. visit=90min, finish=17:50, slack=10min > 0
    assert result["color"] == "gray"
    assert "Closed" in result["reason"]


def test_feasibility_unknown():
    """Place with no opening_hours -> unknown."""
    current = datetime(2026, 4, 15, 9, 0)
    result = calculate_feasibility(
        place=make_place(opening_hours=None),
        travel_to_place_seconds=600,
        travel_to_endpoint_seconds=600,
        current_time=current,
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
    )
    assert result["color"] == "unknown"
    assert "No opening hours" in result["reason"]


def test_feasibility_yellow_tight():
    """Slack ratio between 0.10 and 0.30 -> yellow."""
    # Need: 0.10 <= slack_ratio < 0.30, no closing-time pressure
    # remaining = trip_end - current
    # slack = remaining - travel_to - visit - travel_back
    # slack_ratio = slack / remaining
    # Want slack_ratio ~ 0.20
    # remaining = 300 min (5 hours). slack = 0.20 * 300 = 60 min.
    # used = 240 min. museum(90) + travel_to + travel_back = 240 -> travels = 150 min = 9000s each side? No, total.
    # travel_to + travel_back = 150 min = 9000 sec total. Let's split: 4500 each.
    current = datetime(2026, 4, 15, 13, 0)
    # remaining = 5h = 300 min. museum = 90 min.
    # travel_to=4500s(75min) + visit(90min) + travel_back=4500s(75min) = 240 min
    # slack = 300 - 240 = 60 min. ratio = 60/300 = 0.20
    result = calculate_feasibility(
        place=make_place(
            opening_hours="Mo-Su 09:00-22:00"
        ),  # far closing to avoid closing urgency
        travel_to_place_seconds=4500,
        travel_to_endpoint_seconds=4500,
        current_time=current,
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
    )
    assert result["color"] == "yellow"
    assert result["reason"] == "Feasible but limited time"


def test_feasibility_red_very_tight():
    """Slack ratio < 0.10 -> red."""
    # remaining = 300 min. Want slack_ratio ~ 0.05
    # slack = 0.05 * 300 = 15 min. used = 285 min.
    # museum(90) + travel = 195 min = 11700 sec total. 5850 each.
    current = datetime(2026, 4, 15, 13, 0)
    result = calculate_feasibility(
        place=make_place(opening_hours="Mo-Su 09:00-22:00"),
        travel_to_place_seconds=5850,
        travel_to_endpoint_seconds=5850,
        current_time=current,
        trip_end_time=TRIP_END,
        trip_date=TRIP_DATE,
    )
    assert result["color"] == "red"
    assert result["reason"] == "Very tight schedule"


# ---------------------------------------------------------------------------
# Unit tests: category_defaults
# ---------------------------------------------------------------------------


def test_category_default_museum():
    assert get_duration_minutes("museum") == 90


def test_category_default_unknown():
    assert get_duration_minutes("unknown_thing") == 45


def test_duration_override():
    assert get_duration_minutes("museum", override=30) == 30


# ---------------------------------------------------------------------------
# Integration test: feasibility endpoint
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
async def test_feasibility_endpoint(client):
    """Create trip + places, call feasibility endpoint, verify response shape."""
    # Create trip
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    assert resp.status_code == 201
    trip_id = resp.json()["id"]

    # Add a place
    place_data = {
        "name": "Parliament",
        "lat": 47.5073,
        "lon": 19.0458,
        "category": "landmark",
        "priority": "want",
    }
    resp = await client.post(f"/api/trips/{trip_id}/places", json=place_data)
    assert resp.status_code == 201

    # Mock OSRM distance matrix
    # Matrix shape: 3x3 (current_pos, place, endpoint)
    mock_matrix = [
        [0, 600, 0],  # from current pos
        [600, 0, 600],  # from place
        [0, 600, 0],  # from endpoint
    ]

    with patch(
        "app.routers.feasibility.get_distance_matrix",
        new_callable=AsyncMock,
        return_value=mock_matrix,
    ):
        resp = await client.get(f"/api/trips/{trip_id}/feasibility?time=09:00")

    assert resp.status_code == 200
    data = resp.json()
    assert "current_time" in data
    assert "remaining_minutes" in data
    assert "places" in data
    assert isinstance(data["places"], list)
    assert len(data["places"]) == 1

    place_result = data["places"][0]
    assert "color" in place_result
    assert "reason" in place_result
    assert "slack_minutes" in place_result
    assert place_result["color"] in ("green", "yellow", "red", "gray", "unknown")
