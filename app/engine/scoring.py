"""Opportunity-cost scoring for 'What Next?' recommendations."""

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from app.engine.category_defaults import get_duration_minutes
from app.engine.feasibility import calculate_feasibility

logger = logging.getLogger(__name__)

PRIORITY_WEIGHTS: dict[str, float] = {"must": 1.0, "want": 0.5, "if_time": 0.2}


__all__ = ["score_next_actions"]


def _is_feasible(place: dict[str, Any], feas: dict[str, Any]) -> bool:
    """A place is feasible if its color is not gray."""
    return feas.get("color") != "gray"


def score_next_actions(
    places: list[dict[str, Any]],
    matrix: Sequence[Sequence[float]],
    current_time: datetime,
    trip_end_time: datetime,
    trip_date: date,
    endpoint_idx: int,
    precomputed_feasibility: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Score pending places using opportunity-cost algorithm.

    This function optionally accepts a precomputed_feasibility mapping (place_id -> feasibility dict)
    so callers that already computed feasibility (e.g. the /next endpoint) can avoid duplicate work.

    Args:
        places: list of place dicts (index i corresponds to matrix row/col i+1)
        matrix: OSRM distance matrix. Index 0 = current position, 1..N = places, N+1 = endpoint
        current_time: current datetime
        trip_end_time: trip end datetime
        trip_date: date of the trip
        endpoint_idx: index of endpoint in matrix
        precomputed_feasibility: optional mapping from place_id to feasibility result

    Returns:
        Top 3 recommendations sorted by score descending.
    """
    if not places:
        return []

    # Step 1: compute or reuse feasibility for each place from current position
    feasibility: dict[int, dict[str, Any]] = {}

    # Use provided precomputed feasibility where available
    if precomputed_feasibility:
        # copy to avoid mutating caller's dict
        feasibility.update(precomputed_feasibility)

    # Compute feasibility for any places missing from the provided map
    for i, place in enumerate(places):
        pid: int = place["id"]
        if pid in feasibility:
            continue
        idx: int = i + 1
        feas: dict[str, Any] = calculate_feasibility(
            place=place,
            travel_to_place_seconds=matrix[0][idx],
            travel_to_endpoint_seconds=matrix[idx][endpoint_idx],
            current_time=current_time,
            trip_end_time=trip_end_time,
            trip_date=trip_date,
        )
        feasibility[pid] = feas

    # Step 2: filter to feasible candidates only
    feasible: list[tuple[int, dict[str, Any]]] = [
        (i, p)
        for i, p in enumerate(places)
        if _is_feasible(p, feasibility.get(p["id"], {}))
    ]

    if not feasible:
        # No feasible places from current position/time
        return []

    # Step 3: compute opportunity cost for each feasible candidate
    scores: list[dict[str, Any]] = []
    max_travel: float = max(matrix[0][i + 1] for i, _ in feasible) if feasible else 1

    for idx, place in feasible:
        place_idx: int = idx + 1
        travel_to_place: float = matrix[0][place_idx]

        # Opportunity cost: how many other feasible places become unreachable
        # if we visit each other place first?
        cost: int = 0
        for other_idx, other in feasible:
            if other["id"] == place["id"]:
                continue
            other_place_idx: int = other_idx + 1
            other_visit_sec: float = (
                get_duration_minutes(
                    other.get("category"), other.get("estimated_duration_min")
                )
                * 60
            )

            # Simulate visiting 'other' first
            time_after_other: datetime = current_time + timedelta(
                seconds=matrix[0][other_place_idx] + other_visit_sec
            )
            # Then check: can we still reach 'place' from 'other' and make it to endpoint?
            travel_other_to_place: float = matrix[other_place_idx][place_idx]
            travel_place_to_endpoint: float = matrix[place_idx][endpoint_idx]

            simulated_feas: dict[str, Any] = calculate_feasibility(
                place=place,
                travel_to_place_seconds=travel_other_to_place,
                travel_to_endpoint_seconds=travel_place_to_endpoint,
                current_time=time_after_other,
                trip_end_time=trip_end_time,
                trip_date=trip_date,
            )
            if not _is_feasible(place, simulated_feas):
                cost += 1

        # Normalize scores to 0-1
        opportunity_score: float = cost / max(len(feasible) - 1, 1)
        proximity_score: float = (
            1 - (travel_to_place / max_travel) if max_travel > 0 else 1
        )
        priority_score: float = PRIORITY_WEIGHTS.get(place.get("priority", "want"), 0.5)

        total: float = (
            0.40 * opportunity_score + 0.30 * proximity_score + 0.30 * priority_score
        )

        # Human-readable reason
        reasons: list[str] = []
        if opportunity_score > 0.5:
            reasons.append(f"high risk of becoming unreachable ({cost} conflicts)")
        if place.get("priority") == "must":
            reasons.append("must-visit")
        if proximity_score > 0.8:
            reasons.append(f"nearby ({int(travel_to_place / 60)} min)")
        feas = feasibility.get(place["id"], {})
        if feas.get("closing_urgency_minutes") is not None:
            closing_min: float = feas["closing_urgency_minutes"]
            if closing_min < 120:
                reasons.append(f"closes in {int(closing_min)} min")

        scores.append(
            {
                "place_id": place["id"],
                "place_name": place.get("name", ""),
                "score": round(total, 3),
                "opportunity_cost": cost,
                "travel_minutes": round(travel_to_place / 60, 1),
                "reason": " — ".join(reasons) if reasons else "good option",
            }
        )

    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:3]
