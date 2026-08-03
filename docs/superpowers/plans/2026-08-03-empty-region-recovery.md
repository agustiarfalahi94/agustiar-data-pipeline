# Empty-Region Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the app deleting the map, the stop rings and the way forward whenever the selected region has no live vehicles or does not contain the user.

**Architecture:** Two pure lookups are added to `gtfs_static.py` — one that finds which other regions have stops near a point, one that resolves a hand-maintained route alias. `live_map.py` then stops returning early on an empty vehicle frame, widens its stop search when the first radius finds nothing, and uses both lookups to offer a way out instead of a dead end.

**Tech Stack:** Python 3, Streamlit, pydeck, pandas, pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-08-03-empty-region-recovery-design.md`

## Global Constraints

- Target version **2.10.0**. `CHANGELOG.md`, `pyproject.toml` and `README.md` must agree, per `CLAUDE.md`.
- **No new dependency.** `requirements.txt`, `requirements-dev.txt` and the `pyproject.toml` dependency list are unchanged. Verify, do not edit.
- The suite must pass under **both** pandas string dtypes; both commands appear in every task.
- **Tests must make no network calls.** The `PageTestReachedNetwork` guard added in 2.7.1 stays in force.
- **Nothing may raise into a Streamlit render.** Every new lookup returns `[]`/`None` on missing or malformed data.
- **Every message naming a radius takes it from the radius actually applied**, never from a literal. 2.6.0 already fixed a version of the copy disagreeing with the number.
- **An alias must disclose itself.** A match found through the hand-maintained table says so; a local guess is never presented as feed data.
- The stop-selection state machine (`cleared_stop_id`, `deck_generation`, last-tap-wins) took two fix rounds in 2.7.0. Do not restructure it.

---

### Task 1: Two lookups in `gtfs_static.py`

**Files:**
- Modify: `src/utils/gtfs_static.py` (`STATIC_API_SOURCES` at :25; new functions after `get_stops_near` at :654)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: the existing `get_stops_near(agency_slug, lat, lon, radius_m, limit)`.
- Produces:
  - `find_regions_with_stops_near(lat, lon, radius_m=1500, exclude_slug=None, limit=3) -> [{'region': str, 'slug': str, 'count': int, 'nearest_m': float}, ...]` nearest first
  - `resolve_route_alias(query) -> (resolved_query: str, alias_source: str | None)`
  - `ROUTE_ALIASES` — module-level dict

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`:

