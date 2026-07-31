# Arrivals Legibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the arrivals feature legible — pick stops by usefulness rather than raw proximity, label the route information instead of running it into one sentence, and show stops on the map.

**Architecture:** `gtfs_static` gains a route-parts lookup so the UI can label a route separately from its path. `eta.arrivals_for_stops` carries `route_id` through so the UI can resolve those parts. `live_map.py` evaluates every stop in the radius before choosing which to show, renders labelled lines, adds a stops layer, and links each stop name to Google Maps.

**Tech Stack:** Python 3.9+, Streamlit ≥1.40, pandas, pydeck, pytest.

## Global Constraints

- Target version **2.6.0** across `CHANGELOG.md`, `README.md`, `pyproject.toml`; keep `requirements.txt`/`requirements-dev.txt` consistent. Bump ONCE, in the final task.
- **Do not change the arrival arithmetic.** 2.5.x settled it, including frequency-based delay suppression (`delay_seconds is None` means "not knowable", never zero). This release changes selection and presentation only.
- Any new pydeck layer **must** carry an explicit `id=` and must not receive per-render-volatile columns. An unnamed `pdk.Layer` takes a fresh `uuid4()` per render, and Streamlit hashes the deck spec into the widget id — that is what previously broke map selection entirely, and later made "Clear bus selection" appear inert.
- `src/utils/eta.py` stays free of Streamlit and DuckDB imports.
- `NEARBY_STOP_RADIUS_M = 800` is unchanged. The defect is truncation *within* the radius.
- Test baseline is **155 passing**. Run with `export PATH="$(pwd)/.venv/bin:$PATH"` first.
- The suite must pass under **both** pandas string dtypes. CI infers Arrow-backed strings while local pandas infers object, and that difference has already broken the build once. Verify with:
  `python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))"`
- Every task ends with a commit carrying the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

### The real data this plan targets (verified against the cached feed)

```
routes.txt   route_id=S6060
             route_short_name = "PAVILION BUKIT JALIL (PAVBJ)"      <- a place, not a code
             route_long_name  = "Stesen LRT Awan Besar ~ Pavilion Bukit Jalil"
trips.txt    trip_headsign    = ""                                   <- often empty
```

`get_route_name` joins short and long with `" — "`, which is what produced the reported
run-on. The route is *named after one end of its own path*, so the repetition is inherent to the
data — the fix is to label the two parts, not to strip either.

---

### Task 1: Route parts, and `route_id` on each arrival

**Files:**
- Modify: `src/utils/gtfs_static.py` (append)
- Modify: `src/utils/eta.py` (`arrivals_for_stops`)
- Test: `tests/test_eta.py` (append)

**Interfaces:**
- Produces: `gtfs_static.get_route_parts(agency_slug, route_id) -> dict` — `{'short': str, 'long': str}`, both `''` when unknown or the feed is unavailable. Never raises.
- Produces: each arrival dict from `arrivals_for_stops` gains `route_id`, taken from the vehicle row (`''` when absent).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eta.py`:

```python
def test_get_route_parts_splits_short_from_long(tmp_path, monkeypatch):
    import zipfile
    p = tmp_path / "routes.zip"
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('routes.txt',
                    "route_id,route_short_name,route_long_name\n"
                    "S6060,PAVILION BUKIT JALIL (PAVBJ),Stesen LRT Awan Besar ~ Pavilion Bukit Jalil\n")
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(p))
    parts = gtfs_static.get_route_parts('any', 'S6060')
    assert parts['short'] == 'PAVILION BUKIT JALIL (PAVBJ)'
    assert parts['long'] == 'Stesen LRT Awan Besar ~ Pavilion Bukit Jalil'


def test_get_route_parts_is_empty_for_unknown_or_failed(monkeypatch):
    import zipfile
    monkeypatch.setattr(gtfs_static, '_load_zip',
                        lambda slug: (_ for _ in ()).throw(OSError('feed down')))
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_eta.py -k "route_parts or route_id" -q`
Expected: FAIL — `AttributeError: module 'utils.gtfs_static' has no attribute 'get_route_parts'`

- [ ] **Step 3: Add `get_route_parts`**

Append to `src/utils/gtfs_static.py`:

```python
def get_route_parts(agency_slug: str, route_id: str) -> dict:
    """
    A route's short and long names, kept separate.

    `get_route_name` joins them for a tooltip, which reads badly in a panel:
    Rapid KL's short name is often a place ("PAVILION BUKIT JALIL (PAVBJ)")
    and the long name is the path between two places, one of which is that
    same place. Joined, it looks like the name was printed twice. Returned
    separately, the UI can label which is which.
    """
    empty = {'short': '', 'long': ''}
    if not route_id:
        return empty
    try:
        with _load_zip(agency_slug) as zf:
            for row in _read_csv_from_zip(zf, 'routes.txt') or []:
                if (row.get('route_id') or '').strip() == route_id.strip():
                    return {
                        'short': (row.get('route_short_name') or '').strip(),
                        'long': (row.get('route_long_name') or '').strip(),
                    }
    except Exception:
        return empty
    return empty
