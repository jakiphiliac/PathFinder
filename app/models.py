"""Pydantic request/response models for the PathFinder API."""

from typing import Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Trip
# ---------------------------------------------------------------------------


class TripCreate(BaseModel):
    city: str
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    start_time: str  # "09:00"
    end_time: str  # "18:00"
    date: str  # "2026-04-15"
    transport_mode: Literal["foot", "car", "bicycle"] = "foot"
    timezone: str | None = "UTC"


class TripUpdate(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    transport_mode: Literal["foot", "car", "bicycle"] | None = None
    timezone: str | None = None


class TripResponse(BaseModel):
    id: str
    city: str
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    start_time: str
    end_time: str
    date: str
    transport_mode: str
    timezone: str | None = "UTC"
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Place
# ---------------------------------------------------------------------------


class PlaceAdd(BaseModel):
    name: str
    lat: float
    lon: float
    category: str | None = None
    priority: Literal["must", "want", "if_time"] = "want"
    estimated_duration_min: int | None = None
    opening_hours: str | None = None  # user-supplied override
    opening_hours_source: str | None = None


class PlaceUpdate(BaseModel):
    priority: Literal["must", "want", "if_time"] | None = None
    estimated_duration_min: int | None = None
    opening_hours: str | None = None
    opening_hours_source: str | None = None


class PlaceResponse(BaseModel):
    id: int
    trip_id: str
    name: str
    lat: float
    lon: float
    category: str | None
    priority: str
    estimated_duration_min: int | None
    opening_hours: str | None
    opening_hours_source: str | None
    status: str
    arrived_at: str | None
    departed_at: str | None
    created_at: str


class TripDetailResponse(TripResponse):
    places: list[PlaceResponse] = []


class TripCreatedResponse(BaseModel):
    id: str
    url: str


# ---------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------


class FeasibilityResult(BaseModel):
    place_id: int
    color: Literal["green", "yellow", "red", "gray", "unknown"]
    slack_minutes: float
    closing_urgency_minutes: float | None
    reason: str


class FeasibilityResponse(BaseModel):
    current_time: str
    remaining_minutes: float
    places: list[FeasibilityResult]


class UrgencyAlert(BaseModel):
    place_id: int
    place_name: str
    message: str
    severity: Literal["warning", "critical"]


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------


class CheckinRequest(BaseModel):
    place_id: int
    action: Literal["arrived", "done", "skipped"]


class CheckinResponse(BaseModel):
    place_id: int
    status: str
    arrived_at: str | None
    departed_at: str | None
    message: str


# ---------------------------------------------------------------------------
# Next recommendation
# ---------------------------------------------------------------------------


class NextRecommendation(BaseModel):
    place_id: int
    place_name: str
    score: float
    opportunity_cost: int
    travel_minutes: float
    reason: str


class NextResponse(BaseModel):
    recommendations: list[NextRecommendation]
    message: str | None = None


# ---------------------------------------------------------------------------
# Baseline route
# ---------------------------------------------------------------------------


class BaselineNNResult(BaseModel):
    tour: list[int]
    cost: float
    coords: list[list[float]]


class BaselineSwapEvent(BaseModel):
    i: int
    j: int
    tour: list[int]
    cost: float
    accepted: bool
    coords: list[list[float]]


class BaselineRoadSegment(BaseModel):
    from_idx: int
    to_idx: int
    geometry: str  # encoded polyline (or empty for straight-line fallback)


class BaselineDone(BaseModel):
    tour: list[int]
    cost: float
