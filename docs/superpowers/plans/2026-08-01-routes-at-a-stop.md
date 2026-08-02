# Routes At A Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every route that serves a tapped stop — not just the ones with a live bus — and let each route open to its full stop sequence with journey times, so a rider can tell which bus reaches their destination and how long it takes.

**Architecture:** Two new indexes are populated inside `gtfs_static._build_trip_index`, the pass that already parses `stop_times.txt`, so no file is read twice. Two new public functions expose them. A new pure module `src/utils/route_view.py` turns a stop pattern into display rows and unique expander titles with no Streamlit and no I/O, keeping the logic unit-testable and `live_map.py` (already 1,311 lines) nearly unchanged.

**Tech Stack:** Python 3, Streamlit, pydeck, pandas, pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-08-01-routes-at-a-stop-design.md`

## Global Constraints

- Target version **2.8.0**. `CHANGELOG.md`, `pyproject.toml` and `README.md` must agree, per `CLAUDE.md`.
- **No new dependency.** `requirements.txt`, `requirements-dev.txt` and the `pyproject.toml` dependency list are unchanged. Verify, do not edit.
- The suite must pass under **both** pandas string dtypes. Both commands appear in every task's test step.
- **Tests must make no network calls.** The `PageTestReachedNetwork` guard added in 2.7.1 stays in force; do not weaken or remove it.
- **Nothing may raise into a Streamlit render.** Every new lookup returns `[]`/`{}`/`''` on missing or malformed data, matching `get_stops_near` and `get_trip_stops`.
- **Never cache a failure.** `_build_trip_index` deliberately stores nothing when it raises, so the next call retries. The two new indexes must be written in the same place as the existing three — never independently.
- **Journey times come from the published timetable, not live positions**, and must be labelled as such wherever shown.
- **A headway route never shows a fabricated departure time.** 2,099 of 2,102 Rapid Bus KL trips are frequency-based.
- **Two patterns of one route must never render identical titles.**
- **This is not a journey planner.** No searching for journeys, comparing options or suggesting interchanges.

---

### Task 1: Two indexes and two lookups in `gtfs_static.py`

**Files:**
- Modify: `src/utils/gtfs_static.py` (index declarations near `_TRIP_STOPS_INDEX` ~line 344; `_build_trip_index` ~lines 390-462; new public functions after `is_frequency_based` ~line 503)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `get_routes_at_stop(agency_slug, stop_id) -> [{'route_id': str, 'short': str, 'long': str}, ...]` sorted by display name
  - `get_route_patterns(agency_slug, route_id, stop_id=None) -> [{'trip_id': str, 'stops': [stop_entry, ...]}, ...]`
  - `stop_entry` is exactly what `get_trip_stops` returns: `stop_id`, `stop_name`, `stop_lat`, `stop_lon`, `arrival_seconds`
  - Module globals `_STOP_ROUTES_INDEX`, `_ROUTE_TRIPS_INDEX` (tests clear these by name, matching the existing convention at `tests/test_script.py:2349`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`:

