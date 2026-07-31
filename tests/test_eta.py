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
