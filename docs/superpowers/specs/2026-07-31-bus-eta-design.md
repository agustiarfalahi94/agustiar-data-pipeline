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

Signatures below are the ones as built, not as first sketched.

| Function | Contract |
|---|---|
| `haversine_m(lat1, lon1, lat2, lon2) -> float` | Great-circle distance in metres |
| `nearest_stop_index(stops, lat, lon, start_index=0) -> (int, float)` | Index of the nearest stop at or after `start_index`, and its distance. `(-1, inf)` when there is nothing to search |
| `walking_minutes(distance_m, pace_m_per_min=80) -> int` | Approximate walk time at a documented pace, never below 1 |
| `service_day_epoch(vehicle_timestamp, utc_offset_hours) -> int` | Epoch seconds of local midnight for the service day containing the timestamp |
| `estimate_delay_seconds(stops, bus_index, bus_timestamp, day_epoch) -> int` | Signed seconds; positive = late. `0` for an out-of-range index |
| `compute_eta_seconds(stops, target_index, delay_seconds, now_epoch, day_epoch) -> int \| None` | Seconds until arrival; negative = already passed; `None` = no such stop. Takes no `bus_index` — the bus's own scheduled time is already folded into `delay_seconds` |
| `arrivals_for_stops(vehicles, nearby_stops, trip_stops_lookup, now_epoch, utc_offset_hours, headsign_lookup=None, frequency_lookup=None) -> (dict, dict)` | The Part 1 aggregation. Returns `(arrivals, skipped)`: `stop_id → [arrival, …]` sorted soonest-first, and the three skip counters |

`gtfs_static.py` gains four loaders:

- `get_trip_stops(agency_slug, trip_id) -> list[dict]` — ordered stops for a trip, each with
  `stop_id`, `stop_name`, `stop_lat`, `stop_lon`, `arrival_seconds` (seconds since service-day
  midnight). `[]` when the trip is absent.
- `get_stops_near(agency_slug, lat, lon, radius_m, limit) -> list[dict]` — nearest stops from
  `stops.txt` within `radius_m`, closest first, each carrying its distance.
- `get_trip_headsign(agency_slug, trip_id) -> str` — the destination text a rider reads on the
  front of the bus.
- `is_frequency_based(agency_slug, trip_id) -> bool` — whether the trip is published in
  `frequencies.txt`. See Part 4.

`live_map.py` renders only.

## Part 4 — Four subtleties that must be handled explicitly

**The feed is 99% frequency-based, so lateness is mostly not knowable.** Measured against the
cached feed after implementation: **2,099 of the 2,102 Rapid Bus KL trips appear in
`frequencies.txt` with `exact_times=0`** — including `weekday_T3018_T301802_3`, the very trip
cited as evidence above, with `start_time=09:40:00, end_time=17:00:00, headway_secs=2400`. For
`exact_times=0` the clock times in `stop_times.txt` are a **travel-time template**, not scheduled
wall-clock times: the trip repeats every headway across its window and only the *differences*
between stop times carry meaning.

Evidence point 2 above therefore overstated its case. Travel time between two stops on a trip is
indeed encoded and needs no invention — that part holds, and it is all the arrival uses. But there
is no published absolute start time, so comparing a vehicle's timestamp against
`day_epoch + arrival_seconds[bus_index]` measures nothing real. For a perfectly on-time bus the
resulting "delay" grows through the operating day:

| bus reports at | ETA to stop 6 | delay computed |
|---|---|---|
| 06:00 | 4 min | (below the display threshold) |
| 13:00 | 4 min | 200 min late |
| 21:00 | 4 min | 680 min late |

The ETA is correct at every hour, and that is not luck: `day_epoch` and the bus's own scheduled
time cancel algebraically between `estimate_delay_seconds` and `compute_eta_seconds`, so the
arrival only ever uses the difference between two stop times — exactly what a headway template
encodes. **This cancellation is the load-bearing invariant of the module and is asserted by a test
of its own.** The delay is reported as `None` for these trips: unknown, which is the honest answer,
and neither zero nor a number.



