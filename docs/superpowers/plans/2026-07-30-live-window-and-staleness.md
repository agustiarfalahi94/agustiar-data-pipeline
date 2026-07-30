# Live Window Anchoring + Staleness Tiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop regions vanishing from the Live Map by anchoring the freshness window to wall-clock time, and show vehicles in fresh / stale / hidden tiers so nothing disappears silently.

**Architecture:** `db.py` fetches a wide bounded window anchored to `now` instead of `MAX(timestamp)`. A pure `classify_freshness` helper in `data_processor` assigns each vehicle a tier from its clamped age. `live_map.py` renders fresh solid, stale dimmed with a "last update" tooltip, and reports hidden vehicles in a caption.

**Tech Stack:** Python 3.9+, Streamlit, pandas, DuckDB, pydeck, pytest.

## Global Constraints

- Target version **2.4.0** (feature → minor bump) across `CHANGELOG.md`, `README.md`, `pyproject.toml`; keep `requirements.txt`/`requirements-dev.txt` consistent with `pyproject.toml`. Bump happens ONCE in the final task.
- The window MUST be anchored to wall-clock `now`, never to `MAX(timestamp)`. This is the bug fix; everything else is layered on it.
- Vehicle age is **clamped at zero**: `age = max(0, now - timestamp)`. A mildly future-dated timestamp reads as fresh rather than being dropped.
- Three bounds, all config-driven: `LIVE_FRESH_SECONDS = 60`, `LIVE_STALE_SECONDS = 300`, `LIVE_HIDDEN_SECONDS = 900`. Tiers: `fresh` ≤ 60 < `stale` ≤ 300 < `hidden` ≤ 900; rows older than 900s are never fetched.
- `total` in the metrics dict keeps meaning **fresh only**, so the headline number stays comparable to before. A new `stale` key counts the middle tier. Hidden rows appear in neither.
- Deduplication stays `ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY timestamp DESC)` — unrelated to the window, do not change it.
- Do NOT change `DATA_FUTURE_TOLERANCE` or anything in the ingestion path. This is a read-path fix.
- Do NOT change `get_vehicle_trail`.
- Test baseline is **63 passing**. Run tests with `export PATH="$(pwd)/.venv/bin:$PATH"` first — `pytest` lives in the repo venv.
- Every task ends with a commit whose message carries the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: `classify_freshness` pure helper

**Files:**
- Modify: `src/utils/data_processor.py` (append new function)
- Test: `tests/test_script.py` (append new tests)

**Interfaces:**
- Produces: `data_processor.classify_freshness(df, now, fresh_seconds=60, stale_seconds=300) -> DataFrame` — returns the frame with two added columns: `age_seconds` (int, clamped at 0) and `freshness` (`'fresh'` / `'stale'` / `'hidden'`). Rows are never dropped; the caller needs the hidden ones to count them. An empty frame or a frame without a `timestamp` column is returned unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`:

```python
def test_classify_freshness_assigns_three_tiers():
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['fresh', 'stale', 'hidden'],
        'timestamp': [now - 10, now - 120, now - 600],
    })
    out = data_processor.classify_freshness(df, now)
    assert list(out['freshness']) == ['fresh', 'stale', 'hidden']
    assert list(out['age_seconds']) == [10, 120, 600]


def test_classify_freshness_boundaries_are_inclusive():
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['at_fresh_edge', 'just_past', 'at_stale_edge', 'just_past_stale'],
        'timestamp': [now - 60, now - 61, now - 300, now - 301],
    })
    out = data_processor.classify_freshness(df, now)
    assert list(out['freshness']) == ['fresh', 'stale', 'stale', 'hidden']


def test_classify_freshness_clamps_future_timestamps_to_fresh():
    now = 1_800_000_000
    df = pd.DataFrame({'vehicle_id': ['ahead'], 'timestamp': [now + 250]})
    out = data_processor.classify_freshness(df, now)
    assert list(out['age_seconds']) == [0]
    assert list(out['freshness']) == ['fresh']


