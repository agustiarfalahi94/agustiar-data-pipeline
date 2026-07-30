# Route Name Search + Honest Fetch Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users filter the Live Map to a single route by name (e.g. `T580`), and stop Network Health from scoring withdrawn feeds and out-of-service hours as agency failures.

**Architecture:** Two independent slices. (1) A pure `filter_by_route` helper filters the existing `df_map['route_display']` column in `live_map.py`, applied after route names resolve and before pydeck layers build, so layers, centring, caption and Route Viewer all inherit the filter. (2) `_fetch_endpoint` gains a status classification (`OK`/`EMPTY`/`NO_FEED`/`THROTTLED`/`ERROR`) persisted to a new `fetch_quality_log.fetch_status` column; the dbt marts score only over "scoreable" statuses.

**Tech Stack:** Python 3.9+, Streamlit, pandas, DuckDB, dbt-duckdb, pytest, pydeck.

## Global Constraints

- Target version **2.3.0** (feature → minor bump) across `CHANGELOG.md`, `README.md`, `pyproject.toml`; keep `requirements.txt`/`requirements-dev.txt` consistent with `pyproject.toml`. Bump happens ONCE in the final task, not per task.
- The live-map data path functions `get_live_data_optimized` and `get_vehicle_trail` in `src/utils/db.py` must NOT change.
- Marts stay DuckDB **views**. The `reliability_score` macro's formula is NOT changed — only which rows feed it.
- `total_dropout` keeps its current meaning (`vehicles_received == 0`) for backwards compatibility; marts stop *scoring* from it.
- Existing rows have `fetch_status IS NULL` and MUST be treated as `OK` so historical data keeps scoring.
- Status precedence when a region has multiple endpoints (e.g. `myBAS Seremban` has `mybas-seremban-a` and `mybas-seremban-b`), best-first: **`OK` > `EMPTY` > `ERROR` > `THROTTLED` > `NO_FEED`**.
- Buses only for route search. When region is `KTM Berhad`, the search box is hidden (KTM reports no `route_id`).
- Test baseline is **36 passing**; it must never regress. Run tests with `export PATH="$(pwd)/.venv/bin:$PATH"` first — `dbt` and `pytest` live in the repo venv.
- Every task ends with a commit whose message carries the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: `filter_by_route` pure helper

**Files:**
- Modify: `src/utils/data_processor.py` (append new function)
- Test: `tests/test_script.py` (append new tests)

**Interfaces:**
- Produces: `data_processor.filter_by_route(df, query) -> DataFrame` — returns rows whose `route_display` contains `query` case-insensitively; returns `df` unchanged when `query` is empty/whitespace; returns an empty DataFrame (preserving columns) when nothing matches; returns `df` unchanged when the `route_display` column is absent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`:

```python
def test_filter_by_route_matches_case_insensitively():
    df = pd.DataFrame({
        'vehicle_id': ['A', 'B', 'C'],
        'route_display': ['T580 — Awan Besar ~ TPM', 'U6000 — Klang', 'T581 — Other'],
    })
    out = data_processor.filter_by_route(df, 't580')
    assert list(out['vehicle_id']) == ['A']


def test_filter_by_route_matches_long_name():
    df = pd.DataFrame({
        'vehicle_id': ['A', 'B'],
        'route_display': ['T580 — Awan Besar ~ TPM', 'U6000 — Klang'],
    })
    out = data_processor.filter_by_route(df, 'awan besar')
    assert list(out['vehicle_id']) == ['A']


def test_filter_by_route_empty_query_returns_all():
    df = pd.DataFrame({'vehicle_id': ['A', 'B'], 'route_display': ['T580', 'U6000']})
    assert len(data_processor.filter_by_route(df, '')) == 2
    assert len(data_processor.filter_by_route(df, '   ')) == 2


def test_filter_by_route_no_match_returns_empty_with_columns():
    df = pd.DataFrame({'vehicle_id': ['A'], 'route_display': ['T580']})
    out = data_processor.filter_by_route(df, 'ZZZ999')
    assert out.empty
    assert list(out.columns) == ['vehicle_id', 'route_display']


def test_filter_by_route_missing_column_returns_unchanged():
    df = pd.DataFrame({'vehicle_id': ['A', 'B']})
    assert len(data_processor.filter_by_route(df, 'T580')) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k filter_by_route -v`
Expected: FAIL — `AttributeError: module 'utils.data_processor' has no attribute 'filter_by_route'`

- [ ] **Step 3: Implement the function**

Append to `src/utils/data_processor.py`:

```python
def filter_by_route(df, query):
    """
    Filter vehicles to those whose route matches *query* (case-insensitive substring).

    Matches against the 'route_display' column, which holds the resolved
    "SHORT — Long Name" string, so both "T580" and "awan besar" match.

    Returns df unchanged for an empty/whitespace query or when route_display
    is absent, so callers can pass user input straight through.
    """
    if not query or not query.strip():
        return df
    if 'route_display' not in df.columns:
        return df
    needle = query.strip().lower()
    mask = df['route_display'].fillna('').astype(str).str.lower().str.contains(needle, regex=False)
    return df[mask]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k filter_by_route -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Run the full suite**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 41 passed (36 baseline + 5 new)