```

- [ ] **Step 4: Carry `route_id` through `arrivals_for_stops`**

In `src/utils/eta.py`, inside the dict appended to `arrivals[sid]`, add after the `route_display` entry:

```python
                # Guard NaN as trip_id above does: a pandas NaN is truthy, so
                # `NaN or ''` is NaN and str(NaN) is 'nan' — a bogus, non-empty
                # id. df_map.to_dict('records') produces exactly that shape for
                # a missing value. Mirror trip_id's guard, including .strip().
                'route_id': <same NaN-guarded coercion trip_id uses>,
```

- [ ] **Step 5: Run the tests**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 158 passed (155 + 3)

- [ ] **Step 6: Commit**

```bash
git add src/utils/gtfs_static.py src/utils/eta.py tests/test_eta.py
git commit -m "feat: expose route short and long names separately

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Choose stops by usefulness, not raw proximity

**Files:**
- Modify: `src/app_pages/live_map.py` (the "Arrivals near you" block)
- Test: `tests/test_script.py` (append)

**Interfaces:**
- Consumes: `eta.arrivals_for_stops` unchanged.
- Produces: no new functions — selection logic only.

**This is the reported bug.** Measured against the real feed from the reported location: 15 stops sit within 800 m, but `limit=5` truncated by distance *before* any arrival was computed, and the only stop with a bus inbound (LRT Awan Besar) ranked **9th** at 585 m. Every stop shown said "nothing inbound" while a bus was en route to one that was never evaluated.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_script.py`:

```python
def test_a_served_stop_beyond_the_nearest_few_is_still_shown(monkeypatch):
    """
    Stops are chosen by usefulness, not raw proximity.

    Reported from Bukit Jalil: 15 stops sat within 800 m, the panel evaluated
    only the 5 nearest, and the one stop with a bus inbound ranked 9th. Every
    stop on screen said "nothing inbound" while a bus was on its way to one the
    panel never looked at.
    """
    from app_pages import live_map

    st_stub = _stub_streamlit(monkeypatch, live_map)
    st_stub.selectbox.return_value = 'Rapid Bus KL'
    st_stub.text_input.return_value = ''
    st_stub.session_state.update({
        'map_theme': 'dark', 'getting_location': False,
        'selected_region': 'Rapid Bus KL', '_region_for_search': 'Rapid Bus KL',
        'user_location': {'lat': 3.0586, 'lon': 101.6739, 'accuracy': 10},
    })
    st_stub.columns.side_effect = lambda spec, *a, **k: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    now = int(time.time())
    monkeypatch.setattr(live_map.time, 'time', lambda: float(now))

    # Eight stops in range. Only the FAR one is served.
    stops = [
        {'stop_id': f'S{i}', 'stop_name': f'CLOSE STOP {i}',
         'stop_lat': 3.0586 + i * 0.0002, 'stop_lon': 101.6739,
         'distance_m': 20.0 * (i + 1)}
        for i in range(7)
    ]
    served = {'stop_id': 'FAR', 'stop_name': 'LRT AWAN BESAR',
              'stop_lat': 3.0640, 'stop_lon': 101.6739, 'distance_m': 585.0}
    # The stub MUST honour `limit` the way the real get_stops_near does —
    # it sorts by distance and returns found[:limit]. A stub that ignores
    # `limit` bypasses the very truncation this bug is about, and the test
    # would then pass against the unfixed code.
    all_stops = stops + [served]

    def fake_stops_near(agency, lat, lon, radius_m=800, limit=5):
        return sorted(all_stops, key=lambda s: s['distance_m'])[:limit]

    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', fake_stops_near)

    # A trip that stops at the bus's position, then at the far stop.
    trip = [
        {'stop_id': 'ORIGIN', 'stop_name': 'ORIGIN', 'stop_lat': 3.0500,
         'stop_lon': 101.6739, 'arrival_seconds': 0},
        {'stop_id': 'FAR', 'stop_name': 'LRT AWAN BESAR', 'stop_lat': 3.0640,
         'stop_lon': 101.6739, 'arrival_seconds': 600},
    ]
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: trip)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: True)

    df = pd.DataFrame([{
        'region': 'Rapid Bus KL', 'vehicle_id': 'V1',
        'latitude': 3.0500, 'longitude': 101.6739, 'bearing': 90.0, 'speed': 10.0,
        'timestamp': now, 'trip_id': 'T1', 'route_id': 'S6060',
        'freshness': 'fresh', 'age_seconds': 5,
    }])
    monkeypatch.setattr(
        live_map.db, 'get_live_data_optimized',
        lambda *a, **k: (df, {'total': 1, 'stale': 0, 'hidden': 0,
                              'regions': 1, 'busiest': 'Rapid Bus KL'}, 'now'),
    )

    live_map.show()

    said = _texts(st_stub.markdown) + _texts(st_stub.caption)
    assert 'LRT AWAN BESAR' in said, \
        "the only served stop was dropped for being 8th-nearest"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k served_stop_beyond -q`
Expected: FAIL — `LRT AWAN BESAR` absent, because `limit=5` drops it before arrivals are computed.

- [ ] **Step 3: Add the selection constants**

In `src/app_pages/live_map.py`, immediately after the existing `NEARBY_STOP_RADIUS_M = 800` line:

```python
# Evaluate every stop in range, then show the useful ones. Truncating by
# distance first hid a stop that had a bus inbound behind five closer stops
# that had none.
NEARBY_STOP_SCAN_LIMIT = 40     # candidates evaluated
NEARBY_STOP_DISPLAY = 5         # stops rendered
NEARBY_STOP_MIN_SHOWN = 3       # filled with unserved stops when few are served
```

- [ ] **Step 4: Select by usefulness**

In `src/app_pages/live_map.py`, replace:

```python
            nearby = gtfs_static.get_stops_near(
                agency_slug, loc['lat'], loc['lon'],
                radius_m=NEARBY_STOP_RADIUS_M, limit=5)
