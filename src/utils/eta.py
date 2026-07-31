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


def service_day_epoch(vehicle_timestamp, utc_offset_hours):
    """
    Epoch seconds of local midnight for the service day containing *vehicle_timestamp*.

    Derived from the vehicle's own timestamp rather than read from the feed —
    ingestion does not currently capture the trip descriptor's startDate. A trip
    that began before midnight and runs past it therefore resolves against the
    following service day and can produce a wrong arrival. Accepted for now and
    recorded as a follow-up.
    """
    offset = int(utc_offset_hours) * 3600
    local = int(vehicle_timestamp) + offset
    return (local // 86400) * 86400 - offset


def estimate_delay_seconds(stops, bus_index, bus_timestamp, day_epoch):
    """
    How late the bus is, in seconds. Positive means late, negative means early.

    Compares when the vehicle actually reported near *bus_index* against when
    the timetable says it should have been there. Returns 0 when the index is
    out of range, so a caller with no fix on the bus degrades to "on schedule"
    rather than inventing a delay.
    """
    if not (0 <= bus_index < len(stops)):
        return 0
    scheduled = day_epoch + stops[bus_index]['arrival_seconds']
    return int(int(bus_timestamp) - scheduled)


def compute_eta_seconds(stops, target_index, delay_seconds, now_epoch, day_epoch):
    """
    Seconds until the bus reaches *target_index*. Negative means already passed.

    The timetable supplies the travel time; the measured delay shifts it. Both
    are integers of seconds since the service-day epoch, so a stop scheduled at
    25:30:00 resolves to 01:30 the next day rather than wrapping backwards.

    Returns None when *target_index* is out of range — there is no such stop
    to track. A negative int means the stop exists and the bus already passed
    it. The two must stay distinguishable: collapsing "no such stop" into -1
    would be indistinguishable from "passed one second ago", which matters
    because callers commonly chain this straight off nearest_stop_index's own
    -1 "nothing found" sentinel.
    """
    if not (0 <= target_index < len(stops)):
        return None
    scheduled = day_epoch + stops[target_index]['arrival_seconds']
    return int(scheduled + delay_seconds - now_epoch)


def arrivals_for_stops(vehicles, nearby_stops, trip_stops_lookup, now_epoch,
                       utc_offset_hours, headsign_lookup=None):
    """
    Which of *vehicles* are still to reach each of *nearby_stops*, and when.

    Returns (arrivals, skipped):
      arrivals  {stop_id: [ {vehicle_id, route_display, headsign, eta_seconds,
                             delay_seconds, age_seconds}, ... ]} soonest first
      skipped   {'no_trip_id': int, 'trip_not_in_schedule': int}

    The skipped counts exist so the UI can say why a bus is missing. A vehicle
    silently dropped is indistinguishable from one that is not coming.
    """
    arrivals = {s['stop_id']: [] for s in nearby_stops}
    skipped = {'no_trip_id': 0, 'trip_not_in_schedule': 0}
    wanted = {s['stop_id'] for s in nearby_stops}

    for v in vehicles:
        trip_id = (v.get('trip_id') or '').strip()
        if not trip_id:
            skipped['no_trip_id'] += 1
            continue

        stops = trip_stops_lookup(trip_id)
        if not stops:
            skipped['trip_not_in_schedule'] += 1
            continue

        try:
            timestamp = int(v['timestamp'])
        except (KeyError, TypeError, ValueError):
            skipped['trip_not_in_schedule'] += 1
            continue

        day = service_day_epoch(timestamp, utc_offset_hours)
        bus_index, _ = nearest_stop_index(stops, v['latitude'], v['longitude'])
        if bus_index < 0:
            continue
        delay = estimate_delay_seconds(stops, bus_index, timestamp, day)

        # Only stops the bus has yet to reach; a closer stop behind it is not
        # an arrival, it is history.
        for i in range(bus_index + 1, len(stops)):
            sid = stops[i]['stop_id']
            if sid not in wanted:
                continue
            secs = compute_eta_seconds(stops, i, delay, now_epoch, day)
            # None means "no such stop"; a negative int means "already passed".
            # Both are excluded, but they are different facts and must not be
            # compared with `<` against each other.
            if secs is None or secs < 0:
                continue
            arrivals[sid].append({
                'vehicle_id': v.get('vehicle_id', ''),
                'route_display': v.get('route_display', ''),
                'headsign': headsign_lookup(trip_id) if headsign_lookup else '',
                'eta_seconds': secs,
                'delay_seconds': delay,
                'age_seconds': v.get('age_seconds'),
            })

    for sid in arrivals:
        arrivals[sid].sort(key=lambda a: a['eta_seconds'])
    return arrivals, skipped
