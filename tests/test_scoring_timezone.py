from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.engine.scoring import score_next_actions


def test_score_passes_trip_timezone_to_simulations():
    """
    Ensure `score_next_actions` propagates the `trip_timezone` argument into the
    internal `calculate_feasibility` calls used for opportunity-cost simulations.

    This test patches the `calculate_feasibility` symbol that `scoring` uses
    and verifies that at least one invocation received the `trip_timezone`
    keyword argument with the value we supplied.
    """
    places = [
        {
            "id": 1,
            "name": "Place A",
            "category": "museum",
            "priority": "want",
            "estimated_duration_min": 60,
        },
        {
            "id": 2,
            "name": "Place B",
            "category": "cafe",
            "priority": "must",
            "estimated_duration_min": 30,
        },
    ]

    # Matrix shape: index 0 = current position, 1..N = places, last = endpoint
    # Use simple numeric durations (seconds). The scorer only needs the shape/values.
    matrix = [
        [0, 600, 1200, 1800],  # from current to current/A/B/endpoint
        [600, 0, 300, 1200],  # from A
        [1200, 300, 0, 600],  # from B
        [1800, 1200, 600, 0],  # endpoint
    ]

    current_time = datetime.now(timezone.utc)
    trip_end_time = current_time + timedelta(hours=4)
    trip_date = date.today()
    endpoint_idx = 3
    tz = "Europe/Budapest"

    # Patch the calculate_feasibility used by the scoring module
    with patch("app.engine.scoring.calculate_feasibility") as mock_calc:
        # Return a valid-feeling feasibility object for all calls
        mock_calc.return_value = {
            "place_id": 1,
            "color": "green",
            "slack_minutes": 120.0,
            "closing_urgency_minutes": None,
            "reason": "OK",
        }

        # Call the scorer with trip_timezone supplied
        recommendations = score_next_actions(
            places=places,
            matrix=matrix,
            current_time=current_time,
            trip_end_time=trip_end_time,
            trip_date=trip_date,
            endpoint_idx=endpoint_idx,
            trip_timezone=tz,
            precomputed_feasibility=None,
        )

        # Basic sanity: function returns a list (possibly empty)
        assert isinstance(recommendations, list)

        # Ensure at least one call to calculate_feasibility included the trip_timezone kwarg
        called_with_tz = any(
            ("trip_timezone" in kwargs and kwargs["trip_timezone"] == tz)
            for _, kwargs in mock_calc.call_args_list
        )

        assert called_with_tz, (
            "score_next_actions did not pass trip_timezone to calculate_feasibility"
        )
