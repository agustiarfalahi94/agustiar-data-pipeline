import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import walking


def _stops(*pairs):
    """(stop_id, straight_line_metres) -> stop dicts near Bukit Jalil."""
    return [{'stop_id': sid, 'stop_name': 'S' + sid,
             'stop_lat': 3.0586 + i / 10000.0, 'stop_lon': 101.6739,
             'distance_m': float(d)}
            for i, (sid, d) in enumerate(pairs)]


class _Resp:
    def __init__(self, status_code, payload=None, boom=None):
        self.status_code = status_code
        self._payload = payload
        self._boom = boom
        self.text = 'error body'

    def json(self):
        if self._boom:
            raise self._boom
        return self._payload


def _ok(distances):
    return _Resp(200, {'distances': [distances]})


def setup_function():
    walking._clear_cache()


# ── estimate_minutes: migrated from test_eta.py, new constants ──────────

def test_estimate_minutes_rounds_up_and_has_a_floor():
    assert walking.estimate_minutes(0) == 1
    assert walking.estimate_minutes(47) == 1
    assert walking.estimate_minutes(48) == 2
    assert walking.estimate_minutes(240) == 6


def test_estimate_minutes_applies_the_detour_factor():
    # A straight line is not a walk. 240 m of crow-flight is 6 min here but
    # would be 4 at the same pace with no detour allowance.
    import math
    bare = max(1, math.ceil(240 / walking.WALK_PACE_M_PER_MIN))
    assert walking.estimate_minutes(240) > bare


# ── no key: never touches the network ───────────────────────────────────

def test_no_api_key_makes_no_request_and_estimates(monkeypatch):
    def explode(*a, **k):
        raise AssertionError('walk_times called the network without a key')
    monkeypatch.setattr(walking.requests, 'post', explode)

    out = walking.walk_times(3.0586, 101.6739, _stops(('a', 240)), 'slug')
    assert out['a']['routed'] is False
    assert out['a']['minutes'] == walking.estimate_minutes(240)
    assert out['a']['distance_m'] == 240


def test_empty_stops_makes_no_request(monkeypatch):
    def explode(*a, **k):
        raise AssertionError('walk_times called the network for zero stops')
    monkeypatch.setattr(walking.requests, 'post', explode)
    assert walking.walk_times(3.0586, 101.6739, [], 'slug', api_key='k') == {}


# ── routed path ─────────────────────────────────────────────────────────

def test_routed_distance_replaces_the_straight_line(monkeypatch):
    # KL1291: 60 m away, 634 m on foot. The case the old code cannot see.
    monkeypatch.setattr(walking.requests, 'post', lambda *a, **k: _ok([634.0]))

    out = walking.walk_times(3.0586, 101.6739, _stops(('a', 60)), 'slug', api_key='k')
    assert out['a']['routed'] is True
    assert out['a']['distance_m'] == 634.0
    assert out['a']['minutes'] == 10          # ceil(634 / 67)


def test_request_is_one_call_for_all_stops_with_correct_shape(monkeypatch):
    seen = {}

    def capture(url, json=None, headers=None, timeout=None):
        seen['url'] = url
        seen['json'] = json
        seen['headers'] = headers
        seen['timeout'] = timeout
        seen['calls'] = seen.get('calls', 0) + 1
        return _ok([100.0, 200.0, 300.0])

    monkeypatch.setattr(walking.requests, 'post', capture)
    walking.walk_times(3.0586, 101.6739,
                       _stops(('a', 10), ('b', 20), ('c', 30)), 'slug', api_key='secret')

    assert seen['calls'] == 1
    assert seen['json']['sources'] == [0]
    assert seen['json']['destinations'] == [1, 2, 3]
    assert seen['json']['metrics'] == ['distance']
    # ORS takes [lon, lat]; origin first, then stops in order.
    assert seen['json']['locations'][0] == [101.6739, 3.0586]
    assert len(seen['json']['locations']) == 4
    assert seen['headers']['Authorization'] == 'secret'
    assert seen['timeout'] == walking.REQUEST_TIMEOUT


