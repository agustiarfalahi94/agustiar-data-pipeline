# Network Health Page — Design Spec
**Date:** 2026-05-02
**Status:** Approved, ready for implementation

---

## Background

The Malaysia Real-Time Transit Tracker currently displays live vehicle positions, historical data, and analytics. All data comes from the Malaysia government's GTFS Realtime API (`api.data.gov.my`), which is known to be inconsistent — vehicles drop out mid-journey, coordinates are occasionally invalid, and reporting frequency varies by region and operator.

Most apps that consume government APIs silently pass this unreliability through to users. This feature inverts that: it tracks data quality at the source and surfaces it as a first-class insight. For transit enthusiasts and researchers who rely on this app, knowing *how much to trust* the data is as valuable as the data itself.

---

## Target Audience

- **Transit enthusiasts** who monitor the network regularly and want to know which regions are reporting reliably
- **Students and researchers** analysing transit patterns who need to understand data quality before drawing conclusions

---

## Goal

Add a **Network Health** page that gives every user an honest, real-time view of how reliably each transit region is reporting data — including rejection rates, dropout frequency, data lag, and a composite reliability score — backed by a persistent quality log written during every fetch cycle.

---

## Non-Goals

- Per-stop or per-route reliability (vehicle-level only)
- Alerting or push notifications when a region degrades
- Predictive gap-filling or dead reckoning (future enhancement)
- Statistical baselining against historical norms (future enhancement on top of this)

---

## Data Model

### New table: `fetch_quality_log`

Created on first run via `CREATE TABLE IF NOT EXISTS`, managed by `ingestion.py`.

| Column | Type | Description |
|---|---|---|
| `fetch_timestamp` | BIGINT | Unix time when the fetch cycle ran |
| `region` | VARCHAR | Transit region name |
| `vehicles_received` | INTEGER | Raw vehicle count from API before any filtering |
| `vehicles_rejected` | INTEGER | Filtered out (bad coordinates or stale/future timestamps) |
| `vehicles_inserted` | INTEGER | Actually written to `live_buses` after dedup |
| `avg_data_lag_seconds` | DOUBLE | Average of `(insert_timestamp − vehicle_timestamp)` for this region |
| `max_data_lag_seconds` | DOUBLE | Worst lag observed in this fetch for this region |
| `total_dropout` | BOOLEAN | True if API returned zero vehicles for this region |
| `fetch_duration_ms` | INTEGER | Wall-clock time for this region's HTTP fetch |

One row per region per fetch cycle. At 20s auto-refresh with 14 regions, this generates ~2,520 rows/hour — well within DuckDB's capacity.

### Reliability Score Formula

Calculated over a configurable time window (default: last 24h) per region:

```
reporting_rate  = avg(vehicles_inserted / vehicles_received)   [skip rows where received = 0]
availability    = 1 - (dropout_count / total_fetches)
freshness_score = max(0, 1 - avg(avg_data_lag_seconds) / 300)  [300s = full penalty]

reliability_score = round((0.4 * reporting_rate + 0.4 * availability + 0.2 * freshness_score) * 100)
```

Score bands:
- **Green** ≥ 80 — reliable
- **Yellow** 50–79 — degraded
- **Red** < 50 — unreliable

---

## Ingestion Changes (`ingestion.py`)

### 1. Fetch guard (concurrent write protection)

At the start of `fetch_and_store_transit_data`, attempt to query `fetch_quality_log` for any row with `fetch_timestamp >= now - 15`. If found, skip and return early. If the table doesn't exist yet (first ever run), treat it as "no recent fetch" and proceed normally — the table will be created at the end of the run. The guard is wrapped in a `try/except` so a missing table never blocks the first fetch.

### 2. Per-region quality tracking

Capture the following at each stage of the existing pipeline:

- **Before filtering:** group `all_vehicle_data` by region → `vehicles_received` per region; flag `total_dropout = True` where count is 0
- **After filtering:** group filtered DataFrame by region → derive `vehicles_rejected = received - valid_after_filter`
- **Data lag:** compute `insert_timestamp − vehicle_timestamp` per row before inserting, group by region for `avg_data_lag_seconds` and `max_data_lag_seconds`
- **After insert:** query `SELECT region, COUNT(*) FROM live_buses WHERE insert_timestamp = {current_unix} GROUP BY region` → `vehicles_inserted` per region
- **Fetch duration:** record `time.time()` before and after each `_fetch_endpoint` call; `_fetch_endpoint` returns `(vehicles, duration_ms)` instead of just `vehicles`

