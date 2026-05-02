# Network Health Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 📡 Network Health page that tracks and surfaces per-region data quality from the Malaysia GTFS Realtime API, giving transit enthusiasts and researchers an honest view of how much they can trust each region's data.

**Architecture:** A new `fetch_quality_log` DuckDB table is written by `ingestion.py` at every fetch cycle (one row per region). Three new read functions in `db.py` query it. A new `network_health.py` page renders four sections: summary bar, scorecards, region drill-down, and raw fetch log. The Network Health page is read-only — it never triggers a fetch itself.

**Tech Stack:** Python 3.13, DuckDB, Streamlit, Plotly (go.Figure), pandas, unittest.mock

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/utils/ingestion.py` | Modify | Fetch guard, per-region quality tracking, quality log write + prune |
| `src/utils/db.py` | Modify | Three new read-only functions for health data |
| `src/app_pages/network_health.py` | Create | Network Health page — 4 sections |
| `src/app.py` | Modify | Add nav entry + session state key |
| `CHANGELOG.md` | Create | v2.0.0 retroactive + v2.1.0 entries |
| `pyproject.toml` | Modify | Bump version to `2.1.0` |

Run all tests from the repo root: `.venv/bin/python -m pytest tests/ -v`

---

## Task 1: Update `_fetch_endpoint` to return `(vehicles, duration_ms)`

**Files:**
- Modify: `src/utils/ingestion.py`
- Test: `tests/test_script.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_script.py`:

```python
from unittest.mock import patch, MagicMock
from utils.ingestion import _fetch_endpoint


def test_fetch_endpoint_returns_tuple_on_network_error():
    """_fetch_endpoint must return (list, int) even when the request fails."""
    with patch('utils.ingestion.requests.get', side_effect=ConnectionError("timeout")):
        result = _fetch_endpoint("Test Region", "test-endpoint")
    assert isinstance(result, tuple), "should return a tuple"
    assert isinstance(result[0], list), "first element should be a list"
    assert isinstance(result[1], int), "second element should be duration_ms int"
    assert result[0] == [], "vehicle list should be empty on error"
    assert result[1] >= 0, "duration should be non-negative"


def test_fetch_endpoint_returns_tuple_on_non_200():
    """_fetch_endpoint must return ([], duration_ms) for non-200 responses."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    with patch('utils.ingestion.requests.get', return_value=mock_response):
        result = _fetch_endpoint("Test Region", "test-endpoint")
    assert isinstance(result, tuple)
    assert result[0] == []
    assert isinstance(result[1], int)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_script.py::test_fetch_endpoint_returns_tuple_on_network_error tests/test_script.py::test_fetch_endpoint_returns_tuple_on_non_200 -v
```

Expected: FAIL — `_fetch_endpoint` currently returns a list, not a tuple.

- [ ] **Step 3: Update `_fetch_endpoint` in `src/utils/ingestion.py`**

Replace the entire `_fetch_endpoint` function:

```python
def _fetch_endpoint(name, endpoint):
    """
    Fetch vehicle data from a single API endpoint.
    Returns (vehicles, duration_ms) — vehicles is [] on any error.
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
            return vehicles, duration_ms
    except Exception as e:
        print(f"Error fetching {name} ({endpoint}): {e}")
    return [], int((time.time() - t0) * 1000)
```

Also update the caller in `fetch_and_store_transit_data` — find this block:

```python
        for future in as_completed(future_to_task):
            all_vehicle_data.extend(future.result())
```

Replace with:

```python
        duration_by_region = {}
        for future in as_completed(future_to_task):
            name, endpoint = future_to_task[future]
            vehicles, duration_ms = future.result()
            all_vehicle_data.extend(vehicles)
            duration_by_region[name] = duration_by_region.get(name, 0) + duration_ms
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/utils/ingestion.py tests/test_script.py
git commit -m "feat: _fetch_endpoint returns (vehicles, duration_ms) tuple"
```

---

## Task 2: Extract and test `_build_quality_stats` pure function

The quality stats computation is a pure function — extract it so it can be tested independently of DuckDB and the network.

**Files:**
- Modify: `src/utils/ingestion.py`
- Test: `tests/test_script.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_script.py`:

```python
from utils.ingestion import _build_quality_stats


def test_build_quality_stats_basic():
    received = {'Rapid Bus KL': 50, 'KTM Berhad': 30}
    valid = {'Rapid Bus KL': 45, 'KTM Berhad': 28}
    inserted = {'Rapid Bus KL': 40, 'KTM Berhad': 25}
    lag = {
        'Rapid Bus KL': {'avg': 20.0, 'max': 60.0},
        'KTM Berhad': {'avg': 35.0, 'max': 90.0},
    }
    duration = {'Rapid Bus KL': 800, 'KTM Berhad': 600}
    ts = 1000000

    stats = _build_quality_stats(received, valid, inserted, lag, duration, ts)

    assert len(stats) == 2
    kl = next(s for s in stats if s['region'] == 'Rapid Bus KL')
    assert kl['fetch_timestamp'] == ts
    assert kl['vehicles_received'] == 50
    assert kl['vehicles_rejected'] == 5   # 50 - 45
    assert kl['vehicles_inserted'] == 40
    assert kl['avg_data_lag_seconds'] == 20.0
    assert kl['max_data_lag_seconds'] == 60.0
    assert kl['total_dropout'] is False
    assert kl['fetch_duration_ms'] == 800