```

with:

```python
            nearby = gtfs_static.get_stops_near(
                agency_slug, loc['lat'], loc['lon'],
                radius_m=NEARBY_STOP_RADIUS_M, limit=NEARBY_STOP_SCAN_LIMIT)
```

Then replace the `for stop in nearby:` render loop's opening — everything from `any_arrival = False` down to and including the `for stop in nearby:` line — with:

```python
                # Rank by usefulness: stops with a bus actually coming, nearest
                # first, then fill with the nearest unserved ones so the panel
                # is never empty and "nothing anywhere" is distinguishable from
                # "the app found nothing".
                served = [s for s in nearby if arrivals.get(s['stop_id'])]
                unserved = [s for s in nearby if not arrivals.get(s['stop_id'])]
                shown = served[:NEARBY_STOP_DISPLAY]
                if len(shown) < NEARBY_STOP_MIN_SHOWN:
                    shown += unserved[:NEARBY_STOP_MIN_SHOWN - len(shown)]

                any_arrival = bool(served)
                for stop in shown:
```

- [ ] **Step 5: Correct the two "inbound" messages**

In the same block, replace:

```python
                        st.caption("  nothing inbound right now")
```

with:

```python
                        st.caption("  no bus currently en route to this stop")
```

and replace:

```python
                if not any_arrival:
                    st.caption("No buses are currently inbound to these stops.")
```

with:

```python
                if not any_arrival:
                    st.caption(
                        f"No buses are currently en route to any stop within "
                        f"{NEARBY_STOP_RADIUS_M} m of you."
                    )
```

- [ ] **Step 6: Run the tests under both dtypes**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/ -q
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))"
```
Expected: 159 passed both times (158 + 1)

- [ ] **Step 7: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "fix: choose nearby stops by usefulness, not raw proximity

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Labelled route information

**Files:**
- Modify: `src/app_pages/live_map.py` (`format_arrival`, the tapped panel)
- Test: `tests/test_script.py` (append)

