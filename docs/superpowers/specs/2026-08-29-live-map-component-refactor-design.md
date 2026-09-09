# Live Map Component Refactor & Modularization Design Specification

## Overview & Goals
`src/app_pages/live_map.py` is currently a monolith (~1,955 lines) containing map rendering, pydeck layer generation, nearby stop arrival boards, locate-me region switching, route search filtering, and route viewer panels.

This refactor modularizes `live_map.py` into focused sub-modules under `src/app_pages/live_map_components/`:
1. `deck_layers.py`: PyDeck layer builders (vehicle arrows, gold/magenta stop rings, red GPS marker, route polylines).
2. `stop_panel.py`: Nearby stops, walk-time calculations, and stop arrival sequence panels.
3. `route_panel.py`: Route Viewer, bus tracking details, and shape/breadcrumb views.

`src/app_pages/live_map.py` remains the top-level page coordinator, importing functions from `live_map_components`.

---

## 1. Modular Structure

```
src/app_pages/
├── live_map.py                      # Main page controller & Streamlit layout
└── live_map_components/
    ├── __init__.py
    ├── deck_layers.py               # PyDeck layer composition
    ├── stop_panel.py                # Nearby stops & arrival board rendering
    └── route_panel.py               # Route Viewer & vehicle trail panel
```

---

## 2. Invariants & Backward Compatibility
- **API Contracts**: All function signatures, `st.session_state` keys, and widget key names must remain 100% identical.
- **Behavior**: Zero change to user-facing behavior, map rendering, locate-me switching, or route search logic.

---

## 3. Testing Strategy
- Existing test suite (`tests/test_script.py`, `tests/test_route_view.py`, `tests/test_walking.py`, `tests/test_eta.py`) must pass 100% without modification.
- Add unit tests for `deck_layers.py`, `stop_panel.py`, and `route_panel.py`.
- Run `.venv/bin/pytest`.