def test_classify_freshness_never_drops_rows():
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['a', 'b', 'c'],
        'timestamp': [now - 5, now - 400, now - 800],
    })
    assert len(data_processor.classify_freshness(df, now)) == 3


def test_classify_freshness_handles_empty_and_missing_column():
    now = 1_800_000_000
    assert data_processor.classify_freshness(pd.DataFrame(), now).empty
    df = pd.DataFrame({'vehicle_id': ['a']})
    out = data_processor.classify_freshness(df, now)
    assert 'freshness' not in out.columns


def test_classify_freshness_respects_custom_bounds():
    now = 1_800_000_000
    df = pd.DataFrame({'vehicle_id': ['a'], 'timestamp': [now - 30]})
    out = data_processor.classify_freshness(df, now, fresh_seconds=10, stale_seconds=20)
    assert list(out['freshness']) == ['hidden']
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k classify_freshness -v`
Expected: FAIL — `AttributeError: module 'utils.data_processor' has no attribute 'classify_freshness'`

- [ ] **Step 3: Implement the function**

Append to `src/utils/data_processor.py`:

```python
def classify_freshness(df, now, fresh_seconds=60, stale_seconds=300):
    """
    Tag each vehicle with how stale its position is.

    Adds two columns:
      age_seconds  int, clamped at 0 — a future-dated timestamp reads as age 0
                   rather than a negative age, so a feed whose clock runs fast
                   is treated as current instead of being discarded.
      freshness    'fresh'  (age <= fresh_seconds)
                   'stale'  (fresh_seconds < age <= stale_seconds)
                   'hidden' (age > stale_seconds)

    Rows are never dropped — the caller counts the hidden ones for its caption.
    A frame that is empty, or has no 'timestamp' column, is returned unchanged.
    """
    if df.empty or 'timestamp' not in df.columns:
        return df

    out = df.copy()
    ts = pd.to_numeric(out['timestamp'], errors='coerce')
    out['age_seconds'] = (now - ts).clip(lower=0).fillna(stale_seconds + 1).astype(int)
    out['freshness'] = pd.cut(
        out['age_seconds'],
        bins=[-1, fresh_seconds, stale_seconds, float('inf')],
        labels=['fresh', 'stale', 'hidden'],
    ).astype(str)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k classify_freshness -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Run the full suite**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 69 passed (63 baseline + 6 new)

- [ ] **Step 6: Commit**

```bash
git add src/utils/data_processor.py tests/test_script.py
git commit -m "feat: add classify_freshness helper for vehicle staleness tiers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Anchor the live window to now

**Files:**
- Modify: `src/config.example.py` (add three knobs)
- Modify: `src/utils/db.py` (`get_live_data_optimized` — import defaults, rewrite the window, add the `stale` metric)
- Test: `tests/test_script.py` (append)

**Interfaces:**
- Consumes: `data_processor.classify_freshness` from Task 1.
- Produces: `get_live_data_optimized()` returns `(df, metrics, sync_time_str)` where `df` carries `age_seconds` and `freshness` columns for every row within `LIVE_HIDDEN_SECONDS` of now, and `metrics` is `{'total': <fresh count>, 'stale': <stale count>, 'hidden': <hidden count>, 'regions': int, 'busiest': str}`. `regions` and `busiest` are computed over fresh + stale rows only (what is actually drawn).

**This is the bug fix.** The regression test in Step 1 must fail against the current `MAX(timestamp)` implementation — if it passes before you change `db.py`, the test is not exercising the bug.

- [ ] **Step 1: Write the failing regression test**

Append to `tests/test_script.py`:

```python
def test_live_window_ignores_future_dated_rows_from_other_regions(tmp_path, monkeypatch):
    """
    A single future-dated vehicle must not black out regions reporting honestly.

    This is the 2.4.0 bug: the window used to anchor to MAX(timestamp), so one
    row timestamped now+250 shifted the window to [now+190, now+250] and every
    normally-timestamped bus fell outside it.
    """
    import duckdb
    from utils import db as db_mod

    now = int(time.time())
    dbfile = tmp_path / "live.duckdb"
    con = duckdb.connect(str(dbfile))
    con.execute("""
        CREATE TABLE live_buses (
            region VARCHAR, vehicle_id VARCHAR, latitude DOUBLE, longitude DOUBLE,
            bearing DOUBLE, speed DOUBLE, timestamp BIGINT, trip_id VARCHAR,
            route_id VARCHAR, insert_timestamp BIGINT, created_at TIMESTAMP
        )
    """)
    # An honest bus reporting 10 seconds ago...
    con.execute(
        "INSERT INTO live_buses VALUES ('Rapid Bus KL','KL1',3.14,101.68,90,10.0,?, 'T1','T5800',?,current_timestamp)",
        [now - 10, now - 10])
    # ...and a vehicle in ANOTHER region whose clock runs 250s fast.
    con.execute(
        "INSERT INTO live_buses VALUES ('myBAS Melaka','MK1',2.19,102.25,90,5.0,?, 'M1','M100',?,current_timestamp)",
        [now + 250, now])
    con.close()

    monkeypatch.setattr(db_mod, 'DATABASE_NAME', str(dbfile))
    df, metrics, _ = db_mod.get_live_data_optimized()

    regions = set(df['region'])
    assert 'Rapid Bus KL' in regions, "honest bus was excluded by another region's fast clock"
    assert 'myBAS Melaka' in regions
    assert metrics['total'] == 2   # both are fresh; the future-dated one clamps to age 0


