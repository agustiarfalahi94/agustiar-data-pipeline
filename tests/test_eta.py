import math
import os
import sys
import time

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


import zipfile

from utils import gtfs_static


def _make_gtfs_zip(tmp_path, name='feed', frequencies=None):
    """A tiny but structurally real GTFS feed: 3 stops, 1 trip, late-night times.

    Pass *frequencies* as the body rows of a frequencies.txt to publish TRIP1
    (or any trip) as a headway service, the way 2,099 of the 2,102 Rapid Bus KL
    trips actually are.
    """
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
        if frequencies is not None:
            zf.writestr(
                'frequencies.txt',
                "trip_id,start_time,end_time,headway_secs,exact_times\n" + frequencies)
    return str(p)


def _clear_indexes():
    gtfs_static._TRIP_STOPS_INDEX.clear()
    gtfs_static._TRIP_HEADSIGN_INDEX.clear()
    gtfs_static._TRIP_FREQUENCY_INDEX.clear()
    gtfs_static._TRIP_INDEX_MTIME.clear()
    # Every process-global index belongs here. A test that fakes a feed but
    # leaves one cache populated silently reads the previous test's parse.
    gtfs_static._ROUTE_PARTS_INDEX.clear()
    gtfs_static._ROUTE_PARTS_MTIME.clear()
    # Spatial and shapes caches added in 2.18.0.
    gtfs_static._AGENCY_STOPS_INDEX.clear()
    gtfs_static._AGENCY_STOPS_MTIME.clear()
    gtfs_static._TRIP_SHAPES_INDEX.clear()
    gtfs_static._TRIP_SHAPES_MTIME.clear()


def _use_fake_feed(monkeypatch, zip_path):
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(zip_path))
    _clear_indexes()


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
    _clear_indexes()
    assert gtfs_static.get_trip_stops('any', 'TRIP1') == []


def test_a_transient_feed_failure_is_not_cached_forever(tmp_path, monkeypatch):
    """
    The failure path used to write an empty dict into the index, and
    get_trip_stops only rebuilt when the slug was absent. One network blip
    therefore cached "this agency has no timetable" for the life of the
    process: every vehicle counted as not in the schedule, the panel stuck on
    "nothing inbound", permanently. get_stops_near already returns [] without
    caching; this must match.
    """
    calls = {'n': 0}
    zip_path = _make_gtfs_zip(tmp_path)

    def flaky(slug):
        calls['n'] += 1
        if calls['n'] == 1:
            raise OSError('transient feed failure')
        return zipfile.ZipFile(zip_path)

    monkeypatch.setattr(gtfs_static, '_load_zip', flaky)
    _clear_indexes()

    assert gtfs_static.get_trip_stops('any', 'TRIP1') == []
    assert 'any' not in gtfs_static._TRIP_STOPS_INDEX, \
        "an empty result from a failed build must not be cached"

    # The very next call retries and succeeds.
    stops = gtfs_static.get_trip_stops('any', 'TRIP1')
    assert [s['stop_id'] for s in stops] == ['S1', 'S2', 'S3']
    assert gtfs_static.get_trip_headsign('any', 'TRIP1') == 'GAMMA TERMINAL'


