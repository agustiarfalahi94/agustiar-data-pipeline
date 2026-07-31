# Real Walk Times, Tappable Stops, Legible Arrival Rows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quote walking times from real pedestrian routes instead of straight lines, let a tapped stop answer under the map, and give the arrivals panel the legibility fix it never received.

**Architecture:** A new pure module `src/utils/walking.py` turns a location plus a set of stops into walking minutes, asking OpenRouteService's Matrix API for real footpath distances in one request and falling back to a straight-line estimate whenever routing is unavailable. It never reads configuration and never imports Streamlit, so every test is a plain function call. `live_map.py` resolves the API key, consumes the module at both existing walk-time call sites, gains a stop-selection panel mirroring its tapped-bus panel, and collapses its shared map tooltip to a single per-row HTML field so stops can become pickable without endangering the working vehicle tooltip.

**Tech Stack:** Python 3, Streamlit, pydeck, pandas, `requests` (already a dependency — no new package), pytest.

**Spec:** `docs/superpowers/specs/2026-08-01-stop-tap-and-walk-accuracy-design.md`

## Global Constraints

- Target version **2.7.0**. `CHANGELOG.md`, `pyproject.toml`, and `README.md` must all agree, per `CLAUDE.md`.
- **No new dependency.** `requirements.txt`, `requirements-dev.txt`, and the `pyproject.toml` dependency list are unchanged. Verify, do not edit.
- The suite must pass under **both** pandas string dtypes. Run both forms shown in Task 1 Step 8 for every task.
- **One walking-pace constant** in the codebase: `walking.WALK_PACE_M_PER_MIN = 67`. `eta.DEFAULT_PACE_M_PER_MIN` and `eta.walking_minutes` are deleted in Task 2. No second pace may survive anywhere.
- **A delay of `None` renders as nothing** — never `0`, never "on time". A headway-based trip has no published start time to be late against.
- **Never claim more than the code supports.** Routed walk times render bare; fallback walk times carry the `(estimated)` suffix.
- No walking or routing failure may raise into a Streamlit render. Every failure path returns a straight-line estimate.
- Tests must not make network calls. Mock `walking.requests.post` in every test that could reach it.
- Existing behaviour to preserve: the 800 m radius (`NEARBY_STOP_RADIUS_M`) stays **straight-line**; it selects which stops are nearby and must not be re-filtered on routed distance.

---

### Task 1: `src/utils/walking.py`

**Files:**
- Create: `src/utils/walking.py`
- Test: `tests/test_walking.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. `stops` are the dicts `gtfs_static.get_stops_near` returns — each has `stop_id`, `stop_name`, `stop_lat`, `stop_lon`, `distance_m` (straight-line metres).
- Produces:
  - `walk_times(user_lat, user_lon, stops, agency_slug, api_key=None) -> {stop_id: {'minutes': int, 'distance_m': float, 'routed': bool}}`
  - `estimate_minutes(distance_m) -> int`
  - `WALK_PACE_M_PER_MIN = 67`, `DETOUR_FACTOR = 1.4`
  - `_clear_cache()` for tests

- [ ] **Step 1: Write the failing tests**

Create `tests/test_walking.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_walking.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'utils.walking'`.

- [ ] **Step 3: Write the implementation**

Create `src/utils/walking.py`:

```python
"""
Walking time from a location to a set of stops.

Straight-line distance is not a walk. Measured from KL1743 GREEN AVENUE
CONDOMINIUM across the 15 stops within 800 m, the ratio of routed to
straight-line distance ranges from 1.04 to 10.60 — KL1291 KM1 BUKIT JALIL
sits 60 m away and 634 m on foot, and the app used to call that a one-minute
walk. No single multiplier survives that spread, which is why real routing is
asked for and a corrected constant was rejected.

OpenRouteService supplies the distance; the pace stays ours. ORS returns its
own duration, but it walks 957 m in 11 minutes (5.2 km/h) where Google implies
16 (~3.6 km/h) — it models the path, not the crossings, the waiting, the
stairs, or a person who is not in a hurry.

This module knows nothing about Streamlit, GTFS, or arrivals, and never reads
configuration: the caller passes the key in. That keeps every test a plain
function call with no network.
"""
import math
import time

