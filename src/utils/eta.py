"""
eta.py
------
Arrival estimation for live vehicles, derived from the published GTFS timetable.

The provider publishes vehicle positions only — no trip updates — so every
number here is computed locally and is an estimate. Accuracy is granular to
roughly one stop, which is the right resolution for a wait-or-walk decision.

Nothing in this module imports Streamlit or DuckDB, so all of it is directly
unit-testable.
"""

import math

EARTH_RADIUS_M = 6_371_000
DEFAULT_PACE_M_PER_MIN = 80     # ~4.8 km/h, an unhurried walk


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points, in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def nearest_stop_index(stops, lat, lon, start_index=0):
    """
    Index and distance of the stop nearest (lat, lon), searching from
    *start_index* onward.

    The start_index constraint is what keeps an answer ahead of a bus: a stop
    the vehicle has already passed may well be closer, and must not be chosen.
    Returns (-1, inf) when there is nothing to search.
    """
    best_index, best_distance = -1, math.inf
    for i in range(max(0, start_index), len(stops)):
        s = stops[i]
        d = haversine_m(lat, lon, s['stop_lat'], s['stop_lon'])
        if d < best_distance:
            best_index, best_distance = i, d
    return best_index, best_distance


def walking_minutes(distance_m, pace_m_per_min=DEFAULT_PACE_M_PER_MIN):
    """
    Approximate walking time in whole minutes, never less than 1.

    Straight-line distance at a fixed pace — not a routed walking path. The UI
    must present it as approximate.
    """
    return max(1, math.ceil(distance_m / pace_m_per_min))