def test_live_metrics_split_fresh_from_stale(tmp_path, monkeypatch):
    import duckdb
    from utils import db as db_mod

    now = int(time.time())
    dbfile = tmp_path / "tiers.duckdb"
    con = duckdb.connect(str(dbfile))
    con.execute("""
        CREATE TABLE live_buses (
            region VARCHAR, vehicle_id VARCHAR, latitude DOUBLE, longitude DOUBLE,
            bearing DOUBLE, speed DOUBLE, timestamp BIGINT, trip_id VARCHAR,
            route_id VARCHAR, insert_timestamp BIGINT, created_at TIMESTAMP
        )
    """)
    for vid, age in [('f1', 10), ('f2', 30), ('s1', 120), ('h1', 600)]:
        con.execute(
            "INSERT INTO live_buses VALUES ('Rapid Bus KL',?,3.14,101.68,90,10.0,?, 'T1','T5800',?,current_timestamp)",
            [vid, now - age, now - age])
    con.close()

    monkeypatch.setattr(db_mod, 'DATABASE_NAME', str(dbfile))
    df, metrics, _ = db_mod.get_live_data_optimized()

    assert metrics['total'] == 2    # fresh only
    assert metrics['stale'] == 1
    assert metrics['hidden'] == 1
    assert len(df) == 4             # all four returned; the caller decides what to draw
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/test_script.py -k "live_window or live_metrics" -v`
Expected: FAIL — the first with `'Rapid Bus KL' not in regions` (proving the bug), the second with `KeyError: 'stale'`.

- [ ] **Step 3: Add the config knobs**

In `src/config.example.py`, immediately after the `DATA_FUTURE_TOLERANCE = 300` line, add:

```python

# Live Map freshness tiers (seconds). Ages are measured from wall-clock now and
# clamped at zero, so a feed whose clock runs fast cannot shift the window.
LIVE_FRESH_SECONDS = 60     # at or under this age a vehicle is drawn solid
LIVE_STALE_SECONDS = 300    # up to this age it is drawn dimmed with a "last update" note
LIVE_HIDDEN_SECONDS = 900   # up to this age it is counted as hidden; older is not fetched
```

- [ ] **Step 4: Import the knobs in `db.py`**

In `src/utils/db.py`, extend the existing config import block. Change:

```python
try:
    from config import DATABASE_NAME, DATABASE_TABLE, TIMEZONE, UTC_OFFSET_HOURS, DATA_RETENTION_DAYS
except ImportError:
    DATABASE_NAME = 'agustiar_analytics.duckdb'
    DATABASE_TABLE = 'live_buses'
    TIMEZONE = 'Asia/Kuala_Lumpur'
    UTC_OFFSET_HOURS = 8
    DATA_RETENTION_DAYS = 7
