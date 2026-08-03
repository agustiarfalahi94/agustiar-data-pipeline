# Design: Route Name Search + Honest Fetch Status

**Date:** 2026-07-30
**Target version:** 2.3.0 (new feature → minor bump)
**Status:** Approved design, pending implementation plan

---

## Problem

Two unrelated problems, both surfaced by measurement rather than assumption.

**1. You cannot find a specific route.** The Live Map identifies vehicles by opaque IDs
(`JXK4866`). To answer *"where is my T580 right now — do I sprint or walk?"* you must scan
every arrow on the map. The data needed to fix this is already ingested.

**2. Network Health reports failures that are not the agency's fault.** Observed live:

- **Rapid Bus Kuantan — score 20.** Its endpoint returns
  `HTTP 404: "Vehicle Position feed for prasarana (rapid-bus-kuantan) does not exist."`
  The feed was withdrawn upstream; the app polls a dead URL every 20 seconds and records each
  failure as an agency dropout.
- **KTM Berhad — score 46.** Its feed returns `HTTP 200` with zero vehicles when no trains are
  running. Absence of scheduled service is recorded as an outage.

`_fetch_endpoint` discards *why* a fetch failed, so five distinct situations collapse into one
`total_dropout = True`.

### Evidence

A 6-hour probe (12 rounds, 4 feeds, 1,203 vehicle observations) established:

| Feed | Vehicles sampled | `route_id` present | `trip_id` present |
|---|---|---|---|
| Rapid Bus KL | 372 | **100%** | 100% |
| Rapid Bus MRT Feeder | 512 | **100%** | 100% |
| myBAS Johor | 273 | **100%** | 100% |
| KTM Berhad | 46 | **0%** | 100% |

T580 was present in every good sample as `route_id = T5800`. Buses therefore need no fallback.
KTM carries only `tripId` (verified payload: `{"trip": {"tripId": "56"}, "vehicle": {"id": "56",
"label": "DMU11(D6122)"}}`), so route search for rail requires a `trip_id → trips.txt → route_id`
join that buses do not.

## Non-goals

- **No route planner.** Origin→destination journey planning duplicates Google Maps.
- **No cross-region search.** Search operates within the already-selected region.
- **No train search in this version.** Deferred; see Follow-ups.
- **No schedule-awareness.** Distinguishing "no service scheduled" from "should be running but
  isn't" needs GTFS `calendar.txt`; recorded as a known limitation, not built.

---

## Part 1 — Route name search

### Behaviour

A search box sits under the existing region dropdown in `src/app_pages/live_map.py`.

- Matching is **case-insensitive substring** against the resolved route display string, so
  `t580`, `T580`, and `awan besar` all match
  `"T580 — Stesen LRT Awan Besar ~ TPM via Stesen LRT Bukit Jalil"`.
- **Empty query** → unchanged current behaviour.
- **Matches** → the map renders only those vehicles, captioned
  *"Showing 4 vehicles on T580 — …"*.
- **No matches** → the map is left unfiltered and a warning is shown:
  *"No live vehicles found on 'X' right now."* An empty map would be ambiguous between a bad
  search term and a route with no buses currently running.
- **KTM Berhad selected** → the search box is hidden, with a caption explaining route search is
  not yet available for KTM. A visible box that never matches would read as broken.

### Integration point

`live_map.py` already resolves route names for every mapped vehicle into `df_map['route_display']`
(added in 2.1.2 for tooltips), immediately after `prepare_map_data`. The filter is applied
**after** `route_display` is populated and **before** the pydeck layers are built, so the
arrow/scatter layers and the Route Viewer's vehicle dropdown reflect the filtered set with no
further changes.

View centring does **not** follow for free. The viewport is deliberately sticky — it is
recomputed only on a *change of what is being shown*, so auto-refresh never yanks the camera away
from wherever the user panned. Region change was originally the only such trigger, which left a
search made while parked elsewhere in the region filtering the layers but not moving the camera.
The search query is therefore tracked in session state alongside the region, and the view
recentres on the filtered frame's mean latitude/longitude when either changes.

