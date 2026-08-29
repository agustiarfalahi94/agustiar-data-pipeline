# Service Disruption Alerts & Multi-Agency Stop Clustering Design Specification

## Overview & Goals
1. **GTFS-RT Service Disruption Alerts**:
   - Ingest GTFS-RT `Alert` entities from feed messages (`entity.HasField('alert')`).
   - Parse cause, effect, header text, description, active period, and informed entities (`route_id`, `stop_id`).
   - Persist into DuckDB table `service_alerts` with schema migration and retention pruning.
   - Expose helper functions in `src/utils/db.py` to fetch active alerts for UI display in Live Map and Network Health.

2. **Multi-Agency Stop Hub Clustering**:
   - Group stops across agencies that fall within a 50-meter cluster radius into transit hubs.
   - Aggregate route connections across agencies for clustered hubs.
   - Expose `get_clustered_stops_near()` in `src/utils/gtfs_static.py`.

---

## 1. Schema & Ingestion

### DuckDB Table: `service_alerts`
```sql
CREATE TABLE IF NOT EXISTS service_alerts (
    alert_id VARCHAR,
    region VARCHAR,
    cause VARCHAR,
    effect VARCHAR,
    header_text VARCHAR,
    description_text VARCHAR,
    active_period_start BIGINT,
    active_period_end BIGINT,
    route_id VARCHAR,
    stop_id VARCHAR,
    fetched_at BIGINT
);
```

### Alert Parsing Logic (`src/utils/ingestion.py`)
- When `entity.HasField('alert')`, extract:
  - `alert_id`: `entity.id`
  - `cause`, `effect`: converted string names or fallback
  - `header_text`: `alert.header_text.translation[0].text`
  - `description_text`: `alert.description_text.translation[0].text`
  - `informed_entity`: extract `route_id` and `stop_id`

---

## 2. Multi-Agency Stop Hub Clustering (`src/utils/gtfs_static.py`)

### `get_clustered_stops_near(lat, lon, radius_m=800, cluster_distance_m=50)`
- Collects stops within `radius_m` across requested agencies using `get_stops_near()`.
- Groups stops whose mutual distance is $\le 50\text{ m}$ into unified hub clusters.
- Returns cluster dicts with combined agency lists, stop names, coordinates, and total unique routes.

---

## 3. Testing Strategy
- Unit tests for GTFS-RT alert parsing from FeedMessage protobuf.
- Unit tests for DuckDB `service_alerts` queries and retention pruning.
- Unit tests for stop clustering algorithm across multiple fake feeds.
- Full test suite run (`pytest`).