```python
def _make_timetable_zip(tmp_path, name):
    """
    A minimal GTFS static ZIP with a loop route and a second route.

    Models the real shape that motivated this feature: T580 leaves LRT Awan
    Besar, reaches KM1 Bukit Jalil one minute later, and returns past Green
    Avenue Condominium 32 minutes later before ending where it began. Two trips
    share that pattern so de-duplication has something to collapse.
    """
    import zipfile
    p = tmp_path / f"{name}.zip"
    stops = (
        "stop_id,stop_name,stop_lat,stop_lon\n"
        "S1,LRT AWAN BESAR,3.0621,101.6706\n"
        "S2,KM1 BUKIT JALIL,3.0584,101.6744\n"
        "S3,GREEN AVENUE CONDOMINIUM,3.0587,101.6740\n"
        "S4,ELSEWHERE,3.0700,101.6800\n"
    )
    routes = (
        "route_id,route_short_name,route_long_name\n"
        "T5800,T580,Awan Besar ~ TPM\n"
        "U6000,650,Elsewhere ~ Awan Besar\n"
    )
    trips = (
        "route_id,trip_id,trip_headsign\n"
        "T5800,t_loop_a,\n"
        "T5800,t_loop_b,\n"
        "T5800,t_short,\n"
        "U6000,t_other,Awan Besar\n"
    )
    # t_loop_a and t_loop_b are the same sequence at different times of day —
    # one pattern. t_short is a genuinely different sequence on the same route,
    # so the fixture exercises both collapsing and keeping.
    stop_times = (
        "trip_id,stop_sequence,arrival_time,stop_id\n"
        "t_loop_a,1,06:00:00,S1\n"
        "t_loop_a,2,06:01:00,S2\n"
        "t_loop_a,3,06:32:00,S3\n"
        "t_loop_a,4,06:40:00,S1\n"
        "t_loop_b,1,07:00:00,S1\n"
        "t_loop_b,2,07:01:00,S2\n"
        "t_loop_b,3,07:32:00,S3\n"
        "t_loop_b,4,07:40:00,S1\n"
        "t_short,1,09:00:00,S1\n"
        "t_short,2,09:05:00,S4\n"
        "t_short,3,09:12:00,S1\n"
        "t_other,1,08:00:00,S4\n"
        "t_other,2,08:10:00,S1\n"
    )
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('stops.txt', stops)
        zf.writestr('routes.txt', routes)
        zf.writestr('trips.txt', trips)
        zf.writestr('stop_times.txt', stop_times)
    return str(p)


def _use_timetable_zip(monkeypatch, path):
    """Point gtfs_static at a fixture ZIP and clear every index it fills."""
    import zipfile
    from utils import gtfs_static
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(path))
    monkeypatch.setattr(gtfs_static, '_zip_mtime', lambda slug: 1000.0)
    gtfs_static._TRIP_STOPS_INDEX.clear()
    gtfs_static._TRIP_HEADSIGN_INDEX.clear()
    gtfs_static._TRIP_FREQUENCY_INDEX.clear()
    gtfs_static._TRIP_INDEX_MTIME.clear()
    gtfs_static._STOP_ROUTES_INDEX.clear()
    gtfs_static._ROUTE_TRIPS_INDEX.clear()
    gtfs_static._ROUTE_PARTS_INDEX.clear()
    gtfs_static._ROUTE_PARTS_MTIME.clear()
    return gtfs_static


def test_routes_at_stop_lists_every_route_serving_it(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    names = [r['short'] for r in g.get_routes_at_stop('kl', 'S1')]
    assert names == ['650', 'T580']


def test_routes_at_stop_returns_the_only_route_that_serves_a_stop(tmp_path, monkeypatch):
    # The reported failure in miniature: three routes reach the interchange,
    # exactly one reaches the destination.
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    served = g.get_routes_at_stop('kl', 'S3')
    assert [r['short'] for r in served] == ['T580']
    assert served[0]['route_id'] == 'T5800'
    assert served[0]['long'] == 'Awan Besar ~ TPM'


def test_routes_at_stop_is_empty_for_an_unknown_stop(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    assert g.get_routes_at_stop('kl', 'NOPE') == []
    assert g.get_routes_at_stop('kl', '') == []


def test_routes_at_stop_is_empty_when_the_feed_is_unavailable(monkeypatch):
    from utils import gtfs_static
    def boom(slug):
        raise OSError('no zip')
    monkeypatch.setattr(gtfs_static, '_load_zip', boom)
    monkeypatch.setattr(gtfs_static, '_zip_mtime', lambda slug: 1.0)
    gtfs_static._STOP_ROUTES_INDEX.clear()
    gtfs_static._TRIP_INDEX_MTIME.clear()
    assert gtfs_static.get_routes_at_stop('kl', 'S1') == []


def test_route_patterns_collapse_trips_that_share_a_sequence(tmp_path, monkeypatch):
    # t_loop_a and t_loop_b visit the same stops at different times of day.
    # That is one pattern, not two.
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    sequences = [[s['stop_id'] for s in p['stops']]
                 for p in g.get_route_patterns('kl', 'T5800')]
    assert sequences.count(['S1', 'S2', 'S3', 'S1']) == 1


def test_route_patterns_returns_each_genuinely_different_sequence(tmp_path, monkeypatch):
    # 37 of Rapid KL's 136 routes run two patterns and one runs three. Showing
    # a single "the" sequence would be wrong for a quarter of the network.
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    sequences = [[s['stop_id'] for s in p['stops']]
                 for p in g.get_route_patterns('kl', 'T5800')]
    assert len(sequences) == 2
    assert ['S1', 'S2', 'S3', 'S1'] in sequences
    assert ['S1', 'S4', 'S1'] in sequences


def test_route_patterns_carry_arrival_seconds_for_journey_times(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    loop = next(p for p in g.get_route_patterns('kl', 'T5800')
                if [s['stop_id'] for s in p['stops']] == ['S1', 'S2', 'S3', 'S1'])
    base = loop['stops'][0]['arrival_seconds']
    assert [(s['arrival_seconds'] - base) // 60 for s in loop['stops']] == [0, 1, 32, 40]


def test_route_patterns_can_be_filtered_to_one_stop(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    # S2 is on the long loop only; S4 on the short one only; S9 on neither.
    assert len(g.get_route_patterns('kl', 'T5800', stop_id='S2')) == 1
    assert len(g.get_route_patterns('kl', 'T5800', stop_id='S4')) == 1
    assert g.get_route_patterns('kl', 'T5800', stop_id='S9') == []


def test_route_patterns_is_empty_for_an_unknown_route(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    assert g.get_route_patterns('kl', 'NOPE') == []
    assert g.get_route_patterns('kl', '') == []


def test_the_new_indexes_are_filled_by_the_same_pass_as_the_old_ones(tmp_path, monkeypatch):
    # They must be written together. An index built independently could survive
    # a rebuild of its siblings and serve a superseded timetable.
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    assert g._STOP_ROUTES_INDEX == {}
    g.get_trip_stops('kl', 't_loop_a')          # touches only the old API
    assert g._STOP_ROUTES_INDEX.get('kl'), "the new index was not filled by the shared pass"
    assert g._ROUTE_TRIPS_INDEX.get('kl')


def test_a_failed_build_stores_neither_new_index(monkeypatch):
    from utils import gtfs_static
    def boom(slug):
        raise OSError('no zip')
    monkeypatch.setattr(gtfs_static, '_load_zip', boom)
    monkeypatch.setattr(gtfs_static, '_zip_mtime', lambda slug: 1.0)
    gtfs_static._STOP_ROUTES_INDEX.clear()
    gtfs_static._ROUTE_TRIPS_INDEX.clear()
    gtfs_static._TRIP_INDEX_MTIME.clear()
    gtfs_static._build_trip_index('kl')
    assert 'kl' not in gtfs_static._STOP_ROUTES_INDEX
    assert 'kl' not in gtfs_static._ROUTE_TRIPS_INDEX
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "routes_at_stop or route_patterns or new_indexes or failed_build" -q
```

