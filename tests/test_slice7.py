"""Slice 7 tests: Edge Cases & Polish."""

import pytest
from unittest.mock import AsyncMock, patch

import httpx
from httpx import ASGITransport

from app.main import app
from app.db import init_db
from app.config import settings
from app.routers.feasibility import (
    _haversine_distance_m,
    _haversine_matrix,
)


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
# Unit tests: Haversine fallback
# ---------------------------------------------------------------------------


def test_haversine_distance_known_pair():
    """Haversine distance between two known Budapest points is roughly correct."""
    # Parliament to Buda Castle ~1.5 km straight line
    dist = _haversine_distance_m(19.0458, 47.5073, 19.0346, 47.4961)
    assert 1000 < dist < 2500  # sanity check


def test_haversine_distance_same_point():
    """Same point has zero distance."""
    dist = _haversine_distance_m(19.04, 47.50, 19.04, 47.50)
    assert dist == 0.0


def test_haversine_matrix_shape():
    """Matrix has correct shape for given coords."""
    coords = [[19.04, 47.50], [19.05, 47.51], [19.06, 47.52]]
    matrix = _haversine_matrix(coords, "foot")
    assert len(matrix) == 3
    assert all(len(row) == 3 for row in matrix)
    # Diagonal is zero
    for i in range(3):
        assert matrix[i][i] == 0.0


def test_haversine_matrix_profile_affects_speed():
    """Different profiles should give different travel times."""
    coords = [[19.04, 47.50], [19.05, 47.51]]
    foot = _haversine_matrix(coords, "foot")
    car = _haversine_matrix(coords, "car")
    # Walking should be slower than driving
    assert foot[0][1] > car[0][1]


# ---------------------------------------------------------------------------
# Integration tests: OSRM fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feasibility_with_osrm_failure(client):
    """When OSRM is unreachable, feasibility falls back to Haversine estimates."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    assert resp.status_code == 201
    trip_id = resp.json()["id"]

    # Add a place
    resp = await client.post(
        f"/api/trips/{trip_id}/places",
        json={
            "name": "Parliament",
            "lat": 47.5073,
            "lon": 19.0458,
            "category": "landmark",
        },
    )
    assert resp.status_code == 201

    # Mock OSRM to raise
    with patch(
        "app.routers.feasibility.get_distance_matrix",
        new_callable=AsyncMock,
        side_effect=ValueError("OSRM unreachable"),
    ):
        resp = await client.get(f"/api/trips/{trip_id}/feasibility?time=09:00")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["places"]) == 1
    # Should have a valid color (not crash)
    assert data["places"][0]["color"] in ("green", "yellow", "red", "gray", "unknown")


# ---------------------------------------------------------------------------
# Edge case: trip with 0 places
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feasibility_no_places(client):
    """Trip with 0 places returns empty places list, not an error."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    resp = await client.get(f"/api/trips/{trip_id}/feasibility?time=09:00")
    assert resp.status_code == 200
    data = resp.json()
    assert data["places"] == []
    assert data["remaining_minutes"] > 0


@pytest.mark.asyncio
async def test_next_no_places(client):
    """Trip with 0 places returns empty recommendations."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    resp = await client.get(f"/api/trips/{trip_id}/next?time=09:00")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommendations"] == []


# ---------------------------------------------------------------------------
# Edge case: all places done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feasibility_all_done(client):
    """All places checked in → feasibility returns empty (only pending places computed)."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    resp = await client.post(
        f"/api/trips/{trip_id}/places",
        json={"name": "Museum", "lat": 47.498, "lon": 19.041, "category": "museum"},
    )
    place_id = resp.json()["id"]

    # Check in: arrived then done
    await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": place_id, "action": "arrived"},
    )
    await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": place_id, "action": "done"},
    )

    resp = await client.get(f"/api/trips/{trip_id}/feasibility?time=12:00")
    assert resp.status_code == 200
    data = resp.json()
    assert data["places"] == []


# ---------------------------------------------------------------------------
# Edge case: trip end time passed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feasibility_after_trip_end(client):
    """Requesting feasibility after end time returns 0 remaining minutes."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    resp = await client.get(f"/api/trips/{trip_id}/feasibility?time=19:00")
    assert resp.status_code == 200
    data = resp.json()
    assert data["remaining_minutes"] == 0


# ---------------------------------------------------------------------------
# Edge case: API error responses have correct status codes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trip_not_found(client):
    """Non-existent trip returns 404."""
    resp = await client.get("/api/trips/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_place_not_found(client):
    """Non-existent place delete returns 404."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    resp = await client.delete(f"/api/trips/{trip_id}/places/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_checkin_invalid_action(client):
    """Invalid check-in transition returns 400."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    resp = await client.post(
        f"/api/trips/{trip_id}/places",
        json={"name": "Spot", "lat": 47.5, "lon": 19.04, "category": "cafe"},
    )
    place_id = resp.json()["id"]

    # Can't "done" a pending place
    resp = await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": place_id, "action": "done"},
    )
    assert resp.status_code == 400
