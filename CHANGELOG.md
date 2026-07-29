# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.1] - 2026-07-30

### Changed
- Roadmap: replaced the planned **Route Planner** with **search by route name**. Origin→destination
  journey planning duplicates Google Maps without improving on it; the unmet need is the inverse —
  when you already know your route, see where those specific vehicles are right now.
- Roadmap: marked the dbt analytics layer complete.

### Documentation
- Recorded that live vehicle positions for LRT/MRT/Monorail are **not available**: the
  `prasarana?category=rapid-rail-kl` vehicle-position feed returns a 404 (*"feed does not exist"*),
  and the API currently publishes vehicle positions only — trip updates and service alerts are on
  the provider's 2026 roadmap. Rail **static** data (8 lines, stops, shapes, frequencies) is
  available, and KTM Berhad realtime is already ingested.

## [2.2.0] - 2026-07-29

### Added
- **dbt (dbt-duckdb) analytics layer** under `transform/` — bronze sources (`live_buses`, `fetch_quality_log`), silver staging views, and three gold mart views (`mart_network_health`, `mart_region_health_trend`, `mart_region_vehicle_counts`)
- `reliability_score` dbt macro — single definition of the 0–100 score formula, replacing the copy in two `db.py` functions
- dbt data tests: region `accepted_values` (the 14 canonical regions), 0–100 score and 0–1 rate range checks, key `not_null`/`unique`, and a composite-uniqueness test pinning `mart_region_health_trend` to its documented `region + fetch_timestamp` grain
- Local generic test macros `accepted_range` and `unique_combination_of_columns` in `transform/macros/` — the project has **no dbt package dependencies**, so `dbt run` works on a fresh clone and on Streamlit Cloud without a `dbt deps` step
- Model, source, and column `description:` metadata throughout, so `dbt docs generate` renders a documented project
- GitHub Actions CI running `dbt build --target ci` (seed → run → test) and pytest on every push/PR, plus a separate informational `dbt source freshness` step (`dbt build` does not run freshness; the committed fixtures carry fixed, old timestamps, so the step is `continue-on-error`)
- `dbt_runner.ensure_dbt_models` — creates the mart views once after the first ingestion (bootstraps Streamlit Cloud). It requires **all three** marts before skipping, backs off after two consecutive failed attempts and automatically retries once a 10-minute cooldown elapses (so a doomed bootstrap cannot stall every ~20s auto-refresh, but a transient failure — e.g. a DuckDB write-lock collision or the 120s subprocess timeout — cannot lock analytics out for the life of a long-running Streamlit Cloud process either), and invokes dbt via `sys.executable -m dbt.cli.main` rather than a bare `dbt` on `PATH`
- Model lineage diagram (Mermaid) in the README's new "Data Modeling (dbt)" section
- Regression test (`tests/test_dbt_marts.py`) asserting `dbt ls --target dev --resource-type seed` returns no seed nodes while `--target ci` does — guarding the seed-disabled-on-`dev` data-loss fix with an explicit check

### Changed
- Network Health reads (`get_network_health_summary`, `get_region_health_trend`) and the Analytics region charts now query dbt marts instead of inline SQL; the live-map path is unchanged
- `get_network_health_summary()` no longer takes a `window_hours` argument — the mart bakes in the 24h window, so the parameter was ignored — and now orders explicitly by `reliability_score DESC` instead of relying on a view's inner `ORDER BY`
- dbt seeds are enabled only on the `ci` target. They are CI fixtures named identically to the real app tables, so an unqualified `dbt build` against a live database would have truncated ingested history; the documented local command is now `dbt run` / `dbt test`
- The `ci` target now resolves its DuckDB path from its own `DBT_CI_DUCKDB_PATH` env var (default `ci.duckdb`), never `DBT_DUCKDB_PATH` — closing a gap where a shell that had exported `DBT_DUCKDB_PATH` to the real app database would have had `dbt build --target ci` truncate it, since seeds are enabled on that target
- Minimum Python raised to **3.9** (`pyproject.toml`, README badge) — dbt-core does not support 3.8; CI runs 3.11
- Analytics page now shows a refresh hint instead of a blank bar and pie when regional vehicle counts are unavailable

### Fixed
- Corrected two stale entries in the README's "Key Design Decisions" table that had drifted since 2.1.1: the fetch guard is a **3s** window (not 15s), and the `@st.cache_data(ttl=60)` row was removed entirely — that caching was dropped in 2.1.1 and `st.cache_data` is no longer used anywhere in `src/`
- Corrected the matching stale "last 15 seconds" comment on the fetch guard in `src/utils/ingestion.py` (the guard queries a 3-second window)

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