```python
# ── Which other region has stops near me? ─────────────────────────────────

def _stub_stops_near(monkeypatch, by_slug):
    """by_slug: {slug: [(stop_id, distance_m), ...]}."""
    from utils import gtfs_static

    def fake(slug, lat, lon, radius_m=800, limit=5):
        rows = by_slug.get(slug)
        if rows is None:
            raise OSError('no timetable for ' + slug)
        return [{'stop_id': sid, 'stop_name': 'S' + sid, 'stop_lat': 3.0,
                 'stop_lon': 101.0, 'distance_m': float(d)}
                for sid, d in rows if d <= radius_m][:limit]

    monkeypatch.setattr(gtfs_static, 'get_stops_near', fake)
    return gtfs_static


def test_region_scan_orders_regions_by_their_closest_stop(monkeypatch):
    g = _stub_stops_near(monkeypatch, {
        'prasarana?category=rapid-bus-kl': [('a', 152), ('b', 300)],
        'prasarana?category=rapid-bus-mrtfeeder': [('c', 1200)],
        'ktmb': [],
    })
    out = g.find_regions_with_stops_near(3.0586, 101.6739)
    assert [r['region'] for r in out] == ['Rapid Bus KL', 'Rapid Bus MRT Feeder']
    assert out[0]['nearest_m'] == 152
    assert out[0]['count'] == 2


def test_region_scan_excludes_the_region_already_selected(monkeypatch):
    g = _stub_stops_near(monkeypatch, {
        'prasarana?category=rapid-bus-kl': [('a', 152)],
        'ktmb': [('k', 400)],
    })
    out = g.find_regions_with_stops_near(
        3.0586, 101.6739, exclude_slug='prasarana?category=rapid-bus-kl')
    assert [r['region'] for r in out] == ['KTM Berhad']


def test_region_scan_skips_an_agency_whose_timetable_is_unavailable(monkeypatch):
    # One dead feed must not cost the user the other twelve answers.
    g = _stub_stops_near(monkeypatch, {'ktmb': [('k', 400)]})   # every other slug raises
    out = g.find_regions_with_stops_near(3.0586, 101.6739)
    assert [r['region'] for r in out] == ['KTM Berhad']


def test_region_scan_returns_nothing_when_no_region_has_stops(monkeypatch):
    g = _stub_stops_near(monkeypatch, {'ktmb': [], 'mybas-melaka': []})
    assert g.find_regions_with_stops_near(3.0586, 101.6739) == []


def test_region_scan_caps_the_number_of_suggestions(monkeypatch):
    g = _stub_stops_near(monkeypatch, {
        'ktmb': [('a', 100)], 'mybas-melaka': [('b', 200)],
        'mybas-johor': [('c', 300)], 'mybas-kuching': [('d', 400)],
    })
    assert len(g.find_regions_with_stops_near(3.0586, 101.6739, limit=2)) == 2


# ── Route aliases ─────────────────────────────────────────────────────────

def test_gokl14_resolves_to_the_route_the_feed_publishes():
    # The bus is branded GOKL14; no GOKL route exists anywhere in the feed.
    from utils import gtfs_static
    resolved, source = gtfs_static.resolve_route_alias('GOKL14')
    assert resolved == 'PAVILION BUKIT JALIL (PAVBJ)'
    assert source == 'GOKL14', "the caller needs this to disclose the alias"


def test_alias_lookup_is_case_and_space_insensitive():
    from utils import gtfs_static
    for q in ('gokl14', '  GoKL14 '):
        resolved, source = gtfs_static.resolve_route_alias(q)
        assert resolved == 'PAVILION BUKIT JALIL (PAVBJ)', q
        assert source is not None


def test_a_real_route_name_is_never_rewritten():
    from utils import gtfs_static
    resolved, source = gtfs_static.resolve_route_alias('T580')
    assert resolved == 'T580'
    assert source is None, "no disclosure when the feed's own name matched"


def test_an_unknown_query_passes_through_untouched():
    from utils import gtfs_static
    assert gtfs_static.resolve_route_alias('ZZZ9') == ('ZZZ9', None)
    assert gtfs_static.resolve_route_alias('') == ('', None)
    assert gtfs_static.resolve_route_alias(None) == ('', None)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "region_scan or alias or gokl14" -q
```

Expected: FAIL — `module 'utils.gtfs_static' has no attribute 'find_regions_with_stops_near'`.

- [ ] **Step 3: Add the alias table beside `STATIC_API_SOURCES`**

```python
# Hand-maintained, and not from any feed. A rider reads "GOKL14" on the front
# of the bus; Prasarana publishes that route as PAVILION BUKIT JALIL (PAVBJ),
# route_id S6060. Searched across all 137 Rapid KL routes, no GOKL route
# exists anywhere in the data — the connection lives only on the vehicle's
# livery, so nothing in the feed can be derived from it.
#
# The standing risk: route branding changes and this table will not notice.
# Every match made through it is disclosed to the user for that reason.
ROUTE_ALIASES = {
    'GOKL14': 'PAVILION BUKIT JALIL (PAVBJ)',
}
```

- [ ] **Step 4: Add both functions after `get_stops_near`**

```python
def resolve_route_alias(query):
    """
    Map a rider's wording onto the name the feed publishes.

    Returns (resolved_query, alias_source). alias_source is the matched alias
    when the table did the work and None otherwise, so the caller can disclose
    the substitution — a hand-written guess must never be presented as feed
    data.

    A query that is not a key passes through untouched: the table is consulted,
    never imposed, so a real route name can never be rewritten by it.
    """
    text = (query or '').strip()
    if not text:
        return '', None
    canonical = ROUTE_ALIASES.get(text.upper())
    if canonical is None:
        return text, None
    return canonical, text.upper()


def find_regions_with_stops_near(lat, lon, radius_m=1500, exclude_slug=None,
                                 limit=3):
    """
    Regions with stops near (lat, lon), closest stop first.

    Called only when the selected region has no stops near the user — a dead
    end where the app has already failed to help. Standing in Bukit Jalil with
    KTM Berhad selected, the panel said only "no stops found", while Rapid Bus
    KL had 15 within 800 m and the app knew it.

    An agency whose timetable is missing or unreadable is skipped rather than
    raising: one dead feed must not cost the user the other twelve answers.
    """
    found = []
    for region, slug in STATIC_API_SOURCES.items():
        if exclude_slug and slug == exclude_slug:
            continue
        try:
            stops = get_stops_near(slug, lat, lon, radius_m=radius_m, limit=50)
        except Exception:
            continue
        if not stops:
            continue
        found.append({
            'region': region,
            'slug': slug,
            'count': len(stops),
            'nearest_m': min(s['distance_m'] for s in stops),
        })

    found.sort(key=lambda r: r['nearest_m'])
    return found[:limit]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "region_scan or alias or gokl14" -q
```

