# Stop-Centric Arrivals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer "I'm standing here — what can I catch, and when?" by joining live vehicles to the published timetable, with no route knowledge or map reading required.

**Architecture:** A new pure module `src/utils/eta.py` holds all arithmetic (distance, stop matching, delay, arrival, aggregation) and imports neither Streamlit nor DuckDB. `gtfs_static.py` gains two cached loaders that turn GTFS ZIPs into the shapes `eta` consumes. `live_map.py` renders a "Near you" panel and, secondarily, a per-vehicle panel driven by map selection.

**Tech Stack:** Python 3.9+, Streamlit ≥1.40, pandas, pydeck, pytest.

## Global Constraints

- Target version **2.5.0** (feature → minor bump) across `CHANGELOG.md`, `README.md`, `pyproject.toml`; keep `requirements.txt`/`requirements-dev.txt` consistent. Bump happens ONCE, in the final task.
- `src/utils/eta.py` must import neither Streamlit nor DuckDB. Everything in it is unit-testable in isolation.
- **GTFS times may exceed 24:00:00.** `25:30:00` means 01:30 the next day. Parse to *seconds since service-day midnight* as an integer; never use a time/date formatter. Combine with a service-day epoch only at comparison time.
- **Service day is derived from the vehicle's timestamp**, not read from the feed. Documented caveat: a trip beginning before midnight and running past it may resolve to the wrong day.
- No fabricated numbers. Every failure mode in the spec's Part 5 renders its own message.
- Arrival estimates are granular to roughly one stop and must be presented as approximate.
- Stops are searched **within the selected region only**.
- Do NOT change ingestion, `DATA_FUTURE_TOLERANCE`, `get_live_data_optimized`, or `get_vehicle_trail`.
- Test baseline is **97 passing**. Run tests with `export PATH="$(pwd)/.venv/bin:$PATH"` first.
- Every task ends with a commit carrying the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

### GTFS shapes this plan targets (verified against the live feed)

```
stops.txt       stop_id, stop_name, stop_desc, stop_lat, stop_lon
stop_times.txt  trip_id, arrival_time, departure_time, stop_id, stop_sequence, stop_headsign
trips.txt       route_id, service_id, trip_id, shape_id, trip_headsign, direction_id
routes.txt      route_id, agency_id, route_short_name, route_long_name, route_type, ...
```

Note `route_id` `T3018` has `route_short_name` `T301` — the short name is NOT derivable from the id by string manipulation. Always resolve through `routes.txt`.

---

### Task 1: `eta.py` geometry and stop matching

**Files:**
- Create: `src/utils/eta.py`
- Test: `tests/test_eta.py`

**Interfaces:**
- Produces:
  - `eta.haversine_m(lat1, lon1, lat2, lon2) -> float` — great-circle metres.
  - `eta.nearest_stop_index(stops, lat, lon, start_index=0) -> tuple[int, float]` — `(index, metres)` of the nearest stop at or after `start_index`; `(-1, inf)` for an empty or fully-skipped list. `stops` is a list of dicts with `stop_lat`/`stop_lon`.
  - `eta.walking_minutes(distance_m, pace_m_per_min=80) -> int` — minutes, rounded up, minimum 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eta.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_eta.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.eta'`

- [ ] **Step 3: Implement**

Create `src/utils/eta.py`:

```python
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_eta.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Full suite**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 103 passed (97 + 6)

- [ ] **Step 6: Commit**

```bash
git add src/utils/eta.py tests/test_eta.py
git commit -m "feat: add eta geometry and stop matching primitives

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: GTFS loaders — trip stops and nearby stops