- [ ] **Step 6: Commit**

```bash
git add src/utils/data_processor.py tests/test_script.py
git commit -m "feat: add filter_by_route helper for live map route search

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire the search box into the Live Map

**Files:**
- Modify: `src/app_pages/live_map.py` (region selector block ~line 120; filter application after `route_display` is built ~line 197)

**Interfaces:**
- Consumes: `data_processor.filter_by_route(df, query)` from Task 1.
- Produces: no new public functions — UI wiring only.

**Context the implementer needs:** `live_map.py` builds `df_map` via `data_processor.prepare_map_data(df_live, selected_region)`, then adds `speed_display`, `bearing_display`, and `route_display`. Immediately after that block (before the pydeck layers are constructed) is where the filter goes, so layers, view centring, the vehicle-count caption, and the Route Viewer dropdown all inherit it. `data_processor` is already imported in this file.

- [ ] **Step 1: Add the search input under the region selector**

In `src/app_pages/live_map.py`, immediately after the `if selected_region != st.session_state.selected_region:` block that closes the region selectbox (inside the same `with` column block), add:

```python
        # Route search — hidden for KTM, whose realtime feed carries no route_id
        if selected_region == 'KTM Berhad':
            route_query = ''
            st.caption("Route search is not yet available for KTM Berhad.")
        else:
            route_query = st.text_input(
                "Search route (e.g. T580)",
                value='',
                placeholder='Route number or name',
                key='route_search_live_map',
            )
```

- [ ] **Step 2: Apply the filter after route names resolve**

In `src/app_pages/live_map.py`, find this existing block:

```python
    else:
        df_map['route_display'] = df_map.get('route_id', '—').fillna('—')
```

Immediately after it, insert:

```python
    # Filter to the searched route. Applied after route_display is resolved and
    # before layers are built, so layers, centring, the caption and the Route
    # Viewer all reflect the filtered set.
    if route_query and route_query.strip():
        df_filtered = data_processor.filter_by_route(df_map, route_query)
        if df_filtered.empty:
            # Leave the map unfiltered: a blank map cannot be told apart from
            # a bad search term.
            st.warning(f"No live vehicles found on '{route_query.strip()}' right now.")
        else:
            df_map = df_filtered
            matched = sorted(df_map['route_display'].unique())
            st.success(f"Showing {len(df_map)} vehicle(s) on {', '.join(matched[:3])}")
```

- [ ] **Step 3: Verify the module still parses and the suite is green**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -c "import ast; ast.parse(open('src/app_pages/live_map.py').read()); print('live_map.py parses OK')"
python -m pytest tests/ -q
```
Expected: parses OK; 41 passed

- [ ] **Step 4: Confirm the filter is positioned before the layers**

Run: `grep -n "route_display\|filter_by_route\|ScatterplotLayer\|PathLayer" src/app_pages/live_map.py | head -20`
Expected: the `filter_by_route` line number is GREATER than the `route_display` assignment lines and LESS than the first layer construction line. If it is not, move it.

- [ ] **Step 5: Commit**

```bash
git add src/app_pages/live_map.py
git commit -m "feat: search the live map by route name

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Classify fetch status in ingestion

**Files:**
- Modify: `src/utils/ingestion.py` (`_fetch_endpoint`, the ThreadPoolExecutor loop, `_build_quality_stats`)
- Test: `tests/test_script.py` (append)

**Interfaces:**
- Produces:
  - `ingestion._classify_status(status_code, vehicle_count) -> str` — returns one of `'OK'`, `'EMPTY'`, `'NO_FEED'`, `'THROTTLED'`, `'ERROR'`.
  - `ingestion._merge_status(a, b) -> str` — combines two endpoint statuses for one region using precedence `OK > EMPTY > ERROR > THROTTLED > NO_FEED`.
  - `_fetch_endpoint(name, endpoint)` now returns a **3-tuple** `(vehicles, duration_ms, status)`.
  - `_build_quality_stats(...)` gains a `status_by_region` parameter and emits `'fetch_status'` in each stat dict.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`:

