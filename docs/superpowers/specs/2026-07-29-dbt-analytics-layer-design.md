# Design: dbt Analytics Layer

**Date:** 2026-07-29
**Target version:** 2.2.0 (new feature → minor bump)
**Status:** Approved design, pending implementation plan

---

## Problem

Transformation logic is scattered across three files, recomputed on every render, and
untestable in isolation:

- `src/utils/ingestion.py` — Python filtering (bad coords, stale timestamps) and quality-stat
  construction.
- `src/utils/db.py` — large f-string SQL; the **reliability-score formula is copy-pasted into
  two functions** (`get_network_health_summary` and `get_region_health_trend`), a real
  duplication hazard.
- `src/utils/data_processor.py` — pandas transforms (speed m/s→km/h, display formatting).

The `data/bronze|silver|gold` folders exist but are empty — a medallion structure was intended
but never realized.

There is no versioned, tested, documented transformation layer. This is the exact gap dbt fills.
Adding dbt also provides the single highest-signal "analytics engineering" keyword for the
data analyst / data engineer roles this portfolio targets.

## Non-goals

- **The live path is not touched.** The Live Map needs sub-minute freshness (last 60 seconds);
  routing it through a batch model would add staleness. It keeps its direct-query path.
- No new infrastructure. dbt runs against the existing DuckDB file via the `dbt-duckdb` adapter.
- No orchestration engine, no cloud warehouse — those are later-version items (see Roadmap).

---

## Architecture

dbt owns the **analytical** layer only. Live vehicle positions bypass it.

```
sources (bronze)          staging (silver, views)        marts (gold, views)
─────────────────         ───────────────────────        ───────────────────
live_buses          →     stg_vehicle_positions    →     mart_analytics_summary
fetch_quality_log   →     stg_fetch_quality        →     mart_network_health
                                                         mart_region_health_trend
```

- **Sources** are the two tables the existing pipeline already lands (`live_buses`,
  `fetch_quality_log`). dbt reads them as-is via `source()` — it does not create them.
- **Staging views** centralize cleaning currently split between `ingestion.py` and
  `data_processor.py`: type casts, speed m/s→km/h, coordinate validity, timestamp casting.
- **Mart views** replace the hand-written SQL in `db.py`.

### Materialization: views (decided)

All marts are DuckDB **views**, not materialized tables. Rationale:

- The dataset is bounded to a 7-day retention window — query cost is trivial.
- `dbt run` becomes a one-time metadata operation, so it never contends with Streamlit for
  DuckDB's single-writer lock (the contention problem already fought in v2.1.1).
- No staleness: a view always reflects current source rows.

Incremental/table materialization is documented as a future option for when data volume grows.

### The reliability-score macro

The score formula (`0.4 * reporting_rate + 0.4 * availability + 0.2 * freshness`, ×100) is
currently duplicated. It becomes a single dbt macro `reliability_score(...)` referenced by both
`mart_network_health` (aggregate) and `mart_region_health_trend` (per-row). Defined once, tested
once.

### Streamlit integration

`db.py` read functions are slimmed from embedded transformation SQL to thin reads:

- `get_network_health_summary()` → `SELECT * FROM main.mart_network_health WHERE ...`
- `get_region_health_trend()` → `SELECT * FROM main.mart_region_health_trend WHERE region = ?`
- Analytics-page rollups → `mart_analytics_summary`.

The live-path functions (`get_live_data_optimized`, `get_vehicle_trail`) are unchanged.

### Bootstrapping dbt on first run / Streamlit Cloud

On Streamlit Cloud the DuckDB file is built by the first fetch, so the mart views cannot exist
until the source tables exist. Approach:

- After the first successful `fetch_and_store_transit_data()` (source tables now present), invoke
  `dbt run` **programmatically once** to create the views. Guard so it runs only when the views
  are absent (cheap `information_schema` check), not on every fetch.
- Because marts are views, this is a fast metadata operation with no lock contention against
  concurrent reads.

Exact invocation mechanism (dbt Python entrypoint vs. subprocess) is an implementation-plan
detail.

---

## Data tests (portfolio headline)

Declared in schema YAML, enforced by `dbt test` / `dbt build`:

- **Keys:** `not_null` + `unique` on mart grain keys (e.g. region for the summary; region +
  fetch_timestamp for the trend).
- **`accepted_values`** on `region` — the 14 known transit regions.
- **Range tests:** `reliability_score` ∈ [0, 100]; `reporting_rate` ∈ [0, 1]; `speed_kmh` ≥ 0.
- **Source freshness:** on `fetch_quality_log.fetch_timestamp` — reinforces the existing Network
  Health data-quality theme.

