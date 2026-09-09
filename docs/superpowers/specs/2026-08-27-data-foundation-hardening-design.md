# Design: Data Layer & Concurrency Hardening, startDate Capture, and Platform Portability

**Date:** 2026-08-27  
**Target version:** 2.17.0 (schema extension + connection resilience + environment secrets → minor bump)  
**Status:** Approved design  

---

## 1. Problems

### 1.1 DuckDB Lock Contention on Background Auto-Refresh
When `background_fetch.py` executes `fetch_and_store_transit_data()` in a background worker thread (writing to `live_buses` and `fetch_quality_log`), any concurrent Streamlit UI session performing read queries (`get_live_data_optimized`, `get_table_page`, etc.) calls `duckdb.connect(DATABASE_NAME)`. Because DuckDB enforces exclusive access for write locks, concurrent connects without retry or read-only flags can raise `duckdb.IOException` or `duckdb.ConnectionException`, crashing page renders.

### 1.2 Split-Brain Database Paths
`DATABASE_NAME = 'agustiar_analytics.duckdb'` uses a relative path. Running ingestion or tests from `src/` versus the repo root creates disjoint `.duckdb` files in separate directories.

### 1.3 Midnight Transit Boundary Delay Calculation
`eta.py:service_day_epoch` derives the service-day midnight epoch solely from `vehicle_timestamp` because `startDate` was not captured during ingestion. A trip that started before midnight (e.g. 23:50) and runs past midnight (00:30) resolves against the new day's midnight, creating a spurious ~-1,400 min delay that suppresses lateness reporting.

### 1.4 Hardcoded `/tmp` and Secrets Resolution
`gtfs_static.py` hardcodes `/tmp/gtfs_static_*.zip`, which fails on non-Unix environments (Windows) and is shared across multi-user environments. `live_map.py` does not check `os.environ.get('ORS_API_KEY')`, hindering 12-factor container deployment.

---

## 2. Decisions

1. **Connection Resilience with `read_only` and Exponential Backoff**:
   - `db.get_connection(read_only=False)` supports `read_only=True` for read queries, avoiding write-lock acquisition.
   - Wrap connection creation with retry logic (up to 3 attempts with 50-150ms backoff and jitter) to handle transient lock contention during background writes.
2. **Absolute Path Resolution for Database**:
   - Resolve `DATABASE_NAME` relative to project repository root or config directory.
3. **Capture `startDate` & `startTime` in Ingestion & Schema**:
   - Extract `trip_info.get('startDate', '')` and `trip_info.get('startTime', '')` in `ingestion.py`.
   - Add additive schema migration (`ALTER TABLE live_buses ADD COLUMN start_date VARCHAR; ALTER TABLE live_buses ADD COLUMN start_time VARCHAR;`).
   - Pass `start_date` into `eta.service_day_epoch()`: if provided as `"YYYYMMDD"`, calculate service day epoch from that date and timezone offset, fixing the midnight boundary.
4. **Portable Temporary Directory**:
   - In `gtfs_static.py`, use `tempfile.gettempdir()` for caching downloaded static GTFS archives.
5. **Environment Variable Secrets Hierarchy**:
   - In `live_map.py`, resolve `ORS_API_KEY` checking: `os.environ.get('ORS_API_KEY')` $\rightarrow$ `config.py` $\rightarrow$ `st.secrets`.
6. **HTML Sanitization**:
   - Use `html.escape()` for user/dynamic inputs inside `st.markdown(..., unsafe_allow_html=True)`.

---

## 3. Testing Strategy

- Unit test DuckDB retry wrapper and read-only connections under simulated contention.
- Unit test `service_day_epoch` with `start_date` spanning midnight (e.g. timestamp at 00:30 with `start_date` of previous day).
- Unit test ingestion capturing `start_date` and `start_time`.
- Unit test GTFS static cache path using `tempfile.gettempdir()`.
- Unit test `_ors_api_key()` prioritizing `os.environ`.
- Ensure all 392+ existing tests continue to pass.
