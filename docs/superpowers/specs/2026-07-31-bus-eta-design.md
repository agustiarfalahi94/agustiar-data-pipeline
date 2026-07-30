# Design: Bus ETA to Your Nearest Stop

**Date:** 2026-07-31
**Target version:** 2.5.0 (new feature → minor bump)
**Status:** Approved design, pending implementation plan

---

## Problem

The Live Map shows *where* a bus is. It cannot answer the question that actually decides behaviour
at a bus stop: **"do I wait, or do I walk?"**

Concretely: standing near Green Avenue in Bukit Jalil waiting for a T580 toward Awan Besar LRT,
you can see a T580 on the map but have no idea whether it arrives in 2 minutes or 15. Google Maps
answers journey planning well; it does not answer "where is *that specific bus* and when does it
reach *my* stop".

## Evidence gathered before designing

Three checks, all run against the live API and the published static feed:

**1. The realtime `trip_id` matches the static schedule.** A `trip_id` captured from the live
Rapid Bus KL feed — `weekday_T3018_T301802_3` — is present in `stop_times.txt`, with 27 stops and
a scheduled end-to-end run of 25 minutes. This is the finding the whole design rests on: it means
scheduled stop-to-stop times can be joined directly to a live vehicle.

**2. `stop_times.txt` carries real per-stop times.** 87,936 rows for Rapid Bus KL, e.g.
`06:15:00`, `06:17:41`, `06:19:02`. Travel time between any two stops on a trip is therefore
already encoded — including typical traffic and dwell — and does not need to be invented.

**3. `shapes.txt` has no `shape_dist_traveled`.** Only `shape_pt_lat` / `shape_pt_lon` /
`shape_pt_sequence`. Any distance-along-route approach would have to compute cumulative distance
itself. This is a reason to prefer stop-matching over shape projection for v1.

Also relevant, established earlier: the provider publishes **vehicle positions only** — trip
updates and service alerts are on their 2026 roadmap — so there are no official arrival
predictions to consume. Every ETA here is derived locally and must present as an estimate.

## Non-goals

- **No journey planning.** Origin→destination routing duplicates Google Maps; explicitly ruled out
  of this project's roadmap.
- **No shape projection.** Sub-stop precision is not worth the cumulative-distance computation and
  the loop/overlap ambiguity it introduces. Recorded as a follow-up.
- **No historical travel-time mining.** Deriving observed segment times from the project's own
  position history is a stronger story but needs far more data than a 7-day retention window holds
  per route. Recorded as a follow-up.
- **No train ETA.** KTM's realtime feed carries no `route_id` and rail has no realtime feed at all
  beyond KTM; out of scope.

---

## Part 1 — The computation

For a selected vehicle:

1. Read its `trip_id`, position and timestamp — all already ingested into `live_buses`.
2. Resolve the trip's ordered stops from `stop_times.txt`, joined to `stops.txt` for coordinates
   and names.
3. **Locate the bus:** find the stop in that sequence nearest to the bus's position → index `i`.
4. **Estimate delay:** compare the bus's reported timestamp against the scheduled time at stop `i`.
   A positive delay means running late.
5. **Locate the user:** find the stop nearest the user's GPS position **with index `j > i`**, so
   the answer is always a stop the bus has yet to reach.
6. **ETA** = scheduled time at `j` + delay − now.
7. Report walking distance from the user to stop `j`.

Accuracy is granular to roughly one stop, which on these routes is 1–2 minutes. That is the right
resolution for a wait-or-walk decision and is stated as such in the UI.

## Part 2 — New units

`src/utils/eta.py` — a new module. Nothing in it imports Streamlit or DuckDB, so all of it is
directly unit-testable.

| Function | Contract |
|---|---|
| `haversine_m(lat1, lon1, lat2, lon2) -> float` | Great-circle distance in metres |
| `nearest_stop_index(stops, lat, lon, start_index=0) -> (int, float)` | Index of the nearest stop at or after `start_index`, and its distance in metres |
| `estimate_delay_seconds(stops, bus_index, bus_timestamp, service_day_epoch) -> int` | Signed seconds; positive = late |
| `compute_eta(stops, bus_index, target_index, delay_seconds, now_epoch, service_day_epoch) -> int` | Seconds until arrival; negative means already passed |