```python
from utils import ingestion


def test_classify_status_ok_and_empty():
    assert ingestion._classify_status(200, 5) == 'OK'
    assert ingestion._classify_status(200, 0) == 'EMPTY'


def test_classify_status_no_feed_and_throttled():
    assert ingestion._classify_status(404, 0) == 'NO_FEED'
    assert ingestion._classify_status(429, 0) == 'THROTTLED'


def test_classify_status_other_codes_are_errors():
    assert ingestion._classify_status(500, 0) == 'ERROR'
    assert ingestion._classify_status(None, 0) == 'ERROR'


def test_merge_status_precedence():
    # OK beats everything
    assert ingestion._merge_status('OK', 'NO_FEED') == 'OK'
    assert ingestion._merge_status('NO_FEED', 'OK') == 'OK'
    # a responding-but-empty feed beats a dead one
    assert ingestion._merge_status('EMPTY', 'NO_FEED') == 'EMPTY'
    # a real error outranks throttling and a dead feed
    assert ingestion._merge_status('ERROR', 'THROTTLED') == 'ERROR'
    assert ingestion._merge_status('NO_FEED', 'NO_FEED') == 'NO_FEED'


def test_build_quality_stats_carries_fetch_status():
    stats = ingestion._build_quality_stats(
        received_by_region={'R1': 10},
        valid_by_region={'R1': 8},
        inserted_by_region={'R1': 8},
        lag_by_region={'R1': {'avg': 1.0, 'max': 2.0}},
        duration_by_region={'R1': 120},
        status_by_region={'R1': 'OK'},
        fetch_timestamp=1750000000,
    )
    assert len(stats) == 1
    assert stats[0]['fetch_status'] == 'OK'


def test_build_quality_stats_defaults_missing_status_to_error():
    stats = ingestion._build_quality_stats(
        received_by_region={'R1': 0},
        valid_by_region={},
        inserted_by_region={},
        lag_by_region={},
        duration_by_region={'R1': 5},
        status_by_region={},
        fetch_timestamp=1750000000,
    )
    assert stats[0]['fetch_status'] == 'ERROR'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k "classify_status or merge_status or fetch_status" -v`
Expected: FAIL — `AttributeError: module 'utils.ingestion' has no attribute '_classify_status'`

- [ ] **Step 3: Add the two classification helpers**

In `src/utils/ingestion.py`, insert immediately above `def _fetch_endpoint(name, endpoint):`

```python
# Ranked best-first. A region with several endpoints takes the best status any
# of them achieved: if one endpoint returned data the region is working, even
# if a sibling endpoint is dead.
_STATUS_PRECEDENCE = ['OK', 'EMPTY', 'ERROR', 'THROTTLED', 'NO_FEED']


def _classify_status(status_code, vehicle_count):
    """Map an HTTP status + vehicle count onto a fetch status label."""
    if status_code == 200:
        return 'OK' if vehicle_count > 0 else 'EMPTY'
    if status_code == 404:
        return 'NO_FEED'
    if status_code == 429:
        return 'THROTTLED'
    return 'ERROR'


def _merge_status(a, b):
    """Combine two endpoint statuses for one region, best status winning."""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b, key=lambda s: _STATUS_PRECEDENCE.index(s)
               if s in _STATUS_PRECEDENCE else len(_STATUS_PRECEDENCE))
```

- [ ] **Step 4: Return the status from `_fetch_endpoint`**

Replace the whole body of `_fetch_endpoint` in `src/utils/ingestion.py` with:

```python
def _fetch_endpoint(name, endpoint):
    """
    Fetch vehicle data from a single API endpoint.
    Returns (vehicles, duration_ms, status) — vehicles is [] on any error.
    status is one of OK / EMPTY / NO_FEED / THROTTLED / ERROR.
    """
    url = f'{API_BASE_URL}{endpoint}'
    t0 = time.time()
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        duration_ms = int((time.time() - t0) * 1000)
        if response.status_code == 200:
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
            vehicles = []
            for entity in feed.entity:
                if entity.HasField('vehicle'):
                    v = MessageToDict(entity.vehicle)
                    pos = v.get('position', {})
                    vehicle_info = v.get('vehicle', {})
                    trip_info = v.get('trip', {})
                    vehicles.append({
                        'region': name,
                        'latitude': pos.get('latitude'),
                        'longitude': pos.get('longitude'),
                        'bearing': pos.get('bearing', 0),
                        'speed': pos.get('speed', 0),
                        'vehicle_id': vehicle_info.get('id', 'Unknown'),
                        'timestamp': v.get('timestamp'),
                        'trip_id': trip_info.get('tripId', ''),
                        'route_id': trip_info.get('routeId', ''),
                    })
            return vehicles, duration_ms, _classify_status(200, len(vehicles))
        return [], duration_ms, _classify_status(response.status_code, 0)
    except Exception as e:
        print(f"Error fetching {name} ({endpoint}): {e}")
    return [], int((time.time() - t0) * 1000), 'ERROR'
```

- [ ] **Step 5: Collect statuses in the fetch loop**

In `fetch_and_store_transit_data`, replace this existing block:

```python
        duration_by_region = {}
        for future in as_completed(future_to_task):
            name, endpoint = future_to_task[future]
            vehicles, duration_ms = future.result()
            all_vehicle_data.extend(vehicles)
            duration_by_region[name] = duration_by_region.get(name, 0) + duration_ms
```

with:

```python
        duration_by_region = {}
        status_by_region = {}
        for future in as_completed(future_to_task):
            name, endpoint = future_to_task[future]
            vehicles, duration_ms, status = future.result()
            all_vehicle_data.extend(vehicles)
            duration_by_region[name] = duration_by_region.get(name, 0) + duration_ms
            status_by_region[name] = _merge_status(status_by_region.get(name), status)
```

