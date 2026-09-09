# Spatial & Domain Optimization Implementation Plan

> **Goal:** Accelerate nearby stops lookups and route shape visualization via mtime-aware in-memory stop caching, fast bounding-box spatial filtering, and shapes indexing.

**Architecture:**
- `src/utils/gtfs_static.py`: Implement `_AGENCY_STOPS_INDEX` with bounding box pre-filter for `get_stops_near()`, and `_TRIP_SHAPES_INDEX` with mtime-aware invalidation for `get_shapes_for_trip()`.

---

## Tasks

### Task 1: Agency Stops Index & Bounding Box Spatial Filtering
- [ ] Add `_AGENCY_STOPS_INDEX` and `_AGENCY_STOPS_MTIME` dictionaries.
- [ ] Implement `_ensure_agency_stops(agency_slug)` with ZIP mtime checking.
- [ ] Add bounding box coordinate calculations in `get_stops_near()` to filter candidates prior to `haversine_m()`.
- [ ] Update `_clear_indexes()` in tests to include stops index.

### Task 2: Shapes Index & Caching
- [ ] Add `_TRIP_SHAPES_INDEX` and `_TRIP_SHAPES_MTIME` dictionaries.
- [ ] Update `get_shapes_for_trip()` to check and populate cache.
- [ ] Update `_clear_indexes()` in tests to include shapes index.

### Task 3: Unit Testing & Verification
- [ ] Add unit tests for spatial bounding box filtering in `tests/test_script.py` and `tests/test_eta.py`.
- [ ] Add unit tests for trip shapes caching.
- [ ] Run full test suite with `.venv/bin/pytest`.

### Task 4: Documentation & Release Bump
- [ ] Update `CHANGELOG.md` with version `2.18.0`.
- [ ] Update `README.md` if relevant.
- [ ] Bump `pyproject.toml` version to `2.18.0`.
