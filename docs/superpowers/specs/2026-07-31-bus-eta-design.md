# Design: "What can I catch from here?" — Stop-Centric Arrivals

**Date:** 2026-07-31 (revised same day — see *Revision*)
**Target version:** 2.5.0 (new feature → minor bump)
**Status:** Approved design, pending implementation plan

---

## Revision

This spec originally led with a **bus-centric** view: tap a vehicle on the map, see when it reaches
your nearest stop. That was approved, then reconsidered on a direct question from the owner:

> *"will the ETA explain itself for someone who doesn't know the route for the bus he wants to
> ride? or worse, they cannot read map at all?"*

It would not. Tapping a bus presumes you already know which bus you want and can find it on a map.
The primary view is therefore inverted to **stop-centric** — *"I am standing here; what can I
catch, and when?"* — which needs neither route knowledge nor map reading. Tap-a-bus survives as a
secondary path. The computation underneath is unchanged, so this costs a screen, not a rewrite.

---

## Problem

The Live Map shows *where* buses are. It cannot answer the question that actually decides behaviour
at a bus stop: **"do I wait, or do I walk?"** — and it cannot be asked at all by someone who does
not already know the network.

Concretely: standing near Green Avenue in Bukit Jalil, the useful answer is *"Awan Besar LRT is
240 m away, and a T580 reaches it in about 6 minutes."* Google Maps answers journey planning well;
it does not answer "what is coming to the stop I am standing at, right now."

## Evidence gathered before designing

Three checks, run against the live API and the published static feed:

**1. The realtime `trip_id` matches the static schedule.** A `trip_id` captured from the live
Rapid Bus KL feed — `weekday_T3018_T301802_3` — is present in `stop_times.txt`, with 27 stops and
a scheduled end-to-end run of 25 minutes. The whole design rests on this: scheduled stop-to-stop
times can be joined directly to a live vehicle.

**2. `stop_times.txt` carries real per-stop times.** 87,936 rows for Rapid Bus KL, e.g.
`06:15:00`, `06:17:41`, `06:19:02`. Travel time between any two stops on a trip is already
encoded — including typical traffic and dwell — and does not need to be invented.

**3. `shapes.txt` has no `shape_dist_traveled`.** Any distance-along-route approach would have to
compute cumulative distance itself, which is why stop-matching is preferred for v1.

Also established earlier: the provider publishes **vehicle positions only** — trip updates and
service alerts are on their 2026 roadmap — so there are no official arrival predictions to consume.
Every ETA here is derived locally and must present as an estimate.

## Non-goals

- **No journey planning.** Ruled out of this project's roadmap.
- **No shape projection.** Sub-stop precision is not worth the cumulative-distance computation or
  the loop/overlap ambiguity. Follow-up.
- **No historical travel-time mining.** A stronger story, but needs far more data than a 7-day
  retention window holds per route. Follow-up.
- **No train arrivals.** KTM's realtime feed carries no `route_id`, and LRT/MRT/Monorail have no
  realtime feed at all. Out of scope.
- **No cross-region arrivals.** Stops are searched within the selected region only, consistent with
  how route search already behaves.

---

## Part 1 — The primary view: arrivals near you

Given the user's GPS position and the selected region:

1. Find stops within walking distance — nearest few from `stops.txt`.
2. For every live vehicle in the region that reports a `trip_id`:
   - resolve its trip's ordered stops;
   - locate the bus by its nearest stop in that sequence → index `i`;
   - estimate its delay from the scheduled time at stop `i` versus its reported timestamp;
   - for each nearby stop appearing in that trip **after** index `i`, compute an arrival time.
3. Group by stop, sort by soonest, and show the next few arrivals per stop.

Rendered:

```
📍 Near you
  STESEN LRT AWAN BESAR · 240 m · ~3 min walk
     T580  → TPM              ~6 min    · 3 min late
     T581  → Bandar Kinrara   ~14 min
  JALAN JALIL PERKASA 1 · 90 m · ~1 min walk
     U6000 → Klang            ~4 min    · position 2 min old
```

Walking time is a plain distance estimate at a documented constant pace, labelled as approximate —
it is not routed, and must not imply it is.

## Part 2 — The secondary view: a selected bus

Tapping a vehicle on the map selects it and shows the same computation for that one vehicle
against the user's nearest stop on its trip. This reuses Part 1's primitives entirely; it is a
filter over the same result, not a second implementation.

Selection is held in session state and re-resolved from the current frame on every render, so
auto-refresh advances the bus without clearing the selection, and a vehicle that drops out of the
feed reports that rather than emptying silently.

Requires `pydeck_chart(selection_mode=…, on_select=…)`, raising the declared Streamlit floor from
`>=1.28.0` to `>=1.40.0`. Streamlit Cloud already runs 1.53 and the repo venv has 1.53.1, so this
is a declaration change rather than an upgrade.

## Part 3 — New units