**Files:**
- Modify: `src/utils/gtfs_static.py` (append)
- Test: `tests/test_eta.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `gtfs_static.parse_gtfs_time(value) -> int` — `"HH:MM:SS"` to seconds since service-day midnight; handles hours ≥ 24. Returns `-1` on anything unparseable.
  - `gtfs_static.get_trip_stops(agency_slug, trip_id) -> list[dict]` — ordered by `stop_sequence`, each dict carrying `stop_id`, `stop_name`, `stop_lat`, `stop_lon`, `arrival_seconds`. `[]` when the trip is unknown or the feed is unavailable.
  - `gtfs_static.get_stops_near(agency_slug, lat, lon, radius_m=800, limit=5) -> list[dict]` — nearest stops within `radius_m`, closest first, each with `stop_id`, `stop_name`, `stop_lat`, `stop_lon`, `distance_m`.
  - `gtfs_static.get_trip_headsign(agency_slug, trip_id) -> str` — the destination text, `''` when unknown.

**Note for the implementer:** `get_trip_stops` must NOT re-parse `stop_times.txt` per call — it is ~88,000 rows for Rapid Bus KL. Build a `trip_id`-keyed index once per agency and memoise it in a module-level dict, following the `_ROUTE_REGION_INDEX` pattern already in this file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eta.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_eta.py -q`
Expected: FAIL — `AttributeError: module 'utils.gtfs_static' has no attribute 'parse_gtfs_time'`

- [ ] **Step 3: Implement**

Append to `src/utils/gtfs_static.py`:

```python
# ---------------------------------------------------------------------------
# Timetable lookups
#
# stop_times.txt is ~88,000 rows for Rapid Bus KL, so it is parsed once per
# agency into a trip-keyed index and kept for the life of the process. The
# underlying ZIPs already carry a 24h cache.
# ---------------------------------------------------------------------------

# agency slug -> {trip_id: [stop dict, ...]} ordered by stop_sequence
_TRIP_STOPS_INDEX = {}
# agency slug -> {trip_id: headsign}
_TRIP_HEADSIGN_INDEX = {}


def parse_gtfs_time(value):
    """
    "HH:MM:SS" to seconds since service-day midnight. Returns -1 if unparseable.

    GTFS hours legitimately exceed 24 — "25:30:00" means 01:30 the following
    day on the same service day. Formatting these as clock times silently
    breaks late-night arrivals, so they stay integer seconds throughout.
    """
    try:
        h, m, s = (int(part) for part in str(value).strip().split(':'))
    except (ValueError, AttributeError):
        return -1
    return h * 3600 + m * 60 + s


def _build_trip_index(agency_slug):
    """Populate the trip-stops and headsign indexes for one agency."""
    stops_by_id = {}
    trip_stops = {}
    headsigns = {}
    try:
        with _load_zip(agency_slug) as zf:
            for row in _read_csv_from_zip(zf, 'stops.txt') or []:
                try:
                    stops_by_id[row['stop_id'].strip()] = {
                        'stop_id': row['stop_id'].strip(),
                        'stop_name': (row.get('stop_name') or '').strip(),
                        'stop_lat': float(row['stop_lat']),
                        'stop_lon': float(row['stop_lon']),
                    }
                except (KeyError, ValueError, TypeError):
                    continue

            rows = []
            for row in _read_csv_from_zip(zf, 'stop_times.txt') or []:
                stop = stops_by_id.get((row.get('stop_id') or '').strip())
                if stop is None:
                    continue
                try:
                    seq = int(row['stop_sequence'])
                except (KeyError, ValueError, TypeError):
                    continue
                rows.append((row.get('trip_id', '').strip(), seq,
                             parse_gtfs_time(row.get('arrival_time')), stop))

            for trip_id, seq, arrival_seconds, stop in rows:
                if not trip_id or arrival_seconds < 0:
                    continue
                entry = dict(stop)
                entry['arrival_seconds'] = arrival_seconds
                trip_stops.setdefault(trip_id, []).append((seq, entry))

            for trip_id in trip_stops:
                trip_stops[trip_id] = [e for _, e in sorted(trip_stops[trip_id],
                                                            key=lambda pair: pair[0])]

            for row in _read_csv_from_zip(zf, 'trips.txt') or []:
                tid = (row.get('trip_id') or '').strip()
                if tid:
                    headsigns[tid] = (row.get('trip_headsign') or '').strip()
    except Exception:
        trip_stops, headsigns = {}, {}

    _TRIP_STOPS_INDEX[agency_slug] = trip_stops
    _TRIP_HEADSIGN_INDEX[agency_slug] = headsigns


def get_trip_stops(agency_slug: str, trip_id: str) -> list:
    """Ordered stops for *trip_id*, each with coordinates and arrival_seconds."""
    if not trip_id:
        return []
    if agency_slug not in _TRIP_STOPS_INDEX:
        _build_trip_index(agency_slug)
    return _TRIP_STOPS_INDEX.get(agency_slug, {}).get(trip_id.strip(), [])


def get_trip_headsign(agency_slug: str, trip_id: str) -> str:
    """Destination text for *trip_id* — what a rider reads on the front of the bus."""
    if not trip_id:
        return ''
    if agency_slug not in _TRIP_HEADSIGN_INDEX:
        _build_trip_index(agency_slug)
    return _TRIP_HEADSIGN_INDEX.get(agency_slug, {}).get(trip_id.strip(), '')


def get_stops_near(agency_slug: str, lat: float, lon: float,
                   radius_m: float = 800, limit: int = 5) -> list:
    """Nearest stops to (lat, lon) within *radius_m*, closest first."""
    from utils.eta import haversine_m

    found = []
    try:
        with _load_zip(agency_slug) as zf:
            for row in _read_csv_from_zip(zf, 'stops.txt') or []:
                try:
                    slat, slon = float(row['stop_lat']), float(row['stop_lon'])
                except (KeyError, ValueError, TypeError):
                    continue
                d = haversine_m(lat, lon, slat, slon)
                if d <= radius_m:
                    found.append({
                        'stop_id': row['stop_id'].strip(),
                        'stop_name': (row.get('stop_name') or '').strip(),
                        'stop_lat': slat,
                        'stop_lon': slon,
                        'distance_m': d,
                    })
    except Exception:
        return []

    found.sort(key=lambda s: s['distance_m'])
    return found[:limit]
```

