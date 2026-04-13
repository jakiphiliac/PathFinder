"""
Feasibility calculation utilities.

This module provides:
- calculate_feasibility(...): core per-place feasibility calculation used by
  the API endpoints and scoring engine.
- parse_closing_time(...): a lightweight parser that extracts a closing
  datetime for a given trip date from a simple OSM `opening_hours` fragment.

Notes:
- All datetimes used for arithmetic in this module are normalized to
  timezone-aware UTC datetimes. If callers pass naive datetimes we assume
  they are in UTC and tag them accordingly. This keeps arithmetic safe
  and avoids TypeError caused by mixing naive and aware datetimes.
- The OSM `opening_hours` format can be far more complex than handled here.
  This parser supports common simple cases (day ranges and a single time
  interval per rule). For higher fidelity consider using a dedicated
  opening_hours parser library.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from app.engine.category_defaults import get_duration_minutes

logger = logging.getLogger(__name__)


def _to_utc_aware(dt: datetime | None) -> datetime:
    """
    Ensure datetime is timezone-aware in UTC.

    - If `dt` already has tzinfo, convert to UTC.
    - If `dt` is naive, assume UTC and attach tzinfo=UTC.
    """
    if dt is None:
        raise ValueError("datetime value required")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def calculate_feasibility(
    place: dict[str, Any],
    travel_to_place_seconds: float,
    travel_to_endpoint_seconds: float,
    current_time: datetime,
    trip_end_time: datetime,
    trip_date: date,
    trip_timezone: str | None = None,
) -> dict[str, Any]:
    """
    Calculate feasibility for a single place.

    Args:
        place: dict with keys including: id, category, estimated_duration_min,
               opening_hours, status
        travel_to_place_seconds: travel time (seconds) from current pos -> place
        travel_to_endpoint_seconds: travel time (seconds) from place -> endpoint
        current_time: current datetime (may be naive or tz-aware)
        trip_end_time: trip end datetime (may be naive or tz-aware)
        trip_date: date of the trip (used when parsing opening hours)
        trip_timezone: optional IANA timezone name for the trip (e.g. "Europe/Budapest").
                       If provided, opening-hours will be interpreted in this timezone
                       and converted to UTC for internal arithmetic.

    Returns:
        dict with keys:
          - place_id
          - color: one of ("green","yellow","red","gray","unknown")
          - slack_minutes: float (may be negative)
          - closing_urgency_minutes: float | None
          - reason: human-readable explanation
    """
    # Normalize incoming datetimes to tz-aware UTC to avoid mixing naive/aware
    now_utc: datetime = _to_utc_aware(current_time)
    trip_end_utc: datetime = _to_utc_aware(trip_end_time)

    visit_duration_min: float = get_duration_minutes(
        place.get("category"), place.get("estimated_duration_min")
    )
    visit_duration_sec: int = int(visit_duration_min * 60)

    travel_to_sec: float = float(travel_to_place_seconds or 0)
    travel_back_sec: float = float(travel_to_endpoint_seconds or 0)

    arrival_at_place: datetime = now_utc + timedelta(seconds=travel_to_sec)
    departure_from_place: datetime = arrival_at_place + timedelta(
        seconds=visit_duration_sec
    )
    finish_time: datetime = departure_from_place + timedelta(seconds=travel_back_sec)

    # Slack calculations (seconds)
    slack_seconds: float = (trip_end_utc - finish_time).total_seconds()
    remaining_seconds: float = (trip_end_utc - now_utc).total_seconds()
    slack_ratio: float = (
        slack_seconds / remaining_seconds if remaining_seconds > 0 else 0.0
    )

    # Opening hours handling
    closing_time_utc: datetime | None = None
    closing_urgency_sec: float | None = None
    window_remaining_sec: float | None = None

    opening_hours: str | None = place.get("opening_hours")
    if opening_hours:
        # Pass trip_timezone through to parse_closing_time so the parser can
        # interpret the wall-clock close time in the trip's local timezone.
        parsed: datetime | None = parse_closing_time(
            opening_hours, trip_date, trip_timezone
        )
        if parsed:
            # parse_closing_time returns a timezone-aware UTC datetime; coerce defensively
            try:
                closing_time_utc = _to_utc_aware(parsed)
            except Exception:
                # If parse returned something odd, skip opening-hours logic
                logger.exception(
                    "parse_closing_time returned unparseable value; ignoring opening hours for place %s",
                    place.get("id"),
                )
                closing_time_utc = None

    if closing_time_utc:
        closing_urgency_sec = (closing_time_utc - arrival_at_place).total_seconds()
        window_remaining_sec = (closing_time_utc - now_utc).total_seconds()

    # Determine color and reason
    color: str
    reason: str
    if slack_seconds < 0:
        color = "gray"
        reason = "Not enough time to visit and reach endpoint"
    elif closing_time_utc and arrival_at_place > closing_time_utc:
        color = "gray"
        reason = f"Closed by the time you arrive ({arrival_at_place.strftime('%H:%M')})"
    elif (
        closing_time_utc
        and window_remaining_sec is not None
        and window_remaining_sec < 30 * 60
    ):
        color = "red"
        reason = f"Closes in {_format_duration(window_remaining_sec)}"
    elif slack_ratio < 0.10:
        color = "red"
        reason = "Very tight schedule"
    elif (
        closing_time_utc
        and window_remaining_sec is not None
        and window_remaining_sec < 2 * 60 * 60
    ):
        color = "yellow"
        reason = f"Closes in {_format_duration(window_remaining_sec)}"
    elif slack_ratio < 0.30:
        color = "yellow"
        reason = "Feasible but limited time"
    elif not opening_hours:
        color = "unknown"
        reason = "No opening hours data — time-feasible"
    else:
        color = "green"
        reason = "Plenty of time"

    return {
        "place_id": place.get("id"),
        "color": color,
        "slack_minutes": round(slack_seconds / 60, 1),
        "closing_urgency_minutes": (
            round(closing_urgency_sec / 60, 1)
            if closing_urgency_sec is not None
            else None
        ),
        "reason": reason,
    }


def parse_closing_time(
    opening_hours: str, trip_date: date, trip_timezone: str | None = None
) -> datetime | None:
    """
    Parse a closing time from a simple OSM opening_hours string for the given trip_date.

    Behavior:
    - Splits rules by ';' and scans each rule for a time interval like 'HH:MM-HH:MM'.
    - If a rule has a day specification (e.g. 'Mo-Fr'), it will be matched against trip_date.weekday().
    - If `trip_timezone` (IANA string) is provided, the parsed wall-clock closing time is interpreted
      in that timezone and converted to a UTC-aware datetime. If not provided, the parsed time
      is assumed to be in UTC (backwards-compatible behavior).

    Returns:
        timezone-aware datetime in UTC, or None if unparseable.

    Limitations:
    - This supports only a single interval per rule and simple day specs ('Mo', 'Tu', 'Mo-Fr', 'Sa,Su').
    - Complex expressions (exceptions, holidays, 24/7, overnight spans like 20:00-02:00) are not fully supported.
    """
    if not opening_hours:
        return None

    DAY_MAP: dict[str, int] = {
        "Mo": 0,
        "Tu": 1,
        "We": 2,
        "Th": 3,
        "Fr": 4,
        "Sa": 5,
        "Su": 6,
    }
    weekday: int = trip_date.weekday()

    # Resolve timezone object if provided
    tz: tzinfo = timezone.utc
    if trip_timezone:
        try:
            tz = ZoneInfo(trip_timezone)
        except Exception:
            logger.exception(
                "Invalid trip_timezone %r; falling back to UTC", trip_timezone
            )

    # Split alternative rules
    rules: list[str] = [r.strip() for r in opening_hours.split(";") if r.strip()]

    time_re = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")

    for rule in rules:
        # Find the time interval in the rule
        tm = time_re.search(rule)
        if not tm:
            continue

        close_str: str = tm.group(2)
        # The day part is everything before the time interval
        day_part: str = rule[: tm.start()].strip().rstrip(",").strip()

        matches_day: bool = False
        if day_part:
            # day_part may have comma-separated segments like "Mo,Tu" or range "Mo-Fr"
            segments: list[str] = [
                seg.strip() for seg in day_part.split(",") if seg.strip()
            ]
            for seg in segments:
                # try range like Mo-Fr
                rng = re.match(r"^([A-Za-z]{2})\s*-\s*([A-Za-z]{2})$", seg)
                if rng:
                    start: int | None = DAY_MAP.get(rng.group(1))
                    end: int | None = DAY_MAP.get(rng.group(2))
                    if start is None or end is None:
                        continue
                    if start <= end:
                        if start <= weekday <= end:
                            matches_day = True
                            break
                    else:
                        # wrapped range (e.g., Fr-Mo)
                        if weekday >= start or weekday <= end:
                            matches_day = True
                            break
                else:
                    # single day like 'Mo' or abbreviation
                    single: int | None = DAY_MAP.get(seg)
                    if single is not None and single == weekday:
                        matches_day = True
                        break
        else:
            # No day part means applies to all days
            matches_day = True

        if not matches_day:
            continue

        # Parse closing time as a localized datetime and convert to UTC
        try:
            h, m = close_str.split(":")
            closing_local: datetime = datetime.combine(trip_date, time(int(h), int(m)))
            # Attach the trip-local timezone and convert to UTC
            closing_with_tz: datetime = closing_local.replace(tzinfo=tz)
            closing_utc: datetime = closing_with_tz.astimezone(timezone.utc)
            return closing_utc
        except Exception:
            logger.exception(
                "Failed to parse close time %r for rule %r", close_str, rule
            )
            continue

    return None


def _format_duration(seconds: float) -> str:
    """Human-readable duration from seconds (minutes/hours)."""
    try:
        secs: int = int(seconds)
    except Exception:
        return "0 min"
    if secs < 60:
        return f"{secs} sec"
    minutes: int = secs // 60
    if minutes < 60:
        return f"{minutes} min"
    hours: int = minutes // 60
    mins: int = minutes % 60
    if mins:
        return f"{hours}h {mins}min"
    return f"{hours}h"