**Interfaces:**
- Consumes: `gtfs_static.get_route_parts` and the `route_id` on each arrival, both from Task 1.
- Produces: `live_map.format_route_heading(parts, fallback) -> list[str]` — the labelled route lines for one arrival: a `Route:` line always, and a `Runs:` line only when the long name adds information.

**The reported symptom:** *"which one is the route/bus name? why pavilion bukit jalil written twice?"* — because `route_short_name` is `PAVILION BUKIT JALIL (PAVBJ)` (a place) and `route_long_name` is `Stesen LRT Awan Besar ~ Pavilion Bukit Jalil` (the path through that place), joined by `" — "` into one unlabelled string.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`:

```python
def test_format_route_heading_labels_the_two_parts():
    from app_pages import live_map
    lines = live_map.format_route_heading(
        {'short': 'PAVILION BUKIT JALIL (PAVBJ)',
         'long': 'Stesen LRT Awan Besar ~ Pavilion Bukit Jalil'},
        fallback='ignored',
    )
    assert lines[0] == '**Route:** PAVILION BUKIT JALIL (PAVBJ)'
    assert lines[1] == '**Runs:** Stesen LRT Awan Besar ↔ Pavilion Bukit Jalil'


def test_format_route_heading_omits_a_path_that_repeats_the_name():
    from app_pages import live_map
    lines = live_map.format_route_heading(
        {'short': 'T580', 'long': 'T580'}, fallback='ignored')
    assert lines == ['**Route:** T580']


def test_format_route_heading_falls_back_when_parts_are_missing():
    from app_pages import live_map
    lines = live_map.format_route_heading({'short': '', 'long': ''}, fallback='T580')
    assert lines == ['**Route:** T580']
```

- [ ] **Step 2: Run them to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k format_route_heading -q`
Expected: FAIL — `AttributeError: module 'app_pages.live_map' has no attribute 'format_route_heading'`

- [ ] **Step 3: Implement the helper**

In `src/app_pages/live_map.py`, immediately above `def format_arrival(`:

```python
def format_route_heading(parts, fallback=''):
    """
    A route's identity as labelled lines rather than one run-on string.

    Rapid KL's short name is frequently a place — "PAVILION BUKIT JALIL
    (PAVBJ)" — and the long name is the path between two places, one of which
    is that same place. Joined with a dash and no labels, it reads as the name
    printed twice and leaves no way to tell which part is the route. Labelling
    answers that; the repetition itself is in the source data and is not ours
    to strip.

    The path line is omitted when it adds nothing, i.e. when it is identical
    to the short name.
    """
    short = (parts or {}).get('short') or ''
    long_ = (parts or {}).get('long') or ''
    name = short or long_ or fallback or '—'
    lines = [f"**Route:** {name}"]
    if long_ and long_ != short:
        lines.append(f"**Runs:** {long_.replace('~', '↔')}")
    return lines
```

- [ ] **Step 4: Use it in the tapped panel**

In `src/app_pages/live_map.py`, find the tapped-bus success block that currently reads:

```python
                        st.info(
                            f"{format_arrival(a)} at **{s['stop_name']}** — "
```

Replace that whole `st.info(...)` call with:

```python
                        parts = gtfs_static.get_route_parts(
                            agency_slug, a.get('route_id', ''))
                        heading = format_route_heading(
                            parts, fallback=a.get('route_display', ''))
                        mins = max(1, round(a['eta_seconds'] / 60))
                        body = list(heading)
                        body.append(
                            f"**Arrives** {s['stop_name']} in **~{mins} min**")
                        body.append(
                            f"That stop is ~{int(s['distance_m'])} m from you "
                            f"(~{eta.walking_minutes(s['distance_m'])} min walk)")
                        age = a.get('age_seconds')
                        if age and age > LIVE_FRESH_SECONDS:
                            body.append(
                                f"⚠️ This bus last reported "
                                f"{round(age / 60)} min ago")
                        st.info("  \n".join(body))
```

Note `"  \n"` — two spaces then a newline is a Markdown hard line break, which is what keeps each fact on its own line inside one `st.info` box.