```

to:

```python
try:
    from config import (
        DATABASE_NAME, DATABASE_TABLE, TIMEZONE, UTC_OFFSET_HOURS, DATA_RETENTION_DAYS,
        LIVE_FRESH_SECONDS, LIVE_STALE_SECONDS, LIVE_HIDDEN_SECONDS,
    )
except ImportError:
    DATABASE_NAME = 'agustiar_analytics.duckdb'
    DATABASE_TABLE = 'live_buses'
    TIMEZONE = 'Asia/Kuala_Lumpur'
    UTC_OFFSET_HOURS = 8
    DATA_RETENTION_DAYS = 7
    LIVE_FRESH_SECONDS = 60
    LIVE_STALE_SECONDS = 300
    LIVE_HIDDEN_SECONDS = 900
```

Note: a `config.py` that predates these knobs would raise `ImportError` and fall back to ALL the defaults above, including `DATABASE_NAME`. That is acceptable because the defaults match `config.example.py`, but mention it in your report if you see a risk.

- [ ] **Step 5: Rewrite the window in `get_live_data_optimized`**

In `src/utils/db.py`, replace this block:

```python
    try:
        max_timestamp_raw = con.execute(f"SELECT MAX(timestamp) FROM {DATABASE_TABLE}").fetchone()[0]

        if max_timestamp_raw is None:
            return pd.DataFrame(), {}, None

        max_timestamp = int(max_timestamp_raw)
        sixty_seconds_ago = max_timestamp - 60

        query = f"""
        SELECT * FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY CAST(timestamp AS BIGINT) DESC) as rn
            FROM {DATABASE_TABLE}
            WHERE CAST(timestamp AS BIGINT) >= {sixty_seconds_ago}
        ) WHERE rn = 1
        """

        df = con.execute(query).df()

        if df.empty:
            return df, {}, None

        if 'rn' in df.columns:
            df = df.drop(columns=['rn'])

        sync_time_str = _format_sync_time(max_timestamp)
```

with:

```python
    try:
        # Anchor to wall-clock now, NOT MAX(timestamp). Anchoring to the newest
        # row let one feed with a fast clock drag the window into the future and
        # black out every region reporting honestly.
        now = int(time.time())
        cutoff = now - LIVE_HIDDEN_SECONDS

        query = f"""
        SELECT * FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY CAST(timestamp AS BIGINT) DESC) as rn
            FROM {DATABASE_TABLE}
            WHERE CAST(timestamp AS BIGINT) >= {cutoff}
        ) WHERE rn = 1
        """

        df = con.execute(query).df()

        if df.empty:
            return df, {}, None

        if 'rn' in df.columns:
            df = df.drop(columns=['rn'])

        max_timestamp_raw = con.execute(f"SELECT MAX(timestamp) FROM {DATABASE_TABLE}").fetchone()[0]
        sync_time_str = _format_sync_time(int(max_timestamp_raw)) if max_timestamp_raw else None
```

- [ ] **Step 6: Classify tiers and split the metrics**

Still in `get_live_data_optimized`, replace the metrics block:

```python
    metrics = {
        'total': len(df),
        'regions': len(df['region'].unique()),
        'busiest': df['region'].value_counts().idxmax() if len(df) > 0 else 'N/A'
    }

    return df, metrics, sync_time_str
```

with:

```python
    df = data_processor.classify_freshness(
        df, now, fresh_seconds=LIVE_FRESH_SECONDS, stale_seconds=LIVE_STALE_SECONDS
    )

    # Drawn on the map = fresh + stale. 'total' keeps its old meaning of
    # "reporting right now" so the headline number stays comparable.
    drawn = df[df['freshness'] != 'hidden']
    metrics = {
        'total': int((df['freshness'] == 'fresh').sum()),
        'stale': int((df['freshness'] == 'stale').sum()),
        'hidden': int((df['freshness'] == 'hidden').sum()),
        'regions': len(drawn['region'].unique()),
        'busiest': drawn['region'].value_counts().idxmax() if len(drawn) > 0 else 'N/A',
    }

    return df, metrics, sync_time_str
