# Data Foundation & Concurrency Hardening Implementation Plan

> **Goal:** Eliminate DuckDB lock collisions on background auto-refresh, prevent split-brain database files by standardizing absolute path resolution, fix the midnight transit boundary ETA delay calculation via `startDate` ingestion, make static GTFS caching portable across operating systems, and support standard environment variable secrets.

**Architecture:**
1. Database layer (`src/utils/db.py`): Add `read_only` connection support, retry policy with exponential backoff on lock contention, and absolute DB path resolution.
2. Ingestion layer (`src/utils/ingestion.py`): Capture `startDate` and `startTime` from GTFS-RT `TripDescriptor`, migrate database schema additively.
3. ETA domain logic (`src/utils/eta.py`): Calculate exact service-day epoch when `start_date` is available, fixing the midnight boundary issue.
4. Portability & Secrets (`src/utils/gtfs_static.py`, `src/app_pages/live_map.py`, `src/app_pages/network_health.py`): Use `tempfile.gettempdir()`, check `os.environ` for `ORS_API_KEY`, and escape dynamic HTML values.

**Tech Stack:** Python 3, DuckDB, Pandas, GTFS-Realtime Protobuf, Requests, Streamlit, Pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-data-foundation-hardening-design.md`

---

## Tasks

### Task 1: Database Path Resolution and Concurrency Resilience with Retries

**Files:**
- Modify: `src/config.py`, `src/config.example.py`, `src/utils/db.py`
- Test: `tests/test_script.py`

- [ ] **Step 1: Write unit tests for DuckDB connection retry & read_only connection helper**
- [ ] **Step 2: Implement absolute path resolver for `DATABASE_NAME`**
- [ ] **Step 3: Implement `get_connection(read_only=False)` with retry logic in `src/utils/db.py`**
- [ ] **Step 4: Update read queries in `src/utils/db.py` to use `read_only=True`**
- [ ] **Step 5: Run tests and verify they pass**

---

### Task 2: Ingest `startDate` and `startTime` into Schema & Fix Midnight Delay in `eta.py`

**Files:**
- Modify: `src/utils/ingestion.py`, `src/utils/eta.py`
- Test: `tests/test_eta.py`, `tests/test_script.py`

- [ ] **Step 1: Write unit tests for `startDate`-based `service_day_epoch` spanning midnight**
- [ ] **Step 2: Update `_fetch_endpoint` in `src/utils/ingestion.py` to capture `start_date` and `start_time`**
- [ ] **Step 3: Add schema migration in `fetch_and_store_transit_data` for `start_date` and `start_time`**
- [ ] **Step 4: Update `eta.service_day_epoch()` to parse `start_date` ("YYYYMMDD") and pass through in `arrivals_for_stops`**
- [ ] **Step 5: Run tests and verify they pass**

---

### Task 3: Secrets Hierarchy, Temporary Cache Directory & HTML Sanitization

**Files:**
- Modify: `src/app_pages/live_map.py`, `src/utils/gtfs_static.py`, `src/app_pages/network_health.py`
- Test: `tests/test_script.py`

- [ ] **Step 1: Update `gtfs_static.get_cached_path` to use `tempfile.gettempdir()`**
- [ ] **Step 2: Update `live_map._ors_api_key()` to check `os.environ.get('ORS_API_KEY')`**
- [ ] **Step 3: Add `html.escape()` for dynamic values in `network_health.py`**
- [ ] **Step 4: Run tests to verify all pass**

---

### Task 4: Documentation, Changelog & Version Bump

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `pyproject.toml`
- Verify: `requirements.txt`, `requirements-dev.txt`

- [ ] **Step 1: Update `CHANGELOG.md` for version 2.17.0**
- [ ] **Step 2: Update `README.md`**
- [ ] **Step 3: Bump `pyproject.toml` version to 2.17.0**
- [ ] **Step 4: Run the full test suite to verify 100% green tests**