- [ ] **Step 5: Run the tests under both dtypes**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/ -q
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))"
```
Expected: 162 passed both times (159 + 3)

- [ ] **Step 6: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "fix: label the route and its path instead of running them together

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Stops on the map, and a link out

**Files:**
- Modify: `src/app_pages/live_map.py` (stops layer, stop-name links)
- Test: `tests/test_script.py` (append)

**Interfaces:**
- Produces: no new functions — a new pydeck layer plus Markdown links.

**Hard constraint, learned twice on this branch:** the new layer MUST carry an explicit `id=`, and its data MUST contain only stable columns. An unnamed layer takes a fresh `uuid4()` each render; Streamlit hashes the deck spec into the widget id; a churning id previously broke map selection outright and later made "Clear bus selection" appear inert. Stop coordinates and names are static, so restricting the frame to exactly those columns keeps it stable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`:

```python
def test_nearby_stops_layer_has_a_stable_explicit_id(monkeypatch):
    """An unnamed pdk.Layer takes a fresh uuid each render, which churns the
    deck spec hash and breaks map selection. Every layer must be named."""
    import pydeck as pdk
    from app_pages import live_map

    made = []
    real_layer = pdk.Layer

    def recording_layer(*a, **k):
        made.append(k.get('id'))
        return real_layer(*a, **k)

    monkeypatch.setattr(live_map.pdk, 'Layer', recording_layer)
    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map_mod, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.0586, 'lon': 101.6739,
                                              'accuracy': 10}
    monkeypatch.setattr(live_map_mod.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'A STOP',
                                          'stop_lat': 3.0586, 'stop_lon': 101.6739,
                                          'distance_m': 50.0}])
    live_map_mod.show()

    assert None not in made, "a pydeck layer was created without an explicit id"
    assert 'nearby-stops' in made


def test_stop_names_link_to_google_maps(monkeypatch):
    from app_pages import live_map

    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map_mod, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.0586, 'lon': 101.6739,
                                              'accuracy': 10}
    monkeypatch.setattr(live_map_mod.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'A STOP',
                                          'stop_lat': 3.058659, 'stop_lon': 101.673981,
                                          'distance_m': 50.0}])
    live_map_mod.show()

    said = _texts(st_stub.markdown)
    assert 'maps.google.com' in said or 'google.com/maps' in said
    assert '3.058659,101.673981' in said
```