`dbt_utils` is added as a package dependency for range/relationship test helpers.

---

## Documentation & CI

- **Lineage graph:** `dbt docs generate` → capture the DAG into `docs/screenshots/`; add a
  "Data Modeling" section to `README.md` describing the bronze/silver/gold layers and linking the
  graph.
- **GitHub Actions CI:** a workflow running `dbt build` (run + test against a seeded/ephemeral
  DuckDB) plus the existing `pytest` on every push. Enforces tests; a green badge is strong
  portfolio signal.
- Per `CLAUDE.md`: add a `CHANGELOG.md` entry under a new `[2.2.0]` section, update `README.md`,
  bump `pyproject.toml` to `2.2.0`.

---

## Directory layout (proposed)

```
transform/                     # dbt project root (name TBD in plan)
├── dbt_project.yml
├── profiles.yml               # dbt-duckdb, points at the existing .duckdb file
├── packages.yml               # dbt_utils
├── macros/
│   └── reliability_score.sql
├── models/
│   ├── staging/
│   │   ├── _sources.yml        # live_buses, fetch_quality_log + freshness
│   │   ├── stg_vehicle_positions.sql
│   │   └── stg_fetch_quality.sql
│   └── marts/
│       ├── _marts.yml          # tests + docs for the three marts
│       ├── mart_analytics_summary.sql
│       ├── mart_network_health.sql
│       └── mart_region_health_trend.sql
```

---

## Validation strategy

Before rewiring `db.py`, prove each mart reproduces the current query output:

- For a representative DB snapshot, diff `mart_network_health` output against the existing
  `get_network_health_summary` SQL result (same window). Scores must match to rounding.
- Same for `mart_region_health_trend` vs. the current per-row SQL.
- Only rewire `db.py` once parity holds. Existing `pytest` suite must stay green.

---

## Roadmap (later versions — out of scope for 2.2.0)

| Version | Enhancement | Rationale |
|---|---|---|
| 2.3 | **Apache Airflow** orchestration — ingestion + dbt as a scheduled DAG | Owner already lists Airflow on their CV but has no public artifact for it; this turns a résumé claim into shown evidence. Preferred over Dagster for that reason. |
| 2.3 | dbt `exposure` declaring the dashboard | Connects models → app in the lineage graph. |
| 3.0 | **dbt-bigquery adapter swap** — run the same models on BigQuery | Owner has heavy BigQuery + migration experience on their CV; a dbt-on-BigQuery artifact converts that into demonstrated dbt-warehouse work and showcases dbt portability. (MotherDuck is the lighter-weight alternative.) |
| 3.x | Trip/route performance marts (headway, dwell, route reliability) | Deeper analyst-flavored modeling. |
| 3.x | dbt Semantic Layer / MetricFlow | Define metrics once; owner has semantic-layer exposure (Holistics/Domo) but no dbt equivalent — closes that gap. |

---

## CV-informed emphasis (owner: Muhamad Agustiar Falahi)

The CV brands as "BI Developer | Analytics Engineer | Data Analyst" and validates this plan
strongly. dbt appears **nowhere** on it despite the "Analytics Engineer" title — so dbt is the
single most glaring résumé gap this project can close. Specific tuning applied:

- **Dimensional modeling:** the CV claims "Star Schema, Dimensional Modeling" but this project is
  currently flat. Frame the marts with explicit dimensional language (fact/dimension separation
  where it fits) so the README demonstrates a skill currently only asserted.
- **Testing as a framework:** the CV repeatedly describes *manual* source-vs-output validation
  ("validated all migrated output against source data"). Position `dbt test` as automating exactly
  that instinct — a natural, credible narrative.
- **CI on GitHub:** the CV shows GitLab CI/CD but this GitHub portfolio repo has none. GitHub
  Actions closes that and matches the CV's listed GitHub skill.
- **README "Data Modeling" section** should serve both target roles: tested + documented pipeline
  (engineer) and clear analytical marts/metrics (analyst). No single-role bias needed — the CV is
  strong on both; dbt is the shared gap.

## Version scheme (resolved)

No discrepancy. The saved `v0.x.x` versioning memory belongs to a *different* project
(**Random Recall**, the owner's mobile app). This transit repo uses standard semver; `pyproject.toml`
at `2.1.2` is correct, and this feature bumps to `2.2.0` per the `CLAUDE.md` rule. No memory change
needed.