- [ ] **Step 4: Run them to verify they pass**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_eta.py -q`
Expected: PASS — 13 passed

- [ ] **Step 5: Full suite**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 110 passed (103 + 7)

- [ ] **Step 6: Commit**

```bash
git add src/utils/gtfs_static.py tests/test_eta.py
git commit -m "feat: add cached GTFS trip-stop and nearby-stop lookups

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Delay and arrival arithmetic

**Files:**
- Modify: `src/utils/eta.py` (append)
- Test: `tests/test_eta.py` (append)

**Interfaces:**
- Consumes: `nearest_stop_index` from Task 1.
- Produces:
  - `eta.service_day_epoch(vehicle_timestamp, utc_offset_hours) -> int` — epoch seconds of local midnight for the service day containing that timestamp.
  - `eta.estimate_delay_seconds(stops, bus_index, bus_timestamp, day_epoch) -> int` — signed; positive means running late. `0` when `bus_index` is out of range.
  - `eta.compute_eta_seconds(stops, target_index, delay_seconds, now_epoch, day_epoch) -> int | None` — seconds until arrival; a negative int means already passed; **`None` means there is no such stop**. These must stay distinguishable: `-1` for both would make a missing stop read as "left one second ago", and `nearest_stop_index` already returns `-1` for its own not-found case, so the obvious call chain would conflate them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eta.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_eta.py -q`
Expected: FAIL — `AttributeError: module 'utils.eta' has no attribute 'service_day_epoch'`

- [ ] **Step 3: Implement**

Append to `src/utils/eta.py`:

```python
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
    return int(bus_timestamp) - scheduled


def compute_eta_seconds(stops, target_index, delay_seconds, now_epoch, day_epoch):
    """
    Seconds until the bus reaches *target_index*. Negative means already passed.

    The timetable supplies the travel time; the measured delay shifts it. Both
    are integers of seconds since the service-day epoch, so a stop scheduled at
    25:30:00 resolves to 01:30 the next day rather than wrapping backwards.
    """
    if not (0 <= target_index < len(stops)):
        return -1
    scheduled = day_epoch + stops[target_index]['arrival_seconds']
    return int(scheduled + delay_seconds - now_epoch)
```

- [ ] **Step 4: Run them to verify they pass**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_eta.py -q`
Expected: PASS — 20 passed

- [ ] **Step 5: Full suite**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 117 passed (110 + 7)