- [ ] **Step 2: Run them to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k "nearby_stops_layer or link_to_google_maps" -q`
Expected: FAIL — no `nearby-stops` layer, no link in the rendered Markdown.

- [ ] **Step 3: Build the stops layer**

In `src/app_pages/live_map.py`, immediately before the `layers = [` assignment that collects the deck's layers, add:

```python
    # Nearby stops, drawn beneath the vehicles. Only static columns reach the
    # layer: a per-render-volatile field here would churn the deck spec hash
    # and break map selection, as it did in 2.5.0.
    stops_layer = None
    _loc = st.session_state.get('user_location')
    if _loc and agency_slug:
        _stops = gtfs_static.get_stops_near(
            agency_slug, _loc['lat'], _loc['lon'],
            radius_m=NEARBY_STOP_RADIUS_M, limit=NEARBY_STOP_SCAN_LIMIT)
        if _stops:
            stops_layer = pdk.Layer(
                "ScatterplotLayer",
                id="nearby-stops",
                data=[{'stop_name': s['stop_name'],
                       'stop_lat': s['stop_lat'],
                       'stop_lon': s['stop_lon']} for s in _stops],
                get_position=['stop_lon', 'stop_lat'],
                get_fill_color=[255, 200, 60, 180],
                get_radius=40,
                radius_min_pixels=4,
                radius_max_pixels=9,
                pickable=False,
            )
```

Then change the layer list (currently `layers = [icon_layer, arrow_layer]`, around line 517) to put stops underneath the vehicles:

```python
    layers = ([stops_layer] if stops_layer else []) + [icon_layer, arrow_layer]
```

The user-location layers appended after this block are unaffected — leave them as they are.

- [ ] **Step 4: Link the stop names**

In the "Arrivals near you" render loop, replace the stop heading line:

```python
                    st.markdown(
                        f"**{stop['stop_name']}** · ~{int(stop['distance_m'])} m "
                        f"· ~{walk} min walk"
                    )
```

with:

```python
                    maps_url = (
                        "https://www.google.com/maps/search/?api=1&query="
                        f"{stop['stop_lat']},{stop['stop_lon']}"
                    )
                    st.markdown(
                        f"**[{stop['stop_name']}]({maps_url})** "
                        f"· ~{int(stop['distance_m'])} m · ~{walk} min walk"
                    )
```

- [ ] **Step 5: Run the tests under both dtypes**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/ -q
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))"
```
Expected: 164 passed both times (162 + 2)

- [ ] **Step 6: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "feat: draw nearby stops on the map and link them to Google Maps

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Docs and version bump (2.6.0)

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add the `[2.6.0]` section to `CHANGELOG.md`** above the most recent entry

```markdown
## [2.6.0] - 2026-07-31

### Fixed
- **"Nothing inbound" no longer hides a bus that is on its way.** Nearby stops were truncated to
  the five closest *before* any arrival was computed. Measured from the reported location: 15 stops
  sit within 800 m, and the only one with a bus inbound ranked 9th at 585 m — so every stop on
  screen reported nothing while a bus was en route to one the panel never evaluated. Every stop in
  range is now evaluated, and those with a bus coming are shown first
- The wording said "nothing inbound right now", which reads as *no bus ever serves this stop*. It
  now says "no bus currently en route to this stop", and the panel-level message names the radius
- **The tapped-bus panel is legible.** It ran six facts into one sentence, and the staleness note
  welded itself onto the stop name — *"position 1 min old, so less certain at KL2324 LRT AWAN
  BESAR"*. Route, path, arrival, walk and staleness are now labelled lines

### Added
- Nearby stops are drawn on the map beneath the vehicles, so their position is visible rather than
  only named
- Stop names link to Google Maps for walking directions, which this app deliberately does not
  compute itself
- `gtfs_static.get_route_parts` — a route's short and long names kept separate

### Notes
- The reported "why is Pavilion Bukit Jalil written twice" is inherent to the feed, not a bug:
  `route_short_name` is `PAVILION BUKIT JALIL (PAVBJ)` — a place — and `route_long_name` is
  `Stesen LRT Awan Besar ~ Pavilion Bukit Jalil`, the path through that same place. Labelling the
  two makes it readable; stripping either would lose information
```

- [ ] **Step 2: Update `README.md`**

In the `### 🗺️ Live Map` feature list, extend the arrivals bullet to mention stop markers and links, and add to the Key Design Decisions table:

```markdown
| **Nearby stops ranked by usefulness** | Truncating to the closest few stops before computing arrivals hid a stop that had a bus inbound behind five that did not. Every stop within the radius is evaluated, then those with a bus coming are shown first |
```

- [ ] **Step 3: Bump `pyproject.toml`**

Change `version = "2.5.3"` to `version = "2.6.0"`.

- [ ] **Step 4: Verify the four files agree**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
grep -n '^version' pyproject.toml
grep -n '## \[2.6.0\]' CHANGELOG.md
python3 -c "
import re
p=open('pyproject.toml').read()
d=set(re.findall(r'\"([a-zA-Z0-9_.-]+[^\"]*)\"', p.split('dependencies = [')[1].split(']')[0]))
r=set(l.strip() for l in open('requirements.txt') if l.strip() and not l.startswith('#'))
print('dependency parity:', 'OK' if d==r else f'MISMATCH {d^r}')"
python -m pytest tests/ -q
```
Expected: version 2.6.0; CHANGELOG section present; parity OK; 164 passed

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml
git commit -m "docs: document arrivals legibility fixes, bump to 2.6.0

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** usefulness-ranked selection and reworded messages (T2), labelled route lines with the path omitted when it adds nothing (T1 + T3), stop markers and Google Maps links (T4), docs and version (T5). The spec's required regression test — a served stop beyond the old `limit=5` — is T2 Step 1 and must fail before the change.
- **Test arithmetic:** 155 → 158 (T1) → 159 (T2) → 162 (T3) → 164 (T4). T5 adds none.
- **A correction the spec did not anticipate:** the spec assumed the duplication came from `route_display` repeating the *headsign*. It does not — the headsign is empty for this route. It comes from `route_short_name` being a place and `route_long_name` being the path through it. The rule is therefore "omit the path line when it equals the short name", not "omit when it equals the headsign", and the fix is labelling rather than suppression.
- **Type consistency:** `get_route_parts` always returns `{'short': str, 'long': str}`; arrivals always carry `route_id` as a `str`; `format_route_heading` always returns a non-empty list.
- **Dtype safety:** every task verifies under both pandas string dtypes, because CI infers Arrow-backed strings and local pandas infers object — a difference that already broke the build once on this project.
- **Deliberate omissions:** the "Clear bus selection" defect was fixed in 2.5.3 and is not revisited here; `startDate` ingestion and browser verification remain spec follow-ups.