Expected: FAIL — `AttributeError: module 'utils.gtfs_static' has no attribute '_STOP_ROUTES_INDEX'`.

- [ ] **Step 3: Declare the two indexes**

In `src/utils/gtfs_static.py`, beside the existing `_TRIP_STOPS_INDEX` / `_TRIP_HEADSIGN_INDEX` / `_TRIP_FREQUENCY_INDEX` declarations (~line 344):

```python
# agency slug -> {stop_id: {route_id, ...}}
_STOP_ROUTES_INDEX = {}
# agency slug -> {route_id: [trip_id, ...]}
#
# Both are filled by _build_trip_index alongside the three above, never
# independently: they are derived from the same stop_times.txt pass, and an
# index that could be rebuilt on its own would let a stale entry survive a
# refresh of its siblings and serve a superseded timetable.
_ROUTE_TRIPS_INDEX = {}
```

- [ ] **Step 4: Fill them in `_build_trip_index`**

Add two locals beside `trip_stops`, `headsigns`, `frequency_trips`:

```python
    stop_routes = {}
    route_trips = {}
```

The existing loop over `trips.txt` reads only `trip_headsign`. Extend it to capture the route as well:

```python
            for row in _read_csv_from_zip(zf, 'trips.txt') or []:
                tid = (row.get('trip_id') or '').strip()
                if tid:
                    headsigns[tid] = (row.get('trip_headsign') or '').strip()
                    rid = (row.get('route_id') or '').strip()
                    if rid:
                        route_trips.setdefault(rid, []).append(tid)
```

Then, after `trip_stops` has been ordered and before the `except`, derive the stop→routes map from data already in hand:

```python
            # Derived from trip_stops, which is already built: no second parse
            # of stop_times.txt, which is 87,935 rows for Rapid Bus KL alone.
            trip_route = {tid: rid for rid, tids in route_trips.items() for tid in tids}
            for trip_id, entries in trip_stops.items():
                rid = trip_route.get(trip_id)
                if not rid:
                    continue
                for entry in entries:
                    stop_routes.setdefault(entry['stop_id'], set()).add(rid)
```

Finally, store them with the other three (after the `except`, in the block that already assigns `_TRIP_STOPS_INDEX`):

```python
    _STOP_ROUTES_INDEX[agency_slug] = stop_routes
    _ROUTE_TRIPS_INDEX[agency_slug] = route_trips
```

- [ ] **Step 5: Add the two public functions**

After `is_frequency_based` (~line 503):

```python
def get_routes_at_stop(agency_slug: str, stop_id: str) -> list:
    """
    Every route whose timetable calls at *stop_id*, sorted by display name.

    This is the timetable's answer, not the live feed's. The arrivals panels
    show buses currently en route, which is a different and much smaller set —
    a rider at LRT Awan Besar saw three routes arriving and could not learn
    that a fourth, the only one reaching their destination, serves the stop at
    all. Empty list on any missing or malformed data; never raises.
    """
    if not stop_id:
        return []
    if not _trip_index_is_current(agency_slug):
        _build_trip_index(agency_slug)
    route_ids = _STOP_ROUTES_INDEX.get(agency_slug, {}).get(stop_id.strip(), set())

    routes = []
    for route_id in route_ids:
        parts = get_route_parts(agency_slug, route_id)
        routes.append({'route_id': route_id,
                       'short': parts['short'],
                       'long': parts['long']})
    routes.sort(key=lambda r: (r['short'] or r['long'] or r['route_id']))
    return routes


def get_route_patterns(agency_slug: str, route_id: str, stop_id: str = None) -> list:
    """
    The distinct stop sequences *route_id* runs, optionally only those calling
    at *stop_id*.

    A route can run more than one pattern — 37 of Rapid KL's 136 routes run
    two and one runs three — so picking a single "the" sequence would be wrong
    for a quarter of the network, in a feature whose whole purpose is to stop
    the app misdirecting someone. Trips that visit the same stops in the same
    order are one pattern however many times a day they run.

    Each result carries a representative trip_id, because the headsign and
    whether the service runs to a headway are per-trip facts the caller needs
    and would otherwise have to re-derive.

    Empty list on any missing or malformed data; never raises.
    """
    if not route_id:
        return []
    if not _trip_index_is_current(agency_slug):
        _build_trip_index(agency_slug)

    trip_ids = _ROUTE_TRIPS_INDEX.get(agency_slug, {}).get(route_id.strip(), [])
    all_stops = _TRIP_STOPS_INDEX.get(agency_slug, {})
    wanted = stop_id.strip() if stop_id else None

    seen = set()
    patterns = []
    for trip_id in trip_ids:
        stops = all_stops.get(trip_id) or []
        if not stops:
            continue
        key = tuple(s['stop_id'] for s in stops)
        if key in seen:
            continue
        # Recorded before the filter, so de-duplication does not depend on
        # which stop was asked for.
        seen.add(key)
        if wanted and wanted not in key:
            continue
        patterns.append({'trip_id': trip_id, 'stops': stops})
    return patterns
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "routes_at_stop or route_patterns or new_indexes or failed_build" -q
```