Expected: all pass.

- [ ] **Step 6: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

- [ ] **Step 7: Commit**

```bash
git add src/utils/gtfs_static.py tests/test_script.py
git commit -m "feat: find regions with stops near a point, and resolve route aliases"
```

---

### Task 2: The map survives an empty vehicle frame

**Files:**
- Modify: `src/app_pages/live_map.py` (the two early returns at :448 and :462; the vehicle-layer construction; the tapped-vehicle panel)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: a `no_vehicles` boolean in `show()`; the deck is built in every branch where a user location exists.

**This is the riskiest task in the plan.** `show()` is long and the code after the early returns assumes `df_map` has rows. Work carefully and report anything the plan did not anticipate.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_map_still_renders_when_the_region_has_no_vehicles(monkeypatch):
    # Rapid Bus KL's feed went quiet upstream. The map, the user's marker and
    # the stop rings all vanished with it -- but stops come from the timetable
    # and never needed a live vehicle.
    st_stub = _live_map_no_vehicles(monkeypatch)
    assert st_stub.pydeck_chart.called, "the deck was not built"
    said = _texts(st_stub.warning)
    assert 'has reported in the last' in said, "the cause must still be named"


def test_nearby_stops_are_still_offered_when_no_vehicle_is_reporting(monkeypatch):
    st_stub = _live_map_no_vehicles(monkeypatch)
    said = _texts(st_stub.markdown) + _texts(st_stub.info) + _texts(st_stub.caption)
    assert 'GREEN AVENUE' in said, "timetable stops disappeared with the buses"


def test_no_vehicle_layer_is_built_when_there_are_no_vehicles(monkeypatch):
    # Building a vehicles layer from an empty frame would need columns that
    # were never computed.
    st_stub = _live_map_no_vehicles(monkeypatch)
    deck = st_stub.pydeck_chart.call_args[0][0]
    ids = [l.id for l in deck.layers]
    assert 'vehicles' not in ids, ids
    assert 'nearby-stops' in ids, ids
```

These need a fixture, `_live_map_no_vehicles(monkeypatch)`. **Do not invent new scaffolding for
it** — `tests/test_script.py:1349` already defines `_live_map_with_selection(monkeypatch,
selection_payload, df=None)`, which drives `live_map.show()` with a stubbed Streamlit and a
dataframe. Read that first and build the new fixture from the same parts.

What it must set up, precisely:

- `df_live` **non-empty overall** but containing **no rows for the selected region**. That is the
  exact shape of the outage: another region is reporting, so the network-wide return at `:276` is
  not taken, while `region_row_count == 0` for the selected one. A fixture whose `df_live` is
  empty tests the wrong branch.
- `user_location` set in session state, so a map is expected at all.
- `gtfs_static.get_stops_near` stubbed to return one stop named `GREEN AVENUE CONDOMINIUM` with a
  `distance_m` inside the primary radius, so the "stops survived" assertion has something to find.
- The stubbed `st` returned, so the tests can read `pydeck_chart.call_args` and the text mocks.

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "no_vehicles or still_renders or still_offered" -q
```

Expected: FAIL — no deck is built; `pydeck_chart.called` is False.

- [ ] **Step 3: Replace the two early returns with a flag**

At `live_map.py:435-448`, keep every warning exactly as written — the three messages distinguish
different causes and each was written deliberately — and replace only the `return`:

```python
    no_vehicles = False
    if df_map.empty:
        if region_row_count == 0:
            st.warning(
                f"No vehicle in {selected_region} has reported in the last {window_label}."
            )
        else:
            st.warning(
                f"{region_row_count} vehicle(s) reported for {selected_region}, but none "
                "carried usable coordinates."
            )
        # No return: stops come from the published timetable and do not need a
        # live vehicle. Returning here deleted the map, the user's own location
        # marker and the tapped-stop panel along with the buses.
        no_vehicles = True
```

At `:457-462`, the same for the all-hidden case:

```python
    if not no_vehicles and df_map.empty:
        st.warning(
            f"No recent data for {selected_region} — "
            f"{hidden_count} vehicle(s) last reported over {drawn_label} ago."
        )
        no_vehicles = True
```

Leave the network-wide empty return at `:276` alone: with no rows at all there is no region
context, and its message already tells the user what to do.

- [ ] **Step 4: Guard the vehicle-derived work**

Everything that reads a vehicle column must be skipped when `no_vehicles` is true. Wrap, do not
delete:

- the `speed_display` / `bearing_display` / `last_report_display` / `dot_color` / `arrow_color` /
  `arrow_path` / `tip_html` column construction
- the `vehicles` `ScatterplotLayer` and the arrow `PathLayer` — these are appended to `layers` only
  when there are vehicles
- the route-search filtering block at `:524`
- the tapped-vehicle panel and its selection resolution
- the "Showing N active vehicles" caption

The user marker, the accuracy circle, the `nearby-stops` layer, the deck itself, the tapped-stop
panel and `Arrivals near you` all still run.

- [ ] **Step 5: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "no_vehicles or still_renders or still_offered" -q
```

- [ ] **Step 6: Run the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Expected: both green. Existing tests that asserted the page returns early on an empty region will
need their expectation updated — the page now renders. Update the assertion to match the new
behaviour; do not restore the early return.

- [ ] **Step 7: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "fix: keep the map and its stops when a region reports no vehicles"
```

---

### Task 3: Progressive radius and the region hint

**Files:**
- Modify: `src/app_pages/live_map.py` (`NEARBY_STOP_RADIUS_M` at :73; `_nearby_stops` resolution at :719; the "No stops found" branch at :1249)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: `gtfs_static.find_regions_with_stops_near` (Task 1).
- Produces: `NEARBY_STOP_WIDE_RADIUS_M = 1500`; a `_nearby_radius_used` value carried alongside `_nearby_stops`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_stop_search_widens_when_the_first_radius_finds_nothing(monkeypatch):
    from app_pages import live_map
    asked = []

    def fake(slug, lat, lon, radius_m=800, limit=5):
        asked.append(radius_m)
        return [] if radius_m <= live_map.NEARBY_STOP_RADIUS_M else [
            {'stop_id': 'S1', 'stop_name': 'FAR STOP', 'stop_lat': 3.0,
             'stop_lon': 101.0, 'distance_m': 900.0}]

    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', fake)
    st_stub = _render_live_map(monkeypatch)

    assert live_map.NEARBY_STOP_RADIUS_M in asked
    assert live_map.NEARBY_STOP_WIDE_RADIUS_M in asked
    said = _texts(st_stub.markdown) + _texts(st_stub.info) + _texts(st_stub.caption)
    assert 'FAR STOP' in said
    assert str(live_map.NEARBY_STOP_WIDE_RADIUS_M) in said, \
        "the panel must name the radius it actually used"


def test_the_stop_search_does_not_widen_when_the_first_radius_finds_stops(monkeypatch):
    from app_pages import live_map
    asked = []

    def fake(slug, lat, lon, radius_m=800, limit=5):
        asked.append(radius_m)
        return [{'stop_id': 'S1', 'stop_name': 'NEAR STOP', 'stop_lat': 3.0,
                 'stop_lon': 101.0, 'distance_m': 200.0}]

    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', fake)
    _render_live_map(monkeypatch)
    assert live_map.NEARBY_STOP_WIDE_RADIUS_M not in asked, \
        "widening when the near search succeeded costs a second scan for nothing"


def test_the_dead_end_names_regions_that_do_have_stops_near_you(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [])
    monkeypatch.setattr(live_map.gtfs_static, 'find_regions_with_stops_near',
                        lambda *a, **k: [
                            {'region': 'Rapid Bus KL', 'slug': 's1',
                             'count': 15, 'nearest_m': 152.0}])
    st_stub = _render_live_map(monkeypatch)

    said = _texts(st_stub.markdown) + _texts(st_stub.info) + _texts(st_stub.caption)
    assert 'Rapid Bus KL' in said
    assert '15' in said
    assert 'Switch region' in said