- [ ] **Step 6: Commit**

```bash
git add src/utils/eta.py tests/test_eta.py
git commit -m "feat: add delay estimation and arrival arithmetic

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: The arrivals aggregation

**Files:**
- Modify: `src/utils/eta.py` (append)
- Test: `tests/test_eta.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1 and 3.
- Produces:
  - `eta.arrivals_for_stops(vehicles, nearby_stops, trip_stops_lookup, now_epoch, utc_offset_hours, headsign_lookup=None) -> tuple[dict, dict]`

    `vehicles` is a list of dicts with `vehicle_id`, `latitude`, `longitude`, `timestamp`, `trip_id`, `route_display`, and optionally `age_seconds`.
    `trip_stops_lookup` is a callable `trip_id -> list[stop dict]`.
    `headsign_lookup` is an optional callable `trip_id -> str`.

    Returns `(arrivals, skipped)` where `arrivals` maps `stop_id` to a list of dicts — `vehicle_id`, `route_display`, `headsign`, `eta_seconds`, `delay_seconds`, `age_seconds` — sorted soonest-first; and `skipped` counts why vehicles were excluded: `{'no_trip_id': int, 'trip_not_in_schedule': int}`.

**Why the skipped counts:** a vehicle silently dropped from the list is indistinguishable from a vehicle that is not coming. The UI reports these as a footnote so the omission is visible.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eta.py`:

```python
def _vehicle(vid, lat, lon, ts, trip='TRIP1', route='T580'):
    return {'vehicle_id': vid, 'latitude': lat, 'longitude': lon,
            'timestamp': ts, 'trip_id': trip, 'route_display': route}


def test_arrivals_groups_by_stop_and_sorts_soonest_first():
    stops = _timed_stops()
    day = 1785427200
    now = day + 9 * 3600
    nearby = [dict(stops[2], distance_m=100.0)]     # user waits at the last stop

    # Two buses, one running 3 minutes early. Note the delay is what separates
    # them, NOT their positions: with schedule-based ETA two on-time buses reach
    # a stop at the same scheduled minute however far apart they are, so a
    # fixture where both are on time cannot produce an ordering to assert.
    behind = _vehicle('SLOW', 3.10, 101.70, day + 9 * 3600, route='T580')
    closer = _vehicle('FAST', 3.20, 101.70, day + 9 * 3600 + 420, route='T581')

    arrivals, skipped = eta.arrivals_for_stops(
        [behind, closer], nearby, lambda t: stops, now, 8)

    got = arrivals[stops[2]['stop_id']]
    assert [a['vehicle_id'] for a in got] == ['FAST', 'SLOW']
    assert got[0]['eta_seconds'] < got[1]['eta_seconds']
    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0}


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
    assert skipped == {'no_trip_id': 1, 'trip_not_in_schedule': 1}


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
    assert skipped == {'no_trip_id': 0, 'trip_not_in_schedule': 0}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_eta.py -q`
Expected: FAIL — `AttributeError: module 'utils.eta' has no attribute 'arrivals_for_stops'`

- [ ] **Step 3: Implement**

Append to `src/utils/eta.py`:

```python
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_eta.py -q`
Expected: PASS — 25 passed

- [ ] **Step 5: Full suite**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 122 passed (117 + 5)

- [ ] **Step 6: Commit**

```bash
git add src/utils/eta.py tests/test_eta.py
git commit -m "feat: aggregate live vehicles into per-stop arrivals

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: The "Near you" panel

**Files:**
- Modify: `src/app_pages/live_map.py`
- Modify: `requirements.txt`, `pyproject.toml` (Streamlit floor)
- Test: `tests/test_script.py` (append page-level tests)

**Interfaces:**
- Consumes: `eta.arrivals_for_stops`, `eta.walking_minutes`, `gtfs_static.get_stops_near`, `gtfs_static.get_trip_stops`, `gtfs_static.get_trip_headsign`.
- Produces: no new functions — rendering only.

