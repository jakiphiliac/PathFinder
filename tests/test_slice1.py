"""Slice 1 tests: Trip and Place CRUD endpoints."""

import pytest
import httpx
from httpx import ASGITransport

from app.main import app
from app.db import init_db
from app.config import settings


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    """Use a temporary database for each test."""
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

SAMPLE_PLACE = {
    "name": "Hungarian Parliament",
    "lat": 47.5073,
    "lon": 19.0458,
    "category": "landmark",
    "priority": "want",
    "estimated_duration_min": 60,
}


async def _create_trip(client: httpx.AsyncClient) -> dict:
    """Helper to create a trip and return the response JSON."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    assert resp.status_code == 201
    return resp.json()


async def _add_place(
    client: httpx.AsyncClient, trip_id: str, place: dict | None = None
) -> dict:
    """Helper to add a place and return the response JSON."""
    resp = await client.post(f"/api/trips/{trip_id}/places", json=place or SAMPLE_PLACE)
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_create_trip(client: httpx.AsyncClient):
    data = await _create_trip(client)
    assert "id" in data
    assert "url" in data
    # id should be a valid UUID (36 chars with hyphens)
    assert len(data["id"]) == 36
    assert data["url"] == f"/api/trips/{data['id']}"


@pytest.mark.asyncio
async def test_get_trip(client: httpx.AsyncClient):
    created = await _create_trip(client)
    resp = await client.get(f"/api/trips/{created['id']}")
    assert resp.status_code == 200
    trip = resp.json()
    assert trip["id"] == created["id"]
    assert trip["city"] == "Budapest"
    assert trip["transport_mode"] == "foot"
    assert trip["start_time"] == "09:00"
    assert trip["end_time"] == "18:00"
    assert trip["date"] == "2026-04-15"
    assert "places" in trip
    assert trip["places"] == []


@pytest.mark.asyncio
async def test_get_trip_not_found(client: httpx.AsyncClient):
    resp = await client.get("/api/trips/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_trip(client: httpx.AsyncClient):
    created = await _create_trip(client)
    resp = await client.patch(
        f"/api/trips/{created['id']}",
        json={"transport_mode": "car"},
    )
    assert resp.status_code == 200
    assert resp.json()["transport_mode"] == "car"


@pytest.mark.asyncio
async def test_delete_trip(client: httpx.AsyncClient):
    created = await _create_trip(client)
    resp = await client.delete(f"/api/trips/{created['id']}")
    assert resp.status_code == 204

    resp = await client.get(f"/api/trips/{created['id']}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_place(client: httpx.AsyncClient):
    created = await _create_trip(client)
    place = await _add_place(client, created["id"])
    assert place["name"] == "Hungarian Parliament"
    assert place["lat"] == 47.5073
    assert place["lon"] == 19.0458
    assert place["category"] == "landmark"
    assert place["priority"] == "want"
    assert place["estimated_duration_min"] == 60
    assert place["trip_id"] == created["id"]
    assert place["status"] == "pending"


@pytest.mark.asyncio
async def test_get_trip_with_places(client: httpx.AsyncClient):
    created = await _create_trip(client)
    await _add_place(client, created["id"])
    await _add_place(
        client,
        created["id"],
        {
            "name": "Buda Castle",
            "lat": 47.4961,
            "lon": 19.0398,
            "category": "landmark",
            "priority": "must",
            "estimated_duration_min": 90,
        },
    )

    resp = await client.get(f"/api/trips/{created['id']}")
    assert resp.status_code == 200
    trip = resp.json()
    assert len(trip["places"]) == 2


@pytest.mark.asyncio
async def test_delete_place(client: httpx.AsyncClient):
    created = await _create_trip(client)
    place = await _add_place(client, created["id"])

    resp = await client.delete(f"/api/trips/{created['id']}/places/{place['id']}")
    assert resp.status_code == 204

    resp = await client.get(f"/api/trips/{created['id']}")
    assert resp.status_code == 200
    assert len(resp.json()["places"]) == 0


@pytest.mark.asyncio
async def test_update_place(client: httpx.AsyncClient):
    created = await _create_trip(client)
    place = await _add_place(client, created["id"])

    resp = await client.patch(
        f"/api/trips/{created['id']}/places/{place['id']}",
        json={"priority": "must"},
    )
    assert resp.status_code == 200
    assert resp.json()["priority"] == "must"