- [ ] **Step 6: Carry the status through `_build_quality_stats`**

Change the signature line in `src/utils/ingestion.py` from:

```python
def _build_quality_stats(received_by_region, valid_by_region, inserted_by_region,
                          lag_by_region, duration_by_region, fetch_timestamp):
```

to:

```python
def _build_quality_stats(received_by_region, valid_by_region, inserted_by_region,
                          lag_by_region, duration_by_region, status_by_region,
                          fetch_timestamp):
```

In the same function, change `all_regions` to include status keys:

```python
    all_regions = set(received_by_region) | set(duration_by_region) | set(status_by_region)
```

and add this key to the dict appended to `stats` (after `'fetch_duration_ms'`):

```python
            'fetch_status': status_by_region.get(region, 'ERROR'),
```

- [ ] **Step 7: Update the call site**

In `fetch_and_store_transit_data`, change:

```python
        quality_stats = _build_quality_stats(
            received_by_region, valid_by_region, inserted_by_region,
            lag_by_region, duration_by_region, current_unix
        )
```

to:

```python
        quality_stats = _build_quality_stats(
            received_by_region, valid_by_region, inserted_by_region,
            lag_by_region, duration_by_region, status_by_region, current_unix
        )
```

- [ ] **Step 8: Handle the early-return path**

`fetch_and_store_transit_data` returns early when `all_vehicle_data` is empty, so a fully-dead
fetch cycle would log nothing. Replace the existing early return:

```python
    if not all_vehicle_data:
        print("No vehicle data fetched")
        return
```

with:

```python
    if not all_vehicle_data:
        print("No vehicle data fetched")
        # Still record why, so a withdrawn feed is visible rather than silent.
        _write_quality_log(_build_quality_stats(
            {}, {}, {}, {}, duration_by_region, status_by_region, current_unix
        ))
        return
```

- [ ] **Step 9: Run the tests**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 47 passed (41 + 6 new)

- [ ] **Step 10: Commit**

```bash
git add src/utils/ingestion.py tests/test_script.py
git commit -m "feat: classify fetch status (OK/EMPTY/NO_FEED/THROTTLED/ERROR)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Persist `fetch_status` to DuckDB

**Files:**
- Modify: `src/utils/ingestion.py` (`_write_quality_log`)
- Test: `tests/test_script.py` (append)

**Interfaces:**
- Consumes: stat dicts containing `'fetch_status'` from Task 3.
- Produces: `fetch_quality_log` gains a `fetch_status VARCHAR` column, populated on every write.

**Critical hazard:** the current INSERT is positional — `INSERT INTO fetch_quality_log VALUES (?,?,?,?,?,?,?,?,?)` with 9 placeholders. Adding a column breaks it. This task MUST switch to an explicit column list, otherwise inserts fail or silently misalign.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_script.py`:

```python
def test_write_quality_log_persists_fetch_status(tmp_path, monkeypatch):
    import duckdb
    db = tmp_path / "q.duckdb"
    monkeypatch.setattr(ingestion, 'DATABASE_NAME', str(db))
    ingestion._write_quality_log([{
        'fetch_timestamp': 1750000000, 'region': 'R1',
        'vehicles_received': 5, 'vehicles_rejected': 0, 'vehicles_inserted': 5,
        'avg_data_lag_seconds': 1.0, 'max_data_lag_seconds': 2.0,
        'total_dropout': False, 'fetch_duration_ms': 100, 'fetch_status': 'OK',
    }])
    con = duckdb.connect(str(db))
    try:
        row = con.execute(
            "SELECT region, fetch_status FROM fetch_quality_log"
        ).fetchone()
    finally:
        con.close()
    assert row == ('R1', 'OK')


def test_write_quality_log_migrates_existing_table(tmp_path, monkeypatch):
    import duckdb
    db = tmp_path / "old.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE fetch_quality_log (
            fetch_timestamp BIGINT, region VARCHAR,
            vehicles_received INTEGER, vehicles_rejected INTEGER,
            vehicles_inserted INTEGER, avg_data_lag_seconds DOUBLE,
            max_data_lag_seconds DOUBLE, total_dropout BOOLEAN,
            fetch_duration_ms INTEGER
        )
    """)
    con.execute("INSERT INTO fetch_quality_log VALUES "
                "(1749999999,'OLD',1,0,1,0.0,0.0,false,10)")
    con.close()
    monkeypatch.setattr(ingestion, 'DATABASE_NAME', str(db))
    ingestion._write_quality_log([{
        'fetch_timestamp': 1750000000, 'region': 'NEW',
        'vehicles_received': 0, 'vehicles_rejected': 0, 'vehicles_inserted': 0,
        'avg_data_lag_seconds': 0.0, 'max_data_lag_seconds': 0.0,
        'total_dropout': True, 'fetch_duration_ms': 50, 'fetch_status': 'NO_FEED',
    }])
    con = duckdb.connect(str(db))
    try:
        rows = dict(con.execute(
            "SELECT region, fetch_status FROM fetch_quality_log"
        ).fetchall())
    finally:
        con.close()
    assert rows['NEW'] == 'NO_FEED'
    assert rows['OLD'] is None      # pre-existing row keeps NULL
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k write_quality_log -v`
Expected: FAIL — `duckdb.BinderException` / column `fetch_status` does not exist