**Context:** `live_map.py` already has `st.session_state.user_location` (`{'lat','lon','accuracy'}`) set by the Locate Me control, `agency_slug` resolved from the selected region, and `df_map` carrying `route_display`, `age_seconds` and `freshness`. `UTC_OFFSET_HOURS` must be imported the same way the other config knobs are — via `getattr(_config, 'UTC_OFFSET_HOURS', 8)`, NOT added to a tuple import, because a tuple import that names a knob a user's `config.py` lacks discards their whole config.

- [ ] **Step 1: Raise the Streamlit floor**

In `requirements.txt`, change `streamlit>=1.28.0` to:

```
streamlit>=1.40.0
```

In `pyproject.toml`, change `"streamlit>=1.28.0",` to:

```toml
    "streamlit>=1.40.0",
```

- [ ] **Step 2: Render the panel**

In `src/app_pages/live_map.py`, immediately BEFORE the existing `with st.expander("🚌 Route Viewer", expanded=False):` line, insert:

```python
    # ── What can I catch from here? ─────────────────────────────────────────
    with st.expander("📍 Arrivals near you", expanded=True):
        loc = st.session_state.get('user_location')
        if not loc:
            st.info("Tap **📍 Locate Me** above to see what is arriving near you.")
        elif not agency_slug:
            st.info(f"No timetable is published for {selected_region}.")
        else:
            nearby = gtfs_static.get_stops_near(
                agency_slug, loc['lat'], loc['lon'], radius_m=800, limit=5)
            if not nearby:
                st.info(
                    f"No stops found within 800 m of you in {selected_region}."
                )
            else:
                vehicles = df_map.to_dict('records')
                arrivals, skipped = eta.arrivals_for_stops(
                    vehicles, nearby,
                    lambda t: gtfs_static.get_trip_stops(agency_slug, t),
                    int(time.time()), UTC_OFFSET_HOURS,
                    headsign_lookup=lambda t: gtfs_static.get_trip_headsign(agency_slug, t),
                )

                any_arrival = False
                for stop in nearby:
                    walk = eta.walking_minutes(stop['distance_m'])
                    st.markdown(
                        f"**{stop['stop_name']}** · {int(stop['distance_m'])} m "
                        f"· ~{walk} min walk"
                    )
                    rows = arrivals.get(stop['stop_id'], [])
                    if not rows:
                        st.caption("  nothing inbound right now")
                        continue
                    any_arrival = True
                    for a in rows[:3]:
                        mins = max(1, round(a['eta_seconds'] / 60))
                        line = f"  {a['route_display']}"
                        if a['headsign']:
                            line += f" → {a['headsign']}"
                        line += f" · **~{mins} min**"
                        if a['delay_seconds'] >= 60:
                            line += f" · {round(a['delay_seconds'] / 60)} min late"
                        if a.get('age_seconds') and a['age_seconds'] > LIVE_FRESH_SECONDS:
                            line += f" · position {round(a['age_seconds'] / 60)} min old"
                        st.caption(line)

                st.caption(
                    "Estimated from the published timetable and each bus's "
                    "measured delay — accurate to about one stop."
                )
                # skipped has three keys — no_trip_id, trip_not_in_schedule and
                # bad_position. Report every non-zero one; a vehicle omitted
                # without explanation is indistinguishable from one that simply
                # is not coming, which is the whole reason these are counted.
                if any(skipped.values()):
                    reasons = []
                    if skipped.get('no_trip_id'):
                        reasons.append(f"{skipped['no_trip_id']} without trip info")
                    if skipped.get('trip_not_in_schedule'):
                        reasons.append(
                            f"{skipped['trip_not_in_schedule']} on a trip missing "
                            f"from the timetable")
                    if skipped.get('bad_position'):
                        reasons.append(
                            f"{skipped['bad_position']} with an unusable position")
                    st.caption("Not shown: " + ", ".join(reasons) + ".")
                if not any_arrival:
                    st.caption("No buses are currently inbound to these stops.")
```

- [ ] **Step 3: Add the imports**

`time` is NOT currently imported in `src/app_pages/live_map.py`. Add it to the imports at the top of the file, and add `eta` to the existing `from utils import ...` line:

```python
import time
```

The file already holds a `_config` reference and reads knobs individually. Immediately after the existing line:

```python
LIVE_HIDDEN_SECONDS = getattr(_config, 'LIVE_HIDDEN_SECONDS', 900)
```

add:

```python
UTC_OFFSET_HOURS = getattr(_config, 'UTC_OFFSET_HOURS', 8)
```

Do NOT add `UTC_OFFSET_HOURS` to the `from config import DEFAULT_ZOOM, ARROW_SIZE` tuple — the comment directly above that block explains why, and this project already shipped and fixed that exact bug in 2.4.0.

- [ ] **Step 4: Write page-level tests**

Append to `tests/test_script.py`:

```python
def test_arrivals_panel_prompts_for_location_when_unknown(monkeypatch):
    live_map, st_stub = _live_map_with_one_region(monkeypatch, 'Rapid Bus KL')
    st_stub.session_state['_region_for_search'] = 'Rapid Bus KL'
    st_stub.session_state.pop('user_location', None)

    live_map.show()

    said = _texts(st_stub.info) + _texts(st_stub.caption)
    assert 'Locate Me' in said


def test_arrivals_panel_reports_when_no_stops_are_nearby(monkeypatch):
    live_map, st_stub = _live_map_with_one_region(monkeypatch, 'Rapid Bus KL')
    st_stub.session_state['_region_for_search'] = 'Rapid Bus KL'
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [])

    live_map.show()

    said = _texts(st_stub.info) + _texts(st_stub.caption)
    assert 'No stops found' in said
```

- [ ] **Step 5: Verify**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -c "import ast; ast.parse(open('src/app_pages/live_map.py').read()); print('parses OK')"
python -m pytest tests/ -q
```
Expected: parses OK; 124 passed (122 + 2)

- [ ] **Step 6: Commit**

```bash
git add src/app_pages/live_map.py requirements.txt pyproject.toml tests/test_script.py
git commit -m "feat: show what is arriving at the stops near you

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Tap a bus for its own arrival

**Files:**
- Modify: `src/app_pages/live_map.py`

**Interfaces:**
- Consumes: the same helpers as Task 5.
- Produces: no new functions — rendering only.

**Context:** `st.pydeck_chart` accepts `selection_mode="single-object"` and `on_select="rerun"` from Streamlit 1.40. Layers must be `pickable=True` and carry an `id` for selection to resolve; the ScatterplotLayer in this file is already `pickable=True`.

**There are THREE `st.pydeck_chart` calls in this file** — the main map at roughly line 494, and two more inside the Route Viewer expander (roughly 600 and 636) that draw a planned route and a breadcrumb trail. Only the **first** one is the live vehicle map. Do not touch the other two.

- [ ] **Step 1: Make the main chart selectable**

In `src/app_pages/live_map.py`, add `id="vehicles",` to the ScatterplotLayer's `pdk.Layer(...)` arguments (the layer that already has `pickable=True`).

Then replace the FIRST chart call — identified by `layers=layers` and the tooltip containing `{freshness_display}` — which currently reads:

```python
    st.pydeck_chart(
        pdk.Deck(
            map_style=map_style,
            initial_view_state=view_state,
            layers=layers,
            tooltip={
                "html": "<b>Vehicle:</b> {vehicle_id}<br/><b>Route:</b> {route_display}<br/><b>Speed:</b> {speed_display} km/h<br/><b>Bearing:</b> {bearing_display}°<br/><b>Updated:</b> {freshness_display}",
                "style": {"backgroundColor": "steelblue", "color": "white"},
            },
        )
    )
```

with:

```python
    selection = st.pydeck_chart(
        pdk.Deck(
            map_style=map_style,
            initial_view_state=view_state,
            layers=layers,
            tooltip={
                "html": "<b>Vehicle:</b> {vehicle_id}<br/><b>Route:</b> {route_display}<br/><b>Speed:</b> {speed_display} km/h<br/><b>Bearing:</b> {bearing_display}°<br/><b>Updated:</b> {freshness_display}",
                "style": {"backgroundColor": "steelblue", "color": "white"},
            },
        ),
        selection_mode="single-object",
        on_select="rerun",
        key="live_map_deck",
    )
```