### 3. Write quality log

After the main insert completes, build one row per region from the collected stats and insert into `fetch_quality_log`. Uses the same `CREATE TABLE IF NOT EXISTS` pattern as `live_buses`. Wrapped in its own `try/finally` so a log write failure never blocks the main data insert.

---

## New DB Functions (`db.py`)

Three new read-only functions added to `db.py`:

### `get_network_health_summary(window_hours=24)`
Returns one row per region with: `reliability_score`, `reporting_rate`, `availability`, `avg_data_lag_seconds`, `dropout_count`, `total_fetches`, `last_fetch_timestamp`. Used by scorecards and summary bar.

### `get_region_health_trend(region, window_hours=24)`
Returns time-series rows from `fetch_quality_log` for one region within the window. Used by the drill-down trend charts.

### `get_region_fetch_log(region, limit=100)`
Returns the most recent raw `fetch_quality_log` rows for one region. Used by the raw fetch log table.

---

## Network Health Page (`app_pages/network_health.py`)

New file. Registered in `app.py` navigation as **📡 Network Health** between Analytics and the end of the nav list.

### Section 1 — Network Summary Bar

A single metric row at the top showing:
- Total regions tracked
- Regions healthy (score ≥ 80) / degraded (50–79) / unreliable (< 50)
- Timestamp of the most recent fetch

Gives an immediate network-wide answer without scrolling.

### Section 2 — Region Scorecards

14 cards in a grid of 4 `st.columns`, rendered via `st.container` with markdown. Each card shows:
- Region name
- Reliability score as a large number with colour band
- Reporting rate %, dropout count, avg lag (seconds)
- A small Plotly sparkline showing score over the last 24h

A `st.selectbox` below the grid labelled "Select region to inspect" drives `st.session_state.health_selected_region`. Cards are visual-only; the selectbox is the interaction point for the drill-down.

### Section 3 — Region Drill-Down

Visible when a region is selected. Contains:
- **Line chart:** reliability score over time — time window toggle: 1h / 6h / 24h / 7d
- **Stacked bar chart:** vehicles received / rejected / inserted per fetch cycle (sampled to keep chart readable at the selected window)
- **Avg lag trend:** secondary line chart showing data lag over the same window

### Section 4 — Raw Fetch Log

Paginated `st.dataframe` of recent `fetch_quality_log` rows for the selected region. Columns: timestamp, received, rejected, inserted, avg lag, max lag, dropout, duration. CSV export button, consistent with the Data Table page.

### Thin-data notice

If `fetch_quality_log` has fewer than 10 rows for any region (or doesn't exist yet), display an info banner:
> "Reliability scores improve with more data. Enable auto-refresh to build history."

Scorecards still render with whatever data is available; scores are marked as provisional.

---

## Navigation Change (`app.py`)

Add `"📡 Network Health"` to the page radio list. Route to `network_health.show()`. Add `health_selected_region` to session state initialisation (default `None`).

---

## File Changes Summary

| File | Change |
|---|---|
| `src/utils/ingestion.py` | Fetch guard, per-region quality tracking, `fetch_quality_log` write |
| `src/utils/db.py` | Three new read functions for health data |
| `src/app_pages/network_health.py` | New page (4 sections) |
| `src/app.py` | Add nav entry, session state key |

No changes to `live_map.py`, `data_table.py`, `analytics.py`, or `config.py`.

---

## Open Questions (resolved)

- **Shared database on Streamlit Cloud?** Yes — all users share the same DuckDB file. One user's auto-refresh benefits everyone. The fetch guard prevents concurrent write collisions.
- **Auto-refresh required?** Not strictly — any manual refresh populates the log. The thin-data notice handles the cold-start case.
- **Retention policy for `fetch_quality_log`?** Same `DATA_RETENTION_DAYS` window as `live_buses`. Add a `DELETE` prune in the same ingestion step.