Expected: all pass.

- [ ] **Step 7: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green.

- [ ] **Step 8: Commit**

```bash
git add src/utils/gtfs_static.py tests/test_script.py
git commit -m "feat: index which routes serve each stop, and their distinct stop patterns"
```

---

### Task 2: `src/utils/route_view.py` — the pure formatter

**Files:**
- Create: `src/utils/route_view.py`
- Test: `tests/test_route_view.py`

**Interfaces:**
- Consumes: the `stop_entry` shape from Task 1 — dicts with `stop_id`, `stop_name`, `arrival_seconds`. No import of `gtfs_static` is needed or wanted.
- Produces:
  - `build_stop_rows(stops, tapped_stop_id, nearby_by_id=None) -> [row, ...]` where `row` is `{'seq': int, 'stop_id': str, 'stop_name': str, 'offset_minutes': int | None, 'is_tapped': bool, 'near': {'distance_m': float, 'walk_label': str} | None}`
  - `pattern_label(stops, headsign='') -> str`
  - `pattern_titles(patterns, headsigns) -> [str]` — one unique title per pattern, same order

- [ ] **Step 1: Write the failing tests**

Create `tests/test_route_view.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import route_view


def _stops(*rows):
    """(stop_id, stop_name, minutes_from_start) -> stop entries."""
    return [{'stop_id': sid, 'stop_name': name, 'stop_lat': 3.0, 'stop_lon': 101.0,
             'arrival_seconds': 21600 + mins * 60}
            for sid, name, mins in rows]


LOOP = _stops(('S1', 'LRT AWAN BESAR', 0),
              ('S2', 'KM1 BUKIT JALIL', 1),
              ('S3', 'GREEN AVENUE CONDOMINIUM', 32),
              ('S1', 'LRT AWAN BESAR', 40))

LINE = _stops(('A', 'FIRST', 0), ('B', 'MIDDLE', 5), ('C', 'LAST', 12))


# ── journey times ──────────────────────────────────────────────────────

def test_offsets_are_measured_from_the_tapped_stop():
    rows = route_view.build_stop_rows(LOOP, 'S1')
    assert [r['offset_minutes'] for r in rows] == [0, 1, 32, 40]


def test_the_stop_named_after_the_destination_is_thirty_two_minutes_away():
    # The failure this feature exists to prevent: boarding at S1 and waiting
    # for the stop named after the building costs 32 minutes, while the useful
    # stop is 1 minute out and named after a shopping mall.
    rows = route_view.build_stop_rows(LOOP, 'S1')
    by_name = {r['stop_name']: r['offset_minutes'] for r in rows}
    assert by_name['KM1 BUKIT JALIL'] == 1
    assert by_name['GREEN AVENUE CONDOMINIUM'] == 32


def test_offsets_are_measured_from_a_mid_route_tapped_stop():
    rows = route_view.build_stop_rows(LINE, 'B')
    assert [r['offset_minutes'] for r in rows] == [-5, 0, 7]


def test_offsets_anchor_on_the_first_occurrence_of_a_repeated_stop():
    # S1 appears at both ends of the loop. Anchoring on the later one would
    # make every other stop negative.
    rows = route_view.build_stop_rows(LOOP, 'S1')
    assert rows[0]['offset_minutes'] == 0
    assert rows[-1]['offset_minutes'] == 40


def test_sequence_numbers_are_one_based_and_in_order():
    assert [r['seq'] for r in route_view.build_stop_rows(LOOP, 'S1')] == [1, 2, 3, 4]


# ── the tapped stop ────────────────────────────────────────────────────

def test_every_occurrence_of_the_tapped_stop_is_marked():
    rows = route_view.build_stop_rows(LOOP, 'S1')
    assert [r['is_tapped'] for r in rows] == [True, False, False, True]


def test_nothing_is_marked_when_the_tapped_stop_is_absent():
    rows = route_view.build_stop_rows(LOOP, 'ZZ')
    assert not any(r['is_tapped'] for r in rows)
    assert all(r['offset_minutes'] is None for r in rows), \
        "without an anchor there is no journey time to state"


# ── near-you marks ─────────────────────────────────────────────────────

def test_near_marks_are_applied_only_to_supplied_stops():
    nearby = {'S2': {'distance_m': 150.0, 'walk_label': '~3 min walk'},
              'S3': {'distance_m': 152.0, 'walk_label': '~3 min walk'}}
    rows = route_view.build_stop_rows(LOOP, 'S1', nearby)
    assert rows[0]['near'] is None
    assert rows[1]['near']['distance_m'] == 150.0
    assert rows[2]['near']['walk_label'] == '~3 min walk'


def test_near_marks_are_absent_when_no_map_is_given():
    assert all(r['near'] is None for r in route_view.build_stop_rows(LOOP, 'S1'))


# ── malformed input ────────────────────────────────────────────────────

def test_an_entry_without_a_time_still_renders_without_an_offset():
    broken = [{'stop_id': 'S1', 'stop_name': 'A', 'arrival_seconds': 0},
              {'stop_id': 'S2', 'stop_name': 'B'}]
    rows = route_view.build_stop_rows(broken, 'S1')
    assert rows[1]['offset_minutes'] is None
    assert rows[1]['stop_name'] == 'B'


def test_an_empty_pattern_yields_no_rows():
    assert route_view.build_stop_rows([], 'S1') == []


# ── labels ─────────────────────────────────────────────────────────────

def test_a_loop_is_labelled_by_where_it_starts():
    # "by its terminus" would read "to LRT AWAN BESAR", which explains nothing
    # when that is also where it began.
    assert route_view.pattern_label(LOOP) == 'loop from LRT AWAN BESAR'


def test_a_line_is_labelled_by_its_last_stop():
    assert route_view.pattern_label(LINE) == 'to LAST'


def test_a_published_headsign_wins():
    assert route_view.pattern_label(LOOP, 'Pavilion Bukit Jalil') == 'Pavilion Bukit Jalil'


def test_a_blank_headsign_falls_back_to_the_shape():
    assert route_view.pattern_label(LOOP, '   ') == 'loop from LRT AWAN BESAR'


def test_pattern_label_survives_an_empty_pattern():
    assert route_view.pattern_label([]) == ''


# ── unique titles ──────────────────────────────────────────────────────

def test_titles_state_the_stop_count_and_running_time():
    titles = route_view.pattern_titles([LOOP], [''])
    assert titles == ['loop from LRT AWAN BESAR · 4 stops · ~40 min']


def test_two_patterns_never_share_a_title():
    # Same shape, same endpoints, same length: only a suffix can separate them.
    other = _stops(('S1', 'LRT AWAN BESAR', 0), ('S9', 'VIA SOMEWHERE', 1),
                   ('S3', 'GREEN AVENUE CONDOMINIUM', 32), ('S1', 'LRT AWAN BESAR', 40))
    titles = route_view.pattern_titles([LOOP, other], ['', ''])
    assert len(set(titles)) == 2, titles
    assert titles[0] != titles[1]


def test_distinct_patterns_keep_their_natural_titles():
    titles = route_view.pattern_titles([LOOP, LINE], ['', ''])
    assert titles[0].startswith('loop from LRT AWAN BESAR')
    assert titles[1].startswith('to LAST')


def test_titles_tolerate_a_short_headsign_list():
    assert len(route_view.pattern_titles([LOOP, LINE], [])) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_route_view.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'utils.route_view'`.