def test_the_region_scan_runs_only_at_the_dead_end(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'NEAR',
                                          'stop_lat': 3.0, 'stop_lon': 101.0,
                                          'distance_m': 100.0}])

    def explode(*a, **k):
        raise AssertionError('scanned every region when stops were already found')

    monkeypatch.setattr(live_map.gtfs_static, 'find_regions_with_stops_near', explode)
    _render_live_map(monkeypatch)
```

These need `_render_live_map(monkeypatch, route_query='')`. Build it from the same parts as
`_live_map_with_selection` (`tests/test_script.py:1349`) — do not invent a parallel harness.

What it must set up:

- `user_location` in session state, and a selected region **with** vehicles, so the page reaches
  the stop-resolution and arrivals code rather than an early branch.
- `route_query` forwarded to the search box stub, defaulting to `''` — Task 4's tests reuse this
  fixture with `route_query='GOKL14'`.
- The stubbed `st` returned so tests can read the text mocks.

Each test then overrides `gtfs_static.get_stops_near` (and, where relevant,
`find_regions_with_stops_near`) for the case it is exercising.

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "widens or dead_end or region_scan_runs" -q
```

- [ ] **Step 3: Add the wide radius constant**

Beside `NEARBY_STOP_RADIUS_M` at `:73`:

```python
# Tried only when the primary radius finds nothing. A rider who would happily
# walk a kilometre was being told nothing existed, because 800 m was chosen
# conservatively and never revisited.
#
# This widens an inaccuracy as well as the search: the radius is straight-line,
# and at 1500 m the gap between crow-flight and footpath is larger than at 800.
# KL1291 sits 60 m away by crow and 634 m on foot. Routing corrects the walk
# time displayed; it does not correct which stops are selected.
NEARBY_STOP_WIDE_RADIUS_M = 1500
```

- [ ] **Step 4: Widen the search at the resolution point**

At `:719`, where `_nearby_stops` is resolved once per render:

```python
    _nearby_stops = None
    _nearby_radius_used = NEARBY_STOP_RADIUS_M
    if _loc and agency_slug:
        _nearby_stops = gtfs_static.get_stops_near(
            agency_slug, _loc['lat'], _loc['lon'],
            radius_m=NEARBY_STOP_RADIUS_M, limit=NEARBY_STOP_SCAN_LIMIT)
        if not _nearby_stops:
            _nearby_stops = gtfs_static.get_stops_near(
                agency_slug, _loc['lat'], _loc['lon'],
                radius_m=NEARBY_STOP_WIDE_RADIUS_M, limit=NEARBY_STOP_SCAN_LIMIT)
            if _nearby_stops:
                _nearby_radius_used = NEARBY_STOP_WIDE_RADIUS_M
```

Every message that names a radius must read `_nearby_radius_used`, never a literal.

- [ ] **Step 5: Replace the dead end at `:1249`**

The dead-end message names the **wide** radius, and that satisfies the global constraint rather
than breaching it: reaching this branch means both searches ran and both found nothing, so the
widest radius actually applied is the honest number to quote. Quoting 800 here would understate
how hard the app looked.

```python
                st.info(
                    f"No stops found within {NEARBY_STOP_WIDE_RADIUS_M} m of you "
                    f"in {selected_region}."
                )
                loc_now = st.session_state.get('user_location')
                if loc_now:
                    with st.spinner("Checking other regions…"):
                        elsewhere = gtfs_static.find_regions_with_stops_near(
                            loc_now['lat'], loc_now['lon'],
                            radius_m=NEARBY_STOP_WIDE_RADIUS_M,
                            exclude_slug=agency_slug)
                    if elsewhere:
                        for r in elsewhere:
                            st.markdown(
                                f"→ **{r['region']}** — {r['count']} stop(s), "
                                f"nearest ~{int(r['nearest_m'])} m")
                        st.caption("Switch region above to see them.")
                    else:
                        st.caption(
                            "No other region has stops near you either.")
```

When the widened search *did* find stops, the panel says which radius it used:

