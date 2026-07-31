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
    following service day.

    What that corrupts is `estimate_delay_seconds`, not the arrival. The epoch
    cancels between the two functions -- `compute_eta_seconds` only ever sees
    the *difference* between two stop times -- so the ETA is unaffected. The
    delay is not: a bus at 00:30 on a trip that started at 23:50 has its
    timestamp compared against the *following* midnight, so a true +40 min
    reads as roughly -1,400 min. The UI's "late" threshold then suppresses it
    and a genuinely late bus is shown as on time. Accepted for now and recorded
    as a follow-up; ingesting startDate removes it entirely.
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
                       utc_offset_hours, headsign_lookup=None,
                       frequency_lookup=None):
    """
    Which of *vehicles* are still to reach each of *nearby_stops*, and when.

    Returns (arrivals, skipped):
      arrivals  {stop_id: [ {vehicle_id, route_display, headsign, eta_seconds,
                             delay_seconds, age_seconds}, ... ]} soonest first
      skipped   {'no_trip_id': int, 'trip_not_in_schedule': int, 'bad_position': int}

    `delay_seconds` is None when it is not knowable — see *frequency_lookup*.
    Callers must handle that; it is not zero and not a number.

    *frequency_lookup*, when given, is called with a trip_id and returns True if
    that trip is published in frequencies.txt with exact_times=0. For such a
    trip the clock times in stop_times.txt are a *travel-time template*, not
    scheduled wall-clock times: the trip repeats every headway across its window
    and only the differences between stop times carry meaning. 2,099 of 2,102
    Rapid Bus KL trips are published this way. Comparing a vehicle's timestamp
    against an absolute scheduled time therefore measures nothing real -- it
    yields a "delay" that grows through the operating day for a perfectly
    on-time bus -- so the delay is reported as None, which is the honest answer.

    The arrival itself is unaffected and stays correct on these trips: the
    service-day epoch and the bus's own scheduled time cancel algebraically, so
    compute_eta_seconds only ever uses the *difference* between two stop times,
    which is exactly what a frequency template encodes. The measured delay is
    still applied internally to preserve that cancellation; only the reported
    figure becomes None.

    The skipped counts exist so the UI can say why a bus is missing. A vehicle
    silently dropped is indistinguishable from one that is not coming. A
    malformed vehicle must be counted and skipped, never allowed to raise —
    one bad record must not take the whole call (every stop, every other
    vehicle) down with it. Feeds have been observed publishing garbage this
    bad: a timestamp of 1886017556 (~year 2029) from Rapid Bus MRT Feeder.
    """
    arrivals = {s['stop_id']: [] for s in nearby_stops}
    skipped = {'no_trip_id': 0, 'trip_not_in_schedule': 0, 'bad_position': 0}
    wanted = {s['stop_id'] for s in nearby_stops}

    for v in vehicles:
        raw_trip = v.get('trip_id')
        # A float NaN trip_id -- what a pandas frame carries for a missing
        # value -- stringifies to 'nan', which is a perfectly good-looking
        # trip_id that no timetable contains. Left unguarded it lands in
        # trip_not_in_schedule, blaming the timetable for a vehicle that
        # simply never reported a trip at all.
        if isinstance(raw_trip, float) and not math.isfinite(raw_trip):
            trip_id = ''
        else:
            trip_id = str(raw_trip or '').strip()
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
            # The trip *is* in the timetable; it is the vehicle's clock that is
            # unreadable. Counting this as trip_not_in_schedule told the user
            # something false about the schedule, which is worse than saying
            # nothing -- explaining an omission wrongly defeats the purpose of
            # counting it. bad_position is the unusable-telemetry bucket.
            skipped['bad_position'] += 1
            continue

        try:
            lat = float(v['latitude'])
            lon = float(v['longitude'])
        except (KeyError, TypeError, ValueError):
            skipped['bad_position'] += 1
            continue
        # float() happily accepts inf/-inf/nan (and the strings "inf",
        # "-inf"), which would otherwise reach haversine_m and raise out of
        # this whole call, or -- for nan -- silently resolve to no match
        # and vanish uncounted. Reject non-finite and out-of-range
        # coordinates here, same as any other malformed position.
        if not (math.isfinite(lat) and math.isfinite(lon)
                and -90 <= lat <= 90 and -180 <= lon <= 180):
            skipped['bad_position'] += 1
            continue

        day = service_day_epoch(timestamp, utc_offset_hours)
        bus_index, _ = nearest_stop_index(stops, lat, lon)
        if bus_index < 0:
            continue
        delay = estimate_delay_seconds(stops, bus_index, timestamp, day)
        headsign = headsign_lookup(trip_id) if headsign_lookup else ''
        # Applied to the ETA regardless; only the *reported* figure is withheld.
        reported_delay = None if (
            frequency_lookup and frequency_lookup(trip_id)) else delay

        # Only stops the bus has yet to reach; a closer stop behind it is not
        # an arrival, it is history.
        #
        # 48% of Rapid Bus KL trips revisit at least one stop_id (up to 8
        # times), so one vehicle can match the same stop at several indexes
        # ahead of it. Listing each would show one bus twice at one stop --
        # "~5 min" and "~48 min" -- reading as two separate services and
        # occupying two of the three displayed slots. Keep only the earliest.
        # Indexes ascend and so do arrival times, so the first one that is
        # actually in the future is the earliest; a stop is only marked seen
        # once an arrival for it was really recorded.
        seen_stops = set()
        for i in range(bus_index + 1, len(stops)):
            sid = stops[i]['stop_id']
            if sid not in wanted or sid in seen_stops:
                continue
            secs = compute_eta_seconds(stops, i, delay, now_epoch, day)
            # None means "no such stop"; a negative int means "already passed".
            # Both are excluded, but they are different facts and must not be
            # compared with `<` against each other.
            if secs is None or secs < 0:
                continue
            seen_stops.add(sid)
            arrivals[sid].append({
                'vehicle_id': v.get('vehicle_id', ''),
                'route_display': v.get('route_display', ''),
                'route_id': str(v.get('route_id') or ''),
                'headsign': headsign,
                'eta_seconds': secs,
                'delay_seconds': reported_delay,
                'age_seconds': v.get('age_seconds'),
            })

    for sid in arrivals:
        arrivals[sid].sort(key=lambda a: a['eta_seconds'])
    return arrivals, skipped