- [ ] **Step 3: Write the implementation**

Create `src/utils/route_view.py`:

```python
"""
Turning one route's stop sequence into rows a panel can render.

Pure: no Streamlit, no GTFS, no I/O. Everything here is a function over plain
dicts, so loops, repeated stops and multi-pattern routes are all testable
without a browser or a feed.

The case that motivated this module: T580 leaves Stesen LRT Awan Besar,
reaches KM1 Bukit Jalil after one minute, and comes back past Green Avenue
Condominium 32 minutes later on the return leg of a 40-minute loop. The two
stops are 60 m apart on the ground. A rider heading for the condominium who
trusts the stop names rides 32 minutes instead of 1, and the next bus is ~50
minutes away. 100 of Rapid KL's 136 timetabled routes are loops, so this is
the normal shape of the network rather than an oddity.
"""


def build_stop_rows(stops, tapped_stop_id, nearby_by_id=None):
    """
    Display rows for one stop sequence, timed from *tapped_stop_id*.

    offset_minutes is measured from the FIRST occurrence of the tapped stop.
    On a loop the tapped stop appears at both ends; anchoring on the later one
    would make every other stop negative. It is None when the sequence never
    calls at that stop, or when an entry carries no time — a missing offset is
    stated as nothing rather than as a wrong number.

    is_tapped is set on every occurrence, so both ends of a loop are marked and
    the rider can see the route returns.

    nearby_by_id is {stop_id: {'distance_m', 'walk_label'}}, supplied by the
    caller. This module never measures a distance or calls a routing API; it
    only annotates what it is given.
    """
    nearby_by_id = nearby_by_id or {}

    anchor = None
    for entry in stops:
        if entry.get('stop_id') == tapped_stop_id:
            anchor = entry.get('arrival_seconds')
            break

    rows = []
    for index, entry in enumerate(stops, start=1):
        seconds = entry.get('arrival_seconds')
        if anchor is None or seconds is None:
            offset = None
        else:
            offset = round((seconds - anchor) / 60)
        rows.append({
            'seq': index,
            'stop_id': entry.get('stop_id', ''),
            'stop_name': entry.get('stop_name', ''),
            'offset_minutes': offset,
            'is_tapped': entry.get('stop_id') == tapped_stop_id,
            'near': nearby_by_id.get(entry.get('stop_id')),
        })
    return rows


def pattern_label(stops, headsign=''):
    """
    How to name one stop sequence in a heading.

    A published headsign wins: it is the operator's own wording, and it is what
    a rider reads on the front of the bus. These feeds frequently leave it
    blank, hence the fallbacks.

    Labelling by terminus fails on exactly the routes this feature exists for.
    A loop's last stop is its first, so "to LRT Awan Besar" would describe a
    40-minute circle as though it were a destination. A loop is therefore named
    by where it starts.
    """
    if headsign and headsign.strip():
        return headsign.strip()
    if not stops:
        return ''
    first, last = stops[0], stops[-1]
    if first.get('stop_id') == last.get('stop_id'):
        return f"loop from {first.get('stop_name', '')}"
    return f"to {last.get('stop_name', '')}"


def _running_minutes(stops):
    """End-to-end time for a sequence, or None when it cannot be measured."""
    if len(stops) < 2:
        return None
    first = stops[0].get('arrival_seconds')
    last = stops[-1].get('arrival_seconds')
    if first is None or last is None:
        return None
    return round((last - first) / 60)


def pattern_titles(patterns, headsigns):
    """
    One title per pattern, in the same order, guaranteed unique.

    Two patterns of the same route rendering as two identical headings would
    leave a rider unable to tell which one they had opened. Uniqueness is
    therefore constructed here rather than hoped for in the data: the stop
    count and running time separate most collisions, and a numbered suffix
    settles the rest.

    *patterns* are the dicts get_route_patterns returns, or bare stop lists.
    """
    titles = []
    for index, pattern in enumerate(patterns):
        stops = pattern.get('stops', pattern) if isinstance(pattern, dict) else pattern
        headsign = headsigns[index] if index < len(headsigns) else ''
        title = pattern_label(stops, headsign)
        if stops:
            title += f" · {len(stops)} stops"
        minutes = _running_minutes(stops)
        if minutes is not None:
            title += f" · ~{minutes} min"
        titles.append(title)

    seen = {}
    unique = []
    for title in titles:
        seen[title] = seen.get(title, 0) + 1
        unique.append(title if seen[title] == 1 else f"{title} ({seen[title]})")
    return unique
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_route_view.py -q
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
git add src/utils/route_view.py tests/test_route_view.py
git commit -m "feat: format a route's stop sequence into rows timed from a tapped stop"
```

