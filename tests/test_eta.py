import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import eta


def _stops(*coords):
    return [{'stop_id': str(i), 'stop_name': f'S{i}', 'stop_lat': la, 'stop_lon': lo}
            for i, (la, lo) in enumerate(coords)]


def test_haversine_matches_a_known_distance():
    # KL Sentral -> Awan Besar LRT, ~8.1 km apart
    d = eta.haversine_m(3.134620, 101.686855, 3.062131, 101.670555)
    assert 7500 < d < 8700, d


def test_haversine_is_zero_for_the_same_point():
    assert eta.haversine_m(3.1, 101.7, 3.1, 101.7) == 0


def test_nearest_stop_index_picks_the_closest():
    stops = _stops((3.10, 101.70), (3.20, 101.70), (3.30, 101.70))
    idx, dist = eta.nearest_stop_index(stops, 3.199, 101.70)
    assert idx == 1
    assert dist < 200


def test_nearest_stop_index_respects_start_index():
    """The answer must stay ahead of the bus, even when a closer stop is behind it."""
    stops = _stops((3.10, 101.70), (3.20, 101.70), (3.30, 101.70))
    idx, _ = eta.nearest_stop_index(stops, 3.10, 101.70, start_index=2)
    assert idx == 2


def test_nearest_stop_index_on_empty_or_exhausted_list():
    assert eta.nearest_stop_index([], 3.1, 101.7) == (-1, math.inf)
    stops = _stops((3.10, 101.70))
    assert eta.nearest_stop_index(stops, 3.1, 101.7, start_index=5) == (-1, math.inf)


def test_walking_minutes_rounds_up_and_has_a_floor():
    assert eta.walking_minutes(0) == 1
    assert eta.walking_minutes(80) == 1
    assert eta.walking_minutes(81) == 2
    assert eta.walking_minutes(240) == 3


import zipfile

from utils import gtfs_static


def _make_gtfs_zip(tmp_path, name='feed'):
    """A tiny but structurally real GTFS feed: 3 stops, 1 trip, late-night times."""
    p = tmp_path / f"{name}.zip"
    stops = (
        "stop_id,stop_name,stop_desc,stop_lat,stop_lon\n"
        "S1,ALPHA,,3.10,101.70\n"
        "S2,BETA,,3.20,101.70\n"
        "S3,GAMMA,,3.30,101.70\n"
    )
    stop_times = (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence,stop_headsign\n"
        "TRIP1,23:50:00,23:50:00,S1,1,\n"
        "TRIP1,24:05:00,24:05:00,S2,2,\n"
        "TRIP1,25:30:00,25:30:00,S3,3,\n"
    )
    trips = (
        "route_id,service_id,trip_id,shape_id,trip_headsign,direction_id\n"
        "R1,weekday,TRIP1,SH1,GAMMA TERMINAL,0\n"
    )
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('stops.txt', stops)
        zf.writestr('stop_times.txt', stop_times)
        zf.writestr('trips.txt', trips)
    return str(p)


def _use_fake_feed(monkeypatch, zip_path):
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(zip_path))
    gtfs_static._TRIP_STOPS_INDEX.clear()
    gtfs_static._TRIP_HEADSIGN_INDEX.clear()


def test_parse_gtfs_time_handles_hours_past_midnight():
    assert gtfs_static.parse_gtfs_time('00:00:00') == 0
    assert gtfs_static.parse_gtfs_time('06:15:00') == 22500
    # 25:30:00 is 01:30 the NEXT day, not an error and not 01:30 today
    assert gtfs_static.parse_gtfs_time('25:30:00') == 91800
    assert gtfs_static.parse_gtfs_time('nonsense') == -1
    assert gtfs_static.parse_gtfs_time('') == -1


def test_get_trip_stops_returns_ordered_stops_with_coords(tmp_path, monkeypatch):
    _use_fake_feed(monkeypatch, _make_gtfs_zip(tmp_path))
    stops = gtfs_static.get_trip_stops('any', 'TRIP1')
    assert [s['stop_id'] for s in stops] == ['S1', 'S2', 'S3']
    assert [s['stop_name'] for s in stops] == ['ALPHA', 'BETA', 'GAMMA']
    assert stops[0]['stop_lat'] == 3.10
    # rollover preserved as seconds, not wrapped back to 05:30
    assert [s['arrival_seconds'] for s in stops] == [85800, 86700, 91800]


def test_get_trip_stops_is_empty_for_an_unknown_trip(tmp_path, monkeypatch):
    _use_fake_feed(monkeypatch, _make_gtfs_zip(tmp_path))
    assert gtfs_static.get_trip_stops('any', 'NOPE') == []


def test_get_trip_stops_is_empty_when_the_feed_fails(monkeypatch):
    def boom(slug):
        raise OSError('feed down')
    monkeypatch.setattr(gtfs_static, '_load_zip', boom)
    gtfs_static._TRIP_STOPS_INDEX.clear()
    gtfs_static._TRIP_HEADSIGN_INDEX.clear()
    assert gtfs_static.get_trip_stops('any', 'TRIP1') == []