```

- [ ] **Step 7: Add the import**

`src/utils/db.py` does not currently import `data_processor`. Add it to the import block at the top of the file, after the existing `from datetime import ...` line:

```python
from utils import data_processor
```

This is verified safe: `data_processor` imports only `pandas`, so there is no circular dependency, and `tests/conftest.py` puts `src/` on `sys.path`, so the `utils.` prefix resolves under pytest as well as under Streamlit. `time` is already imported in this module — do not add it again.

- [ ] **Step 8: Run the tests**

Run: `export PATH="$(pwd)/.venv/bin:$PATH" && python -m pytest tests/ -q`
Expected: 71 passed (69 + 2 new)

- [ ] **Step 9: Commit**

```bash
git add src/config.example.py src/utils/db.py tests/test_script.py
git commit -m "fix: anchor the live window to now instead of MAX(timestamp)

One feed with a fast clock could drag the window into the future and black
out every region reporting honestly. Ages are now measured from wall-clock
now and clamped at zero.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Render the tiers on the Live Map

**Files:**
- Modify: `src/app_pages/live_map.py` (metrics row, colour columns, layers, tooltip, hidden caption)

**Interfaces:**
- Consumes: `df` carrying `age_seconds` and `freshness`, and `metrics` carrying `stale` / `hidden`, from Task 2.
- Produces: no new functions — rendering only.

**Context the implementer needs:** `prepare_map_data` filters `df_live` to the selected region and cleans coordinates; it preserves any extra columns, so `freshness` and `age_seconds` survive it. The layers currently use static colours: the ScatterplotLayer has `get_fill_color=[51, 153, 255, 255]` and the PathLayer `get_color=[255, 255, 255, 255]`. pydeck accepts a **column name** in place of a literal, so per-row dimming is done by adding colour columns to the frame.

- [ ] **Step 1: Add a Stale metric to the header**

In `src/app_pages/live_map.py`, replace:

```python
    col1.metric("Total Active Buses", metrics['total'])
    col2.metric("Regions Monitored", metrics['regions'])
    col3.metric("Busiest Region", metrics['busiest'])
```

with:

```python
    col1.metric("Total Active Buses", metrics['total'])
    col2.metric("Regions Monitored", metrics['regions'])
    col3.metric("Busiest Region", metrics['busiest'])
    if metrics.get('stale'):
        st.caption(
            f"⏳ {metrics['stale']} vehicle(s) last reported over "
            f"{LIVE_FRESH_SECONDS}s ago — shown dimmed on the map."
        )
```

and add `LIVE_FRESH_SECONDS` to this module's config import block, defaulting to `60` in the `except ImportError` branch, matching how the other knobs are handled in this file.

- [ ] **Step 2: Split the frame into drawn and hidden**

In `src/app_pages/live_map.py`, immediately after the existing early return:

```python
    if df_map.empty:
        st.warning(f"No valid data for {selected_region}")
        return
```

insert:

```python
    # Hidden vehicles are counted but not drawn — a 7-day retention window would
    # otherwise fill the map with buses parked at depots overnight.
    hidden_count = 0
    if 'freshness' in df_map.columns:
        hidden_count = int((df_map['freshness'] == 'hidden').sum())
        df_map = df_map[df_map['freshness'] != 'hidden']

    if df_map.empty:
        st.warning(
            f"No recent data for {selected_region} — "
            f"{hidden_count} vehicle(s) last reported over 5 minutes ago."
        )
        return
```

- [ ] **Step 3: Add per-row colour columns**

In `src/app_pages/live_map.py`, immediately after the `bearing_display` assignment, add:

```python
    # Stale vehicles keep their colour but drop to ~35% alpha, so they read as
    # present-but-uncertain rather than as a different kind of thing.
    is_stale = df_map.get('freshness', pd.Series('fresh', index=df_map.index)) == 'stale'
    df_map['dot_color'] = [
        [51, 153, 255, 90] if s else [51, 153, 255, 255] for s in is_stale
    ]
    df_map['arrow_color'] = [
        [255, 255, 255, 90] if s else [255, 255, 255, 255] for s in is_stale
    ]
```

