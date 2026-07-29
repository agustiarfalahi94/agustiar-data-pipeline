# 🚇 Malaysia Real-Time Transit Tracker

A web dashboard for tracking live bus and rail positions across Malaysia with real-time updates, interactive maps, route visualisation, and comprehensive analytics.

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.28%2B-FF4B4B.svg)](https://streamlit.io/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/agustiarfalahi94/agustiar-data-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/agustiarfalahi94/agustiar-data-pipeline/actions/workflows/ci.yml)

**🚀 [Live Demo](https://malaysia-realtime-transit-tracker.streamlit.app/)**

![Dashboard Preview](docs/screenshots/dashboard1.png)

---

## ✨ Features

### 🗺️ Live Map
- **Real-time vehicle tracking** across 14 transit regions in Malaysia
- **Directional arrows** showing each vehicle's heading
- **Hover tooltips** — vehicle ID, speed (km/h), and bearing
- **📍 Locate Me** — centres the map on your current GPS location with a red marker
- **🚌 Route Viewer** — select any vehicle to see its planned route (from GTFS Static) or historical breadcrumb trail as a fallback
- **Dark/Light map themes**

### 📊 Data Table
- **Multi-region filtering** with sortable, filterable table
- **CSV export** for offline analysis
- **Audit timestamp** — `created_at` column showing when each record was first ingested
- Auto-refresh compatible

### 📈 Analytics
- **Buses by Region** — bar chart of unique vehicle counts
- **Speed Distribution** — histogram of average speeds per vehicle
- **Regional Distribution** — pie chart
- **Speed Analysis by Region** — box plot comparing regions
- **Summary Statistics** — total vehicles, moving vehicles, max/min/avg/median speed

### 📡 Network Health
- **Per-region reliability scorecards** — composite score (0–100) for each of the 14 transit regions
- **Score breakdown** — reporting rate, dropout count, average data lag
- **24h sparklines** — at-a-glance trend per region
- **Region drill-down** — reliability score over time, vehicles received vs. rejected per cycle, data lag trend
- **Raw Fetch Log** — every API fetch event with full quality metadata, CSV export

### ⚙️ Settings & Controls
- **Manual or Auto refresh** (20-second interval)
- **Independent map theme** toggle (separate from the page theme)

---

## 📁 Project Structure

```
agustiar-data-pipeline/
│
├── src/
│   ├── app.py                    # Entry point — Streamlit app shell, navigation, session state
│   ├── config.py                 # Local config (not in git — copy from config.example.py)
│   ├── config.example.py         # Configuration template
│   │
│   ├── app_pages/
│   │   ├── live_map.py           # Live map, Locate Me, Route Viewer
│   │   ├── data_table.py         # Historical data table with CSV export
│   │   ├── analytics.py          # Plotly charts and summary statistics
│   │   └── network_health.py     # Data quality scorecards, drill-down, fetch log
│   │
│   └── utils/
│       ├── ingestion.py          # Parallel GTFS Realtime fetch → DuckDB
│       ├── db.py                 # DuckDB queries and schema migration
│       ├── data_processor.py     # Speed conversion, filtering, display formatting
│       └── gtfs_static.py        # GTFS Static ZIP download, caching, shape/route lookup
│
├── transform/                    # dbt-duckdb project (transformation layer scaffold)
│   ├── dbt_project.yml
│   ├── profiles.yml
│   └── packages.yml
│
├── tests/
├── docs/
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- pip

### Installation

```bash
# 1. Clone
git clone https://github.com/agustiarfalahi94/agustiar-data-pipeline.git
cd agustiar-data-pipeline

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up config
cp config.example.py src/config.py
# Edit src/config.py if you want to customise settings

# 5. Run
cd src
streamlit run app.py
```

Open `http://localhost:8501`, then click **Refresh Data** to fetch live transit data.

---

## ⚙️ Configuration

`config.py` (local dev) or Streamlit Secrets (cloud deployment):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_NAME` | `agustiar_analytics.duckdb` | DuckDB filename |
| `DATABASE_TABLE` | `live_buses` | Table name |
| `TIMEZONE` | `Asia/Kuala_Lumpur` | Display timezone |
| `UTC_OFFSET_HOURS` | `8` | UTC offset |
| `DEFAULT_ZOOM` | `13` | Default map zoom level |
| `ARROW_SIZE` | `0.001` | Vehicle arrow size multiplier |
| `DATA_MAX_AGE` | `3600` | Max record age accepted (seconds) |
| `DATA_FUTURE_TOLERANCE` | `300` | Max future timestamp tolerance (seconds) |

### Streamlit Cloud Secrets (TOML)

```toml
[database]
name = "agustiar_analytics.duckdb"
table = "live_buses"

[timezone]
name = "Asia/Kuala_Lumpur"
utc_offset_hours = 8

[map]
default_zoom = 13

[arrow]
size = 0.001

[regions]
list = ["Rapid Bus KL", "KTM Berhad"]
```

---

## 🔧 Technical Details

### Data Pipeline

```
GTFS Realtime API
       │
       ▼ (parallel fetch — ThreadPoolExecutor)
 _fetch_endpoint() × 15 endpoints simultaneously
       │
       ▼
 Validate & filter (bad coords, stale timestamps)
       │
       ▼
 Deduplicate (SQL-level, no re-inserts)
       │
       ▼
     DuckDB
       │                    │
  ┌────┴──────────┬──────────┴──────┬──────────────┐
  ▼               ▼                 ▼               ▼
Live Map      Data Table        Analytics     Network Health
(last 60s)   (7-day history)  (7-day history) (fetch_quality_log)
```

### Key Design Decisions

| Decision | Reason |
|---|---|
| **Parallel fetch with ThreadPoolExecutor** | Cuts refresh time from ~15s to ~2-3s |
| **DuckDB (local)** | Zero-cost, fast columnar queries, no server needed |
| **Append-only inserts** | Transit positions are facts — never updated, only added |
| **`created_at` audit timestamp** | Tracks when each record entered the system |
| **Hardcoded region dropdown** | Prevents dropdown re-ordering during auto-refresh |
| **GTFS Static 24h cache** | Static schedules change daily at most — avoids hammering the API |
| **`streamlit-js-eval` for geolocation** | `components.html()` is one-way only; `streamlit-js-eval` provides the two-way JS bridge needed to return browser GPS coordinates to Python |
| **`fetch_quality_log` table** | Records per-region API quality stats at every fetch — received, rejected, inserted, lag, dropout. Powers the Network Health page without touching `live_buses` |
| **Fetch guard (15s window)** | DuckDB only supports one writer at a time; the guard prevents concurrent write collisions when multiple users trigger refresh simultaneously |
| **`@st.cache_data(ttl=60)` on health queries** | Network Health loads 14+ DB queries for sparklines — caching cuts this to one round-trip per minute instead of per render |

### Route Viewer — How It Works

1. User selects a vehicle in the Route Viewer expander
2. The vehicle's `trip_id` (captured from the realtime feed) is looked up against the **GTFS Static** ZIP for that region (`https://api.data.gov.my/gtfs-static/<agency>`)
3. `trips.txt` → resolves `shape_id` → `shapes.txt` → ordered `[lon, lat]` path
4. Drawn as a green `PathLayer` on the map
5. If no shape is available (optional field in GTFS), falls back to the vehicle's historical breadcrumb trail from DuckDB

### Database Schema (`live_buses`)

| Column | Type | Description |
|---|---|---|
| `region` | VARCHAR | Transit region name |
| `vehicle_id` | VARCHAR | Vehicle identifier |
| `latitude` | DOUBLE | GPS latitude |
| `longitude` | DOUBLE | GPS longitude |
| `bearing` | DOUBLE | Heading in degrees (0–360) |
| `speed` | DOUBLE | Speed in m/s (converted to km/h for display) |
| `timestamp` | BIGINT | Vehicle's reported Unix timestamp |
| `trip_id` | VARCHAR | GTFS trip ID (for route lookup) |
| `route_id` | VARCHAR | GTFS route ID |
| `insert_timestamp` | BIGINT | Unix time when row was inserted |
| `created_at` | TIMESTAMP | Datetime when row was first ingested |

### Database Schema (`fetch_quality_log`)

One row per region per fetch cycle. Powers the Network Health page.

| Column | Type | Description |
|---|---|---|
| `fetch_timestamp` | BIGINT | Unix time when the fetch cycle ran |
| `region` | VARCHAR | Transit region name |
| `vehicles_received` | INTEGER | Raw count from API before any filtering |
| `vehicles_rejected` | INTEGER | Filtered out (bad coords or stale timestamps) |
| `vehicles_inserted` | INTEGER | Actually written to `live_buses` after dedup |
| `avg_data_lag_seconds` | DOUBLE | Average of `insert_timestamp − vehicle_timestamp` |
| `max_data_lag_seconds` | DOUBLE | Worst lag observed in this fetch |
| `total_dropout` | BOOLEAN | True if API returned zero vehicles for this region |
| `fetch_duration_ms` | INTEGER | Wall-clock time for this region's HTTP fetch |

---

### Data Modeling (dbt)

Analytical transformations live in a dbt project (`transform/`, dbt-duckdb adapter) as a
bronze → silver → gold medallion model. The live-map path stays on direct DuckDB queries for
sub-minute freshness; only the analytical pages read dbt marts.

| Layer | Model | Grain | Feeds |
|---|---|---|---|
| source (bronze) | `live_buses`, `fetch_quality_log` | raw rows | — |
| staging (silver) | `stg_vehicle_positions`, `stg_fetch_quality` | cleaned rows | marts |
| mart (gold) | `mart_network_health` | region (24h) | Network Health scorecards |
| mart (gold) | `mart_region_health_trend` | region × fetch cycle | drill-down charts |
| mart (gold) | `mart_region_vehicle_counts` | region | Analytics bar + pie |

The reliability-score formula is a single dbt macro (`reliability_score`) shared by the two
health marts. Data quality is enforced by dbt tests (region `accepted_values`, 0–100 score range,
0–1 rate range, key uniqueness) and source freshness, run in CI via `dbt build`.

```mermaid
flowchart LR
  subgraph bronze["bronze — sources"]
    A[live_buses]
    B[fetch_quality_log]
  end
  subgraph silver["silver — staging"]
    C[stg_vehicle_positions]
    D[stg_fetch_quality]
  end
  subgraph gold["gold — marts"]
    E[mart_region_vehicle_counts]
    F[mart_network_health]
    G[mart_region_health_trend]
  end
  A --> C --> E
  B --> D --> F
  D --> G
```

Run locally:

    export DBT_DUCKDB_PATH="$(pwd)/src/agustiar_analytics.duckdb"
    dbt build --project-dir transform --profiles-dir transform

For the full interactive lineage graph:

    dbt docs generate --project-dir transform --profiles-dir transform
    dbt docs serve --project-dir transform --profiles-dir transform

---

## 📊 Data Sources

| Source | URL | Used For |
|---|---|---|
| GTFS Realtime | [api.data.gov.my/gtfs-realtime](https://developer.data.gov.my/realtime-api/gtfs-realtime) | Live vehicle positions |
| GTFS Static | [api.data.gov.my/gtfs-static](https://developer.data.gov.my/realtime-api/gtfs-static) | Route shapes, stop names, schedules |

**Coverage:** Rapid Bus KL, Rapid Bus MRT Feeder, Rapid Bus Kuantan, Rapid Bus Penang, KTM Berhad, myBAS (Kangar, Alor Setar, Kota Bharu, Kuala Terengganu, Ipoh, Seremban, Melaka, Johor, Kuching)

---

## 🛠️ Dependencies

```
streamlit>=1.28.0              # Web framework
streamlit-autorefresh>=1.0.1   # 20s auto-refresh trigger
streamlit-js-eval>=0.1.7       # Browser geolocation bridge
pandas>=2.0.0                  # Data manipulation
duckdb>=0.9.0                  # Local columnar database
numpy>=1.24.0                  # Arrow geometry calculations
pydeck>=0.8.0                  # Interactive map (WebGL)
plotly>=5.14.0                 # Analytics charts
requests>=2.31.0               # HTTP API calls
gtfs-realtime-bindings>=1.0.0  # GTFS Protobuf parsing
protobuf>=4.21.0               # Protocol Buffers
dbt-duckdb>=1.7.0,<2.0.0       # Analytics transformation layer (transform/)
```

---

## 🐛 Troubleshooting

| Problem | Fix |
|---|---|
| No data showing | Click "Refresh Data", check internet connection |
| Map not loading | Toggle map theme (light↔dark), check browser console |
| Locate Me does nothing | Allow location permission in browser when prompted |
| Route Viewer shows "No route data" | That vehicle's region may not have `shapes.txt` in its GTFS Static feed — historical trail is shown as fallback |
| Database errors | Delete `agustiar_analytics.duckdb` and click "Refresh Data" |

---

## 🗺️ Roadmap

- [x] Live vehicle tracking across 14 regions
- [x] Auto-refresh (20s interval)
- [x] Parallel API fetching (ThreadPoolExecutor)
- [x] Historical data table with CSV export
- [x] Analytics dashboard
- [x] Locate Me (browser GPS)
- [x] Route Viewer (GTFS Static planned routes)
- [x] Audit timestamps (`created_at`)
- [x] Network Health page — per-region reliability scores and fetch quality log
- [ ] Route Planner — enter origin/destination, get transit directions

---

## 📝 License

MIT — see [LICENSE](LICENSE).

---

## 🙏 Acknowledgments

- **Data**: [Malaysia Open Data Portal](https://data.gov.my)
- **Framework**: [Streamlit](https://streamlit.io)
- **Map**: [Pydeck / deck.gl](https://deckgl.readthedocs.io)
- **Charts**: [Plotly](https://plotly.com)
- **Database**: [DuckDB](https://duckdb.org)

---

**Maintainer**: Agustiar Falahi — [@agustiarfalahi94](https://github.com/agustiarfalahi94)

<div align="center"><a href="#-malaysia-real-time-transit-tracker">⬆ back to top</a></div>
