"""Slice 4 tests: SSE Urgency Alerts."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

import httpx
from httpx import ASGITransport

from app.main import app
from app.db import init_db
from app.config import settings
from app.routers.stream import _detect_alerts
from app.models import UrgencyAlert


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_trip_with_places(client, places_data=None):
    """Create a trip and add places, return (trip_id, place_ids)."""
    trip_payload = {
        "city": "Budapest",
        "start_lat": 47.4979,
        "start_lon": 19.0402,
        "end_lat": 47.5,
        "end_lon": 19.05,
        "start_time": "09:00",
        "end_time": "18:00",
        "date": "2026-04-15",
        "transport_mode": "foot",
    }
    res = await client.post("/api/trips", json=trip_payload)
    assert res.status_code in (200, 201)
    trip_id = res.json()["id"]

    if places_data is None:
        places_data = [
            {"name": "Museum A", "lat": 47.498, "lon": 19.041, "category": "museum"},
        ]

    place_ids = []
    for p in places_data:
        res = await client.post(f"/api/trips/{trip_id}/places", json=p)
        assert res.status_code in (200, 201)
        place_ids.append(res.json()["id"])

    return trip_id, place_ids


def _parse_sse_events(text: str) -> list[dict]:
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
    # Handle trailing event without final blank line
    if current and "data" in current:
        events.append(current)
    return events


# ---------------------------------------------------------------------------
# Unit tests: _detect_alerts
# ---------------------------------------------------------------------------


def test_detect_alerts_color_degradation():
    """Color going from green to yellow produces a warning alert."""
    results = [
        {
            "place_id": 1,
            "color": "yellow",
            "closing_urgency_minutes": 55,
            "slack_minutes": 10,
        },
    ]
    last_colors = {1: "green"}
    place_names = {1: "Museum A"}
    place_priorities = {1: "want"}

    alerts = _detect_alerts(results, last_colors, place_names, place_priorities)
    assert len(alerts) >= 1
    assert alerts[0].severity == "warning"
    assert alerts[0].place_id == 1


def test_detect_alerts_to_red_is_critical():
    """Color going from yellow to red produces a critical alert."""
    results = [
        {
            "place_id": 1,
            "color": "red",
            "closing_urgency_minutes": 25,
            "slack_minutes": 2,
        },
    ]
    last_colors = {1: "yellow"}
    place_names = {1: "Museum A"}
    place_priorities = {1: "want"}

    alerts = _detect_alerts(results, last_colors, place_names, place_priorities)
    assert len(alerts) >= 1
    assert alerts[0].severity == "critical"


def test_detect_alerts_to_gray_is_critical():
    """Color going to gray (unreachable) is always critical."""
    results = [
        {
            "place_id": 1,
            "color": "gray",
            "closing_urgency_minutes": None,
            "slack_minutes": -10,
        },
    ]
    last_colors = {1: "yellow"}
    place_names = {1: "Museum A"}
    place_priorities = {1: "want"}

    alerts = _detect_alerts(results, last_colors, place_names, place_priorities)
    assert len(alerts) >= 1
    assert alerts[0].severity == "critical"
    assert "no longer reachable" in alerts[0].message


def test_detect_alerts_no_change_no_alert():
    """Same color as before produces no degradation alert."""
    results = [
        {
            "place_id": 1,
            "color": "green",
            "closing_urgency_minutes": 120,
            "slack_minutes": 60,
        },
    ]
    last_colors = {1: "green"}
    place_names = {1: "Museum A"}
    place_priorities = {1: "want"}

    alerts = _detect_alerts(results, last_colors, place_names, place_priorities)
    assert len(alerts) == 0


def test_detect_alerts_must_visit_closing_soon():
    """Must-visit place closing in < 30 min triggers critical alert."""
    results = [
        {
            "place_id": 1,
            "color": "red",
            "closing_urgency_minutes": 25,
            "slack_minutes": 5,
        },
    ]
    last_colors = {1: "red"}  # No color change
    place_names = {1: "Cathedral"}
    place_priorities = {1: "must"}

    alerts = _detect_alerts(results, last_colors, place_names, place_priorities)
    assert len(alerts) >= 1
    critical = [a for a in alerts if a.severity == "critical"]
    assert len(critical) >= 1
    assert "must-visit" in critical[0].message


def test_detect_alerts_must_visit_closing_within_hour():
    """Must-visit place closing in < 60 min triggers warning alert."""
    results = [
        {
            "place_id": 1,
            "color": "yellow",
            "closing_urgency_minutes": 45,
            "slack_minutes": 15,
        },
    ]
    last_colors = {1: "yellow"}  # No color change
    place_names = {1: "Cathedral"}
    place_priorities = {1: "must"}

    alerts = _detect_alerts(results, last_colors, place_names, place_priorities)
    assert len(alerts) >= 1
    warning = [a for a in alerts if a.severity == "warning"]
    assert len(warning) >= 1


def test_detect_alerts_first_tick_no_degradation():
    """First tick (empty last_colors) should not trigger degradation alerts."""
    results = [
        {
            "place_id": 1,
            "color": "yellow",
            "closing_urgency_minutes": 55,
            "slack_minutes": 10,
        },
    ]
    last_colors = {}  # First tick
    place_names = {1: "Museum A"}
    place_priorities = {1: "want"}

    alerts = _detect_alerts(results, last_colors, place_names, place_priorities)
    # No degradation alert (no previous color to compare)
    degradation_alerts = [a for a in alerts if "must-visit" not in a.message]
    assert len(degradation_alerts) == 0


# ---------------------------------------------------------------------------
# Integration tests: SSE endpoint
# ---------------------------------------------------------------------------


# Fake distance matrix for mocking OSRM
MOCK_MATRIX = [
    [0, 600, 600],
    [600, 0, 600],
    [600, 600, 0],
]


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    test_db = str(tmp_path / "test.db")
    settings.database_path = test_db
    await init_db()
    yield


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
@patch("app.routers.stream.compute_feasibility", new_callable=AsyncMock)
async def test_stream_initial_feasibility(mock_compute, async_client):
    """SSE stream should send a feasibility_update event on connect."""
    from app.models import FeasibilityResponse, FeasibilityResult

    # Setup trip (no OSRM needed since we mock compute_feasibility)
    trip_payload = {
        "city": "Budapest",
        "start_lat": 47.4979,
        "start_lon": 19.0402,
        "end_lat": 47.5,
        "end_lon": 19.05,
        "start_time": "09:00",
        "end_time": "18:00",
        "date": "2026-04-15",
        "transport_mode": "foot",
    }
    res = await async_client.post("/api/trips", json=trip_payload)
    trip_id = res.json()["id"]

    from app.routers.feasibility import FeasibilityContext
    from datetime import date, datetime, timezone

    mock_response = FeasibilityResponse(
        current_time="2026-04-15T09:00:00",
        remaining_minutes=540.0,
        places=[
            FeasibilityResult(
                place_id=1,
                color="green",
                slack_minutes=60.0,
                closing_urgency_minutes=120.0,
                reason="plenty of time",
            )
        ],
    )
    mock_ctx = FeasibilityContext(
        places=[],
        matrix=[],
        current_time=datetime(2026, 4, 15, 9, 0, tzinfo=timezone.utc),
        trip_end_dt=datetime(2026, 4, 15, 18, 0, tzinfo=timezone.utc),
        trip_date=date(2026, 4, 15),
        endpoint_idx=0,
        place_names={1: "Museum A"},
        place_priorities={1: "want"},
    )
    mock_compute.return_value = (mock_response, mock_ctx)

    # Use a regular GET request (not streaming) with a short timeout.
    # sse-starlette with httpx ASGI transport delivers the full body on close.
    call_count = 0

    async def limited_sleep(duration):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            # Raise CancelledError to break the SSE loop after 2 iterations
            raise asyncio.CancelledError()
        # Don't actually wait
        return

    with patch("app.routers.stream.asyncio.sleep", side_effect=limited_sleep):
        res = await async_client.get(
            f"/api/trips/{trip_id}/stream?lat=47.4979&lon=19.0402"
        )

    text = res.text
    events = _parse_sse_events(text)
    assert len(events) >= 1
    assert events[0]["event"] == "feasibility_update"
    data = json.loads(events[0]["data"])
    assert "places" in data
    assert data["places"][0]["color"] == "green"


@pytest.mark.asyncio
async def test_urgency_alert_model():
    """UrgencyAlert model serializes correctly."""
    alert = UrgencyAlert(
        place_id=1,
        place_name="Museum A",
        message="closes in 30 min",
        severity="critical",
    )
    d = alert.model_dump()
    assert d["severity"] == "critical"
    assert d["place_id"] == 1

    j = alert.model_dump_json()
    parsed = json.loads(j)
    assert parsed["place_name"] == "Museum A"
