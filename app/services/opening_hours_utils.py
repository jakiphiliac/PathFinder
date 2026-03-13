"""
Opening hours utilities — parse OSM opening_hours strings into time windows.

Converts opening_hours strings like "Mo-Fr 09:00-17:00" into (earliest, latest)
tuples in seconds from midnight, suitable for the TSPTW solver.
"""

import datetime

import humanized_opening_hours as hoh


def parse_to_time_window(
    opening_hours_str: str,
    target_date: datetime.date | None = None,
) -> tuple[float, float] | None:
    """
    Parse an OSM opening_hours string into a time window for a specific date.

    Args:
        opening_hours_str: OSM opening_hours string, e.g. "Mo-Fr 09:00-17:00".
        target_date: The date to get hours for. Defaults to today.

    Returns:
        (earliest_seconds, latest_seconds) from midnight, or None if closed.
        For places open 24/7, returns (0, 86400).
        If multiple periods exist on the same day, returns the span covering
        the first opening to the last closing.
    """
    if not opening_hours_str or not opening_hours_str.strip():
        return None

    if target_date is None:
        target_date = datetime.date.today()

    try:
        oh = hoh.OHParser(opening_hours_str)
    except Exception:
        # Unparseable string — caller should fall back to default
        return None

    try:
        day = oh.get_day(target_date)
    except Exception:
        return None

    if not day.opens_today():
        return None

    # Check for 24/7
    if day.is_always_open():
        return (0.0, 86400.0)

    periods = day.periods
    if not periods:
        return None

    # Get the earliest opening and latest closing across all periods
    earliest_secs = float("inf")
    latest_secs = 0.0

    for period in periods:
        try:
            begin_time = period.beginning.time()
            end_time = period.end.time()

            begin_secs = begin_time.hour * 3600 + begin_time.minute * 60
            end_secs = end_time.hour * 3600 + end_time.minute * 60

            # Handle midnight or near-midnight closing
            if end_secs == 0 or end_secs >= 86340:  # 23:59 or later
                end_secs = 86400.0

            # Handle overnight periods (e.g., 22:00-02:00)
            if end_secs <= begin_secs:
                end_secs = 86400.0

            earliest_secs = min(earliest_secs, begin_secs)
            latest_secs = max(latest_secs, end_secs)
        except Exception:
            continue

    if earliest_secs == float("inf"):
        return None

    return (float(earliest_secs), float(latest_secs))


def parse_user_override(earliest_str: str, latest_str: str) -> tuple[float, float]:
    """
    Parse user-provided time strings like "09:00" and "17:00" to seconds.

    Args:
        earliest_str: Opening time, e.g. "09:00" or "9:00".
        latest_str: Closing time, e.g. "17:00".

    Returns:
        (earliest_seconds, latest_seconds) from midnight.

    Raises:
        ValueError: If the time strings are invalid.
    """
    earliest_secs = _time_str_to_seconds(earliest_str)
    latest_secs = _time_str_to_seconds(latest_str)
    return (earliest_secs, latest_secs)


def _time_str_to_seconds(time_str: str) -> float:
    """Convert "HH:MM" to seconds from midnight."""
    time_str = time_str.strip()
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: '{time_str}'. Expected HH:MM.")
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid time format: '{time_str}'. Expected HH:MM.")
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Time out of range: '{time_str}'.")
    return float(hours * 3600 + minutes * 60)


# Default time window for places without opening hours data.
DEFAULT_WINDOW = (0.0, 86400.0)