import requests

ORS_MATRIX_URL = 'https://api.openrouteservice.org/v2/matrix/foot-walking'

# The one walking pace in the codebase. 4.0 km/h, matching observed Google
# walking estimates rather than an unobstructed stride.
WALK_PACE_M_PER_MIN = 67

# Applied only when routing is unavailable. Measured median in Bukit Jalil is
# 1.62; a walkable grid runs ~1.2. This is a deliberate compromise between the
# two regimes, not a fit to either — and no multiplier applied to KL1291's 60 m
# will ever yield its true ten minutes, which is why the fallback is a degraded
# mode rather than the design.
DETOUR_FACTOR = 1.4

# ~55 m. GPS jitter while standing still resolves to the same cell, so
# auto-refresh costs nothing. A cell this size bounds the induced error below
# the one-minute resolution the UI displays.
GRID_DEGREES = 0.0005

CACHE_TTL_SECONDS = 86400       # footpaths do not move
REQUEST_TIMEOUT = 5             # this call sits inside a page render

# (snapped_lat, snapped_lon, agency_slug, stop_id) -> (stored_at, routed_m)
# Module-level dict, following _TRIP_INDEX_MTIME in gtfs_static.py. The repo
# uses no Streamlit caching and this introduces none.
#
# Keyed per stop, not per stop set. Two different sets are looked up from the
# same location — the nearby stops, and the trip stops of a tapped bus — and a
# set-keyed cache would answer one from the other's entry, silently leaving the
# stops it had never seen on the straight-line fallback forever.
_WALK_CACHE = {}


def _clear_cache():
    """Drop every cached lookup. For tests."""
    _WALK_CACHE.clear()


def _now():
    """Indirection so tests can age the cache without patching the stdlib clock."""
    return time.time()


def _snap(value):
    """Snap a coordinate to the cache grid."""
    return round(round(value / GRID_DEGREES) * GRID_DEGREES, 6)


def estimate_minutes(distance_m):
    """
    Walking minutes from a straight-line distance, when routing is unavailable.

    Whole minutes, never less than one.
    """
    return max(1, math.ceil(distance_m * DETOUR_FACTOR / WALK_PACE_M_PER_MIN))


