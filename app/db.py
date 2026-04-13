"""SQLite schema, connection helper, and initialization."""

import os
from collections.abc import AsyncGenerator
from typing import Any

import aiosqlite

from app.config import settings

CREATE_TRIPS = """
CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    start_lat REAL NOT NULL,
    start_lon REAL NOT NULL,
    end_lat REAL NOT NULL,
    end_lon REAL NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    date TEXT NOT NULL,
    transport_mode TEXT NOT NULL DEFAULT 'foot',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CREATE_PLACES = """
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    category TEXT,
    priority TEXT NOT NULL DEFAULT 'want',
    estimated_duration_min INTEGER,
    opening_hours TEXT,
    opening_hours_source TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    arrived_at TEXT,
    departed_at TEXT,
    created_at TEXT NOT NULL
);
"""

CREATE_DISTANCE_CACHE = """
CREATE TABLE IF NOT EXISTS distance_cache (
    trip_id TEXT NOT NULL REFERENCES trips(id),
    from_place_id INTEGER NOT NULL,
    to_place_id INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    PRIMARY KEY (trip_id, from_place_id, to_place_id)
);
"""


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """FastAPI dependency that yields a DB connection and closes it on exit."""
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def _ensure_timezone_column(db: aiosqlite.Connection) -> None:
    """
    Ensure `timezone` column exists on `trips`. Adds it if missing.

    Works with either default tuple rows returned by PRAGMA or with Row mappings.
    """
    cursor = await db.execute("PRAGMA table_info(trips)")
    rows = await cursor.fetchall()

    col_names: list[Any] = []
    for row in rows:
        # PRAGMA table_info returns rows where column name is either accessible
        # by index 1 (tuple) or by key "name" (mapping). Handle both.
        try:
            name = row["name"]  # type: ignore[index]
        except Exception:
            name = row[1]  # type: ignore[index]
        col_names.append(name)

    if "timezone" not in col_names:
        await db.execute(
            "ALTER TABLE trips ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'"
        )


CREATE_TRAJECTORY_SEGMENTS = """
CREATE TABLE IF NOT EXISTS trajectory_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    from_lat REAL NOT NULL,
    from_lon REAL NOT NULL,
    to_lat REAL NOT NULL,
    to_lon REAL NOT NULL,
    place_id INTEGER,
    geometry TEXT NOT NULL DEFAULT '',
    distance_meters REAL NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


async def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every startup.

    Performs a light, backward-compatible migration: if an existing `trips`
    table lacks a `timezone` column, the column will be added with default 'UTC'.
    """
    os.makedirs(os.path.dirname(os.path.abspath(settings.database_path)), exist_ok=True)
    async with aiosqlite.connect(settings.database_path) as db:
        # Use Row mapping for convenience when inspecting PRAGMA results.
        db.row_factory = aiosqlite.Row

        # Ensure base tables exist (CREATE TABLE IF NOT EXISTS)
        await db.execute(CREATE_TRIPS)

        # If an older DB existed without the timezone column, add it.
        # _ensure_timezone_column inspects PRAGMA table_info and executes ALTER TABLE if needed.
        await _ensure_timezone_column(db)

        await db.execute(CREATE_PLACES)
        await db.execute(CREATE_DISTANCE_CACHE)
        await db.execute(CREATE_TRAJECTORY_SEGMENTS)

        # Indexes for performance
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_places_trip_id ON places(trip_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_distance_cache_trip_id ON distance_cache(trip_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_trajectory_trip_id ON trajectory_segments(trip_id)"
        )

        await db.commit()