`pandas` must be imported in this module — check the existing imports and add `import pandas as pd` only if it is absent.

- [ ] **Step 4: Point the layers at the colour columns**

In `src/app_pages/live_map.py`, in the ScatterplotLayer change:

```python
        get_fill_color=[51, 153, 255, 255],
```

to:

```python
        get_fill_color='dot_color',
```

and in the PathLayer change:

```python
        get_color=[255, 255, 255, 255],
```

to:

```python
        get_color='arrow_color',
```

- [ ] **Step 5: Add a last-update line to the tooltip**

In `src/app_pages/live_map.py`, immediately after the colour-column block from Step 3, add:

```python
    df_map['freshness_display'] = [
        f"{int(a)}s ago" if f == 'fresh' else f"⚠️ last update {int(a) // 60}m {int(a) % 60}s ago"
        for a, f in zip(
            df_map.get('age_seconds', pd.Series(0, index=df_map.index)),
            df_map.get('freshness', pd.Series('fresh', index=df_map.index)),
        )
    ]
```

Then extend the tooltip HTML. Change:

```python
                "html": "<b>Vehicle:</b> {vehicle_id}<br/><b>Route:</b> {route_display}<br/><b>Speed:</b> {speed_display} km/h<br/><b>Bearing:</b> {bearing_display}°",
```

to:

```python
                "html": "<b>Vehicle:</b> {vehicle_id}<br/><b>Route:</b> {route_display}<br/><b>Speed:</b> {speed_display} km/h<br/><b>Bearing:</b> {bearing_display}°<br/><b>Updated:</b> {freshness_display}",
```

- [ ] **Step 6: Report hidden vehicles below the map**

In `src/app_pages/live_map.py`, find the existing caption:

```python
    if not filter_active:
```

Immediately BEFORE that block, add:

```python
    if hidden_count:
        st.caption(
            f"🚫 {hidden_count} vehicle(s) hidden — no update in over 5 minutes."
        )
```

- [ ] **Step 7: Verify parse and suite**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -c "import ast; ast.parse(open('src/app_pages/live_map.py').read()); print('live_map.py parses OK')"
python -m pytest tests/ -q
```
Expected: parses OK; 71 passed

- [ ] **Step 8: Commit**

```bash
git add src/app_pages/live_map.py
git commit -m "feat: draw stale vehicles dimmed and report hidden ones

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Docs and version bump (2.4.0)

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add the `[2.4.0]` section to `CHANGELOG.md`** above `## [2.3.1]`

```markdown
## [2.4.0] - 2026-07-30

### Fixed
- **Regions no longer vanish from the Live Map.** The freshness window was anchored to the newest
  timestamp anywhere in the table, and ingestion accepts timestamps up to 5 minutes in the future —
  so a single vehicle with a fast clock shifted the window past every bus reporting honestly, and
  whole regions blacked out for a refresh at a time. Observed live: 101 buses across 7 regions
  while Rapid Bus KL, the largest network, showed "No valid data". The window is now anchored to
  wall-clock time and vehicle ages are clamped at zero, so no feed's clock can move it

### Added
- **Vehicle freshness tiers on the Live Map.** Vehicles reporting within 60s are drawn as before;
  those between 60s and 5 minutes are drawn dimmed with a "last update" line in their tooltip; those
  older than 5 minutes are hidden but reported in a caption rather than silently dropped
- `data_processor.classify_freshness` — pure, testable tier assignment
- `LIVE_FRESH_SECONDS` (60), `LIVE_STALE_SECONDS` (300) and `LIVE_HIDDEN_SECONDS` (900) config knobs
- A **Stale** count beside the existing Live Map metrics

### Changed
- The live window widened from 60 seconds to 5 minutes of drawn vehicles, so a bus that reported
  90 seconds ago is now visible (marked stale) instead of disappearing
- `get_live_data_optimized`'s metrics gain `stale` and `hidden` counts. `total` still counts only
  vehicles reporting within 60s, so it stays comparable to previous releases

### Notes
- Measured while diagnosing this: the Rapid Bus MRT Feeder feed published a timestamp of
  `1886017556` — roughly the year 2029. Ingestion rejects it, but `myBAS Kuching` and `myBAS Melaka`
  were simultaneously reporting +3s and +10s, so mildly future-dated timestamps are routine in
  these feeds rather than exotic
- `DATA_FUTURE_TOLERANCE` is deliberately unchanged. Clamping ages at zero removes its ability to
  distort the window, so tightening it would treat a symptom that is already fixed and would start
  rejecting real data from feeds whose clocks run slightly fast
```