def _routed_distances(user_lat, user_lon, stops, agency_slug, api_key):
    """
    {stop_id: routed_metres} from ORS, or {} when routing is unavailable.

    The agency slug namespaces the cache: stop ids are unique within a feed but
    may collide across the fourteen.

    Every failure returns {} and the caller estimates. The except is broad on
    purpose — a network error, a rejected key, an exhausted quota, a changed
    response shape and unparseable JSON are all the same event here, and none
    of them may raise into a Streamlit render.
    """
    glat, glon = _snap(user_lat), _snap(user_lon)
    now = _now()

    found = {}
    missing = []
    for stop in stops:
        cached = _WALK_CACHE.get((glat, glon, agency_slug, stop['stop_id']))
        if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
            found[stop['stop_id']] = cached[1]
        else:
            missing.append(stop)

    if not missing or not api_key:
        return found

    locations = [[user_lon, user_lat]]
    locations += [[s['stop_lon'], s['stop_lat']] for s in missing]
    body = {
        'locations': locations,
        'sources': [0],
        'destinations': list(range(1, len(locations))),
        'metrics': ['distance'],
    }
    try:
        response = requests.post(
            ORS_MATRIX_URL,
            json=body,
            headers={'Authorization': api_key,
                     'Content-Type': 'application/json'},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            return found
        row = response.json()['distances'][0]
    except Exception:
        return found

    # Results map back to stops positionally, so a row of the wrong length
    # cannot be trusted at all — discarding it beats guessing an alignment.
    if not isinstance(row, list) or len(row) != len(missing):
        return found

    # Only a successful lookup is cached. Caching a fallback would pin a
    # degraded answer in place for a day after a transient blip.
    for stop, distance in zip(missing, row):
        if distance is None:
            continue
        _WALK_CACHE[(glat, glon, agency_slug, stop['stop_id'])] = (
            _now(), float(distance))
        found[stop['stop_id']] = float(distance)
    return found


def walk_times(user_lat, user_lon, stops, agency_slug, api_key=None):
    """
    {stop_id: {'minutes', 'distance_m', 'routed'}} for each stop.

    'routed' is False when the figure came from the straight-line fallback, so
    the UI can say so rather than claim more than the data supports.

    *stops* are the dicts get_stops_near returns, carrying straight-line
    distance_m. Costs one request for all of them, or none when cached.
    """
    if not stops:
        return {}

    routed = _routed_distances(user_lat, user_lon, stops, agency_slug, api_key)

    out = {}
    for stop in stops:
        stop_id = stop['stop_id']
        distance = routed.get(stop_id)
        if distance is None:
            out[stop_id] = {
                'minutes': estimate_minutes(stop['distance_m']),
                'distance_m': stop['distance_m'],
                'routed': False,
            }
        else:
            out[stop_id] = {
                'minutes': max(1, math.ceil(distance / WALK_PACE_M_PER_MIN)),
                'distance_m': distance,
                'routed': True,
            }
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_walking.py -q
```

Expected: all pass.

- [ ] **Step 5: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green.

- [ ] **Step 6: Commit**

```bash
git add src/utils/walking.py tests/test_walking.py
git commit -m "feat: walking times from real footpaths, with a straight-line fallback"
```

---

### Task 2: Consume `walk_times` in `live_map.py`; delete `eta.walking_minutes`

**Files:**
- Modify: `src/app_pages/live_map.py` (imports; new `_ors_api_key`; the two walk-time call sites near lines 808 and 878)
- Modify: `src/utils/eta.py` (delete `walking_minutes` and `DEFAULT_PACE_M_PER_MIN`)
- Modify: `tests/test_eta.py:46-60` (migrate the two tests out)
- Test: `tests/test_walking.py` (already holds the migrated coverage from Task 1)

**Interfaces:**
- Consumes: `walking.walk_times(...)` and `walking.estimate_minutes(...)` from Task 1.
- Produces: `_ors_api_key() -> str | None` in `live_map.py`; a `_walk_label(entry) -> str` helper returning `"~9 min walk"` or `"~5 min walk (estimated)"`, used by Tasks 2 and 5.

- [ ] **Step 1: Delete the migrated tests from `tests/test_eta.py`**

Remove `test_walking_minutes_rounds_up_and_has_a_floor` (line 46) and `test_walking_minutes_honours_a_non_default_pace` (line 53) in full. Their intent — rounding up, a one-minute floor — is already covered by `test_estimate_minutes_rounds_up_and_has_a_floor` in `tests/test_walking.py`. The non-default-pace test has no successor: pace is no longer a caller's choice.

- [ ] **Step 2: Run to verify the deletion is clean**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_eta.py -q
```

Expected: passes, two fewer tests.

- [ ] **Step 3: Delete `walking_minutes` from `src/utils/eta.py`**

Remove `DEFAULT_PACE_M_PER_MIN = 80` (line 17) and the whole `walking_minutes` function (lines 47-54). Keep `haversine_m` — the fallback and the radius filter both still need it.

- [ ] **Step 4: Verify nothing else references it**

```bash
grep -rn "walking_minutes\|DEFAULT_PACE_M_PER_MIN" src/ tests/
```

Expected: only the two `live_map.py` call sites, which Step 5 replaces. No other hit anywhere.

- [ ] **Step 5: Wire `walk_times` into `live_map.py`**

Add to the imports at the top, alongside `from utils import db, data_processor, eta`:

```python
from utils import walking
```

Add below the existing `LIVE_*` config reads (after `UTC_OFFSET_HOURS`):

```python
def _ors_api_key():
    """
    The OpenRouteService key: config.py for local dev, Streamlit Secrets for
    cloud. None when unset, in which case walk times degrade to estimates.

    Both are needed. config.py is gitignored so it never reaches Streamlit
    Cloud, and no other code in this repository reads st.secrets — a past
    refactor replaced those reads with hardcoded defaults, so a key placed in
    Secrets was silently ignored until this function existed.

    The except is broad because Streamlit raises varied types when no secrets
    file exists at all, which is the normal case for a fresh clone. A missing
    optional key must never break a render.
    """
    key = getattr(_config, 'ORS_API_KEY', None)
    if key:
        return key
    try:
        return st.secrets['routing']['api_key'] or None
    except Exception:
        return None


def _walk_label(entry):
    """
    "~9 min walk", or "~5 min walk (estimated)" when routing was unavailable.

    The suffix is not decoration. A routed figure follows the real footpath; an
    estimate cannot see that KL1291 is 60 m away and 634 m on foot. Saying
    which one you are reading is the difference between an estimate and a
    claim.
    """
    suffix = '' if entry['routed'] else ' (estimated)'
    return f"~{entry['minutes']} min walk{suffix}"
```

Replace the tapped-bus panel's walk line (currently around line 808):

```python
                        walk = walking.walk_times(
                            loc['lat'], loc['lon'], [s], agency_slug,
                            api_key=_ors_api_key())[s['stop_id']]
                        body.append(
                            f"That stop is ~{int(walk['distance_m'])} m from you "
                            f"({_walk_label(walk)})")
```

Replace the arrivals-panel loop's walk lines (currently around line 878). Hoist the lookup above the `for stop in shown:` loop so all stops cost one request, then read per stop inside it:

```python
                walks = walking.walk_times(
                    loc['lat'], loc['lon'], shown, agency_slug,
                    api_key=_ors_api_key())

                any_arrival = bool(served)
                for stop in shown:
                    walk = walks[stop['stop_id']]
                    maps_url = (
                        "https://www.google.com/maps/search/?api=1&query="
                        f"{stop['stop_lat']},{stop['stop_lon']}"
                    )
                    st.markdown(
                        f"**[{stop['stop_name']}]({maps_url})** "
                        f"· ~{int(walk['distance_m'])} m · {_walk_label(walk)}"
                    )
```

- [ ] **Step 6: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green. Any failure here is a real regression in the page tests — fix it, do not skip it.

- [ ] **Step 7: Commit**

```bash
git add src/app_pages/live_map.py src/utils/eta.py tests/test_eta.py
git commit -m "feat: quote walking times from routed distance, not a straight line"
```

---

### Task 3: `format_arrival` becomes route-first and labelled

**Files:**
- Modify: `src/app_pages/live_map.py:80-114` (`format_arrival`)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `format_arrival(arrival, fresh_seconds=LIVE_FRESH_SECONDS) -> str`, signature unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`, following the import and helper conventions already in that file:

```python
def _arrival(**kw):
    base = {'route_display': 'T580', 'headsign': 'Awan Besar',
            'eta_seconds': 360, 'delay_seconds': None, 'age_seconds': 0}
    base.update(kw)
    return base


def test_arrival_row_labels_the_route_so_a_place_name_cannot_be_mistaken():
    # PAVILION BUKIT JALIL is both a route name and a shopping mall. Without
    # the word "Route" there is nothing to tell a reader which it is.
    line = live_map.format_arrival(
        _arrival(route_display='PAVBJ', headsign='Pavilion Bukit Jalil'))
    assert line.startswith('Route PAVBJ')
    assert '→ Pavilion Bukit Jalil' in line


def test_arrival_row_states_the_arrival():
    line = live_map.format_arrival(_arrival(eta_seconds=360))
    assert 'arrives' in line
    assert '~6 min' in line


def test_arrival_row_is_one_line():
    line = live_map.format_arrival(
        _arrival(delay_seconds=120, age_seconds=180))
    assert '\n' not in line


def test_arrival_row_omits_the_headsign_when_there_is_none():
    line = live_map.format_arrival(_arrival(headsign=''))
    assert '→' not in line
    assert line.startswith('Route T580')


def test_arrival_row_states_lateness_when_it_is_knowable():
    line = live_map.format_arrival(_arrival(delay_seconds=120))
    assert '2 min late' in line


def test_arrival_row_says_nothing_at_all_when_lateness_is_unknowable():
    # None means the trip runs to a headway and has no published start time to
    # be late against. It is never zero and never "on time"; it is silence.
    line = live_map.format_arrival(_arrival(delay_seconds=None))
    assert 'late' not in line
    assert 'on time' not in line
    assert '0 min' not in line


def test_arrival_row_does_not_call_a_punctual_bus_late():
    line = live_map.format_arrival(_arrival(delay_seconds=0))
    assert 'late' not in line


def test_arrival_row_flags_a_stale_position_without_hedging_prose():
    line = live_map.format_arrival(_arrival(age_seconds=180), fresh_seconds=60)
    assert 'position 3 min old' in line
    assert 'less certain' not in line


def test_arrival_row_stays_quiet_about_a_fresh_position():
    line = live_map.format_arrival(_arrival(age_seconds=30), fresh_seconds=60)
    assert 'position' not in line


def test_arrival_row_survives_a_missing_route_display():
    line = live_map.format_arrival(_arrival(route_display=''))
    assert line.startswith('Route —')
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k arrival_row -q
```

Expected: FAIL — `assert line.startswith('Route PAVBJ')` fails because the line begins `PAVBJ →`.

- [ ] **Step 3: Rewrite the body of `format_arrival`**

Keep the existing docstring (it records why the two panels must state the same facts, and that history matters). Replace only the code below it:

```python
    line = f"Route {arrival.get('route_display') or '—'}"
    if arrival.get('headsign'):
        line += f" → {arrival['headsign']}"
    line += f" · arrives **~{max(1, round(arrival['eta_seconds'] / 60))} min**"

    delay = arrival.get('delay_seconds')
    if delay is not None and delay >= 60:
        line += f" · {round(delay / 60)} min late"

    age = arrival.get('age_seconds')
    if age and age > fresh_seconds:
        line += f" · position {round(age / 60)} min old"
    return line
```

Add one line to the end of the docstring:

```
    "Route" is unconditional. Rapid KL names routes after places — PAVILION
    BUKIT JALIL is a route and a mall — so without the label a reader cannot
    tell which fact they are looking at.
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k arrival_row -q
```

Expected: all pass.

- [ ] **Step 5: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green. An older test asserting the previous `position N min old, so less certain` wording will fail — update that assertion rather than restoring the prose; the user asked for it gone.

- [ ] **Step 6: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "fix: label the route in arrival rows so a place name cannot be mistaken for one"
```

---

### Task 4: Collapse the shared map tooltip to one per-row field

**Files:**
- Modify: `src/app_pages/live_map.py` (`vehicle_columns` ~line 494; the stops layer ~line 586; the Deck `tooltip` ~line 661)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a `tip_html` column on both layer datasets; `_tip_html(label, value) -> str` escaping helper.

This is the change that unblocks Task 5. The Deck-level tooltip is a single HTML template interpolating vehicle fields, and pydeck renders unmatched keys literally — so making stops pickable today would show raw `{vehicle_id}` text on a stop. Giving every row its own rendered `tip_html` removes the shared template entirely.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`:

```python
def test_tip_html_escapes_a_name_that_would_break_the_markup():
    # Stop names come from a third-party feed. One containing < or & must not
    # be able to inject markup into the tooltip.
    out = live_map._tip_html('Stop', 'A & B <Terminal>')
    assert '&amp;' in out
    assert '&lt;Terminal&gt;' in out
    assert '<Terminal>' not in out


def test_tip_html_labels_the_value():
    assert live_map._tip_html('Stop', 'KL2324') == '<b>Stop:</b> KL2324'
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k tip_html -q
```

Expected: FAIL — `AttributeError: module has no attribute '_tip_html'`.

- [ ] **Step 3: Implement**

Add `import html` to the imports at the top of `live_map.py`, then add beside `format_arrival`:

```python
def _tip_html(label, value):
    """
    One labelled line of tooltip markup, with the value escaped.

    Stop and route names come from third-party feeds and go straight into HTML
    the browser renders.
    """
    return f"<b>{html.escape(str(label))}:</b> {html.escape(str(value))}"
```

Build the vehicle tooltip per row, replacing the `vehicle_columns` block:

```python
    # Each row carries its own rendered tooltip. A single Deck-level template
    # can only name fields of one layer, and pydeck prints unmatched keys
    # literally — so a shared vehicle template made every other layer
    # unpickable. Per-row markup lets stops be tapped without touching this.
    df_map['tip_html'] = (
        df_map['vehicle_id'].map(lambda v: _tip_html('Vehicle', v))
        + '<br/>' + df_map['route_display'].map(lambda v: _tip_html('Route', v))
        + '<br/>' + df_map['speed_display'].map(lambda v: _tip_html('Speed', str(v) + ' km/h'))
        + '<br/>' + df_map['bearing_display'].map(lambda v: _tip_html('Bearing', str(v) + '°'))
        + '<br/>' + df_map['last_report_display'].map(lambda v: _tip_html('Last reported', v))
    )
    vehicle_columns = [
        'longitude', 'latitude', 'dot_color', 'vehicle_id', 'tip_html',
    ]
    vehicle_data = df_map[[c for c in vehicle_columns if c in df_map.columns]].copy()
```

Give stops a `tip_html` too, in the stops layer `data=` list:

```python
                data=[{'stop_id': s['stop_id'],
                       'stop_name': s['stop_name'],
                       'stop_lat': s['stop_lat'],
                       'stop_lon': s['stop_lon'],
                       'tip_html': _tip_html('Stop', s['stop_name'])}
                      for s in _nearby_stops],
```

Reduce the Deck tooltip to the shared field:

```python
            tooltip={
                "html": "{tip_html}",
                "style": {"backgroundColor": "steelblue", "color": "white"},
            },
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k tip_html -q
```

Expected: pass.

- [ ] **Step 5: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green. A page test asserting the old tooltip template must be updated to assert `{tip_html}`.

- [ ] **Step 6: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "refactor: give every map row its own tooltip markup"
```

---

### Task 5: Tapping a stop opens a panel under the map

**Files:**
- Modify: `src/app_pages/live_map.py` (stops layer `pickable`; the selection block ~line 686; a new stop panel beside the tapped-bus panel)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: `walking.walk_times` (Task 1), `_walk_label` (Task 2), `format_arrival` (Task 3), `tip_html` on the stops layer (Task 4).
- Produces: `_picked_stop_id(selection) -> str | None`; `st.session_state['selected_stop_id']`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`:

```python
class _Sel:
    """Minimal stand-in for a Streamlit pydeck selection payload."""
    def __init__(self, objects):
        self.selection = type('S', (), {'objects': objects})()


def test_picked_stop_id_reads_the_stops_layer():
    sel = _Sel({'nearby-stops': [{'stop_id': 'KL2324'}]})
    assert live_map._picked_stop_id(sel) == 'KL2324'


def test_picked_stop_id_ignores_a_vehicle_pick():
    sel = _Sel({'vehicles': [{'vehicle_id': 'BUS1'}]})
    assert live_map._picked_stop_id(sel) is None


def test_picked_stop_id_is_none_for_an_empty_selection():
    assert live_map._picked_stop_id(_Sel({})) is None


def test_picked_stop_id_coerces_a_non_string_id():
    # A non-string id reaching a pandas comparison against an Arrow-backed
    # column raises NotImplementedError and takes the whole page down.
    sel = _Sel({'nearby-stops': [{'stop_id': 2324}]})
    assert live_map._picked_stop_id(sel) == '2324'


def test_picked_stop_id_survives_an_unexpected_payload_shape():
    assert live_map._picked_stop_id(_Sel({'nearby-stops': [{}]})) is None
    assert live_map._picked_stop_id(_Sel({'nearby-stops': 'not a list'})) is None
    assert live_map._picked_stop_id(object()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k picked_stop -q
```

Expected: FAIL — `AttributeError: module has no attribute '_picked_stop_id'`.

- [ ] **Step 3: Implement the selection reader**

Add beside the existing vehicle-selection handling in `live_map.py`:

```python
def _picked_stop_id(selection):
    """
    The tapped stop's id, or None.

    Mirrors the vehicle path exactly, including coercing to str before the id
    can meet a pandas comparison and a broad except for a payload that does not
    match the assumed shape.
    """
    try:
        objects = selection.selection.objects.get("nearby-stops", [])
        raw = objects[0].get("stop_id") if objects else None
    except (AttributeError, KeyError, IndexError, TypeError):
        return None
    return str(raw) if isinstance(raw, (str, int)) else None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k picked_stop -q
```

Expected: pass.

- [ ] **Step 5: Make stops pickable and hold the selection**

In the stops layer, change `pickable=False` to `pickable=True`.

After the existing `picked` resolution block, add the stop selection with last-tap-wins:

```python
    # Last tap wins. Without this the slot below the map could hold a bus panel
    # and a stop panel at once, each answering a question the user did not ask
    # most recently.
    picked_stop = _picked_stop_id(selection)
    if picked is not None:
        st.session_state['selected_stop_id'] = None
    elif picked_stop is not None:
        st.session_state['selected_stop_id'] = picked_stop
    selected_stop_id = st.session_state.get('selected_stop_id')
```

- [ ] **Step 6: Render the stop panel**

Immediately after the tapped-bus panel block, add:

```python
    loc = st.session_state.get('user_location')
    if selected_stop_id and _nearby_stops and agency_slug and loc:
        stop = next((s for s in _nearby_stops
                     if s['stop_id'] == selected_stop_id), None)
        if stop is None:
            # The user walked out of range of a stop they had selected.
            st.session_state['selected_stop_id'] = None
        else:
            walk = walking.walk_times(
                loc['lat'], loc['lon'], [stop], agency_slug,
                api_key=_ors_api_key())[stop['stop_id']]
            body = [f"📍 **{stop['stop_name']}**",
                    f"~{int(walk['distance_m'])} m · {_walk_label(walk)}"]

            arrivals, _ = eta.arrivals_for_stops(
                df_map.to_dict('records'), [stop],
                lambda t: gtfs_static.get_trip_stops(agency_slug, t),
                int(time.time()), UTC_OFFSET_HOURS,
                headsign_lookup=lambda t: gtfs_static.get_trip_headsign(agency_slug, t),
                frequency_lookup=lambda t: gtfs_static.is_frequency_based(agency_slug, t),
            )
            rows = arrivals.get(stop['stop_id'], [])
            if rows:
                body += [format_arrival(r) for r in rows]
            else:
                # A route search narrowed df_map before this panel looked, so
                # with one active the only honest claim is about that route.
                body.append(
                    f"No bus matching '{filter_label}' is currently en route "
                    f"to this stop"
                    if filter_active else
                    "No bus is currently en route to this stop"
                )
            st.info("  \n".join(body))
            st.caption(ARRIVAL_ACCURACY_NOTE)
            if st.button("Clear stop selection"):
                st.session_state['selected_stop_id'] = None
                # Bump the widget key so Streamlit stops handing back the stale
                # payload. Without it, clearing appears to do nothing whenever
                # the deck spec is unchanged between renders — the same bug
                # that made "Clear bus selection" inert.
                st.session_state['deck_generation'] = (
                    st.session_state.get('deck_generation', 0) + 1)
                st.rerun()
```

- [ ] **Step 7: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green.

- [ ] **Step 8: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "feat: tapping a stop opens its arrivals under the map"
```

---

### Task 6: Documentation and version

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml`, `src/config.example.py`
- Verify unchanged: `requirements.txt`, `requirements-dev.txt`

- [ ] **Step 1: Add the key to `src/config.example.py`**

```python
# OpenRouteService API key, for real walking distances to nearby stops.
# Free key from https://account.heigit.org. Optional: without it, walk times
# fall back to a straight-line estimate and are labelled "(estimated)".
ORS_API_KEY = ''
```

- [ ] **Step 2: Update `README.md`**

Add to the configuration table:

| `ORS_API_KEY` | *(unset)* | OpenRouteService key for real walking distances. Optional |

Add to the Streamlit Cloud Secrets TOML block:

```toml
[routing]
api_key = "your-openrouteservice-key"
```

Add a troubleshooting entry stating plainly: without a key, walk times are straight-line estimates labelled `(estimated)`, and a stop across an uncrossable barrier will read as far nearer than it is. State the limitation that the 800 m radius selecting nearby stops remains straight-line even when routing is available.

- [ ] **Step 3: Update `CHANGELOG.md`**

Open a `## [2.7.0] - 2026-08-01` section. Cover, with the measured evidence:

- Walk times come from real footpaths. KL1291 KM1 BUKIT JALIL is 60 m away and 634 m on foot; it was quoted as a one-minute walk and is about ten. Circuity across the 15 stops within 800 m of Green Avenue Condominium ranges 1.04 to 10.60, which is why no corrected constant was used.
- ORS supplies distance, not duration — it walks 957 m in 11 minutes where Google implies 16.
- Without a key, walk times are labelled `(estimated)` rather than presented as fact.
- Streamlit Secrets are read for the first time; a key placed there was previously ignored.
- `.streamlit/secrets.toml` is gitignored (commit `b3a3af2`).
- Tapping a stop on the map opens its arrivals below the map. Hover tooltips were rejected: this is a phone-first app and hover does not exist on touch.
- Arrival rows in "Arrivals near you" are route-first and labelled.

- [ ] **Step 4: Bump `pyproject.toml` to 2.7.0**

- [ ] **Step 5: Verify the manifests are consistent and unchanged**

```bash
git diff --stat requirements.txt requirements-dev.txt
grep -n "version" pyproject.toml | head -2
head -12 CHANGELOG.md
```

Expected: no diff on either requirements file; `pyproject.toml` reads `2.7.0`; CHANGELOG's top section is `[2.7.0]`.

- [ ] **Step 6: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green.

- [ ] **Step 7: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml src/config.example.py
git commit -m "docs: document routed walk times and tappable stops, bump to 2.7.0"
```

---

## Manual verification

Automated tests cannot see a map. After Task 6, run the app and confirm on a phone-width viewport:

1. **Locate Me**, then check a nearby stop's walk time against Google Maps walking directions to the same stop. Expect within a couple of minutes, not a factor of two.
2. Tap a stop ring — a panel opens below the map naming that stop. Tap a bus — the stop panel is replaced. **Clear stop selection** dismisses it and it stays dismissed through an auto-refresh.
3. Hover a bus on desktop — the tooltip still shows vehicle, route, speed, bearing, last reported.
4. Temporarily blank `ORS_API_KEY` and reload: walk times still appear, labelled `(estimated)`, and the page does not error.