---

### Task 3: Render serving routes and their stop sequences in the tapped-stop panel

**Files:**
- Modify: `src/app_pages/live_map.py` (imports ~line 27; the tapped-stop panel, currently `if selected_stop_id:` at ~line 1007 through the clear button at ~line 1056)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: `gtfs_static.get_routes_at_stop`, `gtfs_static.get_route_patterns`, `gtfs_static.get_trip_headsign`, `gtfs_static.is_frequency_based` (Task 1); `route_view.build_stop_rows`, `route_view.pattern_titles` (Task 2); the existing `_walk_label`, `walking.walk_times`, `_nearby_stops`, `ors_key`, `ARRIVAL_ACCURACY_NOTE`.
- Produces: no new public function; the panel gains a `Serves:` line and one expander per pattern.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`. These use the existing `st_stub` / `live_map.show()` harness and the `_live_map_with_selection` helper already used by the stop-selection tests:

```python
def test_the_stop_panel_names_every_route_that_serves_the_stop(monkeypatch, st_stub):
    # The reported failure: the panel listed the routes with a live bus and so
    # omitted the only route that reaches the rider's destination.
    from app_pages import live_map
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [
                            {'route_id': 'T5800', 'short': 'T580', 'long': 'Awan Besar ~ TPM'},
                            {'route_id': 'S6060', 'short': 'PAVBJ', 'long': 'Awan Besar ~ Pavilion'},
                        ])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns', lambda *a, **k: [])
    _render_stop_panel(monkeypatch, st_stub)

    said = _texts(st_stub.info) + _texts(st_stub.markdown) + _texts(st_stub.caption)
    assert 'T580' in said, "a route with no live bus must still be listed"
    assert 'PAVBJ' in said


