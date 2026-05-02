# Malaysia Real-Time Transit Tracker — Presentation Guide

A reference for presenting this project to interviewers, peers, or anyone curious about it. Covers what each feature does, the design decisions behind it, and the questions you are most likely to be asked.

---

## The One-Line Pitch

> "A real-time dashboard that tracks live bus and rail positions across Malaysia — and uniquely, tells you *how much you can trust the data*."

Lead with the second half. Everyone can build a map. Surfacing data quality is the differentiator.

---

## The Portfolio Angle

When an interviewer asks *"what's interesting about this project?"*:

> "Most apps that consume government APIs just pass whatever they get straight to the user. I built a layer that tracks data quality at the source — every fetch is logged, scored, and surfaced to users so they know which parts of the data they can trust before they do any analysis. That's the Network Health page."

Then show the scorecards with a red region next to a green one. That is the moment that lands.

---

## Feature-by-Feature Breakdown

### 🗺️ Live Map

**What it does:**
Fetches vehicle positions from 14 transit regions every 20 seconds via the Malaysia GTFS Realtime API. Renders each vehicle as a dot with a directional arrow showing its heading. Tooltip shows vehicle ID, speed in km/h, and bearing. Supports dark/light map themes.

**Sub-features:**
- **Locate Me** — uses the browser's Geolocation API to centre the map on your current position and draw a red marker with an accuracy circle showing your GPS precision in metres.
- **Route Viewer** — select any vehicle to see its planned route drawn as a green line, fetched from the GTFS Static schedule. Falls back to the vehicle's historical breadcrumb trail from DuckDB if no shape data is available.

**Expected questions:**