`src/utils/eta.py` — a new module importing neither Streamlit nor DuckDB, so all of it is directly
unit-testable.

| Function | Contract |
|---|---|
| `haversine_m(lat1, lon1, lat2, lon2) -> float` | Great-circle distance in metres |
| `nearest_stop_index(stops, lat, lon, start_index=0) -> (int, float)` | Index of the nearest stop at or after `start_index`, and its distance |
| `estimate_delay_seconds(stops, bus_index, bus_timestamp, service_day_epoch) -> int` | Signed seconds; positive = late |
| `compute_eta(stops, bus_index, target_index, delay_seconds, now_epoch, service_day_epoch) -> int` | Seconds until arrival; negative = already passed |
| `arrivals_for_stops(vehicles, trip_stops_lookup, nearby_stops, now_epoch) -> dict` | The Part 1 aggregation: `stop_id → [arrival, …]` sorted soonest-first |
| `walking_minutes(distance_m, pace_m_per_min=80) -> int` | Approximate walk time at a documented pace |

`gtfs_static.py` gains two loaders:

- `get_trip_stops(agency_slug, trip_id) -> list[dict]` — ordered stops for a trip, each with
  `stop_id`, `stop_name`, `stop_lat`, `stop_lon`, `arrival_seconds` (seconds since service-day
  midnight). `[]` when the trip is absent.
- `get_stops_near(agency_slug, lat, lon, radius_m, limit) -> list[dict]` — nearest stops from
  `stops.txt` within `radius_m`, closest first, each carrying its distance.

`live_map.py` renders only.

## Part 4 — Three subtleties that must be handled explicitly

**GTFS times exceed 24:00:00.** A stop may legitimately read `25:30:00`, meaning 01:30 the
following day. These are parsed as *seconds since service-day midnight*, never as clock times.
`arrival_seconds` is an integer throughout and only meets a service-day epoch at the point of
comparison. Parsing them with a time formatter silently breaks late-night arrivals.

**The service day is derived, not read.** The realtime feed carries `startDate`/`startTime` in its
trip descriptor, but ingestion does not capture them. For this version the service day is derived
from the vehicle's own timestamp in the configured timezone. Consequence, stated plainly: a trip
that began before midnight and runs past it may resolve against the wrong service day and produce
a wrong arrival. Rapid KL services largely end by midnight, so this is accepted for v1 and recorded
as a follow-up; ingesting `startDate` removes it entirely but is a schema change.

**Parsing cost.** `stop_times.txt` is ~88,000 rows for Rapid Bus KL. Parsed per interaction it is
unusable. It is parsed once per agency into a `trip_id`-keyed dictionary and memoised alongside the
existing 24-hour ZIP cache, so the cost is paid once per agency per session. The per-render work is
then dictionary lookups over roughly a hundred live vehicles — bounded and cheap.

## Part 5 — Failure modes

No situation below produces a number. A fabricated ETA at a bus stop is worse than no ETA.

| Situation | Shown |
|---|---|
| Location unknown | Prompt to use the existing Locate Me control — this is the primary entry point, so it leads |
| No stops within the radius | "No stops found within {radius} of you in {region}" |
| Stops found, nothing inbound | "Nothing scheduled to arrive at these stops right now" |
| Vehicle has no `trip_id` | Excluded from arrivals; counted in a footnote so the omission is visible |
| `trip_id` absent from the schedule | Excluded; counted in the same footnote |
| Bus already passed the stop | Not listed for that stop |
| Bus position is stale | Arrival shown **with the position's age**, flagged as less certain |
| Selected vehicle no longer in the feed | "That vehicle is no longer reporting" |

The stale row falls out of the 2.4.0 freshness work: a vehicle whose position is four minutes old
must not yield a confident "6 min".

## Testing

`eta.py` is pure, so each function is tested directly against fixed stop lists:

- `haversine_m` against a known coordinate pair
- `nearest_stop_index` including the `start_index` constraint that keeps answers ahead of the bus
- `estimate_delay_seconds` for on-time, late and early buses
- `compute_eta` for a normal case, zero delay, and a negative result meaning already-passed
- `arrivals_for_stops` grouping and sort order, a stop served by two routes, and a bus excluded
  because it has already passed
- `walking_minutes` at the documented pace
- the `25:30:00` rollover, asserted end-to-end from `get_trip_stops` through `compute_eta`

`get_trip_stops` and `get_stops_near` are tested against a small synthetic GTFS ZIP built inside
the test, not the network, so the suite stays offline and deterministic.

The existing 97 tests must stay green.

## Follow-ups (not in this version)

- **Ingest `startDate`/`startTime`** to make service-day resolution exact.
- **Shape projection** for sub-stop precision, once cumulative distance is computed.
- **Observed travel times** mined from the project's own position history, replacing scheduled
  segment times where enough observations exist.
- **Walking directions** to the chosen stop, rather than a straight-line distance and a pace
  constant.