def test_get_stops_near_returns_closest_first_within_radius(tmp_path, monkeypatch):
    _use_fake_feed(monkeypatch, _make_gtfs_zip(tmp_path))
    # sit just north of BETA
    near = gtfs_static.get_stops_near('any', 3.201, 101.70, radius_m=5000, limit=5)
    assert near[0]['stop_id'] == 'S2'
    assert near[0]['distance_m'] < 200
    assert [s['stop_id'] for s in near] == sorted(
        [s['stop_id'] for s in near], key=lambda sid: {'S2': 0, 'S1': 1, 'S3': 2}[sid])


def test_get_stops_near_respects_radius_and_limit(tmp_path, monkeypatch):
    _use_fake_feed(monkeypatch, _make_gtfs_zip(tmp_path))
    assert gtfs_static.get_stops_near('any', 3.20, 101.70, radius_m=100) == [] or \
        all(s['distance_m'] <= 100 for s in gtfs_static.get_stops_near('any', 3.20, 101.70, radius_m=100))
    assert len(gtfs_static.get_stops_near('any', 3.20, 101.70, radius_m=100000, limit=2)) == 2


def test_get_trip_headsign(tmp_path, monkeypatch):
    _use_fake_feed(monkeypatch, _make_gtfs_zip(tmp_path))
    assert gtfs_static.get_trip_headsign('any', 'TRIP1') == 'GAMMA TERMINAL'
    assert gtfs_static.get_trip_headsign('any', 'NOPE') == ''


def _timed_stops():
    """Three stops at 09:00, 09:10, 09:20 on the same service day."""
    out = _stops((3.10, 101.70), (3.20, 101.70), (3.30, 101.70))
    for s, secs in zip(out, (9 * 3600, 9 * 3600 + 600, 9 * 3600 + 1200)):
        s['arrival_seconds'] = secs
    return out


def test_service_day_epoch_is_local_midnight():
    # 2026-07-31 09:00 local (UTC+8) -> local midnight of the same day
    day = eta.service_day_epoch(1785459600, 8)
    assert (1785459600 - day) == 9 * 3600


def test_estimate_delay_is_zero_for_an_on_time_bus():
    stops = _timed_stops()
    day = 1785427200          # local midnight
    on_time = day + 9 * 3600  # exactly the scheduled time at stop 0
    assert eta.estimate_delay_seconds(stops, 0, on_time, day) == 0


def test_estimate_delay_is_positive_when_late_and_negative_when_early():
    stops = _timed_stops()
    day = 1785427200
    assert eta.estimate_delay_seconds(stops, 0, day + 9 * 3600 + 180, day) == 180
    assert eta.estimate_delay_seconds(stops, 0, day + 9 * 3600 - 120, day) == -120


def test_estimate_delay_is_zero_for_an_out_of_range_index():
    stops = _timed_stops()
    assert eta.estimate_delay_seconds(stops, -1, 0, 0) == 0
    assert eta.estimate_delay_seconds(stops, 99, 0, 0) == 0


def test_compute_eta_adds_the_delay():
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600            # 09:00
    # stop 2 is scheduled 09:20; a 3-minute-late bus arrives ~09:23
    assert eta.compute_eta_seconds(stops, 2, 180, now, day) == 1200 + 180


def test_compute_eta_is_negative_once_the_bus_has_passed():
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600 + 1500     # 09:25, past the 09:20 stop
    assert eta.compute_eta_seconds(stops, 2, 0, now, day) < 0


def test_compute_eta_survives_a_past_midnight_schedule():
    """A 25:30:00 stop is 01:30 next day — not 01:30 today, and not an error."""
    stops = _stops((3.10, 101.70), (3.20, 101.70))
    stops[0]['arrival_seconds'] = 85800   # 23:50
    stops[1]['arrival_seconds'] = 91800   # 25:30 == 01:30 next day
    day = 1785427200
    now = day + 85800                     # 23:50
    assert eta.compute_eta_seconds(stops, 1, 0, now, day) == 6000   # 100 minutes


def test_compute_eta_returns_none_for_an_index_that_does_not_exist():
    """
    None means "no such stop"; a negative number means "already passed".
    Returning -1 for both made a missing stop indistinguishable from a bus
    that left one second ago.
    """
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    assert eta.compute_eta_seconds(stops, 99, 0, now, day) is None
    assert eta.compute_eta_seconds(stops, -1, 0, now, day) is None
    # and a genuinely-passed stop still returns a negative int, not None
    passed = eta.compute_eta_seconds(stops, 0, 0, day + 9 * 3600 + 60, day)
    assert isinstance(passed, int) and passed < 0


def _vehicle(vid, lat, lon, ts, trip='TRIP1', route='T580'):
    return {'vehicle_id': vid, 'latitude': lat, 'longitude': lon,
            'timestamp': ts, 'trip_id': trip, 'route_display': route}