def test_the_trip_index_is_rebuilt_when_the_cached_zip_changes(tmp_path, monkeypatch):
    """
    The 24-hour ZIP cache refreshes; a new static release brings new trip_ids.
    Keyed on the slug alone, a process served a superseded timetable
    indefinitely and every vehicle on a new trip_id silently counted as "not
    in the schedule". The index is keyed on the cached ZIP's mtime instead.
    """
    cache_path = tmp_path / 'cache.zip'
    first = _make_gtfs_zip(tmp_path, name='first')
    cache_path.write_bytes(open(first, 'rb').read())

    monkeypatch.setattr(gtfs_static, 'get_cached_path', lambda slug: str(cache_path))
    monkeypatch.setattr(
        gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(str(cache_path)))
    _clear_indexes()

    assert [s['stop_id'] for s in gtfs_static.get_trip_stops('any', 'TRIP1')] == \
        ['S1', 'S2', 'S3']
    assert gtfs_static.get_trip_stops('any', 'TRIP2') == []

    # A new static release lands in the same cache path, renaming the trip.
    second = tmp_path / 'second.zip'
    with zipfile.ZipFile(second, 'w') as zf:
        zf.writestr('stops.txt',
                    "stop_id,stop_name,stop_lat,stop_lon\nS1,ALPHA,3.10,101.70\n")
        zf.writestr('stop_times.txt',
                    "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                    "TRIP2,06:00:00,06:00:00,S1,1\n")
        zf.writestr('trips.txt',
                    "route_id,service_id,trip_id,trip_headsign\nR1,weekday,TRIP2,ALPHA\n")
    cache_path.write_bytes(second.read_bytes())
    os.utime(cache_path, (time.time() + 10, time.time() + 10))

    assert [s['stop_id'] for s in gtfs_static.get_trip_stops('any', 'TRIP2')] == ['S1'], \
        "a refreshed ZIP must invalidate the memoised trip index"


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


def test_is_frequency_based_reads_frequencies_txt(tmp_path, monkeypatch):
    """
    A trip listed in frequencies.txt runs to a headway, so stop_times.txt is a
    travel-time template rather than a wall clock and no lateness can be
    measured against it.
    """
    _use_fake_feed(monkeypatch, _make_gtfs_zip(
        tmp_path, frequencies="TRIP1,09:40:00,17:00:00,2400,0\n"))
    assert gtfs_static.is_frequency_based('any', 'TRIP1') is True
    assert gtfs_static.is_frequency_based('any', 'NOPE') is False
    assert gtfs_static.is_frequency_based('any', '') is False


def test_is_frequency_based_is_false_without_a_frequencies_file(tmp_path, monkeypatch):
    """A feed with no frequencies.txt publishes real scheduled times."""
    _use_fake_feed(monkeypatch, _make_gtfs_zip(tmp_path))
    assert gtfs_static.is_frequency_based('any', 'TRIP1') is False


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


def test_the_rollover_survives_end_to_end_from_the_feed(tmp_path, monkeypatch):
    """
    The 25:30:00 rollover asserted end-to-end, from get_trip_stops through
    compute_eta_seconds, as the design's Testing section asks.

    Halves that only meet in a fixture prove each side in isolation; a
    formatter creeping into the parse (or a wrap back to 05:30) would pass
    both and still break every late-night arrival.
    """
    _use_fake_feed(monkeypatch, _make_gtfs_zip(tmp_path))
    stops = gtfs_static.get_trip_stops('any', 'TRIP1')

    day = 1785427200
    now = day + 85800                     # 23:50, at the first stop
    # S3 is published as 25:30:00 -- 01:30 the next day, i.e. 100 minutes out.
    assert stops[2]['arrival_seconds'] == 91800
    assert eta.compute_eta_seconds(stops, 2, 0, now, day) == 6000


def test_compute_eta_survives_a_past_midnight_schedule():
    """A 25:30:00 stop is 01:30 next day — not 01:30 today, and not an error."""
    stops = _stops((3.10, 101.70), (3.20, 101.70))
    stops[0]['arrival_seconds'] = 85800   # 23:50
    stops[1]['arrival_seconds'] = 91800   # 25:30 == 01:30 next day
    day = 1785427200
    now = day + 85800                     # 23:50
    assert eta.compute_eta_seconds(stops, 1, 0, now, day) == 6000   # 100 minutes


def test_the_service_day_epoch_cancels_out_of_the_arrival():
    """
    The load-bearing invariant of this module, and the reason the ETA survives
    a feed that is 99% frequency-based.

    compute_eta_seconds(delay=estimate_delay_seconds(...)) expands to
    arrival[target] - arrival[bus] + bus_timestamp - now: the service-day epoch
    and the bus's own scheduled time cancel algebraically, so only the
    *difference* between two stop times is ever used -- which is exactly what a
    headway template encodes, and why a wrong service day cannot corrupt an
    arrival even though it does corrupt the delay.
    """
    stops = _timed_stops()
    bus_index, target_index = 0, 2
    timestamp = 1785427200 + 9 * 3600 + 137     # arbitrary report time
    now = timestamp + 42

    etas = []
    # Wildly different (even flatly wrong) service days must all agree.
    for day in (1785427200, 1785427200 - 86400, 1785427200 + 86400, 0):
        delay = eta.estimate_delay_seconds(stops, bus_index, timestamp, day)
        etas.append(eta.compute_eta_seconds(stops, target_index, delay, now, day))

    assert len(set(etas)) == 1, f"day_epoch leaked into the arrival: {etas}"
    # And it equals the pure travel-time expression, with no epoch anywhere.
    expected = (stops[target_index]['arrival_seconds']
                - stops[bus_index]['arrival_seconds'] + timestamp - now)
    assert etas[0] == expected


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


def test_arrivals_report_no_delay_at_all_for_a_frequency_based_trip():
    """
    2,099 of the 2,102 Rapid Bus KL trips are published in frequencies.txt with
    exact_times=0, including weekday_T3018_T301802_3 -- the very trip the design
    cited as its evidence, with start_time=09:40:00, headway_secs=2400. For
    those, stop_times.txt is a travel-time template repeated across a window,
    not a wall clock, so there is no published start time to be late against.

    The delay computed from an absolute scheduled time is pure fiction and
    grows through the operating day: the same perfectly on-time bus measured
    "238 min late" at 13:00 and "718 min late" at 21:00. Report None -- unknown
    -- which is neither zero nor a number.
    """
    stops = _timed_stops()
    day = 1785427200
    nearby = [dict(stops[2], distance_m=100.0)]

    # A bus reporting at 13:00 against a 09:00 template: four hours of
    # fabricated "delay" under the old behaviour.
    timestamp = day + 13 * 3600
    bus = _vehicle('HEADWAY', 3.10, 101.70, timestamp)

    arrivals, _ = eta.arrivals_for_stops(
        [bus], nearby, lambda t: stops, timestamp, 8,
        frequency_lookup=lambda t: True)

    a = arrivals[stops[2]['stop_id']][0]
    assert a['delay_seconds'] is None, a['delay_seconds']
    # The arrival is untouched: 20 minutes of scheduled travel from stop 0 to
    # stop 2, and the bus is reporting right now.
    assert a['eta_seconds'] == 1200


def test_arrivals_still_report_a_number_for_a_non_frequency_trip():
    """The companion half: a scheduled trip must keep its measured delay."""
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]
    late = _vehicle('SCHEDULED', 3.10, 101.70, day + 9 * 3600 + 180)

    arrivals, _ = eta.arrivals_for_stops(
        [late], nearby, lambda t: stops, now, 8,
        frequency_lookup=lambda t: False)

    a = arrivals[stops[2]['stop_id']][0]
    assert a['delay_seconds'] == 180