**GTFS times exceed 24:00:00.** A stop may legitimately read `25:30:00`, meaning 01:30 the
following day. These are parsed as *seconds since service-day midnight*, never as clock times.
`arrival_seconds` is an integer throughout and only meets a service-day epoch at the point of
comparison. Parsing them with a time formatter silently breaks late-night arrivals.

**The service day is derived, not read.** The realtime feed carries `startDate`/`startTime` in its
trip descriptor, but ingestion does not capture them. For this version the service day is derived
from the vehicle's own timestamp in the configured timezone. Consequence, stated precisely: a trip
that began before midnight and runs past it resolves against the following service day, and what
that corrupts is the **delay, not the arrival**. The arrival cannot be affected — `day_epoch`
cancels between the two functions, as above. The delay can: a bus at 00:30 on a trip that started
at 23:50 might truly be 40 minutes late, while the computed value is about −1,400 minutes, which
the display threshold then silently suppresses — so a genuinely late bus reads as on time. Rapid KL
services largely end by midnight, so this is accepted for v1 and recorded as a follow-up; ingesting
`startDate` removes it entirely but is a schema change.

**Parsing cost.** `stop_times.txt` is ~88,000 rows for Rapid Bus KL. Parsed per interaction it is
unusable. It is parsed once per agency into a `trip_id`-keyed dictionary, keyed on the modification
time of the existing 24-hour ZIP cache so a refreshed download rebuilds it rather than serving a
superseded timetable. The cost is paid once per agency per static release. A build that fails
stores nothing, so a transient feed error is retried on the next call rather than cached as "this
agency has no timetable". The per-render work is then dictionary lookups over roughly a hundred
live vehicles — bounded and cheap.

**Loops.** 1,003 of 2,096 Rapid Bus KL trips (48%) revisit at least one `stop_id`, up to 8 times.
A vehicle can therefore match the same nearby stop at several indexes ahead of it. Only the
earliest arrival per `(vehicle_id, stop_id)` is kept; listing each visit shows one bus twice at one
stop and reads as two separate services.

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
| Lateness not knowable (headway trip) | Arrival shown with **no lateness clause at all** — never "0 min late" |
| Selected vehicle no longer in the feed | "That vehicle is no longer reporting" |
| Selected vehicle comes no closer than the radius | Said plainly, rather than naming a stop across town |

The stale row falls out of the 2.4.0 freshness work: a vehicle whose position is four minutes old
must not yield a confident "6 min". Both panels render an arrival through **one shared formatter**,
so the delay, the position age and the one-stop caveat cannot appear on one and not the other; and
neither uses `st.success`, whose green box reads as certainty about an estimate.

## Testing

`eta.py` is pure, so each function is tested directly against fixed stop lists:

- `haversine_m` against a known coordinate pair
- `nearest_stop_index` including the `start_index` constraint that keeps answers ahead of the bus
- `estimate_delay_seconds` for on-time, late and early buses
- `compute_eta_seconds` for a normal case, zero delay, and a negative result meaning already-passed
- `arrivals_for_stops` grouping and sort order, a stop served by two routes, and a bus excluded
  because it has already passed
- `walking_minutes` at the documented pace *and* at a second pace, since every other assertion
  would pass a function that ignored the argument and hardcoded 80
- the `25:30:00` rollover, asserted end-to-end from `get_trip_stops` through `compute_eta_seconds`
- the day-cancellation invariant: several different (even wrong) service-day epochs must yield an
  identical arrival. This is what makes the module survive a frequency-based feed
- a frequency-based trip yields `delay_seconds is None` while its arrival is unchanged; a
  non-frequency trip still yields a number
- a looping trip lists one vehicle once per stop, at its earliest visit

`get_trip_stops` and `get_stops_near` are tested against a small synthetic GTFS ZIP built inside
the test, not the network, so the suite stays offline and deterministic.

The 97 tests existing when this was written must stay green. As built the suite stands at 153.

## Follow-ups (not in this version)

- **Ingest `startDate`/`startTime`** to make service-day resolution exact.
- **Shape projection** for sub-stop precision, once cumulative distance is computed.
- **Observed travel times** mined from the project's own position history, replacing scheduled
  segment times where enough observations exist.
- **Walking directions** to the chosen stop, rather than a straight-line distance and a pace
  constant.