```python
                if _nearby_radius_used != NEARBY_STOP_RADIUS_M:
                    st.caption(
                        f"Nothing within {NEARBY_STOP_RADIUS_M} m — showing stops "
                        f"up to {_nearby_radius_used} m.")
```

- [ ] **Step 6: Run tests, then the full suite under both dtypes**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "widens or dead_end or region_scan_runs" -q
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Existing tests asserting the literal `800` in the no-stops message must be updated to the widened
radius — the message changed because the behaviour did.

- [ ] **Step 7: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "feat: widen the stop search and name a region that does have stops near you"
```

---

### Task 4: Apply the alias, and accept both secrets spellings

**Files:**
- Modify: `src/app_pages/live_map.py` (`_ors_api_key` at :52; the route-search block at :524)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: `gtfs_static.resolve_route_alias` (Task 1).

- [ ] **Step 1: Write the failing tests**

```python
def test_secrets_are_read_from_the_sectioned_form(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map, '_config', None)
    live_map.st.secrets = {'routing': {'api_key': 'SECTIONED'}}
    assert live_map._ors_api_key() == 'SECTIONED'


def test_secrets_are_also_read_from_a_flat_api_key(monkeypatch):
    # A single pasted line is what a reader reaches for, and the miss was
    # silent: walk times stayed "(estimated)" with nothing saying why.
    from app_pages import live_map
    monkeypatch.setattr(live_map, '_config', None)
    live_map.st.secrets = {'api_key': 'FLAT'}
    assert live_map._ors_api_key() == 'FLAT'


def test_the_sectioned_form_wins_when_both_are_present(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map, '_config', None)
    live_map.st.secrets = {'routing': {'api_key': 'SECTIONED'}, 'api_key': 'FLAT'}
    assert live_map._ors_api_key() == 'SECTIONED'


def test_no_secret_in_either_form_returns_none(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map, '_config', None)
    live_map.st.secrets = {}
    assert live_map._ors_api_key() is None


def test_searching_the_branding_finds_the_published_route(monkeypatch):
    # GOKL14 is painted on the bus; the feed calls it PAVILION BUKIT JALIL.
    from app_pages import live_map
    seen = {}
    monkeypatch.setattr(live_map.data_processor, 'filter_by_route',
                        lambda df, q: seen.setdefault('query', q) or df)
    st_stub = _render_live_map(monkeypatch, route_query='GOKL14')
    assert seen['query'] == 'PAVILION BUKIT JALIL (PAVBJ)'


def test_an_alias_match_discloses_itself(monkeypatch):
    from app_pages import live_map
    st_stub = _render_live_map(monkeypatch, route_query='GOKL14')
    said = _texts(st_stub.caption) + _texts(st_stub.info) + _texts(st_stub.markdown)
    assert 'GOKL14' in said and 'PAVILION BUKIT JALIL' in said, \
        "a hand-written alias must not pass itself off as feed data"
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Accept both secrets spellings**

```python
    key = getattr(_config, 'ORS_API_KEY', None)
    if key:
        return key
    # Both spellings. The sectioned form is documented and wins; the flat form
    # is what a reader reaches for when pasting one line, and getting it wrong
    # failed silently and indistinguishably from having no key at all.
    for get in (lambda: st.secrets['routing']['api_key'],
                lambda: st.secrets['api_key']):
        try:
            value = get()
        except Exception:
            continue
        if value:
            return value
    return None
```

- [ ] **Step 4: Resolve the alias before the query is used**

At `:524`, resolve once and pass the resolved query to **both** matchers:

```python
    if route_query and route_query.strip():
        resolved_query, alias_source = gtfs_static.resolve_route_alias(route_query)
        if alias_source:
            st.caption(
                f"“{alias_source}” is the name on the bus; the feed publishes "
                f"this route as **{resolved_query}**.")
        df_filtered = data_processor.filter_by_route(df_map, resolved_query)
```

and use `resolved_query` wherever `route_query.strip()` was previously passed to
`region_has_route` / `find_regions_for_route` / `filter_label`.

- [ ] **Step 5: Run tests, then the full suite under both dtypes**

- [ ] **Step 6: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "fix: accept a flat api_key, and find a route by the name painted on the bus"
```

---

### Task 5: Documentation, housekeeping and version

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml`
- Verify unchanged: `requirements.txt`, `requirements-dev.txt`