`gtfs_static.py` gains one loader:

`get_trip_stops(agency_slug, trip_id) -> list[dict]` — ordered stops for a trip, each carrying
`stop_id`, `stop_name`, `stop_lat`, `stop_lon`, and `arrival_seconds` (seconds since service-day
midnight). Returns `[]` when the trip is absent.

`live_map.py` renders only.

## Part 3 — Three subtleties that must be handled explicitly

**GTFS times exceed 24:00:00.** A stop may legitimately read `25:30:00`, meaning 01:30 the
following day. These are parsed as *seconds since service-day midnight*, never as clock times.
Parsing them with a time formatter silently breaks late-night ETAs, so `arrival_seconds` is an
integer throughout and is only combined with a service-day epoch at the point of comparison.

**The service day is derived, not read.** The realtime feed does carry `startDate` and `startTime`
in its trip descriptor, but ingestion does not currently capture them. For this version the
service day is derived from the vehicle's own timestamp, in the configured timezone. Consequence,
stated plainly: a trip that began before midnight and runs past it may resolve against the wrong
service day and produce a wrong ETA. Rapid KL services largely end by midnight, so this is
accepted for v1 and recorded as a follow-up — ingesting `startDate` removes the caveat entirely
but is a schema change.

**Parsing cost.** `stop_times.txt` is ~88,000 rows for Rapid Bus KL. Parsing it per interaction is
unusable. It is parsed once per agency into a `trip_id`-keyed dictionary and memoised alongside
the existing 24-hour ZIP cache, so the cost is paid once per agency per session.

## Part 4 — Failure modes

No situation below produces a number. Each produces its own message, because a fabricated ETA at a
bus stop is worse than no ETA.

| Situation | Shown |
|---|---|
| Vehicle has no `trip_id` | "No trip information for this vehicle" |
| `trip_id` absent from the published schedule | "This trip isn't in the published timetable" |
| User location unknown | Prompt to use the existing Locate Me control |
| No stop ahead of the bus is near the user | "This bus has already passed your nearest stop" |
| Selected vehicle no longer in the feed | "That vehicle is no longer reporting" |
| Bus position is stale | ETA shown **with its age**, explicitly flagged as less certain |

The last row falls out of the 2.4.0 freshness work: a vehicle whose position is four minutes old
must not yield a confident "6 min". The panel states the position's age whenever it exceeds the
fresh threshold.

## Part 5 — Interaction

Tapping a vehicle marker on the map sets `selected_vehicle_id` in session state; an ETA panel
renders below the map. The selection is re-resolved from the current frame on every render, so a
20-second auto-refresh advances the bus without clearing the selection, and a vehicle that drops
out of the feed produces the explicit message above rather than an empty panel.

This requires Streamlit's `pydeck_chart(selection_mode=…, on_select=…)`, which raises the declared
floor from `>=1.28.0` to `>=1.40.0` in both `requirements.txt` and `pyproject.toml`. Streamlit
Cloud already runs 1.53, and the repo's venv has 1.53.1, so this is a declaration change rather
than an upgrade.

## Testing

`eta.py` is pure, so each function is tested directly against fixed stop lists:

- `haversine_m` against a known pair of coordinates
- `nearest_stop_index` including the `start_index` constraint that keeps the answer ahead of the bus
- `estimate_delay_seconds` for an on-time bus, a late bus, and an early bus
- `compute_eta` for a normal case, a zero-delay case, and a negative result meaning already-passed
- the `25:30:00` rollover, asserted end-to-end through `get_trip_stops` → `compute_eta`

`get_trip_stops` is tested against a small synthetic GTFS ZIP built in the test, not the network,
so the suite stays offline and deterministic.

The existing 91 tests must stay green.

## Follow-ups (not in this version)

- **Ingest `startDate`/`startTime`** from the trip descriptor to make service-day resolution exact
  and remove the after-midnight caveat.
- **Shape projection** for sub-stop precision, once cumulative distance along `shapes.txt` is
  computed.
- **Observed travel times** mined from the project's own position history, replacing scheduled
  segment times where enough observations exist.
- **Nearest stops panel** independent of any selected bus — "which stops are near me, and what
  serves them" — which reuses `stops.txt` and `haversine_m` directly.
