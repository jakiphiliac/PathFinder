"""Unit tests for opening hours parsing utilities."""

import datetime

import pytest

from app.services.opening_hours_utils import (
    DEFAULT_WINDOW,
    parse_to_time_window,
    parse_user_override,
)


def test_parse_weekday_hours():
    """Parse 'Mo-Fr 09:00-17:00' on a Friday."""
    friday = datetime.date(2026, 3, 13)  # Friday
    result = parse_to_time_window("Mo-Fr 09:00-17:00", friday)
    assert result == (9 * 3600, 17 * 3600)


def test_parse_weekend_hours():
    """Parse 'Mo-Fr 09:00-17:00; Sa 10:00-14:00' on Saturday."""
    saturday = datetime.date(2026, 3, 14)
    result = parse_to_time_window("Mo-Fr 09:00-17:00; Sa 10:00-14:00", saturday)
    assert result == (10 * 3600, 14 * 3600)


def test_parse_closed_day():
    """Place closed on Sunday returns None."""
    sunday = datetime.date(2026, 3, 15)
    result = parse_to_time_window("Mo-Fr 09:00-17:00", sunday)
    assert result is None


def test_parse_24_7():
    """24/7 returns (0, 86400)."""
    result = parse_to_time_window("24/7", datetime.date(2026, 3, 13))
    assert result == (0.0, 86400.0)


def test_parse_empty_string():
    """Empty string returns None."""
    assert parse_to_time_window("") is None
    assert parse_to_time_window("  ") is None


def test_parse_invalid_string():
    """Unparseable string returns None."""
    result = parse_to_time_window("not valid hours", datetime.date(2026, 3, 13))
    assert result is None


def test_parse_multiple_periods():
    """Multiple periods on same day: span from earliest open to latest close."""
    # Open 09:00-12:00 and 14:00-18:00
    friday = datetime.date(2026, 3, 13)
    result = parse_to_time_window("Mo-Fr 09:00-12:00, 14:00-18:00", friday)
    assert result is not None
    assert result[0] == 9 * 3600  # earliest opening
    assert result[1] == 18 * 3600  # latest closing


def test_parse_defaults_to_today():
    """If no target_date given, uses today."""
    result = parse_to_time_window("Mo-Su 08:00-22:00")
    assert result is not None
    assert result == (8 * 3600, 22 * 3600)


def test_user_override_valid():
    """User override '09:00' to '17:00' returns correct seconds."""
    result = parse_user_override("09:00", "17:00")
    assert result == (9 * 3600, 17 * 3600)


def test_user_override_single_digit():
    """Single digit hour '9:00' works."""
    result = parse_user_override("9:00", "17:30")
    assert result == (9 * 3600, 17 * 3600 + 30 * 60)


def test_user_override_invalid():
    """Invalid time format raises ValueError."""
    with pytest.raises(ValueError):
        parse_user_override("abc", "17:00")


def test_default_window():
    """DEFAULT_WINDOW is a full day."""
    assert DEFAULT_WINDOW == (0.0, 86400.0)