def test_arrivals_groups_by_stop_and_sorts_soonest_first():
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]     # user waits at the last stop

    # two buses on the same trip, one further back than the other. FAST is
    # pinged 3 minutes early against its own stop's schedule (09:10 - 180s)
    # so its projected arrival at the shared target is measurably sooner than
    # SLOW's, which is pinged exactly on time. Two buses that are BOTH
    # perfectly on schedule would tie on eta_seconds regardless of position,
    # since eta is schedule-time-at-target + delay - now; distance alone
    # carries no weight in that formula.
    behind = _vehicle('SLOW', 3.10, 101.70, day + 9 * 3600, route='T580')
    closer = _vehicle('FAST', 3.20, 101.70, day + 9 * 3600 + 420, route='T581')

    arrivals, skipped = eta.arrivals_for_stops(
        [behind, closer], nearby, lambda t: stops, now, 8)

    got = arrivals[stops[2]['stop_id']]
    assert [a['vehicle_id'] for a in got] == ['FAST', 'SLOW']
    assert got[0]['eta_seconds'] < got[1]['eta_seconds']
    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0, 'bad_position': 0}


def test_arrivals_excludes_a_bus_that_already_passed():
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600 + 1500                      # 09:25
    nearby = [dict(stops[0], distance_m=50.0)]       # user at the FIRST stop
    # bus is already at the last stop, so the first is behind it
    passed = _vehicle('GONE', 3.30, 101.70, now)

    arrivals, _ = eta.arrivals_for_stops([passed], nearby, lambda t: stops, now, 8)
    assert arrivals.get(stops[0]['stop_id'], []) == []


def test_arrivals_counts_why_vehicles_were_skipped():
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    no_trip = _vehicle('NOTRIP', 3.10, 101.70, now, trip='')
    unknown = _vehicle('UNKNOWN', 3.10, 101.70, now, trip='GHOST')

    def lookup(trip_id):
        return stops if trip_id == 'TRIP1' else []

    _, skipped = eta.arrivals_for_stops([no_trip, unknown], nearby, lookup, now, 8)
    assert skipped == {'no_trip_id': 1, 'trip_not_in_schedule': 1, 'bad_position': 0}


def test_arrivals_carries_headsign_and_delay():
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]
    late = _vehicle('LATE', 3.10, 101.70, day + 9 * 3600 + 180)

    arrivals, _ = eta.arrivals_for_stops(
        [late], nearby, lambda t: stops, now, 8,
        headsign_lookup=lambda t: 'TPM')

    a = arrivals[stops[2]['stop_id']][0]
    assert a['headsign'] == 'TPM'
    assert a['delay_seconds'] == 180


def test_arrivals_with_no_vehicles_returns_empty_lists_per_stop():
    stops = _timed_stops()
    nearby = [dict(stops[2], distance_m=100.0)]
    arrivals, skipped = eta.arrivals_for_stops([], nearby, lambda t: stops, 0, 8)
    assert arrivals == {stops[2]['stop_id']: []}
    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0, 'bad_position': 0}


def test_arrivals_skips_a_bad_position_without_raising_and_keeps_good_vehicles():
    """
    A vehicle missing latitude/longitude, or carrying None, must be counted
    under 'bad_position' and skipped -- never allowed to raise, since that
    would take down every stop and every other vehicle in the same call.
    A valid vehicle in the same batch must still produce its arrival.
    """
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    none_lat = _vehicle('NONE_LAT', None, 101.70, now)
    missing_lat = {'vehicle_id': 'MISSING_LAT', 'longitude': 101.70,
                   'timestamp': now, 'trip_id': 'TRIP1', 'route_display': 'T580'}
    good = _vehicle('GOOD', 3.10, 101.70, now)

    arrivals, skipped = eta.arrivals_for_stops(
        [none_lat, missing_lat, good], nearby, lambda t: stops, now, 8)

    assert skipped['bad_position'] == 2
    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0, 'bad_position': 2}
    got = arrivals[stops[2]['stop_id']]
    assert [a['vehicle_id'] for a in got] == ['GOOD']


def test_arrivals_skips_an_unparseable_timestamp_without_raising():
    """
    A vehicle with a None or non-numeric timestamp must be counted rather
    than raising or silently vanishing.
    """
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    none_ts = _vehicle('NONE_TS', 3.10, 101.70, None)
    junk_ts = _vehicle('JUNK_TS', 3.10, 101.70, 'not-a-timestamp')

    arrivals, skipped = eta.arrivals_for_stops(
        [none_ts, junk_ts], nearby, lambda t: stops, now, 8)

    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 2, 'bad_position': 0}
    assert arrivals[stops[2]['stop_id']] == []


def test_arrivals_coerces_a_non_string_trip_id():
    """An int trip_id must not raise on .strip() -- it is coerced first."""
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]
    vehicle = _vehicle('INTTRIP', 3.10, 101.70, now, trip=404)

    arrivals, skipped = eta.arrivals_for_stops(
        [vehicle], nearby, lambda t: stops if t == '404' else [], now, 8)

    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0, 'bad_position': 0}
    assert [a['vehicle_id'] for a in arrivals[stops[2]['stop_id']]] == ['INTTRIP']
