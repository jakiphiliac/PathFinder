"""
Slice 0 infrastructure tests.

Covers:
- SQLite DB connection
- Table creation (trips, places, distance_cache)
- OSRM connectivity (foot profile, self-hosted or public fallback)
"""

import pytest
import pytest_asyncio
import aiosqlite
import httpx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path):
    """Fresh temporary DB for each test."""
    test_db = str(tmp_path / "test.db")

    import app.config as cfg

    cfg.settings.database_path = test_db

    from app.db import init_db

    await init_db()

    conn = await aiosqlite.connect(test_db)
    conn.row_factory = aiosqlite.Row
    yield conn
    await conn.close()


# ---------------------------------------------------------------------------
# DB tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_connects(db):
    cursor = await db.execute("SELECT 1")
    row = await cursor.fetchone()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_trips_table_exists(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trips'"
    )
    row = await cursor.fetchone()
    assert row is not None, "trips table not found"


@pytest.mark.asyncio
async def test_places_table_exists(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='places'"
    )
    row = await cursor.fetchone()
    assert row is not None, "places table not found"


@pytest.mark.asyncio
async def test_distance_cache_table_exists(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='distance_cache'"
    )
    row = await cursor.fetchone()
    assert row is not None, "distance_cache table not found"


@pytest.mark.asyncio
async def test_trips_schema(db):
    """All expected columns are present."""
    cursor = await db.execute("PRAGMA table_info(trips)")
    columns = {row["name"] async for row in cursor}
    expected = {
        "id",
        "city",
        "start_lat",
        "start_lon",
        "end_lat",
        "end_lon",
        "start_time",
        "end_time",
        "date",
        "transport_mode",
        "created_at",
        "updated_at",
    }
    assert expected <= columns


@pytest.mark.asyncio
async def test_places_schema(db):
    cursor = await db.execute("PRAGMA table_info(places)")
    columns = {row["name"] async for row in cursor}
    expected = {
        "id",
        "trip_id",
        "name",
        "lat",
        "lon",
        "category",
        "priority",
        "estimated_duration_min",
        "opening_hours",
        "opening_hours_source",
        "status",
        "arrived_at",
        "departed_at",
        "created_at",
    }
    assert expected <= columns


@pytest.mark.asyncio
async def test_distance_cache_schema(db):
    cursor = await db.execute("PRAGMA table_info(distance_cache)")
    columns = {row["name"] async for row in cursor}
    expected = {"trip_id", "from_place_id", "to_place_id", "duration_seconds"}
    assert expected <= columns


@pytest.mark.asyncio
async def test_init_db_is_idempotent(db):
    """Running init_db twice should not raise."""
    from app.db import init_db

    await init_db()  # second call — should succeed silently


# ---------------------------------------------------------------------------
# OSRM connectivity test (skipped if OSRM is not running)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_osrm_foot_responds():
    """
    Checks that the configured foot OSRM endpoint is reachable.
    Skips if OSRM is not running — this is an integration test.
    """
    from app.config import settings

    url = (
        f"{settings.osrm_foot_url}/table/v1/foot/"
        "19.0402,47.4979;19.0534,47.5068"
        "?annotations=duration"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
        assert response.status_code == 200, f"OSRM returned {response.status_code}"
        data = response.json()
        assert data.get("code") == "Ok", f"OSRM code: {data.get('code')}"
        assert "durations" in data
    except (httpx.ConnectError, httpx.TimeoutException):
        pytest.skip("OSRM foot instance not running — skipping connectivity test")