def test_build_quality_stats_dropout():
    received = {'myBAS Johor': 0}
    valid = {}
    inserted = {}
    lag = {}
    duration = {'myBAS Johor': 500}
    ts = 1000000

    stats = _build_quality_stats(received, valid, inserted, lag, duration, ts)

    assert len(stats) == 1
    assert stats[0]['total_dropout'] is True
    assert stats[0]['vehicles_received'] == 0
    assert stats[0]['vehicles_rejected'] == 0
    assert stats[0]['vehicles_inserted'] == 0


def test_build_quality_stats_rejected_never_negative():
    """Dedup may mean inserted < valid — rejected should never go below 0."""
    received = {'Rapid Bus KL': 10}
    valid = {'Rapid Bus KL': 10}
    inserted = {'Rapid Bus KL': 3}   # 7 were duplicates
    lag = {'Rapid Bus KL': {'avg': 5.0, 'max': 10.0}}
    duration = {'Rapid Bus KL': 300}
    ts = 1000000

    stats = _build_quality_stats(received, valid, inserted, lag, duration, ts)
    assert stats[0]['vehicles_rejected'] == 0  # not negative
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_script.py::test_build_quality_stats_basic tests/test_script.py::test_build_quality_stats_dropout tests/test_script.py::test_build_quality_stats_rejected_never_negative -v
```

Expected: FAIL — `_build_quality_stats` does not exist yet.

- [ ] **Step 3: Add `_build_quality_stats` to `src/utils/ingestion.py`**

Add this function before `fetch_and_store_transit_data`:

```python
def _build_quality_stats(received_by_region, valid_by_region, inserted_by_region,
                          lag_by_region, duration_by_region, fetch_timestamp):
    """
    Build one quality-log row per region from per-stage pipeline counts.

    Args:
        received_by_region:  {region: int}  raw count before filtering
        valid_by_region:     {region: int}  count after coord/timestamp filter
        inserted_by_region:  {region: int}  count actually written to live_buses
        lag_by_region:       {region: {'avg': float, 'max': float}}
        duration_by_region:  {region: int}  fetch wall-clock ms
        fetch_timestamp:     int  unix time of this fetch cycle

    Returns:
        list of dicts, one per region
    """
    all_regions = set(received_by_region) | set(duration_by_region)
    stats = []
    for region in sorted(all_regions):
        received = received_by_region.get(region, 0)
        valid = valid_by_region.get(region, 0)
        inserted = inserted_by_region.get(region, 0)
        rejected = max(0, received - valid)
        lag = lag_by_region.get(region, {'avg': 0.0, 'max': 0.0})
        stats.append({
            'fetch_timestamp': fetch_timestamp,
            'region': region,
            'vehicles_received': received,
            'vehicles_rejected': rejected,
            'vehicles_inserted': inserted,
            'avg_data_lag_seconds': float(lag['avg']),
            'max_data_lag_seconds': float(lag['max']),
            'total_dropout': received == 0,
            'fetch_duration_ms': duration_by_region.get(region, 0),
        })
    return stats
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all 17 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/utils/ingestion.py tests/test_script.py
git commit -m "feat: extract _build_quality_stats pure function with tests"
```

---

## Task 3: Add fetch guard to `fetch_and_store_transit_data`

**Files:**
- Modify: `src/utils/ingestion.py`
- Test: `tests/test_script.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_script.py`:

```python
import tempfile
import os


def test_fetch_guard_skips_when_recent_fetch_exists():
    """
    If fetch_quality_log has a row within the last 15 seconds,
    fetch_and_store_transit_data should return early without calling requests.get.
    """
    import duckdb
    from unittest.mock import patch

    with tempfile.NamedTemporaryFile(suffix='.duckdb', delete=False) as f:
        db_path = f.name

    try:
        # Pre-populate fetch_quality_log with a row 5 seconds ago
        con = duckdb.connect(db_path)
        con.execute("""
            CREATE TABLE fetch_quality_log (
                fetch_timestamp BIGINT, region VARCHAR,
                vehicles_received INTEGER, vehicles_rejected INTEGER,
                vehicles_inserted INTEGER, avg_data_lag_seconds DOUBLE,
                max_data_lag_seconds DOUBLE, total_dropout BOOLEAN,
                fetch_duration_ms INTEGER
            )
        """)
        con.execute(f"INSERT INTO fetch_quality_log VALUES ({int(time.time()) - 5}, 'Test', 0, 0, 0, 0, 0, false, 0)")
        con.close()

        call_count = {'n': 0}

        def fake_get(*args, **kwargs):
            call_count['n'] += 1
            raise AssertionError("requests.get should not be called")

        with patch('utils.ingestion.DATABASE_NAME', db_path), \
             patch('utils.ingestion.requests.get', side_effect=fake_get):
            from utils import ingestion
            ingestion.fetch_and_store_transit_data()

        assert call_count['n'] == 0, "requests.get was called despite recent fetch"
    finally:
        os.unlink(db_path)
