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
