"""Slice 6 tests: Transport Mode Switching."""

import pytest
from unittest.mock import AsyncMock, patch

import aiosqlite
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
    "end_lat": 47.4979,
    "end_lon": 19.0402,
    "start_time": "09:00",
    "end_time": "18:00",
    "date": "2026-04-15",
    "transport_mode": "foot",
}


@pytest.mark.asyncio
async def test_patch_transport_mode_updates_db(client):
    """PATCH transport_mode updates the trip in the database."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    assert resp.status_code == 201
    trip_id = resp.json()["id"]

    resp = await client.patch(f"/api/trips/{trip_id}", json={"transport_mode": "car"})
    assert resp.status_code == 200
    assert resp.json()["transport_mode"] == "car"

    # GET confirms persistence
    resp = await client.get(f"/api/trips/{trip_id}")
    assert resp.json()["transport_mode"] == "car"


@pytest.mark.asyncio
async def test_patch_transport_mode_invalidates_cache(client):
    """Switching transport mode clears the distance cache for that trip."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    # Add two places (mock OSRM to avoid real calls)
    mock_matrix = [[0, 300, 300], [300, 0, 300], [300, 300, 0]]
    with patch(
        "app.routers.places.get_distance_matrix",
        new_callable=AsyncMock,
        return_value=mock_matrix,
    ):
        for name in ["Place A", "Place B"]:
            resp = await client.post(
                f"/api/trips/{trip_id}/places",
                json={
                    "name": name,
                    "lat": 47.5,
                    "lon": 19.05,
                    "category": "landmark",
                },
            )
            assert resp.status_code == 201

    # Wait for background tasks to complete (distance caching)
    import asyncio

    await asyncio.sleep(0.5)

    # Verify cache has entries
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM distance_cache WHERE trip_id = ?", (trip_id,)
        )
        count = (await cursor.fetchone())[0]
        assert count > 0, "Cache should be populated after adding places"

    # Switch mode — cache should be cleared synchronously
    with patch(
        "app.routers.trips._recompute_distances_background", new_callable=AsyncMock
    ):
        resp = await client.patch(
            f"/api/trips/{trip_id}", json={"transport_mode": "car"}
        )
        assert resp.status_code == 200

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM distance_cache WHERE trip_id = ?", (trip_id,)
        )
        count = (await cursor.fetchone())[0]
        assert count == 0, "Cache should be empty after mode switch"


@pytest.mark.asyncio
async def test_feasibility_uses_new_mode_after_switch(client):
    """After switching to car, feasibility calls OSRM with 'car' profile."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    # Add a place
    mock_matrix = [[0, 300, 300], [300, 0, 300], [300, 300, 0]]
    with patch(
        "app.routers.places.get_distance_matrix",
        new_callable=AsyncMock,
        return_value=mock_matrix,
    ):
        await client.post(
            f"/api/trips/{trip_id}/places",
            json={
                "name": "Museum",
                "lat": 47.5,
                "lon": 19.05,
                "category": "museum",
            },
        )

    # Switch to car
    with patch(
        "app.routers.trips._recompute_distances_background", new_callable=AsyncMock
    ):
        await client.patch(f"/api/trips/{trip_id}", json={"transport_mode": "car"})

    # Call feasibility and verify OSRM is called with "car"
    with patch(
        "app.routers.feasibility.get_distance_matrix",
        new_callable=AsyncMock,
        return_value=mock_matrix,
    ) as mock_osrm:
        resp = await client.get(f"/api/trips/{trip_id}/feasibility?time=09:00")
        assert resp.status_code == 200
        mock_osrm.assert_called_once()
        # profile is the second positional arg
        call_args = mock_osrm.call_args
        assert call_args is not None
        assert call_args[0][1] == "car" or call_args.kwargs.get("profile") == "car"


@pytest.mark.asyncio
async def test_patch_without_mode_change_keeps_cache(client):
    """PATCHing non-mode fields does not invalidate distance cache."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    # Add a place
    mock_matrix = [[0, 300, 300], [300, 0, 300], [300, 300, 0]]
    with patch(
        "app.routers.places.get_distance_matrix",
        new_callable=AsyncMock,
        return_value=mock_matrix,
    ):
        await client.post(
            f"/api/trips/{trip_id}/places",
            json={
                "name": "Place A",
                "lat": 47.5,
                "lon": 19.05,
                "category": "landmark",
            },
        )
        await client.post(
            f"/api/trips/{trip_id}/places",
            json={
                "name": "Place B",
                "lat": 47.51,
                "lon": 19.06,
                "category": "landmark",
            },
        )

    import asyncio

    await asyncio.sleep(0.5)

    # Verify cache exists
    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM distance_cache WHERE trip_id = ?", (trip_id,)
        )
        count_before = (await cursor.fetchone())[0]

    # PATCH only end_time — should NOT clear cache
    resp = await client.patch(f"/api/trips/{trip_id}", json={"end_time": "20:00"})
    assert resp.status_code == 200

    async with aiosqlite.connect(settings.database_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM distance_cache WHERE trip_id = ?", (trip_id,)
        )
        count_after = (await cursor.fetchone())[0]
        assert count_after == count_before, "Cache should not change for non-mode PATCH"


@pytest.mark.asyncio
async def test_switch_mode_round_trip(client):
    """Switching foot -> car -> foot works correctly."""
    resp = await client.post("/api/trips", json=SAMPLE_TRIP)
    trip_id = resp.json()["id"]

    with patch(
        "app.routers.trips._recompute_distances_background", new_callable=AsyncMock
    ):
        resp = await client.patch(
            f"/api/trips/{trip_id}", json={"transport_mode": "car"}
        )
        assert resp.json()["transport_mode"] == "car"

        resp = await client.patch(
            f"/api/trips/{trip_id}", json={"transport_mode": "bicycle"}
        )
        assert resp.json()["transport_mode"] == "bicycle"

        resp = await client.patch(
            f"/api/trips/{trip_id}", json={"transport_mode": "foot"}
        )
        assert resp.json()["transport_mode"] == "foot"