```

Also add `import time` at the top of `tests/test_script.py` if not already present.

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_script.py::test_fetch_guard_skips_when_recent_fetch_exists -v
```

Expected: FAIL — the function currently always fetches.

- [ ] **Step 3: Add fetch guard at top of `fetch_and_store_transit_data`**

Add this block as the first thing inside `fetch_and_store_transit_data`, after `current_unix = int(time.time())`:

```python
    # Fetch guard: skip if a fetch already ran within the last 15 seconds.
    # Prevents duplicate quality log entries and DuckDB write collisions when
    # multiple Streamlit sessions trigger refresh simultaneously.
    try:
        _guard_con = duckdb.connect(DATABASE_NAME)
        try:
            recent = _guard_con.execute(
                f"SELECT COUNT(*) FROM fetch_quality_log WHERE fetch_timestamp >= {current_unix - 15}"
            ).fetchone()[0]
            if recent > 0:
                print("⚡ Skipping fetch — already ran within last 15 seconds")
                return
        finally:
            _guard_con.close()
    except Exception:
        pass  # Table doesn't exist on first run — proceed normally
```

- [ ] **Step 4: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all 18 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/utils/ingestion.py tests/test_script.py
git commit -m "feat: add fetch guard to prevent concurrent write collisions"
```

---

## Task 4: Wire per-region quality tracking + write quality log

This task adds the stat collection and log write to the existing `fetch_and_store_transit_data` pipeline. No new test needed beyond what Task 2 already covers — the per-region counting is delegation to `_build_quality_stats` which is fully tested.

**Files:**
- Modify: `src/utils/ingestion.py`

- [ ] **Step 1: Add quality stat collection before + after filtering**

In `fetch_and_store_transit_data`, directly after `if not all_vehicle_data: ... return`, add:

```python
    # Count vehicles received per region BEFORE filtering (ground truth)
    received_by_region = {}
    for item in all_vehicle_data:
        r = item.get('region', 'Unknown')
        received_by_region[r] = received_by_region.get(r, 0) + 1
```

Then, directly after the filtering block (after `if df.empty: ... return`), add:

```python
    # Count valid vehicles per region AFTER filtering
    valid_by_region = df.groupby('region').size().to_dict()

    # Compute per-region data lag (insert_timestamp minus vehicle timestamp)
    df['_lag'] = current_unix - pd.to_numeric(df['timestamp'], errors='coerce').fillna(current_unix)
    _lag_stats = df.groupby('region')['_lag'].agg(['mean', 'max'])
    lag_by_region = {
        region: {'avg': float(row['mean']), 'max': float(row['max'])}
        for region, row in _lag_stats.iterrows()
    }
    df = df.drop(columns=['_lag'])
```

- [ ] **Step 2: Capture inserted counts after the INSERT, then write quality log**

In `fetch_and_store_transit_data`, inside the `try` block that wraps the DuckDB operations, add the following **after** the `con.execute(f"""INSERT INTO {DATABASE_TABLE} ...""")` block and the `changes()` call:

```python
            # Capture per-region insert counts for quality log
            inserted_by_region = {}
            try:
                ins_df = con.execute(
                    f"SELECT region, COUNT(*) as cnt FROM {DATABASE_TABLE} "
                    f"WHERE insert_timestamp = {current_unix} GROUP BY region"
                ).df()
                inserted_by_region = ins_df.set_index('region')['cnt'].to_dict()
            except Exception:
                pass
```

Then, still inside the same `try` block, **after** the existing `DELETE FROM {DATABASE_TABLE}` prune line, add:

```python
            # Build and write quality log
            quality_stats = _build_quality_stats(
                received_by_region, valid_by_region, inserted_by_region,
                lag_by_region, duration_by_region, current_unix
            )
            _write_quality_log(quality_stats, con)

            # Prune quality log with same retention window as live_buses
            try:
                from config import DATA_RETENTION_DAYS as _DRD
            except ImportError:
                _DRD = 7
            con.execute(f"DELETE FROM fetch_quality_log WHERE fetch_timestamp < {current_unix - _DRD * 86400}")
