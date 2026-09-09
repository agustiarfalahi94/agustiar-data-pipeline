# Service Disruption Alerts & Multi-Agency Stop Clustering Implementation Plan

> **Goal:** Ingest GTFS-RT service disruption alerts into DuckDB, surface disruption warnings in Network Health and Live Map, and implement multi-agency stop clustering for transit hub interconnections.

---

## Tasks

### Task 1: GTFS-RT Alerts Ingestion & Database Schema
**Files:**
- Modify: `src/utils/ingestion.py`, `src/utils/db.py`
- Test: `tests/test_script.py`

- [ ] **Step 1**: Update `_fetch_endpoint()` in `src/utils/ingestion.py` to extract `entity.alert` records alongside `entity.vehicle`.
- [ ] **Step 2**: Add `_write_service_alerts()` in `src/utils/ingestion.py` to persist alerts into DuckDB table `service_alerts`.
- [ ] **Step 3**: Add `get_active_service_alerts(region=None, route_id=None)` helper query in `src/utils/db.py`.
- [ ] **Step 4**: Write unit tests for alert parsing, storage, and retrieval.

---

### Task 2: Multi-Agency Stop Hub Clustering
**Files:**
- Modify: `src/utils/gtfs_static.py`
- Test: `tests/test_eta.py`

- [ ] **Step 1**: Implement `get_clustered_stops_near(lat, lon, radius_m=800, cluster_distance_m=50)` in `src/utils/gtfs_static.py`.
- [ ] **Step 2**: Combine stop attributes (agencies, routes, stop_names) into unified hub structures.
- [ ] **Step 3**: Write unit tests verifying hub clustering algorithm across multiple agencies.

---

### Task 3: UI Integration for Alerts & Hubs
**Files:**
- Modify: `src/app_pages/network_health.py`, `src/app_pages/live_map.py`

- [ ] **Step 1**: Surface active service alert badges in Network Health scorecards and Live Map headers.
- [ ] **Step 2**: Run pytest test suite to verify 100% green tests.

---

### Task 4: Documentation & Release Bump
**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `pyproject.toml`

- [ ] **Step 1**: Update `CHANGELOG.md` for version `2.19.0`.
- [ ] **Step 2**: Update `README.md` features.
- [ ] **Step 3**: Bump `pyproject.toml` version to `2.18.1` / `2.19.0`.