- [ ] **Step 2: Render the selected vehicle's arrival**

Immediately after the chart call, insert:

```python
    # Secondary view: one tapped vehicle, against the user's nearest stop on
    # its own trip. Re-resolved from the current frame every render, so
    # auto-refresh advances the bus without dropping the selection.
    picked = None
    try:
        objects = selection.selection.objects.get("vehicles", [])
        picked = objects[0].get("vehicle_id") if objects else None
    except (AttributeError, KeyError, IndexError, TypeError):
        picked = None

    if picked:
        row = df_map[df_map['vehicle_id'] == picked]
        if row.empty:
            st.info(f"Vehicle {picked} is no longer reporting.")
        elif not st.session_state.get('user_location'):
            st.info("Tap **📍 Locate Me** to see when this bus reaches you.")
        else:
            loc = st.session_state['user_location']
            v = row.iloc[0].to_dict()
            stops = gtfs_static.get_trip_stops(agency_slug, str(v.get('trip_id') or ''))
            if not stops:
                st.info(
                    f"Vehicle {picked} has no timetable entry for its current trip, "
                    f"so its arrival cannot be estimated."
                )
            else:
                nearby = [dict(s, distance_m=eta.haversine_m(
                    loc['lat'], loc['lon'], s['stop_lat'], s['stop_lon'])) for s in stops]
                arrivals, _ = eta.arrivals_for_stops(
                    [v], nearby,
                    lambda t: stops, int(time.time()), UTC_OFFSET_HOURS,
                    headsign_lookup=lambda t: gtfs_static.get_trip_headsign(agency_slug, t),
                )
                best = None
                for s in sorted(nearby, key=lambda s: s['distance_m']):
                    rows = arrivals.get(s['stop_id'], [])
                    if rows:
                        best = (s, rows[0])
                        break
                if best is None:
                    st.info(
                        f"Vehicle {picked} has already passed the stops nearest you."
                    )
                else:
                    s, a = best
                    mins = max(1, round(a['eta_seconds'] / 60))
                    st.success(
                        f"**{a['route_display']}** reaches **{s['stop_name']}** in "
                        f"~{mins} min — {int(s['distance_m'])} m from you "
                        f"(~{eta.walking_minutes(s['distance_m'])} min walk)."
                    )
```

- [ ] **Step 3: Verify**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -c "import ast; ast.parse(open('src/app_pages/live_map.py').read()); print('parses OK')"
python -m pytest tests/ -q
```
Expected: parses OK; 124 passed (unchanged — this task adds no tests; the MagicMock stub makes `selection.selection.objects.get` return a mock, which the `except` clause absorbs)

- [ ] **Step 4: Commit**

```bash
git add src/app_pages/live_map.py
git commit -m "feat: tap a bus to see when it reaches your nearest stop

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Docs and version bump (2.5.0)

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add the `[2.5.0]` section to `CHANGELOG.md`** above `## [2.4.2]`

```markdown
## [2.5.0] - 2026-07-31

### Added
- **"Arrivals near you"** — the Live Map now answers *"I am standing here; what can I catch?"*
  It finds stops within 800 m of your location and lists the next buses to each, with an estimated
  arrival, the route's destination, and how late the bus is running. No route knowledge and no map
  reading required
- **Tap a bus** to see when that specific vehicle reaches your nearest stop on its trip
- `src/utils/eta.py` — arrival estimation as pure, testable functions: `haversine_m`,
  `nearest_stop_index`, `walking_minutes`, `service_day_epoch`, `estimate_delay_seconds`,
  `compute_eta_seconds` and `arrivals_for_stops`
- `gtfs_static.get_trip_stops`, `get_stops_near`, `get_trip_headsign` and `parse_gtfs_time` —
  timetable lookups, with `stop_times.txt` (~88,000 rows for Rapid Bus KL) parsed once per agency
  into a trip-keyed index rather than per interaction

### Changed
- Minimum Streamlit raised to **1.40** for map click selection (`selection_mode` / `on_select`)

### Notes
- The provider publishes vehicle positions only — trip updates are on their 2026 roadmap — so every
  arrival here is derived locally from the published timetable plus each bus's measured delay. It
  is accurate to about one stop and is labelled as an estimate throughout
- GTFS times legitimately exceed 24:00:00 (`25:30:00` means 01:30 the next day). They are handled
  as integer seconds since service-day midnight, never as clock times
- Known limitation: the service day is derived from the vehicle's own timestamp because ingestion
  does not capture the trip descriptor's `startDate`. A trip that begins before midnight and runs
  past it can therefore resolve against the wrong service day. Rapid KL services largely end by
  midnight, so this is accepted for now
- Walking time is a straight-line distance at a fixed pace, not a routed path
```

