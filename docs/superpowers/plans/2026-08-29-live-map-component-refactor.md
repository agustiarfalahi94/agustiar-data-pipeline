# Live Map Component Refactor Implementation Plan

> **Goal:** Refactor the monolithic `live_map.py` into clean, modular subcomponents (`deck_layers.py`, `stop_panel.py`, `route_panel.py`) while maintaining 100% test compatibility and identical UI contracts.

---

## Tasks

### Task 1: Create `live_map_components/deck_layers.py`
**Files:**
- Create: `src/app_pages/live_map_components/deck_layers.py`

- [ ] Extract pydeck layer composition functions:
  - `build_vehicle_layer()`
  - `build_stop_rings_layer()`
  - `build_location_marker_layer()`
  - `build_route_shape_layer()`

---

### Task 2: Create `live_map_components/stop_panel.py` & `route_panel.py`
**Files:**
- Create: `src/app_pages/live_map_components/stop_panel.py`
- Create: `src/app_pages/live_map_components/route_panel.py`

- [ ] Extract stop arrivals board rendering into `stop_panel.py`.
- [ ] Extract Route Viewer details rendering into `route_panel.py`.

---

### Task 3: Refactor `live_map.py` Page Shell
**Files:**
- Modify: `src/app_pages/live_map.py`
- Create: `src/app_pages/live_map_components/__init__.py`

- [ ] Import modular layer and panel functions into `live_map.py`.
- [ ] Verify clean integration.

---

### Task 4: Testing, Verification & Versioning
**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `pyproject.toml`
- Test: `tests/test_script.py`, `tests/test_route_view.py`

- [ ] Run `.venv/bin/pytest` (404+ tests).
- [ ] Update `CHANGELOG.md` for version `2.20.0`.
- [ ] Bump `pyproject.toml` to `2.20.0`.
