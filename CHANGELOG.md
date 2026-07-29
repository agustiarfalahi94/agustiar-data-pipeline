# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.0] - 2026-07-29

### Added
- **dbt (dbt-duckdb) analytics layer** under `transform/` — bronze sources (`live_buses`, `fetch_quality_log`), silver staging views, and three gold mart views (`mart_network_health`, `mart_region_health_trend`, `mart_region_vehicle_counts`)
- `reliability_score` dbt macro — single definition of the 0–100 score formula, replacing the copy in two `db.py` functions
- dbt data tests: region `accepted_values`, score/rate range checks, key `not_null`/`unique`, plus `fetch_quality_log` source freshness
- GitHub Actions CI running `dbt build` (seed → run → test) and pytest on every push/PR
- `dbt_runner.ensure_dbt_models` — creates the mart views once after the first ingestion (bootstraps Streamlit Cloud)
- Model lineage diagram (Mermaid) in the README's new "Data Modeling (dbt)" section

### Changed
- Network Health reads (`get_network_health_summary`, `get_region_health_trend`) and the Analytics region charts now query dbt marts instead of inline SQL; the live-map path is unchanged

## [2.1.2] - 2026-05-14

### Added
- Map tooltip now shows human-readable route name (e.g. "T580 — Terminal Bersepadu Selatan – Klang") resolved from GTFS Static `routes.txt`, falling back to raw `route_id` if no name is found

## [2.1.1] - 2026-05-04

### Fixed
- `_write_quality_log` now uses a dedicated DuckDB connection opened after the main connection is fully closed, preventing write lock conflicts
- Parameterised `INSERT` in `_write_quality_log` replaces f-string construction, fixing a silent write failure caused by type coercion
- Fetch guard window tightened from 15 s to 3 s to reduce unnecessary skip rate while still preventing concurrent collisions
- `inserted_by_region` count now uses a `SELECT COUNT … WHERE insert_timestamp = …` query instead of the SQLite-only `changes()` call, which returned 0 on DuckDB
- Network Health page now follows the same refresh pattern as all other pages (no `st.cache_data`, manual or auto-refresh triggers a fetch)
- All `st.plotly_chart` calls given unique `key=` arguments to prevent duplicate element ID errors on re-render
- Removed debug instrumentation (`DIAGNOSTICS` dict and in-page debug panel) added during diagnosis

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