The vehicle-count caption below the map states the region total, so it is suppressed while a
search is filtering — the match count is already reported by the search banner above the map.

### New unit

`data_processor.filter_by_route(df, query) -> DataFrame`

A pure function: returns rows whose `route_display` contains `query`, case-insensitively;
returns the frame unchanged when `query` is empty or whitespace; returns an empty frame when
nothing matches. No Streamlit dependency, so it is directly testable.

---

## Part 2 — Fetch status and honest scoring

### Status classification

`_fetch_endpoint` returns `(vehicles, duration_ms)` today. It becomes
`(vehicles, duration_ms, status)`, classified by a pure helper `_classify_status`:

| Status | Trigger | Scoring treatment |
|---|---|---|
| `OK` | HTTP 200, ≥1 vehicle | Scored normally |
| `EMPTY` | HTTP 200, 0 vehicles | Feed healthy, no service running — **not** penalised |
| `NO_FEED` | HTTP 404 | Excluded from scoring entirely |
| `THROTTLED` | HTTP 429 | Excluded from scoring entirely (self-inflicted, not the agency) |
| `ERROR` | timeout, connection error, protobuf decode failure, any other non-200 | Counts as a real dropout |

### Schema

One additive column on `fetch_quality_log`:

```
fetch_status VARCHAR
```

Added through the same `information_schema` + `ALTER TABLE` migration pattern the table already
uses for `insert_timestamp` / `created_at`. Pre-existing rows have `NULL`, which the marts treat
as `OK` so historical data keeps scoring as it does today. The 7-day retention window ages NULLs
out naturally.

`total_dropout` is retained and keeps its current meaning (`vehicles_received == 0`) so nothing
that reads it breaks; the marts stop *scoring* from it.

### Scoring changes (dbt)

`mart_network_health` and `mart_region_health_trend` compute over **scoreable** fetches only —
those with status `OK`, `EMPTY`, or `ERROR` (or `NULL`, treated as `OK`).

- `availability` = `1 − (ERROR count ÷ scoreable count)`
- `reporting_rate` is averaged over fetches that actually returned vehicles, as today
- `NO_FEED` and `THROTTLED` are excluded from both numerator and denominator
- A region whose entire window is `NO_FEED` produces **no score**

A fetch cycle that received nothing has **no reporting rate at all** — it is undefined, not zero.
Both marts therefore drop that term for such a cycle and renormalise the remaining weights
(`0.4·availability + 0.2·freshness) ÷ 0.6`), via the `reliability_score_without_reporting` macro.
This is what the aggregate already did implicitly, since `avg()` skips a NULL; stating it
explicitly is what keeps the aggregate scorecard and the per-cycle sparkline beneath it from
describing the same window differently. `dropout_count` and `avg_data_lag_seconds` are likewise
computed over scoreable rows only, so nothing printed on a card contradicts the score beside it.

`mart_network_health` gains four columns: `scoreable_fetches`, `feed_unavailable` (boolean — true
when the window contains no scoreable fetches), `no_feed_count` and `throttled_count`.

### Network Health page

When `feed_unavailable` is true, the region renders a neutral grey card reading
**"Feed unavailable"** with no numeric score, instead of a red 20. The cause line is driven by
`no_feed_count` / `throttled_count`: "withdrawn upstream" is asserted only for a feed that
actually returned 404, never for one we rate-limited ourselves. The reliability formula and the
`reliability_score` macro are otherwise unchanged — this changes *which rows feed the formula*,
not the formula itself.

The Raw Fetch Log shows `fetch_status` as a **Status** column (and exports it), since a dropout
row without its reason is exactly the ambiguity this part of the release removes. A region with
no scoreable fetch in the window has no score series to plot, so the drill-down shows the
not-scored note in place of an empty chart.

### Expected outcome

Rapid Bus Kuantan stops reporting a false 20 and is shown as unavailable upstream. KTM Berhad
stops being penalised for hours when no trains run, so its score reflects feed reliability rather
than timetable density.

### Known limitation (accepted)