def test_results_map_back_to_stops_positionally(monkeypatch):
    monkeypatch.setattr(walking.requests, 'post',
                        lambda *a, **k: _ok([111.0, 222.0, 333.0]))
    out = walking.walk_times(3.0586, 101.6739,
                             _stops(('a', 10), ('b', 20), ('c', 30)), 'slug', api_key='k')
    assert out['a']['distance_m'] == 111.0
    assert out['b']['distance_m'] == 222.0
    assert out['c']['distance_m'] == 333.0


def test_a_null_distance_falls_back_for_that_stop_alone(monkeypatch):
    monkeypatch.setattr(walking.requests, 'post',
                        lambda *a, **k: _ok([100.0, None, 300.0]))
    out = walking.walk_times(3.0586, 101.6739,
                             _stops(('a', 10), ('b', 500), ('c', 30)), 'slug', api_key='k')
    assert out['a']['routed'] is True
    assert out['b']['routed'] is False
    assert out['b']['minutes'] == walking.estimate_minutes(500)
    assert out['c']['routed'] is True


# ── every failure collapses to the estimate ─────────────────────────────

def test_http_error_falls_back(monkeypatch):
    monkeypatch.setattr(walking.requests, 'post', lambda *a, **k: _Resp(429))
    out = walking.walk_times(3.0586, 101.6739, _stops(('a', 240)), 'slug', api_key='k')
    assert out['a']['routed'] is False
    assert out['a']['minutes'] == walking.estimate_minutes(240)


def test_connection_error_falls_back(monkeypatch):
    def boom(*a, **k):
        raise walking.requests.RequestException('no network')
    monkeypatch.setattr(walking.requests, 'post', boom)
    out = walking.walk_times(3.0586, 101.6739, _stops(('a', 240)), 'slug', api_key='k')
    assert out['a']['routed'] is False


def test_malformed_body_falls_back(monkeypatch):
    monkeypatch.setattr(walking.requests, 'post', lambda *a, **k: _Resp(200, {'oops': 1}))
    out = walking.walk_times(3.0586, 101.6739, _stops(('a', 240)), 'slug', api_key='k')
    assert out['a']['routed'] is False


def test_unparseable_json_falls_back(monkeypatch):
    monkeypatch.setattr(walking.requests, 'post',
                        lambda *a, **k: _Resp(200, boom=ValueError('not json')))
    out = walking.walk_times(3.0586, 101.6739, _stops(('a', 240)), 'slug', api_key='k')
    assert out['a']['routed'] is False


def test_wrong_length_row_falls_back(monkeypatch):
    # Two stops asked, one distance returned: the positional mapping is
    # unsafe, so discard the whole response rather than guess.
    monkeypatch.setattr(walking.requests, 'post', lambda *a, **k: _ok([100.0]))
    out = walking.walk_times(3.0586, 101.6739,
                             _stops(('a', 10), ('b', 20)), 'slug', api_key='k')
    assert out['a']['routed'] is False
    assert out['b']['routed'] is False


# ── cache ───────────────────────────────────────────────────────────────

def test_second_call_from_the_same_place_is_served_from_cache(monkeypatch):
    calls = {'n': 0}

    def once(*a, **k):
        calls['n'] += 1
        return _ok([634.0])

    monkeypatch.setattr(walking.requests, 'post', once)
    stops = _stops(('a', 60))
    walking.walk_times(3.0586, 101.6739, stops, 'slug', api_key='k')
    out = walking.walk_times(3.0586, 101.6739, stops, 'slug', api_key='k')
    assert calls['n'] == 1
    assert out['a']['distance_m'] == 634.0


def test_gps_jitter_inside_one_cell_still_hits_cache(monkeypatch):
    calls = {'n': 0}

    def once(*a, **k):
        calls['n'] += 1
        return _ok([634.0])

    monkeypatch.setattr(walking.requests, 'post', once)
    stops = _stops(('a', 60))
    walking.walk_times(3.05860, 101.67390, stops, 'slug', api_key='k')
    walking.walk_times(3.05862, 101.67392, stops, 'slug', api_key='k')   # ~2 m
    assert calls['n'] == 1