- [ ] **Step 3: Add the column to CREATE and add a migration**

In `_write_quality_log` in `src/utils/ingestion.py`, replace the `CREATE TABLE IF NOT EXISTS` statement with:

```python
        con.execute("""
            CREATE TABLE IF NOT EXISTS fetch_quality_log (
                fetch_timestamp BIGINT,
                region VARCHAR,
                vehicles_received INTEGER,
                vehicles_rejected INTEGER,
                vehicles_inserted INTEGER,
                avg_data_lag_seconds DOUBLE,
                max_data_lag_seconds DOUBLE,
                total_dropout BOOLEAN,
                fetch_duration_ms INTEGER,
                fetch_status VARCHAR
            )
        """)

        # Additive migration for databases created before fetch_status existed.
        existing_cols = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'fetch_quality_log'"
        ).df()['column_name'].tolist()
        if 'fetch_status' not in existing_cols:
            con.execute("ALTER TABLE fetch_quality_log ADD COLUMN fetch_status VARCHAR")
```

- [ ] **Step 4: Make the INSERT name its columns**

Replace the insert loop in `_write_quality_log` with:

```python
        for s in stats_list:
            con.execute(
                "INSERT INTO fetch_quality_log ("
                "  fetch_timestamp, region, vehicles_received, vehicles_rejected,"
                "  vehicles_inserted, avg_data_lag_seconds, max_data_lag_seconds,"
                "  total_dropout, fetch_duration_ms, fetch_status"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                [s['fetch_timestamp'], s['region'], s['vehicles_received'],
                 s['vehicles_rejected'], s['vehicles_inserted'],
                 s['avg_data_lag_seconds'], s['max_data_lag_seconds'],
                 bool(s['total_dropout']), s['fetch_duration_ms'],
                 s.get('fetch_status', 'OK')]
            )
```

- [ ] **Step 5: Run the tests**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 49 passed (47 + 2 new)

- [ ] **Step 6: Commit**

```bash
git add src/utils/ingestion.py tests/test_script.py
git commit -m "feat: persist fetch_status to fetch_quality_log with migration

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Score only over scoreable fetches (dbt)

**Files:**
- Modify: `transform/models/staging/stg_fetch_quality.sql`
- Modify: `transform/models/staging/_staging.yml`
- Modify: `transform/models/marts/mart_network_health.sql`
- Modify: `transform/models/marts/_marts.yml`
- Modify: `transform/seeds/fetch_quality_log.csv`
- Test: `tests/test_dbt_marts.py` (append)

**Interfaces:**
- Consumes: `fetch_quality_log.fetch_status` from Task 4.
- Produces: `stg_fetch_quality` exposes `fetch_status` (NULL coalesced to `'OK'`) and a boolean `is_scoreable`. `mart_network_health` gains `scoreable_fetches BIGINT` and `feed_unavailable BOOLEAN`; `availability` is computed as `1 - (ERROR count / scoreable count)`.

- [ ] **Step 1: Extend the seed fixture with status cases**

Replace the contents of `transform/seeds/fetch_quality_log.csv` with:

```csv
fetch_timestamp,region,vehicles_received,vehicles_rejected,vehicles_inserted,avg_data_lag_seconds,max_data_lag_seconds,total_dropout,fetch_duration_ms,fetch_status
1750000000,TestRegion,10,2,8,30.0,60.0,false,120,OK
1750000060,TestRegion,10,0,10,0.0,0.0,false,110,OK
1750000000,DeadFeed,0,0,0,0.0,0.0,true,5,NO_FEED
1750000060,DeadFeed,0,0,0,0.0,0.0,true,5,NO_FEED
1750000000,QuietFeed,0,0,0,0.0,0.0,true,20,EMPTY
1750000060,QuietFeed,0,0,0,0.0,0.0,true,20,EMPTY
```

Note: `TestRegion`'s two rows are unchanged, so the existing hand-computed assertions (aggregate score 95, per-row 90/100) still hold.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_dbt_marts.py`:

```python
def test_dead_feed_is_flagged_unavailable_with_no_score(built_db):
    row = built_db.execute(
        "SELECT feed_unavailable, scoreable_fetches, reliability_score "
        "FROM main.mart_network_health WHERE region = 'DeadFeed'"
    ).fetchone()
    unavailable, scoreable, score = row
    assert unavailable is True
    assert scoreable == 0
    assert score is None


def test_empty_but_healthy_feed_is_not_penalised(built_db):
    row = built_db.execute(
        "SELECT feed_unavailable, scoreable_fetches, availability "
        "FROM main.mart_network_health WHERE region = 'QuietFeed'"
    ).fetchone()
    unavailable, scoreable, availability = row
    assert unavailable is False
    assert scoreable == 2
    assert availability == 1.0     # EMPTY is not an error


def test_ok_region_still_scores_as_before(built_db):
    score = built_db.execute(
        "SELECT reliability_score FROM main.mart_network_health "
        "WHERE region = 'TestRegion'"
    ).fetchone()[0]
    assert score == 95
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_dbt_marts.py -v`
Expected: FAIL — `Binder Error: ... column "feed_unavailable" not found`

- [ ] **Step 4: Expose status in staging**

Replace `transform/models/staging/stg_fetch_quality.sql` with:

```sql
with source as (
    select * from {{ source('transit', 'fetch_quality_log') }}
)
select
    cast(fetch_timestamp as bigint)         as fetch_timestamp,
    region,
    cast(vehicles_received as integer)      as vehicles_received,
    cast(vehicles_rejected as integer)      as vehicles_rejected,
    cast(vehicles_inserted as integer)      as vehicles_inserted,
    cast(avg_data_lag_seconds as double)    as avg_data_lag_seconds,
    cast(max_data_lag_seconds as double)    as max_data_lag_seconds,
    cast(total_dropout as boolean)          as total_dropout,
    cast(fetch_duration_ms as integer)      as fetch_duration_ms,
    -- Rows written before fetch_status existed are treated as OK so historical
    -- data keeps scoring exactly as it did.
    coalesce(fetch_status, 'OK')            as fetch_status,
    -- NO_FEED (withdrawn upstream) and THROTTLED (our own rate limiting) say
    -- nothing about the agency, so they are excluded from scoring entirely.
    coalesce(fetch_status, 'OK') not in ('NO_FEED', 'THROTTLED') as is_scoreable
from source
```

- [ ] **Step 5: Add a staging test for the status values**

In `transform/models/staging/_staging.yml`, under the `stg_fetch_quality` model's `columns:` list, append:

```yaml
      - name: fetch_status
        description: OK, EMPTY, NO_FEED, THROTTLED or ERROR. NULL (pre-migration) reads as OK.
        tests:
          - accepted_values:
              values: ['OK', 'EMPTY', 'NO_FEED', 'THROTTLED', 'ERROR']
```

- [ ] **Step 6: Score only over scoreable fetches**

Replace `transform/models/marts/mart_network_health.sql` with:

```sql
{% set cutoff = "cast(epoch(now()) as bigint) - cast(" ~ var('health_window_hours') ~ " as bigint) * 3600" %}

with q as (
    select * from {{ ref('stg_fetch_quality') }}
    where fetch_timestamp >= {{ cutoff }}
),
agg as (
    select
        region,
        count(*)                                              as total_fetches,
        sum(case when is_scoreable then 1 else 0 end)         as scoreable_fetches,
        sum(case when total_dropout then 1 else 0 end)        as dropout_count,
        coalesce(avg(case when vehicles_received > 0
            then vehicles_inserted::double / vehicles_received end), 0) as reporting_rate,
        -- Only a genuine ERROR counts against availability. EMPTY means the feed
        -- answered correctly and no service was running.
        sum(case when is_scoreable and fetch_status = 'ERROR' then 1 else 0 end) as error_count,
        coalesce(avg(avg_data_lag_seconds), 0)                as avg_data_lag_seconds,
        max(fetch_timestamp)                                  as last_fetch_timestamp
    from q
    group by region
),
scored as (
    select
        *,
        scoreable_fetches = 0 as feed_unavailable,
        case when scoreable_fetches > 0
             then 1.0 - error_count::double / scoreable_fetches
        end as availability
    from agg
)
select
    region,
    total_fetches,
    scoreable_fetches,
    feed_unavailable,
    dropout_count,
    reporting_rate,
    availability,
    avg_data_lag_seconds,
    last_fetch_timestamp,
    case when feed_unavailable then null else
        {{ reliability_score('reporting_rate', 'availability', 'avg_data_lag_seconds') }}
    end as reliability_score
from scored
order by reliability_score desc nulls last
```

- [ ] **Step 7: Add mart tests for the new columns**

In `transform/models/marts/_marts.yml`, under the `mart_network_health` model's `columns:` list, append:

```yaml
      - name: scoreable_fetches
        description: Fetches in the window that say something about agency reliability (excludes NO_FEED and THROTTLED).
        tests:
          - not_null
      - name: feed_unavailable
        description: True when the window contains no scoreable fetches — the feed is gone upstream.
        tests:
          - not_null
```

Also add `'DeadFeed'` and `'QuietFeed'` to the `accepted_values` list on `region` in the SAME model block, so the new fixture regions pass. That list is rendered target-aware; add them to the `ci`-only branch by changing the values expression to:

```yaml
              values: "{{ var('canonical_regions') + (['TestRegion', 'DeadFeed', 'QuietFeed'] if target.name == 'ci' else []) }}"
```

- [ ] **Step 8: Run the tests**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 52 passed (49 + 3 new)

- [ ] **Step 9: Commit**

```bash
git add transform/ tests/test_dbt_marts.py
git commit -m "feat: score network health only over scoreable fetches

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Show unavailable feeds distinctly on Network Health

**Files:**
- Modify: `src/utils/db.py` (`get_network_health_summary` — column passthrough only)
- Modify: `src/app_pages/network_health.py` (summary bar counts, scorecard rendering)

**Interfaces:**
- Consumes: `mart_network_health.feed_unavailable` and `scoreable_fetches` from Task 5.
- Produces: no new functions — presentation only.

**Context:** `get_network_health_summary` already does `SELECT * FROM main.mart_network_health`, so the new columns arrive with no query change. Only the ORDER BY needs to tolerate NULL scores.

- [ ] **Step 1: Make the read tolerate NULL scores**

In `src/utils/db.py`, inside `get_network_health_summary`, change:

```python
        return con.execute("SELECT * FROM main.mart_network_health ORDER BY reliability_score DESC").df()
```

to:

```python
        return con.execute(
            "SELECT * FROM main.mart_network_health "
            "ORDER BY reliability_score DESC NULLS LAST"
        ).df()
```

- [ ] **Step 2: Exclude unavailable feeds from the health counts**

In `src/app_pages/network_health.py`, replace the summary-bar counting block:

```python
    total_regions = len(health_df)
    healthy    = int((health_df['reliability_score'] >= 80).sum())
    degraded   = int(((health_df['reliability_score'] >= 50) & (health_df['reliability_score'] < 80)).sum())
    unreliable = int((health_df['reliability_score'] < 50).sum())
```

with:

```python
    total_regions = len(health_df)
    if 'feed_unavailable' in health_df.columns:
        unavailable_mask = health_df['feed_unavailable'].fillna(False).astype(bool)
    else:
        unavailable_mask = pd.Series(False, index=health_df.index)
    scored_df  = health_df[~unavailable_mask]
    healthy    = int((scored_df['reliability_score'] >= 80).sum())
    degraded   = int(((scored_df['reliability_score'] >= 50) & (scored_df['reliability_score'] < 80)).sum())
    unreliable = int((scored_df['reliability_score'] < 50).sum())
    unavailable = int(unavailable_mask.sum())
```

- [ ] **Step 3: Add an "unavailable" metric to the summary bar**

In the same file, replace the metrics block:

```python
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Regions Tracked", total_regions)
    col2.metric("🟢 Reliable",     healthy)
    col3.metric("🟡 Degraded",     degraded)
    col4.metric("🔴 Unreliable",   unreliable)
```

with:

```python
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Regions Tracked", total_regions)
    col2.metric("🟢 Reliable",     healthy)
    col3.metric("🟡 Degraded",     degraded)
    col4.metric("🔴 Unreliable",   unreliable)
    col6.metric("⚫ No Feed",      unavailable)
```

Note the existing `col5` "Last Fetch" block below stays exactly as it is.

- [ ] **Step 4: Render unavailable regions as a neutral card**

In the scorecard loop in `src/app_pages/network_health.py`, immediately after `with cols[j]:` and BEFORE the line `score = int(row['reliability_score'])...`, insert:

```python
                if row.get('feed_unavailable'):
                    st.markdown(f"""
                    <div style="border:1px solid #666;border-radius:8px;padding:12px;margin-bottom:8px;opacity:0.75;">
                        <div style="font-weight:bold;font-size:0.85em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{row['region']}</div>
                        <div style="font-size:1.1em;color:#999;font-weight:bold;line-height:1.6;">Feed unavailable</div>
                        <div style="font-size:0.75em;color:#999;">Withdrawn upstream — not scored</div>
                    </div>
                    """, unsafe_allow_html=True)
                    continue
```

- [ ] **Step 5: Guard the score formatting against NULL**

Still in the scorecard loop, the existing line

```python
                score     = int(row['reliability_score']) if pd.notna(row['reliability_score']) else 0
```

already handles NaN, so no change is needed. Confirm it is present and unchanged.

- [ ] **Step 6: Verify parse and suite**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -c "import ast; ast.parse(open('src/app_pages/network_health.py').read()); print('network_health.py parses OK')"
python -m pytest tests/ -q
```
Expected: parses OK; 52 passed

- [ ] **Step 7: Commit**

```bash
git add src/utils/db.py src/app_pages/network_health.py
git commit -m "feat: show withdrawn feeds as unavailable instead of a false low score

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Docs and version bump (2.3.0)

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add the `[2.3.0]` section to `CHANGELOG.md`** above `## [2.2.1]`