- [ ] **Step 2: Update `README.md`**

In the `### 🗺️ Live Map` feature list, add after the Route search bullet:

```markdown
- **⏳ Freshness tiers** — vehicles reporting within 60s are drawn solid; those up to 5 minutes old
  are dimmed and their tooltip shows when they last reported; older ones are hidden but counted, so
  nothing disappears without explanation
```

In the Configuration table, add three rows after `DATA_FUTURE_TOLERANCE`:

```markdown
| `LIVE_FRESH_SECONDS` | `60` | Vehicles at or under this age are drawn solid |
| `LIVE_STALE_SECONDS` | `300` | Vehicles up to this age are drawn dimmed |
| `LIVE_HIDDEN_SECONDS` | `900` | Vehicles up to this age are counted as hidden; older are not fetched |
```

In the Key Design Decisions table, add a row:

```markdown
| **Live window anchored to wall-clock now** | Anchoring to `MAX(timestamp)` let one feed with a fast clock drag the window into the future and black out regions reporting honestly. Ages are clamped at zero so a fast clock reads as current rather than being discarded |
```

- [ ] **Step 3: Bump `pyproject.toml`**

Change `version = "2.3.1"` to `version = "2.4.0"`.

- [ ] **Step 4: Verify the four files agree**

Run:
```bash
export PATH="$(pwd)/.venv/bin:$PATH"
grep -n '^version' pyproject.toml
grep -n '## \[2.4.0\]' CHANGELOG.md
grep -n 'LIVE_FRESH_SECONDS' README.md src/config.example.py
python3 -c "
import re
p=open('pyproject.toml').read()
d=set(re.findall(r'\"([a-zA-Z0-9_.-]+[^\"]*)\"', p.split('dependencies = [')[1].split(']')[0]))
r=set(l.strip() for l in open('requirements.txt') if l.strip() and not l.startswith('#'))
print('dependency parity:', 'OK' if d==r else f'MISMATCH {d^r}')"
python -m pytest tests/ -q
```
Expected: version 2.4.0; CHANGELOG section present; the knob appears in both README and config; parity OK; 71 passed

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml
git commit -m "docs: document live-window fix and freshness tiers, bump to 2.4.0

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** anchor-to-now (T2), age clamping (T1 + T2), three tiers (T1), dimmed rendering + tooltip (T3), hidden caption (T3), metrics split (T2 + T3), config knobs (T2), pure-function placement (T1), docs/version (T4). The spec's required regression test — a future-dated fixture row that must fail against the current implementation — is T2 Step 1.
- **A detail the spec left implicit, now pinned:** to report "N vehicles hidden" the query must fetch *wider* than the stale bound, or there is nothing to count. Hence the third knob `LIVE_HIDDEN_SECONDS = 900`, which also bounds the count so a bus that stopped reporting three hours ago is not described as merely "hidden".
- **No dependency changes**, so `requirements.txt` needs no edit; T4 verifies parity regardless.
- **Type consistency:** `freshness` is always one of the three literal strings; `age_seconds` is always a non-negative int; `metrics` keys `total`/`stale`/`hidden`/`regions`/`busiest` are spelled identically in T2 and T3.
- **Test count arithmetic:** 63 → 69 (T1) → 71 (T2). T3 and T4 add no tests (rendering and docs).
- **Deliberate omissions:** ETA and nearest-stops are a separate project per the spec's Follow-ups; `DATA_FUTURE_TOLERANCE` and the ingestion path are untouched by design.