def test_the_frequency_flag_does_not_disturb_the_arrival():
    """
    Withholding the delay must change only what is *reported*. The measured
    delay is still applied internally, because it is what makes the service-day
    epoch cancel; dropping it would break the ETA on exactly the 99% of trips
    this fix is for.
    """
    stops = _timed_stops()
    day = 1785427200
    timestamp = day + 11 * 3600 + 321
    nearby = [dict(stops[2], distance_m=100.0)]
    bus = _vehicle('B', 3.10, 101.70, timestamp)

    with_flag, _ = eta.arrivals_for_stops(
        [bus], nearby, lambda t: stops, timestamp, 8, frequency_lookup=lambda t: True)
    without, _ = eta.arrivals_for_stops(
        [bus], nearby, lambda t: stops, timestamp, 8, frequency_lookup=lambda t: False)

    sid = stops[2]['stop_id']
    assert with_flag[sid][0]['eta_seconds'] == without[sid][0]['eta_seconds']


def test_arrivals_list_a_looping_trip_once_at_its_earliest_visit():
    """
    1,003 of 2,096 Rapid Bus KL trips (48%) revisit at least one stop_id, up to
    8 times. One vehicle then matched the same stop at several indexes ahead of
    it and produced two entries -- "~5 min" and "~48 min" -- reading as two
    separate services and eating two of the three displayed slots. Keep only
    the earliest arrival per (vehicle_id, stop_id).
    """
    # A loop: A -> B -> A -> B, so stop 'B' is reached twice from the start.
    loop = [
        {'stop_id': 'A', 'stop_name': 'A', 'stop_lat': 3.10, 'stop_lon': 101.70,
         'arrival_seconds': 9 * 3600},
        {'stop_id': 'B', 'stop_name': 'B', 'stop_lat': 3.20, 'stop_lon': 101.70,
         'arrival_seconds': 9 * 3600 + 600},
        {'stop_id': 'A', 'stop_name': 'A', 'stop_lat': 3.10, 'stop_lon': 101.70,
         'arrival_seconds': 9 * 3600 + 1800},
        {'stop_id': 'B', 'stop_name': 'B', 'stop_lat': 3.20, 'stop_lon': 101.70,
         'arrival_seconds': 9 * 3600 + 2880},
    ]
    day = 1785427200
    now = day + 9 * 3600
    nearby = [{'stop_id': 'B', 'stop_name': 'B', 'stop_lat': 3.20,
               'stop_lon': 101.70, 'distance_m': 100.0}]
    bus = _vehicle('LOOPER', 3.10, 101.70, now)

    arrivals, _ = eta.arrivals_for_stops([bus], nearby, lambda t: loop, now, 8)

    got = arrivals['B']
    assert len(got) == 1, f"one bus listed {len(got)} times at one stop: {got}"
    assert got[0]['eta_seconds'] == 600, "kept the later visit instead of the earliest"