- [ ] **Step 1: Add a "How to use this app" section to `README.md`**

Written for a rider, not a developer. Place it **before** the developer setup section. Cover:

- one line per page: what question it answers
- **Locate Me** first — most features need your position
- what `(estimated)` on a walk time means and how to remove it (configure `ORS_API_KEY`)
- why a region can show a green **Reliable** score and an empty map at once: the score says the
  feed is answering, not that buses are running
- what to do when a region has no stops near you — the app now names one that does
- that journey times come from the published timetable, not live positions
- that searching a route accepts the name on the bus where an alias is known (`GOKL14`), and that
  such aliases are hand-maintained and unofficial

- [ ] **Step 2: Remove Airflow from the backlog**

In `docs/superpowers/specs/2026-07-30-route-search-and-fetch-status-design.md`, replace the
"Backlog — Airflow orchestration (blocked)" section with a short decided-against note: it cannot
run on Streamlit Cloud, needs a server running 24/7, and the owner has decided not to pay for one.
Keep the reasoning so it is not re-proposed. Remove any Airflow line from the README roadmap.

- [ ] **Step 3: Sweep the deferred cosmetic items**

- `route_view.pattern_titles` docstring says "guaranteed unique"; soften to state the actual
  guarantee (unique among the patterns passed in a single call).
- A single-stop pattern is labelled `loop from X` because first == last; special-case it.
- A route listed under `Serves:` whose `get_route_patterns` returns `[]` gets no expander and no
  explanation; add a one-line note under it.
- `_WALK_CACHE` has no eviction; drop entries older than the TTL when the dict exceeds a few
  thousand keys.
- The over-long README line introduced in 2.8.1 (`README.md:50`); re-wrap.

- [ ] **Step 4: Update `CHANGELOG.md`**

Open `## [2.10.0] - 2026-08-03`. Cover, with the evidence:

- Rapid Bus KL's feed returned HTTP 200, a 15-byte body and zero entities at 12:49 on a Monday
  while MRT Feeder returned 102 vehicles the same second — an upstream outage, not a fault here.
- Three early returns deleted the map, the user's location marker and the tapped-stop panel along
  with the buses. Stops come from the timetable and never needed a live vehicle.
- The stop search now widens to 1500 m when 800 m finds nothing, and names the radius it used.
- A dead end now names regions that do have stops near you: standing in Bukit Jalil with KTM Berhad
  selected, the app said only "no stops found" while Rapid Bus KL had 15 within 800 m.
- Streamlit Secrets now accepts a flat `api_key` as well as `[routing] api_key`. The sectioned form
  remains documented; the flat one previously failed silently.
- `GOKL14` finds the route the feed publishes as `PAVILION BUKIT JALIL (PAVBJ)`, disclosed as a
  hand-maintained alias — no `GOKL` route exists anywhere in the feed.
- Airflow removed from the backlog, with the reason recorded.

Include a **Known Limitations** entry: the widened radius is still straight-line, so at 1500 m the
gap between crow-flight and footpath is larger than before; routing corrects the walk time shown,
not which stops are selected.

- [ ] **Step 5: Bump `pyproject.toml` to 2.10.0**

- [ ] **Step 6: Verify the manifests**

```bash
git diff --stat requirements.txt requirements-dev.txt
grep -n "version" pyproject.toml | head -2
head -12 CHANGELOG.md
```

- [ ] **Step 7: Run the full suite under both dtypes, then commit**

```bash
git add README.md CHANGELOG.md pyproject.toml docs/ src/
git commit -m "docs: usage guide for riders, drop Airflow, bump to 2.10.0"
```

---

## Manual verification

Automated tests cannot see a map. After Task 5, run locally (`streamlit run src/app.py`) and check:

1. Select **Rapid Bus KL** while its feed is empty → the map still draws, your marker is on it, the
   stop rings are there, and a warning above says no vehicle has reported.
2. Tap a stop ring in that state → the panel opens with `Serves:` and the stop sequences.
3. Select **KTM Berhad** from Bukit Jalil → "no stops within 1500 m", then a list naming Rapid Bus
   KL with its stop count and nearest distance.
4. Search `GOKL14` → matches the PAVBJ route, and a line says the feed publishes it under that name.
5. With the key set in Streamlit Secrets as a flat `api_key`, walk times lose the `(estimated)`
   suffix.