- [ ] **Step 2: Update `README.md`**

In the `### 🗺️ Live Map` feature list, add after the Route search bullet:

```markdown
- **📍 Arrivals near you** — with your location set, see the stops within 800 m and the next buses
  to each, with estimated arrivals and how late each bus is running. Tap a bus on the map to see
  when that specific vehicle reaches your nearest stop
```

In the Roadmap, add a completed entry:

```markdown
- [x] Arrivals near you — stop-centric ETAs derived from the published timetable
```

In the Dependencies block, change `streamlit>=1.28.0` to `streamlit>=1.40.0`.

Add a Key Design Decisions row:

```markdown
| **ETAs from the timetable, not from speed** | The provider publishes no trip updates, so arrivals are derived by joining each vehicle's live `trip_id` to `stop_times.txt` and shifting by its measured delay. Instantaneous speed is a poor predictor — a bus at a red light reports 0 km/h |
```

- [ ] **Step 3: Bump `pyproject.toml`**

Change `version = "2.4.2"` to `version = "2.5.0"`.

- [ ] **Step 4: Verify the four files agree**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
grep -n '^version' pyproject.toml
grep -n '## \[2.5.0\]' CHANGELOG.md
grep -n 'streamlit>=' requirements.txt pyproject.toml README.md
python3 -c "
import re
p=open('pyproject.toml').read()
d=set(re.findall(r'\"([a-zA-Z0-9_.-]+[^\"]*)\"', p.split('dependencies = [')[1].split(']')[0]))
r=set(l.strip() for l in open('requirements.txt') if l.strip() and not l.startswith('#'))
print('dependency parity:', 'OK' if d==r else f'MISMATCH {d^r}')"
python -m pytest tests/ -q
```
Expected: version 2.5.0; CHANGELOG section present; **every** `streamlit>=` line reads 1.40.0; parity OK; 124 passed

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml
git commit -m "docs: document stop-centric arrivals, bump to 2.5.0

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** stop-centric primary view (T5), tap-a-bus secondary (T6), the six `eta` functions and two-plus-two GTFS loaders (T1–T4), the `25:30:00` rollover asserted end-to-end (T2 and T3), derived service day with its caveat documented (T3, T7), parsing cost solved by the per-agency trip index (T2), all Part 5 failure modes rendered (T5, T6), Streamlit floor raised (T5, T7).
- **Test arithmetic:** 97 → 103 (T1) → 110 (T2) → 117 (T3) → 122 (T4) → 124 (T5). T6 and T7 add none.
- **Type consistency:** stop dicts always carry `stop_id`/`stop_name`/`stop_lat`/`stop_lon`, plus `arrival_seconds` from `get_trip_stops` and `distance_m` from `get_stops_near`. `arrivals_for_stops` returns `(dict, dict)` in both callers. `route_display` is the column `live_map.py` already builds; `headsign` comes from `trips.txt`.
- **A trap worth naming:** `route_id` `T3018` has `route_short_name` `T301`. Nothing in this plan derives a short name from an id by string manipulation; display names come from `route_display`, which `live_map.py` already resolves through `routes.txt`.
- **Config safety:** `UTC_OFFSET_HOURS` is read with `getattr`, never appended to a tuple `from config import (...)`. Adding a name to that tuple that a user's older `config.py` lacks discards their entire config — a bug this project already shipped and fixed once, in 2.4.0.
- **Deliberate omissions:** shape projection, historical travel times, `startDate` ingestion and routed walking directions are all spec follow-ups, not tasks here.