def test_the_stop_panel_opens_a_route_to_its_stop_sequence(monkeypatch, st_stub):
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'KM1 BUKIT JALIL', 'arrival_seconds': 60},
             {'stop_id': 'S3', 'stop_name': 'GREEN AVENUE', 'arrival_seconds': 1920},
             {'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 2400}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'T5800', 'short': 'T580', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: True)
    _render_stop_panel(monkeypatch, st_stub)

    said = _texts(st_stub.markdown) + _texts(st_stub.caption)
    assert 'KM1 BUKIT JALIL' in said
    assert '+1 min' in said, "the useful stop is one minute out"
    assert '+32 min' in said, "the stop named after the destination is 32 minutes out"


def test_stops_before_the_tapped_one_are_not_rendered_as_plus_minus(monkeypatch, st_stub):
    # A stop the bus passes before reaching yours has a negative offset.
    # Rendering it through the "+N min" branch would print "+-5 min".
    from app_pages import live_map
    stops = [{'stop_id': 'S0', 'stop_name': 'BEFORE', 'arrival_seconds': 0},
             {'stop_id': 'S1', 'stop_name': 'YOURS', 'arrival_seconds': 300},
             {'stop_id': 'S2', 'stop_name': 'AFTER', 'arrival_seconds': 720}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'R', 'short': 'R1', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: False)
    _render_stop_panel(monkeypatch, st_stub)

    said = _texts(st_stub.markdown)
    assert '+-' not in said, said
    assert '5 min earlier' in said
    assert '+7 min' in said


def test_the_stop_panel_says_journey_times_come_from_the_timetable(monkeypatch, st_stub):
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'A', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'B', 'arrival_seconds': 60}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'R', 'short': 'R1', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: False)
    _render_stop_panel(monkeypatch, st_stub)

    said = _texts(st_stub.caption)
    assert 'timetable' in said.lower()


def test_the_stop_panel_never_invents_a_departure_time_for_a_headway_route(monkeypatch, st_stub):
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'A', 'arrival_seconds': 21600},
             {'stop_id': 'S2', 'stop_name': 'B', 'arrival_seconds': 21660}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'R', 'short': 'R1', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: True)
    _render_stop_panel(monkeypatch, st_stub)

    said = _texts(st_stub.markdown) + _texts(st_stub.caption)
    assert '06:00' not in said, "a headway trip has no published departure to show"
```

Add the shared helper beside them:

```python
def _render_stop_panel(monkeypatch, st_stub):
    """
    Run live_map.show() with a stop already selected, so the tapped-stop panel
    renders. Mirrors the setup the stop-selection tests already use.
    """
    from app_pages import live_map
    st_stub.session_state['selected_stop_id'] = 'S1'
    st_stub.session_state['user_location'] = {'lat': 3.06, 'lon': 101.67, 'accuracy': 10}
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR',
                                          'stop_lat': 3.0621, 'stop_lon': 101.6706,
                                          'distance_m': 560.0}])
    live_map.show()
```

Adapt `_render_stop_panel` to whatever the existing stop-selection tests do for `df_live`, region selection and the deck stub — read `test_a_selected_stop_out_of_range_clears_itself` and reuse its scaffolding rather than inventing new stubs.

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "stop_panel_names or stop_sequence or from_the_timetable or invents_a_departure" -q
```

Expected: FAIL — the `Serves:` line and the expanders do not exist yet.

- [ ] **Step 3: Import the new module**

In `src/app_pages/live_map.py`, alongside `from utils import walking`:

```python
from utils import route_view
```

- [ ] **Step 4: Add the serving-routes line and the expanders**

Inside the tapped-stop panel, after the `st.info(...)` / `st.caption(ARRIVAL_ACCURACY_NOTE)` pair and before the `Clear stop selection` button:

```python
            # Every route the timetable says calls here, not only those with a
            # bus running right now. A rider at this stop saw three arrivals
            # and could not learn that a fourth route — the only one reaching
            # their destination — serves the stop at all.
            routes = gtfs_static.get_routes_at_stop(agency_slug, stop['stop_id'])
            if routes:
                st.markdown("**Serves:** " + " · ".join(
                    r['short'] or r['long'] or r['route_id'] for r in routes))

                # Marking stops near the user costs no extra routing request:
                # _nearby_stops was already resolved for this render and
                # walking.walk_times caches per stop.
                nearby_walks = walking.walk_times(
                    loc['lat'], loc['lon'], _nearby_stops or [], agency_slug,
                    api_key=ors_key)
                nearby_by_id = {
                    s['stop_id']: {
                        'distance_m': nearby_walks[s['stop_id']]['distance_m'],
                        'walk_label': _walk_label(nearby_walks[s['stop_id']]),
                    }
                    for s in (_nearby_stops or [])
                    if s['stop_id'] in nearby_walks
                }

                for route in routes:
                    patterns = gtfs_static.get_route_patterns(
                        agency_slug, route['route_id'], stop['stop_id'])
                    if not patterns:
                        continue
                    headsigns = [gtfs_static.get_trip_headsign(agency_slug, p['trip_id'])
                                 for p in patterns]
                    titles = route_view.pattern_titles(patterns, headsigns)
                    label = route['short'] or route['long'] or route['route_id']

                    for pattern, title in zip(patterns, titles):
                        with st.expander(f"{label} — {title}"):
                            for row in route_view.build_stop_rows(
                                    pattern['stops'], stop['stop_id'], nearby_by_id):
                                line = f"`{row['seq']:>2}`  {row['stop_name']}"
                                offset = row['offset_minutes']
                                if row['is_tapped']:
                                    line += "  ← you tapped this"
                                elif offset is not None and offset < 0:
                                    # Stops the bus passes before reaching the
                                    # tapped one. "+-5 min" is not a time.
                                    line += f"  · {abs(offset)} min earlier"
                                elif offset is not None:
                                    line += f"  · +{offset} min"
                                st.markdown(line)
                                if row['near']:
                                    st.caption(
                                        f"       ~{int(row['near']['distance_m'])} m "
                                        f"from you · {row['near']['walk_label']}")
                            # Differences between timetabled stop times, not a
                            # live prediction, and for a headway service there
                            # is no published departure to state at all.
                            note = ("Journey times from the published timetable, "
                                    "measured from the stop you tapped.")
                            if gtfs_static.is_frequency_based(agency_slug, pattern['trip_id']):
                                note += " This route runs to a headway, not a fixed timetable."
                            st.caption(note)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "stop_panel_names or stop_sequence or from_the_timetable or invents_a_departure" -q
```