Treating `EMPTY` as healthy means a genuine outage — feed responding but returning nothing during
service hours — no longer reduces the score. Detecting that requires knowing expected service
hours from GTFS `calendar.txt`. Recorded as a follow-up, not built here.

---

## Testing

- `filter_by_route` — case-insensitivity, long-name match, no match, empty/whitespace query,
  missing `route_display` column
- `_classify_status` — one test per branch: 200 with vehicles, 200 empty, 404, 429, timeout,
  decode failure
- `_build_quality_stats` — carries `fetch_status` through to the log row
- dbt — `fetch_status` constrained by `accepted_values`; a fixture asserting a `NO_FEED`-only
  region yields `feed_unavailable = true` and no score, and that `EMPTY` fetches do not reduce
  availability
- The existing 36 tests must remain green

---

## Airflow orchestration — decided against

Not a backlog item; not being re-proposed. Airflow cannot run on Streamlit Cloud — it needs an
always-on scheduler (VM, Cloud Composer, or Astronomer), and the owner has decided not to pay for
one just to run this project's ingestion. The reasoning is recorded here so it does not get
raised again without new information changing the hosting constraint:

- **The actual blocker is hosting, not the orchestration design.** `fetch_and_store_transit_data()`
  is already a clean entry point a DAG could call, and `ingest → dbt run → dbt test` is a
  straightforward ~5-minute schedule. None of that requires a server running 24/7 — Airflow itself
  does.
- **A zero-cost alternative exists if this is ever revisited:** a GitHub Actions scheduled workflow
  (`on: schedule`) can run ingestion and `dbt run` on a cron for free on public repos, and CI is
  already configured. It offers no backfill UI and weaker retry semantics than Airflow, but it
  would demonstrate an orchestrated pipeline without provisioning anything — the option worth
  evaluating first if the hosting constraint ever changes.

## Follow-ups (not in this version)

- **Train route search** — resolve `trip_id → trips.txt → route_id` for KTM, then enable the
  search box for it
- **Service-hours awareness** — use GTFS `calendar.txt` to separate "no service scheduled" from a
  genuine in-service outage
- **Re-check withdrawn feeds** — `rapid-bus-kuantan` is still listed in the provider's
  documentation despite returning 404; it may return

### Carried over from the 2.3.0 review (fixed in 2.3.1)

- ~~No-match search recentred the map~~ — fixed: the camera now follows what is displayed.
- ~~"⚫ No Feed" summary label covered throttled regions too~~ — fixed: renamed "⚫ Not scored".

### Still open

- **`_marts.yml` overstates mart agreement.** The `availability` description says the two marts
  "agree". They share one scoring rule, which is the property that matters, but they are not
  identical in general: a region with one `OK` cycle at reporting 0.5 (lag 0) plus one `EMPTY`
  cycle yields aggregate 80 against trend `[80, 100]`, mean 90. The direction is now benign — the
  card reads conservatively relative to the sparkline — but the description should be qualified.
- **`EMPTY` cycles feed a fabricated `avg_data_lag_seconds = 0`** into the freshness term of both
  marts. That is the same "a lag it never measured" objection used to exclude `NO_FEED`, so the
  rationale is asymmetric. Applied consistently across both marts, so there is no user-visible
  disagreement; it is what makes an all-quiet region land on exactly 100.
- **An all-quiet region scores 100 "Reliable"** with no on-card statement that the score was
  computed without any reporting-rate evidence. `📶 N/A` and the quiet-cycle count are the only
  signals. A legibility gap rather than a false claim.
- **`get_network_health_summary`'s docstring** in `src/utils/db.py` still lists a pre-2.3 column
  set — no `scoreable_fetches`, `feed_unavailable`, `no_feed_count`, `throttled_count`.
- **`_SCHEMA_SENTINEL_COLUMNS` is hand-maintained.** If a `dbt run` ever succeeded without
  producing them, `ensure_dbt_models` would reset its failure counter and re-run dbt on every
  fetch with no cooldown. Low likelihood, since the model SQL ships with the repo.
- **Search recentring keeps `DEFAULT_ZOOM = 13`.** Matches spread across a wide area are centred
  but may not all be in frame; no fit-to-bounds heuristic exists.
