"""Trajectory persistence tests: segment recording on check-in, retrieval, cascade delete."""

import pytest
import httpx
from httpx import ASGITransport

from app.main import app
from app.db import init_db
from app.config import settings


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
    "end_lat": 47.5100,
    "end_lon": 19.0500,
    "start_time": "09:00",
    "end_time": "18:00",
    "date": "2026-04-15",
    "transport_mode": "foot",
}

SAMPLE_PLACE = {
    "name": "Test Place",
    "lat": 47.5000,
    "lon": 19.0450,
    "category": "museum",
    "priority": "want",
    "estimated_duration_min": 60,
}


async def _create_trip_with_place(client):
    """Helper: create a trip and add a place, return (trip_id, place_id)."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    assert resp.status_code == 201
    trip_id = resp.json()["id"]

    resp = await client.post(f"/api/trips/{trip_id}/places", json=SAMPLE_PLACE)
    assert resp.status_code == 201
    place_id = resp.json()["id"]

    return trip_id, place_id


async def _osrm_available() -> bool:
    """Check if OSRM is reachable (tests may run with or without it)."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get("http://localhost:5000/health")
            return r.status_code == 200
    except Exception:
        return False


@pytest.mark.anyio
async def test_trajectory_empty_on_new_trip(client):
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    resp = await client.get(f"/api/trips/{trip_id}/trajectory")
    assert resp.status_code == 200
    assert resp.json()["segments"] == []


@pytest.mark.anyio
async def test_trajectory_on_arrived(client):
    """Check-in 'arrived' succeeds; trajectory segment is created only when OSRM is up."""
    trip_id, place_id = await _create_trip_with_place(client)
    osrm_up = await _osrm_available()

    resp = await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": place_id, "action": "arrived"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "visiting"

    seg = data.get("trajectory_segment")
    if osrm_up:
        assert seg is not None
        assert seg["place_id"] == place_id
        assert seg["from_lat"] == pytest.approx(47.4979, abs=0.01)
        assert seg["to_lat"] == pytest.approx(47.5000, abs=0.01)
    else:
        # Without OSRM, no segment is recorded (by design)
        assert seg is None


@pytest.mark.anyio
async def test_trajectory_persists_across_requests(client):
    trip_id, place_id = await _create_trip_with_place(client)
    osrm_up = await _osrm_available()

    await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": place_id, "action": "arrived"},
    )

    resp = await client.get(f"/api/trips/{trip_id}/trajectory")
    assert resp.status_code == 200
    segments = resp.json()["segments"]
    if osrm_up:
        assert len(segments) == 1
        assert segments[0]["place_id"] == place_id
    else:
        assert len(segments) == 0


@pytest.mark.anyio
async def test_trajectory_accumulates_segments(client):
    osrm_up = await _osrm_available()
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    place_a = {"name": "A", "lat": 47.500, "lon": 19.045, "priority": "want"}
    place_b = {"name": "B", "lat": 47.510, "lon": 19.050, "priority": "want"}

    resp_a = await client.post(f"/api/trips/{trip_id}/places", json=place_a)
    pid_a = resp_a.json()["id"]
    resp_b = await client.post(f"/api/trips/{trip_id}/places", json=place_b)
    pid_b = resp_b.json()["id"]

    # Arrive at A
    await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": pid_a, "action": "arrived"},
    )
    # Done at A
    await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": pid_a, "action": "done"},
    )
    # Arrive at B (should use A's coords as origin when OSRM is up)
    resp = await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": pid_b, "action": "arrived"},
    )
    seg_b = resp.json().get("trajectory_segment")
    if osrm_up:
        assert seg_b is not None
        assert seg_b["from_lat"] == pytest.approx(47.500, abs=0.01)

    resp = await client.get(f"/api/trips/{trip_id}/trajectory")
    segments = resp.json()["segments"]
    if osrm_up:
        assert len(segments) == 2
    else:
        assert len(segments) == 0


@pytest.mark.anyio
async def test_trajectory_no_segment_on_done(client):
    trip_id, place_id = await _create_trip_with_place(client)

    await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": place_id, "action": "arrived"},
    )
    resp = await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": place_id, "action": "done"},
    )
    assert resp.json().get("trajectory_segment") is None


@pytest.mark.anyio
async def test_trajectory_cascade_delete(client):
    trip_id, place_id = await _create_trip_with_place(client)
    osrm_up = await _osrm_available()

    await client.post(
        f"/api/trips/{trip_id}/checkin",
        json={"place_id": place_id, "action": "arrived"},
    )

    resp = await client.get(f"/api/trips/{trip_id}/trajectory")
    segments = resp.json()["segments"]
    if osrm_up:
        assert len(segments) == 1
    else:
        assert len(segments) == 0

    await client.delete(f"/api/trips/{trip_id}")

    resp = await client.get(f"/api/trips/{trip_id}/trajectory")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_trip_create_defaults(client):
    """Test that date and start_time default to current values when omitted."""
    payload = {
        "city": "Budapest",
        "start_lat": 47.4979,
        "start_lon": 19.0402,
        "end_lat": 47.4979,
        "end_lon": 19.0402,
        "end_time": "18:00",
        "transport_mode": "foot",
    }
    resp = await client.post("/api/trips", json=payload)
    assert resp.status_code == 201

    trip_id = resp.json()["id"]
    resp = await client.get(f"/api/trips/{trip_id}")
    data = resp.json()
    assert data["date"]  # should be set to today
    assert data["start_time"]  # should be set to current time