def test_arrivals_bucket_a_nan_trip_id_as_missing_not_as_unscheduled():
    """
    A pandas frame carries a missing trip_id as float NaN, and str(NaN) is the
    perfectly plausible-looking trip_id 'nan' that no timetable contains. That
    landed in trip_not_in_schedule, blaming the timetable for a vehicle that
    never reported a trip at all.
    """
    stops = _timed_stops()
    now = 1785427200 + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    def lookup(trip_id):
        assert trip_id != 'nan', "the string 'nan' reached the timetable lookup"
        return stops if trip_id == 'TRIP1' else []

    nan_trip = _vehicle('NAN_TRIP', 3.10, 101.70, now, trip=float('nan'))

    _, skipped = eta.arrivals_for_stops([nan_trip], nearby, lookup, now, 8)
    assert skipped == {'no_trip_id': 1, 'trip_not_in_schedule': 0, 'bad_position': 0}


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


def test_arrivals_blames_the_clock_not_the_timetable_for_a_bad_timestamp():
    """
    A vehicle with a None or non-numeric timestamp must be counted rather
    than raising or silently vanishing -- and counted under the *right*
    reason. It was reported as 'trip_not_in_schedule', which the UI renders
    as "on a trip missing from the timetable". The trip is present; the
    clock is not readable. An omission explained wrongly is worse than an
    unexplained one, which is the entire premise of these counters.
    """
    stops = _timed_stops()
    now = 1785427200 + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    none_ts = _vehicle('NONE_TS', 3.10, 101.70, None)
    junk_ts = _vehicle('JUNK_TS', 3.10, 101.70, 'not-a-timestamp')

    # The lookup resolves the trip, proving the timetable is not the problem.
    arrivals, skipped = eta.arrivals_for_stops(
        [none_ts, junk_ts], nearby, lambda t: stops, now, 8)

    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0, 'bad_position': 2}
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


def test_arrivals_skips_an_infinite_position_without_raising():
    """
    float('inf') is accepted by float() but would otherwise reach
    haversine_m and raise ValueError out of the whole call. It must be
    counted under 'bad_position' instead, and a valid vehicle in the same
    call must still produce its arrival.
    """
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    inf_lat = _vehicle('INF', float('inf'), 101.70, now)
    good = _vehicle('GOOD', 3.10, 101.70, now)

    arrivals, skipped = eta.arrivals_for_stops(
        [inf_lat, good], nearby, lambda t: stops, now, 8)

    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0, 'bad_position': 1}
    got = arrivals[stops[2]['stop_id']]
    assert [a['vehicle_id'] for a in got] == ['GOOD']


def test_arrivals_skips_a_nan_position_without_raising():
    """
    float('nan') doesn't raise -- it silently resolves to no nearest-stop
    match and would vanish uncounted, contradicting the whole point of
    'skipped'. It must be counted under 'bad_position', and a valid
    vehicle in the same call must still produce its arrival.
    """
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    nan_lat = _vehicle('NAN', float('nan'), 101.70, now)
    good = _vehicle('GOOD', 3.10, 101.70, now)

    arrivals, skipped = eta.arrivals_for_stops(
        [nan_lat, good], nearby, lambda t: stops, now, 8)

    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0, 'bad_position': 1}
    got = arrivals[stops[2]['stop_id']]
    assert [a['vehicle_id'] for a in got] == ['GOOD']


def test_arrivals_skips_an_out_of_range_position_without_raising():
    """
    A latitude of 999 is finite but not a real position -- it would
    produce nonsense distances rather than an error, which is worse than
    raising. It must be rejected and counted under 'bad_position', and a
    valid vehicle in the same call must still produce its arrival.
    """
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    out_of_range = _vehicle('OUTOFRANGE', 999, 101.70, now)
    good = _vehicle('GOOD', 3.10, 101.70, now)

    arrivals, skipped = eta.arrivals_for_stops(
        [out_of_range, good], nearby, lambda t: stops, now, 8)

    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0, 'bad_position': 1}
    got = arrivals[stops[2]['stop_id']]
    assert [a['vehicle_id'] for a in got] == ['GOOD']