Expected: all pass.

- [ ] **Step 6: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green.

- [ ] **Step 7: Verify the suite still makes no network calls**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python - <<'PY'
import sys, os
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))
from utils import walking
calls = {'n': 0}
def spy(*a, **k):
    calls['n'] += 1
    raise walking.requests.RequestException('blocked')
walking.requests.post = spy
import pytest
pytest.main(['-q', 'tests/', '--no-header', '-p', 'no:cacheprovider'])
print('LIVE ORS REQUEST ATTEMPTS:', calls['n'])
PY
```

Expected: `LIVE ORS REQUEST ATTEMPTS: 0`. Anything else means the new panel code reached the network — fix it before committing.

- [ ] **Step 8: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "feat: list every route serving a tapped stop and open each to its stop sequence"
```

---

### Task 4: Documentation and version

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml`
- Verify unchanged: `requirements.txt`, `requirements-dev.txt`

- [ ] **Step 1: Update `README.md`**

In the section describing the tapped-stop panel, document that it now lists every route the timetable says serves the stop — including routes with no bus running — and that each route opens to its full stop sequence with journey times measured from the tapped stop, with stops near you marked.

State these limits plainly, in the project's established voice:

- Journey times are differences between timetabled stop times, not live predictions.
- A route that runs to a headway is **flagged** as one — never given a fabricated departure time,
  and never given a frequency figure either. `headway_secs` is not read from `frequencies.txt` and
  no interval is retained anywhere in the codebase, so the panel states only that the route runs to
  a headway rather than to a fixed timetable.
- A route with more than one stop pattern appears once per pattern; they are not merged.
- This is not a journey planner: it answers "does this bus stop at X and how far along is it", not "how do I get from A to B".

- [ ] **Step 2: Update `CHANGELOG.md`**

Open a `## [2.8.0] - 2026-08-01` section. Cover, with the measured evidence:

- The tapped-stop panel listed only routes with a live bus. At KL2324 LRT AWAN BESAR that meant three routes — `651`, `652`, `PAVILION BUKIT JALIL (PAVBJ)` — none of which reach KL1743 GREEN AVENUE CONDOMINIUM. The one route that does, `T580`, was absent because no T580 vehicle happened to be running toward the stop. Every route the timetable serves is now listed whether or not a bus is moving.
- Tapping a route now opens its full stop sequence with journey times from the tapped stop. On T580 that shows KM1 BUKIT JALIL at +1 min and GREEN AVENUE CONDOMINIUM at +32 min — the same 40-minute loop, 60 m apart on the ground, on opposite legs. A rider trusting the stop name rides 32 minutes instead of 1, and the next bus is about 50 minutes behind.
- 100 of the 136 Rapid KL routes with timetable data are loops, so this trap is the network's normal shape rather than one route's quirk.
- Stops within walking distance are marked in the sequence, reusing the walking data the panel already has — no extra routing requests.
- A route running more than one stop pattern is shown once per pattern, each distinctly titled. 37 routes run two patterns and one runs three; picking a single sequence would be wrong for a quarter of the network.

- [ ] **Step 3: Bump `pyproject.toml` to 2.8.0**

- [ ] **Step 4: Verify the manifests**

```bash
git diff --stat requirements.txt requirements-dev.txt
grep -n "version" pyproject.toml | head -2
head -12 CHANGELOG.md
```

Expected: no diff on either requirements file; `pyproject.toml` reads `2.8.0`; the CHANGELOG's top section is `[2.8.0]`.

- [ ] **Step 5: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green.

- [ ] **Step 6: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml
git commit -m "docs: document routes at a stop and route stop sequences, bump to 2.8.0"
```

---

## Manual verification

Automated tests cannot see a map. After Task 4, deploy and check on a phone-width viewport:

1. Tap KL2324 LRT AWAN BESAR. The panel lists **T580** among the serving routes even when no T580 bus is running.
2. Open T580. KM1 BUKIT JALIL reads **+1 min**; GREEN AVENUE CONDOMINIUM reads **+32 min**; LRT AWAN BESAR appears at both ends of the list, marked as the stop you tapped.
3. Both stops near your building carry a near-you distance mark.
4. The expander is collapsed on arrival — a 35-row list must not push the map off screen.
5. A route with two patterns shows two expanders with visibly different titles.
