"""Pydantic request/response models for the PathFinder API."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# ---------------------------------------------------------------------------
# Trip
# ---------------------------------------------------------------------------


class TripCreate(BaseModel):
    city: str
    start_lat: float = Field(ge=-90, le=90)
    start_lon: float = Field(ge=-180, le=180)
    end_lat: float = Field(ge=-90, le=90)
    end_lon: float = Field(ge=-180, le=180)
    start_time: str | None = None  # "09:00" — defaults to current time
    end_time: str  # "18:00"
    date: str | None = None  # "2026-04-15" — defaults to today
    transport_mode: Literal["foot", "car", "bicycle"] = "foot"
    timezone: str | None = "UTC"

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_format(cls, v: str | None) -> str | None:
        if v is not None and not _TIME_RE.match(v):
            raise ValueError("time must be in HH:MM format (00:00–23:59)")
        return v


class TripUpdate(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    transport_mode: Literal["foot", "car", "bicycle"] | None = None
    timezone: str | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_format(cls, v: str | None) -> str | None:
        if v is not None and not _TIME_RE.match(v):
            raise ValueError("time must be in HH:MM format (00:00–23:59)")
        return v


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
    trajectory_segment: "TrajectorySegment | None" = None


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
# Trajectory
# ---------------------------------------------------------------------------


class TrajectorySegment(BaseModel):
    id: int
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    place_id: int | None
    geometry: str
    distance_meters: float
    duration_seconds: float
    created_at: str


class TrajectoryResponse(BaseModel):
    segments: list[TrajectorySegment]