def test_get_route_parts_splits_short_from_long(tmp_path, monkeypatch):
    import zipfile
    p = tmp_path / "routes.zip"
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('routes.txt',
                    "route_id,route_short_name,route_long_name\n"
                    "S6060,PAVILION BUKIT JALIL (PAVBJ),Stesen LRT Awan Besar ~ Pavilion Bukit Jalil\n")
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(p))
    # routes.txt is parsed once per agency and kept, like the trip indexes
    # above — a test that swaps the feed must drop the previous parse with it.
    gtfs_static._ROUTE_PARTS_INDEX.clear()
    parts = gtfs_static.get_route_parts('any', 'S6060')
    assert parts['short'] == 'PAVILION BUKIT JALIL (PAVBJ)'
    assert parts['long'] == 'Stesen LRT Awan Besar ~ Pavilion Bukit Jalil'


def test_get_route_parts_is_empty_for_unknown_or_failed(monkeypatch):
    import zipfile
    monkeypatch.setattr(gtfs_static, '_load_zip',
                        lambda slug: (_ for _ in ()).throw(OSError('feed down')))
    gtfs_static._ROUTE_PARTS_INDEX.clear()
    assert gtfs_static.get_route_parts('any', 'S6060') == {'short': '', 'long': ''}


def test_arrivals_carry_route_id():
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]
    v = _vehicle('V1', 3.10, 101.70, now)
    v['route_id'] = 'S6060'
    arrivals, _ = eta.arrivals_for_stops([v], nearby, lambda t: stops, now, 8)
    assert arrivals[stops[2]['stop_id']][0]['route_id'] == 'S6060'


def test_arrivals_coerces_a_nan_route_id_to_empty_string():
    """
    A pandas frame carries a missing route_id as float NaN, and str(NaN) is the
    plausible-looking route_id 'nan' that no timetable contains. It must be
    coerced to empty string, just like trip_id does, so downstream checks like
    if arrival['route_id']: work correctly.
    """
    import pandas as pd
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    # Create a vehicle via DataFrame.to_dict('records') like live_map.py does,
    # so missing route_id becomes float NaN, not an absent key.
    df = pd.DataFrame([{
        'vehicle_id': 'NAN_ROUTE',
        'latitude': 3.10,
        'longitude': 101.70,
        'timestamp': now,
        'trip_id': 'TRIP1',
        'route_display': 'T580',
        'route_id': float('nan'),  # Missing route_id from the feed
    }])
    vehicles = df.to_dict('records')

    arrivals, _ = eta.arrivals_for_stops(vehicles, nearby, lambda t: stops, now, 8)
    assert arrivals[stops[2]['stop_id']][0]['route_id'] == '', \
        "NaN route_id must be coerced to empty string, not 'nan'"


def test_arrivals_route_id_missing_key_becomes_empty_string():
    """
    A vehicle with no route_id key at all must also produce an empty string,
    not raise. This covers the case where a feed simply doesn't provide
    route_id in the vehicle record.
    """
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]

    # Build a vehicle without route_id key
    v = _vehicle('NO_ROUTE_KEY', 3.10, 101.70, now)
    # Ensure route_id key is absent
    v.pop('route_id', None)

    arrivals, _ = eta.arrivals_for_stops([v], nearby, lambda t: stops, now, 8)
    assert arrivals[stops[2]['stop_id']][0]['route_id'] == ''


def test_get_route_parts_short_circuits_on_empty_route_id(tmp_path, monkeypatch):
    """
    get_route_parts should return the empty dict immediately when route_id is
    empty, before reading the ZIP. This guards the loop and makes the contract
    explicit: only non-empty ids are looked up.
    """
    calls = {'n': 0}

    def counting_load_zip(slug):
        calls['n'] += 1
        raise RuntimeError("should not have been called")

    monkeypatch.setattr(gtfs_static, '_load_zip', counting_load_zip)
    result = gtfs_static.get_route_parts('any', '')
    assert result == {'short': '', 'long': ''}
    assert calls['n'] == 0, "empty route_id should short-circuit without loading ZIP"


def test_get_route_parts_unknown_route_in_valid_feed(tmp_path, monkeypatch):
    """
    When the feed loads fine but has no row for the requested route_id,
    the function should return the empty dict, not raise.
    """
    import zipfile
    p = tmp_path / "routes.zip"
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('routes.txt',
                    "route_id,route_short_name,route_long_name\n"
                    "R1,Route One,From A to B\n"
                    "R2,Route Two,From C to D\n")
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(p))
    gtfs_static._ROUTE_PARTS_INDEX.clear()
    parts = gtfs_static.get_route_parts('any', 'UNKNOWN_ROUTE')
    assert parts == {'short': '', 'long': ''}