def test_walking_far_enough_misses_the_cache(monkeypatch):
    calls = {'n': 0}

    def each(*a, **k):
        calls['n'] += 1
        return _ok([634.0])

    monkeypatch.setattr(walking.requests, 'post', each)
    stops = _stops(('a', 60))
    walking.walk_times(3.0586, 101.6739, stops, 'slug', api_key='k')
    walking.walk_times(3.0600, 101.6739, stops, 'slug', api_key='k')     # ~155 m
    assert calls['n'] == 2


def test_a_different_agency_does_not_share_a_cache_entry(monkeypatch):
    calls = {'n': 0}

    def each(*a, **k):
        calls['n'] += 1
        return _ok([634.0])

    monkeypatch.setattr(walking.requests, 'post', each)
    stops = _stops(('a', 60))
    walking.walk_times(3.0586, 101.6739, stops, 'kl', api_key='k')
    walking.walk_times(3.0586, 101.6739, stops, 'penang', api_key='k')
    assert calls['n'] == 2


def test_expired_cache_is_refetched(monkeypatch):
    calls = {'n': 0}

    def each(*a, **k):
        calls['n'] += 1
        return _ok([634.0])

    monkeypatch.setattr(walking.requests, 'post', each)
    stops = _stops(('a', 60))
    walking.walk_times(3.0586, 101.6739, stops, 'slug', api_key='k')
    # Patch walking._now rather than time.time itself: monkeypatching the
    # stdlib clock for the duration of a test affects pytest's own bookkeeping.
    monkeypatch.setattr(walking, '_now',
                        lambda: time.time() + walking.CACHE_TTL_SECONDS + 1)
    walking.walk_times(3.0586, 101.6739, stops, 'slug', api_key='k')
    assert calls['n'] == 2


def test_a_failed_lookup_is_not_cached(monkeypatch):
    # Caching a fallback would pin a degraded answer in place for a day
    # after a transient blip.
    calls = {'n': 0}

    def flaky(*a, **k):
        calls['n'] += 1
        return _Resp(500) if calls['n'] == 1 else _ok([634.0])

    monkeypatch.setattr(walking.requests, 'post', flaky)
    stops = _stops(('a', 60))
    first = walking.walk_times(3.0586, 101.6739, stops, 'slug', api_key='k')
    second = walking.walk_times(3.0586, 101.6739, stops, 'slug', api_key='k')
    assert first['a']['routed'] is False
    assert second['a']['routed'] is True
    assert calls['n'] == 2


def test_only_stops_missing_from_the_cache_are_requested(monkeypatch):
    # The nearby stops and a tapped bus's trip stops are two different sets
    # looked up from the same place. A cache keyed on the set would answer one
    # from the other and never route the stops it had not seen.
    monkeypatch.setattr(walking.requests, 'post', lambda *a, **k: _ok([634.0]))
    walking.walk_times(3.0586, 101.6739, _stops(('a', 60)), 'slug', api_key='k')

    seen = {}

    def capture(url, json=None, headers=None, timeout=None):
        seen['destinations'] = len(json['destinations'])
        return _ok([420.0])

    monkeypatch.setattr(walking.requests, 'post', capture)
    out = walking.walk_times(3.0586, 101.6739,
                             _stops(('a', 60), ('z', 300)), 'slug', api_key='k')

    assert seen['destinations'] == 1            # only the uncached stop
    assert out['a']['distance_m'] == 634.0      # served from cache
    assert out['a']['routed'] is True
    assert out['z']['distance_m'] == 420.0
    assert out['z']['routed'] is True


def test_cached_stops_are_still_served_when_the_key_is_removed(monkeypatch):
    monkeypatch.setattr(walking.requests, 'post', lambda *a, **k: _ok([634.0]))
    walking.walk_times(3.0586, 101.6739, _stops(('a', 60)), 'slug', api_key='k')

    def explode(*a, **k):
        raise AssertionError('should have used the cache')

    monkeypatch.setattr(walking.requests, 'post', explode)
    out = walking.walk_times(3.0586, 101.6739, _stops(('a', 60)), 'slug')
    assert out['a']['routed'] is True
    assert out['a']['distance_m'] == 634.0