```markdown
## [2.3.0] - 2026-07-30

### Added
- **Route name search on the Live Map** — type a route (e.g. `T580`) to show only the vehicles running it. Matches the route number or any part of its name (`awan besar` works), case-insensitively, within the selected region. Hidden for KTM Berhad, whose realtime feed carries no `route_id`
- `data_processor.filter_by_route` — pure, testable route filtering
- `fetch_status` column on `fetch_quality_log`, classifying every fetch as `OK`, `EMPTY`, `NO_FEED` (HTTP 404), `THROTTLED` (HTTP 429) or `ERROR`, with an additive migration for existing databases
- `mart_network_health` gains `scoreable_fetches` and `feed_unavailable`

### Changed
- **Reliability scores now reflect the agency, not the plumbing.** `NO_FEED` and `THROTTLED` fetches are excluded from scoring, and `EMPTY` (feed healthy, no service running) no longer counts as an outage. Availability is now `1 − errors ÷ scoreable fetches`. The `reliability_score` formula itself is unchanged — only which rows feed it
- Regions whose feed has been withdrawn upstream render a neutral "Feed unavailable" card and a ⚫ No Feed count, instead of a misleading low score
- `_fetch_endpoint` returns `(vehicles, duration_ms, status)`; `_build_quality_stats` takes `status_by_region`
- `fetch_quality_log` inserts now name their columns explicitly rather than relying on positional order

### Fixed
- A fetch cycle in which every region fails now writes a quality-log row explaining why, instead of returning silently

### Notes
- Observed upstream: `prasarana?category=rapid-bus-kuantan` returns HTTP 404 (*"feed does not exist"*), which is why that region previously scored 20. It is still listed in the provider's documentation and may return
- Known limitation: because `EMPTY` is treated as healthy, an outage where a feed responds but returns nothing during service hours no longer reduces the score. Separating that from "no service scheduled" needs GTFS `calendar.txt`
```

- [ ] **Step 2: Document the search in `README.md`**

In the `### 🗺️ Live Map` feature list, add after the Route Viewer bullet:

```markdown
- **🔎 Route search** — type a route number or name (e.g. `T580`, or `awan besar`) to show only the vehicles running it. Not available for KTM Berhad, whose realtime feed carries no route ID
```

In the `### 📡 Network Health` feature list, add:

```markdown
- **Honest scoring** — feeds withdrawn upstream (HTTP 404) and self-inflicted rate limiting (HTTP 429) are excluded from reliability scores rather than blamed on the agency; a healthy feed reporting no vehicles out of service hours is not counted as an outage
```

In the `### Database Schema (fetch_quality_log)` table, add a row:

```markdown
| `fetch_status` | VARCHAR | `OK`, `EMPTY`, `NO_FEED` (404), `THROTTLED` (429) or `ERROR` |
```

In the Roadmap, change the search line to checked:

```markdown
- [x] Search by route name — type a route (e.g. `T580`) and see every vehicle on that
      route live on the map, instead of looking up an opaque vehicle ID
```

- [ ] **Step 3: Bump `pyproject.toml`**

Change `version = "2.2.1"` to `version = "2.3.0"`.

- [ ] **Step 4: Verify the four files agree**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
grep -n '^version' pyproject.toml
grep -n '## \[2.3.0\]' CHANGELOG.md
grep -n 'Route search\|fetch_status' README.md | head -5
python3 -c "
import re
p=open('pyproject.toml').read()
d=set(re.findall(r'\"([a-zA-Z0-9_.-]+[^\"]*)\"', p.split('dependencies = [')[1].split(']')[0]))
r=set(l.strip() for l in open('requirements.txt') if l.strip() and not l.startswith('#'))
print('dependency parity:', 'OK' if d==r else f'MISMATCH {d^r}')"
python -m pytest tests/ -q
```
Expected: version 2.3.0; CHANGELOG section present; README lines found; dependency parity OK; 52 passed

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml
git commit -m "docs: document route search and honest fetch scoring, bump to 2.3.0

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** route search behaviour + KTM hidden (T1, T2), `filter_by_route` pure unit (T1), status classification incl. multi-endpoint precedence (T3), schema migration + explicit-column insert (T4), staging `is_scoreable` + mart scoring + `feed_unavailable` (T5), Network Health presentation (T6), docs/version/known-limitation (T7). No spec requirement is unimplemented.
- **No dependency changes**, so `requirements.txt` needs no edit; T7 verifies parity holds anyway.
- **Type consistency:** `_fetch_endpoint` returns a 3-tuple everywhere it is consumed (T3 Step 5 is the only call site); `_build_quality_stats` gains `status_by_region` and both call sites are updated (T3 Steps 7 and 8); `fetch_status` is spelled identically across Python, DuckDB, seeds, staging and marts.
- **Deliberate omissions:** train route search and GTFS `calendar.txt` service-hours awareness are spec follow-ups, not tasks here. Airflow is backlog, blocked on hosting.
- **Test count arithmetic:** 36 → 41 (T1) → 47 (T3) → 49 (T4) → 52 (T5). T2, T6, T7 add no tests (UI wiring and docs).