| Question | Answer |
|---|---|
| Why does a bus sometimes vanish mid-route? | The government API stops reporting it temporarily — GPS dropout on the vehicle, network issues, or the operator's backend not transmitting. This is a known limitation of the upstream API, not a bug. The Network Health page quantifies exactly how often this happens per region. |
| What's the directional arrow? | A triangle rendered as a PathLayer in pydeck (deck.gl), calculated from the vehicle's reported bearing. The geometry is computed in Python using basic trigonometry before being passed to the WebGL renderer. |
| What does the accuracy circle mean? | When you click Locate Me, your device returns a GPS accuracy value in metres. The circle shows that radius — a tight circle means precise location (GPS fix), a large circle means the phone is estimating from cell towers or Wi-Fi. |
| Why use pydeck instead of Folium? | Pydeck uses WebGL via deck.gl, which renders thousands of moving points smoothly in the browser without re-drawing the whole page. Folium generates a static HTML/Leaflet map — it can't animate live updates efficiently. |
| What's the Route Viewer fallback? | Some regions don't include `shapes.txt` in their GTFS Static feed (it's optional in the spec). When that happens, the app falls back to drawing the vehicle's recorded position history from DuckDB as a breadcrumb trail. |

---

### 📊 Data Table

**What it does:**
Shows every vehicle position record ingested in the last 7 days from the `live_buses` DuckDB table. Filterable, sortable, with CSV export. Each row is one position snapshot of one vehicle at one point in time.

**Expected questions:**

| Question | Answer |
|---|---|
| How is this different from the Live Map? | The Live Map shows only the *last 60 seconds* — the current state of the network. The Data Table shows up to *7 days* of history — every position snapshot ever recorded. Useful for spotting patterns over time. |
| What is the "Created At" column? | The timestamp when the record entered *our* database, not when the vehicle reported it. The gap between the vehicle's timestamp and Created At is the data lag — quantified on the Network Health page. |
| Why only 7 days? | DuckDB stores data locally on disk. Without a retention limit, the database would grow indefinitely — after days of 20-second refreshes, it would consume gigabytes and cause memory issues when loading the full table. 7 days is configurable via `DATA_RETENTION_DAYS` in `config.py`. |
| Why would someone export this CSV? | Offline analysis — a researcher could load it into Python or Excel to study speed patterns, coverage gaps, or correlate with external data like weather or events. |

---

### 📈 Analytics

**What it does:**
Aggregated charts over the 7-day history: unique vehicle counts by region (bar), speed distribution (histogram), regional share (pie), speed comparison across regions (box plot), and summary statistics.

**Expected questions:**

| Question | Answer |
|---|---|
| Why do some regions have far fewer vehicles? | Two possible reasons: the operator genuinely runs a smaller fleet, or the API is dropping out frequently. The Network Health page separates these — a low vehicle count with a high reliability score means a genuinely small fleet; a low count with a low reliability score means data is being lost. |
| What does the speed distribution tell us? | Most urban vehicles cluster around 20–50 km/h. Outliers near 0 are idling or stuck in traffic. Outliers near 120 are highway routes like KTM intercity services. |
| Why is speed capped at 120 km/h in the data? | The raw API value is in m/s and occasionally reports garbage values (999 m/s, negative numbers). The cap is a sanity filter — no Malaysian transit vehicle legally exceeds 120 km/h. |

---

### 📡 Network Health

**What it does:**
At every fetch cycle, the app records per-region API quality metadata into a separate `fetch_quality_log` table: how many vehicles were received from the API, how many were rejected (bad coordinates, stale timestamps), how many were actually stored, how old the data was, and whether the region went completely silent. This is aggregated into a reliability score and surfaced on this page.

**Sub-features:**
- **Summary bar** — total regions, count healthy/degraded/unreliable, last fetch time.
- **Scorecards** — one card per region with score, reporting rate, dropout count, data lag, and a 24h sparkline.
- **Drill-down** — reliability score over time, stacked bar of received/rejected/inserted per cycle, lag trend. Time window: 1h / 6h / 24h / 7d.
- **Raw Fetch Log** — table of every fetch event for the selected region with full metadata and CSV export.

**The reliability score formula:**
```
reporting_rate  = avg(vehicles_inserted / vehicles_received)   [dropout rows excluded]
availability    = 1 - (dropout_count / total_fetches)
freshness_score = max(0, 1 - avg_lag_seconds / 300)           [300s = full penalty]

reliability_score = round((0.4 × reporting_rate + 0.4 × availability + 0.2 × freshness_score) × 100)
```

Score bands: ≥ 80 reliable · 50–79 degraded · < 50 unreliable.

**Expected questions:**

| Question | Answer |
|---|---|
| **What's the difference between the Data Table and the Raw Fetch Log?** | The **Data Table** contains vehicle positions — where each bus or train was at each moment. It answers *"what is the network doing?"*. The **Raw Fetch Log** contains API quality metadata — for each fetch cycle, how many vehicles the API sent, how many were rejected, how stale the data was. It answers *"how reliable is the data we're receiving?"*. Same table format, completely different content. One is the passengers; the other is the flight recorder. |
| What does the reliability score actually measure? | Three things: how many of the vehicles the API claims to have we actually end up storing (reporting rate, 40%), how often a region goes completely silent with zero vehicles (availability, 40%), and how old the data is when we receive it (freshness, 20%). |
| Why would a researcher care about this? | Before drawing conclusions from transit data, you need to know if the data is trustworthy. If Rapid Bus KL has a 90% reliability score and myBAS Johor has a 40% score, a speed comparison between the two would be misleading — you'd be comparing clean data against gaps. This page lets you decide which regions to include in your analysis. |
| Isn't this just measuring your own app's performance? | No — it's measuring the *upstream government API's* reliability. The app is just the messenger. What we're surfacing is how consistently the transit operators' systems report to `api.data.gov.my`. |
| What's the fetch guard? | DuckDB only supports one writer at a time. On Streamlit Cloud, all users share the same database file. If two users click Refresh simultaneously, both try to write at once — which causes a lock error. The fetch guard checks if a fetch already ran in the last 15 seconds and skips if so, preventing collisions. |
| Does every user need auto-refresh for the scores to work? | No. Since all users share one DuckDB file on Streamlit Cloud, one user running auto-refresh builds the quality log for everyone. Other users see up-to-date scores without needing auto-refresh themselves. |

---

## Architecture in 30 Seconds

```
GTFS Realtime API (14 regions × 1–2 endpoints)
       │
       ▼  ThreadPoolExecutor — all regions fetched in parallel (~2s vs ~15s sequential)
 _fetch_endpoint() returns (vehicles, duration_ms)
       │
       ├── Count received per region
       ▼
 Filter: remove zero coords, stale timestamps, future timestamps
       │
       ├── Count valid per region, compute lag per region
       ▼
 INSERT INTO live_buses (deduplicated via NOT EXISTS)
       │
       ├── Count inserted per region
       ├── Write to fetch_quality_log (1 row per region)
       └── Prune both tables older than DATA_RETENTION_DAYS

DuckDB on disk
       │
  ┌────┼──────────────┬────────────────┐
  ▼    ▼              ▼                ▼
Live  Data Table   Analytics     Network Health
Map   (7-day)      (7-day)       (fetch_quality_log)
```

**Why DuckDB?** Zero-cost, no server, columnar queries, runs entirely on the Streamlit Cloud instance. Perfect for analytical workloads on a single-user or low-concurrency app.

**Why append-only?** Transit positions are facts — a bus was at a location at a time. There is nothing to update. Append-only makes the data an audit log, not a current-state mirror.

---

## Common Gotchas to Be Aware Of

| Situation | What's happening |
|---|---|
| A bus appears stationary for a long time | The vehicle stopped reporting. The last known position stays visible for 60 seconds before dropping off the live map. |
| No route in the Route Viewer | That region's GTFS Static feed doesn't include `shapes.txt` (optional in spec). The historical breadcrumb trail is shown as fallback. |
| Network Health scores are all 0 or missing | Not enough fetch history yet. Scores need at least 10 fetch cycles (~3 minutes of auto-refresh) to be meaningful. |
| Data Table shows much less data than expected | The 7-day rolling window prunes older rows on each fetch cycle. If the app was offline, the history is shorter. |
| Network Health page is slow to load initially | On first load it queries 14 regions for sparkline data. After the first render, `@st.cache_data(ttl=60)` caches the results for 60 seconds. |

---

## Tech Stack Summary

| Layer | Tool | Why |
|---|---|---|
| Web framework | Streamlit | Rapid Python dashboard, no frontend code needed |
| Map | Pydeck (deck.gl / WebGL) | Smooth rendering of thousands of moving points |
| Charts | Plotly | Interactive, works well with Streamlit |
| Database | DuckDB | Zero-cost columnar DB, runs in-process |
| Data | GTFS Realtime + Static (api.data.gov.my) | Official Malaysia open data portal |
| Geolocation | streamlit-js-eval | Two-way JS bridge for browser GPS coords |
| Parallelism | ThreadPoolExecutor | Cuts fetch time from ~15s to ~2–3s |
| Testing | pytest | 22 unit tests covering data processing and DB functions |