```

- [ ] **Step 3: Add `_write_quality_log` helper function**

Add this function immediately before `fetch_and_store_transit_data`:

```python
def _write_quality_log(stats_list, con):
    """Write quality stats rows to fetch_quality_log. Creates table if needed."""
    if not stats_list:
        return
    try:
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
                fetch_duration_ms INTEGER
            )
        """)
        quality_df = pd.DataFrame(stats_list)
        con.execute("INSERT INTO fetch_quality_log SELECT * FROM quality_df")
    except Exception as e:
        print(f"Quality log write error (non-fatal): {e}")
```

- [ ] **Step 4: Also handle the table-creation path (when `live_buses` doesn't exist yet)**

In the `if not table_exists:` branch (which runs `CREATE TABLE {DATABASE_TABLE} AS SELECT * FROM df`), add after the print statement:

```python
            # Write initial quality log entry
            inserted_by_region = df.groupby('region').size().to_dict()
            quality_stats = _build_quality_stats(
                received_by_region, valid_by_region, inserted_by_region,
                lag_by_region, duration_by_region, current_unix
            )
            _write_quality_log(quality_stats, con)
```

- [ ] **Step 5: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all 18 tests pass (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/utils/ingestion.py
git commit -m "feat: wire per-region quality tracking and fetch_quality_log writes"
```

---

## Task 5: Add `_quality_log_exists` + `get_network_health_summary` to `db.py`

**Files:**
- Modify: `src/utils/db.py`
- Test: `tests/test_script.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_script.py`:

```python
import tempfile
import os
import duckdb as _duckdb


def _make_temp_db_with_quality_log(rows):
    """Helper: create a temp DuckDB file with fetch_quality_log populated."""
    f = tempfile.NamedTemporaryFile(suffix='.duckdb', delete=False)
    f.close()
    con = _duckdb.connect(f.name)
    con.execute("""
        CREATE TABLE fetch_quality_log (
            fetch_timestamp BIGINT, region VARCHAR,
            vehicles_received INTEGER, vehicles_rejected INTEGER,
            vehicles_inserted INTEGER, avg_data_lag_seconds DOUBLE,
            max_data_lag_seconds DOUBLE, total_dropout BOOLEAN,
            fetch_duration_ms INTEGER
        )
    """)
    for row in rows:
        con.execute(
            "INSERT INTO fetch_quality_log VALUES (?,?,?,?,?,?,?,?,?)",
            [row['fetch_timestamp'], row['region'], row['vehicles_received'],
             row['vehicles_rejected'], row['vehicles_inserted'],
             row['avg_data_lag_seconds'], row['max_data_lag_seconds'],
             row['total_dropout'], row['fetch_duration_ms']]
        )
    con.close()
    return f.name


def test_get_network_health_summary_returns_one_row_per_region():
    now = int(time.time())
    db_path = _make_temp_db_with_quality_log([
        {'fetch_timestamp': now - 100, 'region': 'Rapid Bus KL',
         'vehicles_received': 50, 'vehicles_rejected': 5, 'vehicles_inserted': 40,
         'avg_data_lag_seconds': 20.0, 'max_data_lag_seconds': 60.0,
         'total_dropout': False, 'fetch_duration_ms': 800},
        {'fetch_timestamp': now - 100, 'region': 'KTM Berhad',
         'vehicles_received': 30, 'vehicles_rejected': 2, 'vehicles_inserted': 25,
         'avg_data_lag_seconds': 35.0, 'max_data_lag_seconds': 90.0,
         'total_dropout': False, 'fetch_duration_ms': 600},
    ])
    try:
        with patch('utils.db.DATABASE_NAME', db_path):
            from utils import db as _db
            result = _db.get_network_health_summary(window_hours=24)
        assert len(result) == 2
        assert set(result['region'].tolist()) == {'Rapid Bus KL', 'KTM Berhad'}
        assert 'reliability_score' in result.columns
        assert 'reporting_rate' in result.columns
        assert 'dropout_count' in result.columns
        assert 'total_fetches' in result.columns
        assert all(result['reliability_score'].between(0, 100))
    finally:
        os.unlink(db_path)


def test_get_network_health_summary_returns_empty_when_no_table():
    with tempfile.NamedTemporaryFile(suffix='.duckdb', delete=False) as f:
        db_path = f.name
    try:
        with patch('utils.db.DATABASE_NAME', db_path):
            from utils import db as _db
            result = _db.get_network_health_summary()
        assert result.empty
    finally:
        os.unlink(db_path)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_script.py::test_get_network_health_summary_returns_one_row_per_region tests/test_script.py::test_get_network_health_summary_returns_empty_when_no_table -v
```

Expected: FAIL — functions don't exist yet.

- [ ] **Step 3: Add `_quality_log_exists` and `get_network_health_summary` to `src/utils/db.py`**

Add after the existing `prune_old_data` function:

```python
def _quality_log_exists():
    con = get_connection()
    try:
        result = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'fetch_quality_log'"
        ).fetchone()[0]
        return result > 0
    finally:
        con.close()


def get_network_health_summary(window_hours=24):
    """
    Returns one row per region with reliability_score and component metrics,
    calculated over the last window_hours of fetch_quality_log data.

    Columns: region, reliability_score, reporting_rate, availability,
             avg_data_lag_seconds, dropout_count, total_fetches, last_fetch_timestamp
    """
    if not _quality_log_exists():
        return pd.DataFrame()

    cutoff = int(time.time()) - int(window_hours * 3600)
    con = get_connection()
    try:
        query = f"""
        SELECT
            region,
            COUNT(*) AS total_fetches,
            SUM(CASE WHEN total_dropout THEN 1 ELSE 0 END) AS dropout_count,
            COALESCE(AVG(CASE WHEN vehicles_received > 0
                THEN vehicles_inserted::DOUBLE / vehicles_received
                ELSE NULL END), 0) AS reporting_rate,
            1.0 - SUM(CASE WHEN total_dropout THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS availability,
            COALESCE(AVG(avg_data_lag_seconds), 0) AS avg_data_lag_seconds,
            MAX(fetch_timestamp) AS last_fetch_timestamp,
            ROUND((
                0.4 * COALESCE(AVG(CASE WHEN vehicles_received > 0
                    THEN vehicles_inserted::DOUBLE / vehicles_received
                    ELSE NULL END), 0)
                + 0.4 * (1.0 - SUM(CASE WHEN total_dropout THEN 1 ELSE 0 END)::DOUBLE / COUNT(*))
                + 0.2 * GREATEST(0.0, 1.0 - COALESCE(AVG(avg_data_lag_seconds), 0) / 300.0)
            ) * 100) AS reliability_score
        FROM fetch_quality_log
        WHERE fetch_timestamp >= {cutoff}
        GROUP BY region
        ORDER BY reliability_score DESC
        """
        return con.execute(query).df()
    finally:
        con.close()
```

- [ ] **Step 4: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all 20 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/utils/db.py tests/test_script.py
git commit -m "feat: add _quality_log_exists and get_network_health_summary to db.py"
```

---

## Task 6: Add `get_region_health_trend` and `get_region_fetch_log` to `db.py`

**Files:**
- Modify: `src/utils/db.py`
- Test: `tests/test_script.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_script.py`:

```python
def test_get_region_health_trend_returns_time_series():
    now = int(time.time())
    rows = [
        {'fetch_timestamp': now - (i * 20), 'region': 'Rapid Bus KL',
         'vehicles_received': 50, 'vehicles_rejected': 2, 'vehicles_inserted': 45,
         'avg_data_lag_seconds': 15.0, 'max_data_lag_seconds': 40.0,
         'total_dropout': False, 'fetch_duration_ms': 700}
        for i in range(5)
    ]
    db_path = _make_temp_db_with_quality_log(rows)
    try:
        with patch('utils.db.DATABASE_NAME', db_path):
            from utils import db as _db
            result = _db.get_region_health_trend('Rapid Bus KL', window_hours=1)
        assert len(result) == 5
        assert 'reliability_score' in result.columns
        assert 'datetime' in result.columns
        assert 'avg_data_lag_seconds' in result.columns
        assert 'vehicles_received' in result.columns
    finally:
        os.unlink(db_path)


def test_get_region_fetch_log_returns_most_recent_first():
    now = int(time.time())
    rows = [
        {'fetch_timestamp': now - (i * 20), 'region': 'KTM Berhad',
         'vehicles_received': 30, 'vehicles_rejected': 1, 'vehicles_inserted': 28,
         'avg_data_lag_seconds': 25.0, 'max_data_lag_seconds': 70.0,
         'total_dropout': False, 'fetch_duration_ms': 500}
        for i in range(10)
    ]
    db_path = _make_temp_db_with_quality_log(rows)
    try:
        with patch('utils.db.DATABASE_NAME', db_path):
            from utils import db as _db
            result = _db.get_region_fetch_log('KTM Berhad', limit=5)
        assert len(result) == 5
        assert 'datetime' in result.columns
        # Most recent first
        assert result.iloc[0]['fetch_timestamp'] >= result.iloc[1]['fetch_timestamp']
    finally:
        os.unlink(db_path)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_script.py::test_get_region_health_trend_returns_time_series tests/test_script.py::test_get_region_fetch_log_returns_most_recent_first -v
```

Expected: FAIL.

- [ ] **Step 3: Add both functions to `src/utils/db.py`**

Add after `get_network_health_summary`:

```python
def get_region_health_trend(region, window_hours=24):
    """
    Returns time-series rows from fetch_quality_log for one region,
    oldest first, within window_hours. Includes per-row reliability_score
    and a 'datetime' column converted to the configured timezone.
    """
    if not _quality_log_exists():
        return pd.DataFrame()

    cutoff = int(time.time()) - int(window_hours * 3600)
    con = get_connection()
    try:
        query = f"""
        SELECT
            fetch_timestamp,
            vehicles_received,
            vehicles_rejected,
            vehicles_inserted,
            avg_data_lag_seconds,
            max_data_lag_seconds,
            total_dropout,
            ROUND((
                0.4 * CASE WHEN vehicles_received > 0
                    THEN vehicles_inserted::DOUBLE / vehicles_received ELSE 0 END
                + 0.4 * CASE WHEN total_dropout THEN 0.0 ELSE 1.0 END
                + 0.2 * GREATEST(0.0, 1.0 - avg_data_lag_seconds / 300.0)
            ) * 100) AS reliability_score
        FROM fetch_quality_log
        WHERE region = ? AND fetch_timestamp >= {cutoff}
        ORDER BY fetch_timestamp ASC
        """
        df = con.execute(query, [region]).df()
    finally:
        con.close()

    if not df.empty:
        df['datetime'] = pd.to_datetime(
            df['fetch_timestamp'], unit='s', utc=True
        ).dt.tz_convert(TIMEZONE)
    return df


def get_region_fetch_log(region, limit=100):
    """
    Returns the most recent raw fetch_quality_log rows for one region,
    newest first. Adds a human-readable 'datetime' column.
    """
    if not _quality_log_exists():
        return pd.DataFrame()

    con = get_connection()
    try:
        query = f"""
        SELECT *
        FROM fetch_quality_log
        WHERE region = ?
        ORDER BY fetch_timestamp DESC
        LIMIT {int(limit)}
        """
        df = con.execute(query, [region]).df()
    finally:
        con.close()

    if not df.empty:
        df['datetime'] = pd.to_datetime(
            df['fetch_timestamp'], unit='s', utc=True
        ).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')
    return df
```

- [ ] **Step 4: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all 22 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/utils/db.py tests/test_script.py
git commit -m "feat: add get_region_health_trend and get_region_fetch_log to db.py"
```

---

## Task 7: Create `src/app_pages/network_health.py`

The page has no unit tests (Streamlit rendering cannot be unit tested). Verify manually after Task 9 by running the app.

**Files:**
- Create: `src/app_pages/network_health.py`

- [ ] **Step 1: Create the file**

Create `src/app_pages/network_health.py` with this content:

```python
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from utils import db

try:
    from config import UTC_OFFSET_HOURS
except ImportError:
    UTC_OFFSET_HOURS = 8


def _score_color(score):
    if score >= 80:
        return '#2ecc71'
    elif score >= 50:
        return '#f39c12'
    return '#e74c3c'


def _score_label(score):
    if score >= 80:
        return 'Reliable'
    elif score >= 50:
        return 'Degraded'
    return 'Unreliable'


def show():
    st.markdown("## 📡 Network Health")
    st.caption("Per-region data quality tracking — how reliably each transit region reports to the API.")

    health_df = db.get_network_health_summary(window_hours=24)

    # Thin-data / no-data notice
    provisional = False
    if health_df.empty:
        st.info("No quality data yet. Click **Refresh Data** on the Live Map page (or enable auto-refresh) to start building history.")
        return
    if health_df['total_fetches'].max() < 10:
        st.info("⚠️ Reliability scores improve with more data. Enable auto-refresh to build history.")
        provisional = True

    # ── Section 1: Network Summary Bar ─────────────────────────────────────
    total_regions = len(health_df)
    healthy   = int((health_df['reliability_score'] >= 80).sum())
    degraded  = int(((health_df['reliability_score'] >= 50) & (health_df['reliability_score'] < 80)).sum())
    unreliable = int((health_df['reliability_score'] < 50).sum())
    last_ts = health_df['last_fetch_timestamp'].max()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Regions Tracked", total_regions)
    col2.metric("🟢 Reliable",    healthy)
    col3.metric("🟡 Degraded",    degraded)
    col4.metric("🔴 Unreliable",  unreliable)
    if pd.notna(last_ts):
        dt = datetime.fromtimestamp(int(last_ts), tz=timezone.utc) + timedelta(hours=UTC_OFFSET_HOURS)
        col5.metric("Last Fetch", dt.strftime('%H:%M:%S'))

    st.divider()

    # ── Section 2: Region Scorecards ────────────────────────────────────────
    st.markdown("### Region Reliability Scorecards")
    if provisional:
        st.caption("Provisional — fewer than 10 fetch cycles recorded.")

    # Pre-load sparkline trend data for all regions
    trend_cache = {
        row['region']: db.get_region_health_trend(row['region'], window_hours=24)
        for _, row in health_df.iterrows()
    }

    regions = health_df.to_dict('records')
    for i in range(0, len(regions), 4):
        cols = st.columns(4)
        for j, row in enumerate(regions[i:i + 4]):
            with cols[j]:
                score     = int(row['reliability_score']) if pd.notna(row['reliability_score']) else 0
                color     = _score_color(score)
                label     = _score_label(score)
                reporting = f"{row['reporting_rate'] * 100:.0f}%" if pd.notna(row['reporting_rate']) else "N/A"
                lag       = f"{row['avg_data_lag_seconds']:.0f}s"  if pd.notna(row['avg_data_lag_seconds']) else "N/A"
                dropouts  = int(row['dropout_count']) if pd.notna(row['dropout_count']) else 0

                st.markdown(f"""
                <div style="border:1px solid {color};border-radius:8px;padding:12px;margin-bottom:8px;">
                    <div style="font-weight:bold;font-size:0.85em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{row['region']}</div>
                    <div style="font-size:2em;color:{color};font-weight:bold;line-height:1.1;">{score}</div>
                    <div style="font-size:0.75em;color:{color};">{label}</div>
                    <div style="font-size:0.72em;margin-top:4px;color:#888;">
                        📶 {reporting} &nbsp;|&nbsp; ⏱ {lag} &nbsp;|&nbsp; 🚫 {dropouts}
                    </div>
                </div>
                """, unsafe_allow_html=True)

                t_df = trend_cache.get(row['region'], pd.DataFrame())
                if not t_df.empty and 'reliability_score' in t_df.columns:
                    fig = go.Figure(go.Scatter(
                        y=t_df['reliability_score'],
                        mode='lines',
                        line=dict(color=color, width=1.5),
                    ))
                    fig.update_layout(
                        height=55,
                        margin=dict(l=0, r=0, t=0, b=0),
                        showlegend=False,
                        xaxis=dict(visible=False),
                        yaxis=dict(visible=False, range=[0, 100]),
                        plot_bgcolor='rgba(0,0,0,0)',
                        paper_bgcolor='rgba(0,0,0,0)',
                    )
                    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    st.divider()

    # Region selectbox (drives drill-down)
    all_regions = health_df['region'].tolist()
    if (st.session_state.get('health_selected_region') not in all_regions):
        st.session_state.health_selected_region = all_regions[0]

    selected = st.selectbox(
        "Select region to inspect",
        options=all_regions,
        index=all_regions.index(st.session_state.health_selected_region),
        key="health_region_selectbox",
    )
    if selected != st.session_state.health_selected_region:
        st.session_state.health_selected_region = selected

    # ── Section 3: Region Drill-Down ────────────────────────────────────────
    st.markdown(f"### 🔍 {selected}")

    window_map = {'1h': 1, '6h': 6, '24h': 24, '7d': 168}
    window_label = st.radio(
        "Time window", list(window_map.keys()), index=2,
        horizontal=True, key="health_window_radio",
    )
    window_hours = window_map[window_label]

    trend_df = db.get_region_health_trend(selected, window_hours=window_hours)

    if trend_df.empty:
        st.info("No data for this region in the selected window.")
    else:
        # Reliability score over time
        fig_score = go.Figure(go.Scatter(
            x=trend_df['datetime'], y=trend_df['reliability_score'],
            mode='lines', name='Reliability Score',
            line=dict(color='#3498db', width=2),
            fill='tozeroy', fillcolor='rgba(52,152,219,0.1)',
        ))
        fig_score.update_layout(
            title='Reliability Score Over Time',
            yaxis=dict(range=[0, 100], title='Score (0–100)'),
            height=280, margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig_score, use_container_width=True)

        # Vehicles per fetch cycle — stacked bar (sampled if too dense)
        sample_df = trend_df if len(trend_df) <= 150 else trend_df.iloc[::max(1, len(trend_df) // 150)]
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            x=sample_df['datetime'], y=sample_df['vehicles_inserted'],
            name='Inserted', marker_color='#2ecc71',
        ))
        fig_bar.add_trace(go.Bar(
            x=sample_df['datetime'], y=sample_df['vehicles_rejected'],
            name='Rejected', marker_color='#e74c3c',
        ))
        fig_bar.update_layout(
            title='Vehicles per Fetch Cycle',
            barmode='stack', height=260,
            yaxis=dict(title='Vehicles'),
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        # Average data lag
        fig_lag = go.Figure(go.Scatter(
            x=trend_df['datetime'], y=trend_df['avg_data_lag_seconds'],
            mode='lines', name='Avg Lag (s)',
            line=dict(color='#f39c12', width=2),
        ))
        fig_lag.update_layout(
            title='Average Data Lag (seconds)',
            height=230, yaxis=dict(title='Seconds'),
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig_lag, use_container_width=True)

    st.divider()

    # ── Section 4: Raw Fetch Log ─────────────────────────────────────────────
    st.markdown("### 📋 Raw Fetch Log")
    log_df = db.get_region_fetch_log(selected, limit=100)

    if log_df.empty:
        st.info("No fetch log entries for this region.")
    else:
        display_df = log_df[[
            'datetime', 'vehicles_received', 'vehicles_rejected', 'vehicles_inserted',
            'avg_data_lag_seconds', 'max_data_lag_seconds', 'total_dropout', 'fetch_duration_ms',
        ]].rename(columns={
            'datetime':              'Timestamp',
            'vehicles_received':     'Received',
            'vehicles_rejected':     'Rejected',
            'vehicles_inserted':     'Inserted',
            'avg_data_lag_seconds':  'Avg Lag (s)',
            'max_data_lag_seconds':  'Max Lag (s)',
            'total_dropout':         'Dropout',
            'fetch_duration_ms':     'Duration (ms)',
        })
        display_df['Avg Lag (s)'] = display_df['Avg Lag (s)'].round(1)
        display_df['Max Lag (s)'] = display_df['Max Lag (s)'].round(1)

        st.dataframe(display_df, use_container_width=True)

        csv = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="⬇️ Export CSV",
            data=csv,
            file_name=f"fetch_log_{selected.replace(' ', '_')}.csv",
            mime="text/csv",
            key="health_export_csv",
        )
```

- [ ] **Step 2: Run existing tests (no regressions)**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all 22 tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/app_pages/network_health.py
git commit -m "feat: add network_health page with scorecards, drill-down, and raw fetch log"
```

---

## Task 8: Update `app.py` navigation

**Files:**
- Modify: `src/app.py`

- [ ] **Step 1: Add session state key**

In `src/app.py`, find the session state initialization block and add:

```python
if 'health_selected_region' not in st.session_state:
    st.session_state.health_selected_region = None
```

- [ ] **Step 2: Add nav entry to the radio list**

Find this line:

```python
    page = st.radio(
        "Select View",
        ["🗺️ Live Map", "📊 Data Table", "📈 Analytics"],
        index=["🗺️ Live Map", "📊 Data Table", "📈 Analytics"].index(st.session_state.current_page),
```

Replace with:

```python
    _pages = ["🗺️ Live Map", "📊 Data Table", "📈 Analytics", "📡 Network Health"]
    page = st.radio(
        "Select View",
        _pages,
        index=_pages.index(st.session_state.current_page) if st.session_state.current_page in _pages else 0,
```

- [ ] **Step 3: Update the default current_page guard and routing**

Find:

```python
if 'current_page' not in st.session_state:
    st.session_state.current_page = "🗺️ Live Map"
```

No change needed there.

Find the routing block at the bottom of `app.py`:

```python
if st.session_state.current_page == "🗺️ Live Map":
    from app_pages import live_map
    live_map.show()
elif st.session_state.current_page == "📊 Data Table":
    from app_pages import data_table
    data_table.show()
else:
    from app_pages import analytics
    analytics.show()
```

Replace with:

```python
if st.session_state.current_page == "🗺️ Live Map":
    from app_pages import live_map
    live_map.show()
elif st.session_state.current_page == "📊 Data Table":
    from app_pages import data_table
    data_table.show()
elif st.session_state.current_page == "📈 Analytics":
    from app_pages import analytics
    analytics.show()
else:
    from app_pages import network_health
    network_health.show()
```

- [ ] **Step 4: Run all tests**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all 22 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/app.py
git commit -m "feat: add Network Health to sidebar navigation"
```

---

## Task 9: CHANGELOG.md, version bump, git tag

**Files:**
- Create: `CHANGELOG.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create `CHANGELOG.md` at repo root**

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - 2026-05-02

### Added
- 📡 Network Health page with per-region reliability scorecards, drill-down charts, and raw fetch log export
- `fetch_quality_log` DuckDB table tracking vehicles received/rejected/inserted, data lag, dropout status, and fetch duration per region per cycle
- Fetch guard preventing concurrent write collisions when multiple users trigger refresh simultaneously
- `_build_quality_stats` pure function for testable quality stat computation
- `get_network_health_summary`, `get_region_health_trend`, `get_region_fetch_log` DB read functions
- Reliability score formula: 40% reporting rate + 40% availability + 20% data freshness (0–100 scale)
- `fetch_quality_log` pruned with the same `DATA_RETENTION_DAYS` window as `live_buses`

### Changed
- `_fetch_endpoint` now returns `(vehicles, duration_ms)` tuple to support per-region timing

## [2.0.0] - 2026-04-03

### Fixed
- Accuracy circle now uses real GPS accuracy metres instead of hardcoded 30 px radius
- `get_historical_data` now queries a rolling 7-day window instead of the full table (prevents memory exhaustion on long-running instances)
- DuckDB connection leaks fixed with `finally` blocks across all DB functions
- `API_SOURCES` consolidated into `config.py` — `ingestion.py` imports from single source of truth
- `datetime.utcnow()` replaced with `datetime.now(timezone.utc)` (deprecated in Python 3.12+)
- Trail table speed converted to km/h for consistency with the rest of the UI
- `.DS_Store` untracked from git

### Added
- `DATA_RETENTION_DAYS` config constant (default: 7 days) for rolling data retention
- `PRIMARY_REGION` config constant replacing magic string in `data_processor.py`
- `prune_old_data()` function in `db.py`
- `requirements-dev.txt` with pytest; `tests/conftest.py` for sys.path setup
- Expanded test suite from 1 to 12 tests covering speed conversion, coordinate filtering, and region sorting

### Changed
- `pyproject.toml` now has complete dependency list and correct author email
```

- [ ] **Step 2: Bump version in `pyproject.toml`**

Find:

```toml
version = "2.0.0"
```

Replace with:

```toml
version = "2.1.0"
```

- [ ] **Step 3: Run all tests one final time**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all 22 tests pass.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md pyproject.toml
git commit -m "chore: bump version to 2.1.0 and add CHANGELOG.md"
```

- [ ] **Step 5: Create annotated git tag**

```bash
git tag -a v2.1.0 -m "feat: Network Health page — data quality tracking per region"
```

Verify the tag was created:

```bash
git tag -l "v2.1.0"
```

Expected output: `v2.1.0`

> **Note:** Pushing the tag (`git push origin v2.1.0`) and merging are left to the repo owner.

---

## Self-Review Checklist

**Spec coverage:**
- ✅ `fetch_quality_log` schema → Task 4
- ✅ Fetch guard (first-run safe) → Task 3
- ✅ Per-region tracking (received, rejected, inserted, lag, duration) → Tasks 1, 2, 4
- ✅ `_write_quality_log` + prune → Task 4
- ✅ `get_network_health_summary` → Task 5
- ✅ `get_region_health_trend` + `get_region_fetch_log` → Task 6
- ✅ Summary bar → Task 7, Section 1
- ✅ Scorecards (4 columns, sparklines, selectbox) → Task 7, Section 2
- ✅ Drill-down (score chart, stacked bar, lag chart, window toggle) → Task 7, Section 3
- ✅ Raw fetch log + CSV export → Task 7, Section 4
- ✅ Thin-data notice → Task 7
- ✅ Nav entry + session state → Task 8
- ✅ CHANGELOG.md + version bump + git tag → Task 9

**Type consistency:**
- `_build_quality_stats` defined Task 2, called Task 4 — same signature ✅
- `_write_quality_log(stats_list, con)` defined + called Task 4 ✅
- `get_network_health_summary` / `get_region_health_trend` / `get_region_fetch_log` defined Tasks 5–6, called in Task 7 ✅
- `health_selected_region` session state key: set Task 8, read Task 7 ✅