def test_service_day_epoch_with_start_date_spanning_midnight():
    """
    A trip starting 23:50 on 2026-07-31 that reports at 00:30 on 2026-08-01 local time
    must anchor to 2026-07-31 local midnight when start_date='20260731' is provided.
    """
    # 2026-07-31 00:00:00 UTC+8 epoch = 1785427200
    # 2026-08-01 00:30:00 UTC+8 = 1785427200 + 86400 + 1800 = 1785515400
    ts_past_midnight = 1785515400
    day = eta.service_day_epoch(ts_past_midnight, 8, start_date='20260731')
    assert day == 1785427200, "service_day_epoch should anchor to 2026-07-31 midnight"

    # Stop scheduled at 24:20:00 (87600s from 2026-07-31 midnight).
    # Actual reporting time is 00:30:00 (88200s from 2026-07-31 midnight).
    # Expected delay: 88200 - 87600 = +600s (10 min late).
    stops = [{'stop_id': 'S1', 'stop_name': 'Midnight Stop', 'stop_lat': 3.1, 'stop_lon': 101.7, 'arrival_seconds': 87600}]
    delay = eta.estimate_delay_seconds(stops, 0, ts_past_midnight, day)
    assert delay == 600, f"Expected 600s delay, got {delay}s"


def test_service_day_epoch_fallback_on_invalid_start_date():
    # If start_date is invalid or malformed, fallback to timestamp-derived local midnight
    ts = 1785459600  # 2026-07-31 09:00 UTC+8
    day_fallback = eta.service_day_epoch(ts, 8, start_date='invalid_date')
    assert (ts - day_fallback) == 9 * 3600


def test_get_stops_near_spatial_indexing_and_bounding_box(tmp_path, monkeypatch):
    """
    get_stops_near must use _AGENCY_STOPS_INDEX, bounding box filtering,
    and return correct nearest stops without re-reading the zip file.
    """
    p = tmp_path / "spatial_feed.zip"
    stops = (
        "stop_id,stop_name,stop_desc,stop_lat,stop_lon\n"
        "NEAR_1,Near Stop 1,,3.1001,101.7001\n"
        "FAR_1,Far Away Stop,,5.0000,105.0000\n"
    )
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('stops.txt', stops)

    _use_fake_feed(monkeypatch, str(p))

    # First call: populates spatial cache
    near_stops = gtfs_static.get_stops_near('test_slug', 3.1000, 101.7000, radius_m=800, limit=5)
    assert len(near_stops) == 1
    assert near_stops[0]['stop_id'] == 'NEAR_1'
    assert 'test_slug' in gtfs_static._AGENCY_STOPS_INDEX

    # Second call: uses cached index
    near_stops_cached = gtfs_static.get_stops_near('test_slug', 3.1000, 101.7000, radius_m=800, limit=5)
    assert len(near_stops_cached) == 1
    assert near_stops_cached[0]['stop_id'] == 'NEAR_1'


def test_get_shapes_for_trip_caching(tmp_path, monkeypatch):
    """
    get_shapes_for_trip must cache result in _TRIP_SHAPES_INDEX and return [lon, lat] pairs.
    """
    p = tmp_path / "shapes_feed.zip"
    trips = "route_id,service_id,trip_id,shape_id\nR1,weekday,TRIP_SHAPE,SHAPE_1\n"
    shapes = (
        "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        "SHAPE_1,3.1000,101.7000,1\n"
        "SHAPE_1,3.1010,101.7010,2\n"
    )
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('trips.txt', trips)
        zf.writestr('shapes.txt', shapes)

    _use_fake_feed(monkeypatch, str(p))

    # First call: populates cache
    shape_pts = gtfs_static.get_shapes_for_trip('test_slug', 'TRIP_SHAPE')
    assert shape_pts == [[101.7000, 3.1000], [101.7010, 3.1010]]
    assert ('test_slug', 'TRIP_SHAPE') in gtfs_static._TRIP_SHAPES_INDEX

    # Second call: hits cache
    shape_pts_cached = gtfs_static.get_shapes_for_trip('test_slug', 'TRIP_SHAPE')
    assert shape_pts_cached == shape_pts


