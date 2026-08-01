# Design: Which Routes Serve This Stop, and Where Each One Goes

**Date:** 2026-08-01
**Target version:** 2.8.0 (new capability → minor bump)
**Status:** Approved design, pending implementation plan

---

## Problem

A family friend was waiting at Stesen LRT Awan Besar to reach Green Avenue Condominium, Bukit
Jalil. The app could not answer the only question that mattered — *which of these buses drops me
there, and how long does it take?* — and the obvious guess from the stop names is wrong in a way
that costs half an hour.

### What the app showed

Tapping KL2324 LRT AWAN BESAR listed three arrivals: `PAVILION BUKIT JALIL (PAVBJ)`, `652`, `651`.
Reported verbatim: *"i cannot tell which bus can drop the passenger at green avenue. i can tell
lili's friend to ride bus 652 let say, but if the bus cannot drop passenger in green avenue condo,
they are in trouble."*

### What the timetable actually says

Queried against the live Rapid Bus KL feed (137 routes, 2,102 trips, 4,053 stops, 87,935
stop_times) on 2026-08-01:

- **KL2324 LRT AWAN BESAR** (`stop_id` 1006170) is served by **four** routes: `T580`, `651`, `652`,
  `PAVILION BUKIT JALIL (PAVBJ)`.
- **KL1743 GREEN AVENUE CONDOMINIUM** (`stop_id` 1006172) is served by **exactly one**: `T580`.
- So `651`, `652` and `PAVBJ` do not go there at all. Three of the four routes on screen were
  wrong answers, and the one right answer was the one not shown.

`T580` was absent because the panel lists **live buses currently en route**, not routes that serve
the stop. With no `T580` vehicle running toward the stop at that moment, the only usable route
vanished. The user observed exactly this: *"sometimes between the refresh … the bus T580 is
showing."*

### The trap underneath

`T580` (`route_id` T5800) is a **loop**: one direction, 35 stops, 40 minutes end to end, returning
to where it started.

| Seq | Stop | From LRT Awan Besar |
|---|---|---|
| 1 | KL2324 LRT AWAN BESAR | boarding point |
| **2** | **KL1291 KM1 BUKIT JALIL** | **+1 min** |
| 33 | KL2019 ANJUNG HIJAU GREENFIELDS | +28 min |
| **34** | **KL1743 GREEN AVENUE CONDOMINIUM** | **+32 min** |
| 35 | KL2324 LRT AWAN BESAR | +40 min |

Both stops sit ~60 m apart on the ground, on opposite sides of Jalan Jalil Perkasa 1 — they are the
outbound and return legs of the same loop. A passenger who boards at Awan Besar and waits for the
stop **named after the building** rides 32 minutes instead of 1. `T580` runs on a headway of about
50 minutes (07:30–23:20; 40 minutes 06:00–06:40), so the mistake is not cheaply recovered.

The correct instruction — *get off at the second stop, KM1 Bukit Jalil* — is unavailable in the app
and unguessable from the names, because the useful stop is named after a shopping mall.

### Why this generalises

- **100 of the 136 routes with timetable data are loops.** This is the norm in Rapid KL, not an
  edge case, so the same trap exists network-wide.
- Landmark naming is endemic and has bitten this project repeatedly: `PAVILION BUKIT JALIL (PAVBJ)`
  is a route named after a mall, fixed in 2.6.0 by labelling the route line; `KL1291 KM1 BUKIT
  JALIL` is the stop for a condominium, named after a different mall.

---

## Decisions

Three taken with the project owner before design; one taken from measurement.

**The tapped-stop panel is the only surface.** Rejected: adding this to every stop in *Arrivals
near you* (that panel lists up to five stops and would grow long on a phone), and a separate route
browser (answers "where does T580 go" but not "what can I catch from here"). Tapping is already the
focused *tell me about this stop* gesture.

**A route opens to its full stop sequence with journey times measured from the tapped stop.**
Rejected: onward-only stops (still 34 rows on a loop, and it hides the fact that the route returns)
and a stops-at search box (useful on long routes, but a second interaction to build when the list
alone already answers the question).

**Stops near the user are marked in the sequence.** Rejected: a plain list. In the reported case
both KM1 Bukit Jalil (+1 min) and Green Avenue Condominium (+32 min) are near the user's building;
seeing both marked makes the right choice obvious without trusting the stop name — which is
precisely the knowledge that failed.

**Every stop pattern serving the tapped stop is shown separately, each distinctly labelled.**
Taken from measurement rather than preference: 98 of 136 routes run a single pattern, 37 run two,
one runs three, and 34 routes publish two `direction_id`s. Picking one pattern silently would be
wrong for 28% of routes, in a feature whose entire purpose is to stop the app from confidently
misdirecting someone.

---

## Part 1 — Data layer (`src/utils/gtfs_static.py`)

Two indexes, populated inside the **existing** `_build_trip_index` pass:

```python
_STOP_ROUTES_INDEX[agency_slug] = {stop_id: {route_id, ...}}
_ROUTE_TRIPS_INDEX[agency_slug] = {route_id: [trip_id, ...]}
```

`_build_trip_index` already parses `stop_times.txt` into `trip_stops[trip_id]` as an ordered list
of stop entries carrying `arrival_seconds`, and already reads `trips.txt` for headsigns. Both new
indexes derive from data that pass already holds, so this adds **no second parse** of an
87,935-row file and inherits the existing ZIP-mtime invalidation and the existing
"never cache a failure" rule.

`trips.txt` currently supplies only `trip_headsign`; the same loop also reads `route_id`.

### Public functions

```python
get_routes_at_stop(agency_slug, stop_id)
    -> [{'route_id': str, 'short': str, 'long': str}, ...]      # sorted by short name

get_route_patterns(agency_slug, route_id, stop_id=None)
    -> [{'trip_id': str, 'stops': [stop_entry, ...]}, ...]
```

`get_route_patterns` returns **distinct** sequences, de-duplicated on the tuple of their stop ids,
and filtered to those containing `stop_id` when one is given. Each `stop_entry` is what
`get_trip_stops` already returns — `stop_id`, `stop_name`, `stop_lat`, `stop_lon`,
`arrival_seconds` — so journey times need no new parsing.

The representative `trip_id` is carried alongside so the caller can reach the two facts that live
per trip rather than per pattern: `get_trip_headsign` for the operator's own destination wording,
and `is_frequency_based` for whether the pattern runs to a headway. Without it the panel would have
to re-derive both.

Both return `[]` on any missing or malformed data, matching `get_stops_near` and `get_trip_stops`.
Neither raises into a render.

---

## Part 2 — `src/utils/route_view.py` (new, pure)

`live_map.py` is 1,311 lines. The logic does not go there.

```python
build_stop_rows(stops, tapped_stop_id, nearby_by_id=None)
    -> [{'seq': int, 'stop_id': str, 'stop_name': str,
         'offset_minutes': int | None, 'is_tapped': bool,
         'near': {'distance_m': float, 'walk_label': str} | None}, ...]

pattern_label(stops, headsign='') -> str
pattern_titles(patterns, headsigns) -> [str]      # unique, same order
```

No Streamlit, no GTFS, no I/O — a pure function over dicts, so loops, duplicate stops and
multi-pattern routes are all unit-testable without a browser.

**The loop rule.** `offset_minutes` is measured from the tapped stop's **first** occurrence in the
sequence. On T580 from Awan Besar that gives +1 for KM1 Bukit Jalil, +32 for Green Avenue
Condominium, and +40 for the returning row. The duplicated final row is kept rather than trimmed:
it is what makes the loop legible instead of merely puzzling.

`is_tapped` is set on **every** occurrence of the tapped stop, so a loop shows both ends marked.

**Negative offsets are expected and meaningful.** When the tapped stop sits mid-route, every stop
before it has already been passed by a bus travelling this pattern, and its offset is negative.
Those rows render as `N min earlier` — never as `+-N min`, and never suppressed, because a rider
needs to see where the route came from to know they are facing the right way. Only a missing
`arrival_seconds`, or a pattern that never calls at the tapped stop, produces no offset at all.

`nearby_by_id` is `{stop_id: {'distance_m', 'walk_label'}}` supplied by the caller. The formatter
neither computes distances nor calls a routing API; it only annotates.

---

## Part 3 — The panel (`src/app_pages/live_map.py`)

The tapped-stop panel gains:

1. A `Serves: T580 · 651 · 652 · PAVBJ` line, from `get_routes_at_stop`, listing every route
   whether or not it has a live vehicle.
2. One collapsed `st.expander` per route pattern, headed with the route, where the pattern goes,
   the stop count and its end-to-end running time. Collapsed means a 35-row list costs nothing
   until opened.

   **Labelling a pattern.** "By its terminus" fails on exactly the routes this feature exists for:
   T580's last stop *is* its first, so a terminus label would read `T580 → LRT Awan Besar` and
   explain nothing. The rule is therefore:

   - a pattern whose first and last stop are the same is labelled **`loop from <first stop>`**;
   - otherwise it is labelled **`to <last stop>`**;
   - `trip_headsign` is preferred over both when the feed publishes a non-empty one, since it is
     the operator's own wording — falling back to the rule above when it is blank, which is common
     in these feeds;
   - if two patterns of the same route still carry identical labels, the stop count disambiguates
     them (`loop from LRT Awan Besar · 35 stops` versus `· 31 stops`), and a numbered suffix
     settles any remaining tie. **Two patterns must never render as two visually identical
     expanders** — a rider could not tell which one they had opened. Uniqueness is guaranteed by
     construction in `route_view.pattern_titles`, not left to chance in the data.
3. Inside each expander, the rows from `build_stop_rows`.

**Routes with no live bus are listed and say so.** They are not omitted. That omission is the
reported failure and the reason this feature exists.

**Near-you marks cost no extra API calls.** They reuse `_nearby_stops` — the 40 stops already
scanned for the panel — and the `walk_times` result already computed for it. `walking`'s cache is
keyed per stop, so annotating is free after the panel's own lookup.

`live_map.py` grows by roughly twenty lines of rendering. All new logic lives in the two modules
above.

---

## Part 4 — Honesty constraints

This feature is more prone to overclaiming than anything shipped so far, because it looks like
route planning and is not.

- **Journey times come from the published timetable, not from live positions.** Labelled with the
  same caveat the arrivals panels already carry.
- **A headway route never shows a fabricated departure time.** `T580` shows "about every 50 min".
  A trip with no published start time has none invented for it, consistent with the existing rule
  that an unknowable delay renders as nothing.
- **Multiple patterns are never merged.** Each carries a distinct label. A route may legitimately
  appear twice in one panel; that is the honest rendering, not a bug.
- **A near-you mark states a distance.** It never says "get off here", which is an instruction the
  data cannot justify — the user may be travelling somewhere else entirely.
- **This is not a route planner.** It answers "does this bus stop at X, and how far along is it".
  It does not search for journeys, compare options, or suggest interchanges. That was explicitly
  ruled out of the roadmap and remains out.

---

## Testing

Unit tests, no network and no browser:

- **`route_view.build_stop_rows`:** tapped stop at the start of a loop (both occurrences marked,
  +0 and +40); tapped stop mid-route; near-you annotation applied only to supplied ids; a pattern
  where the tapped stop is absent; an empty pattern; a malformed entry missing `arrival_seconds`.
- **`get_routes_at_stop`:** a stop with one route, a stop with several, a stop with none, an
  unknown stop id, and an agency with no timetable.
- **`get_route_patterns`:** two trips sharing a pattern collapse to one; two genuinely different
  patterns both returned; filtering by `stop_id` excludes patterns that do not contain it;
  unknown route id returns `[]`.
- **Pattern labelling:** a loop is labelled `loop from <first stop>` and not `to <last stop>`; a
  non-loop is labelled by its last stop; a published `trip_headsign` wins over both; a blank
  headsign falls back; two same-route patterns never produce identical labels.
- **Index construction:** the new indexes are populated by the same pass as the existing ones, and
  a failed build stores neither — the existing "never cache a failure" invariant must still hold.
- **Panel rendering:** the `Serves:` line lists a route with no live vehicle; an expander is
  produced per pattern.

The suite must pass under both default and Arrow-backed pandas string dtypes, and must make no
network calls — the `PageTestReachedNetwork` guard added in 2.7.1 stays in force.

---

## Documentation

Per the standing project rule, updated at the time of the change:

- **README** — the tapped-stop panel section gains the serving-routes list and the stop sequence;
  state plainly that journey times are timetable-derived and that this is not a journey planner.
- **CHANGELOG** — 2.8.0, carrying the measured evidence: four routes at Awan Besar of which one
  reaches Green Avenue, the +1 versus +32 minute loop trap, and 100 of 136 routes being loops.
- **pyproject.toml** — 2.8.0.
- **requirements.txt / requirements-dev.txt** — verified unchanged; no dependency is added.

---

## Out of scope

- A "does it stop at…" search box within a route. The list with journey times answers the question;
  revisit if long routes prove tiresome in use.
- Route aliases (the vehicle branded `GOKL14` is published as `PAVILION BUKIT JALIL (PAVBJ)`; no
  `GOKL` route exists anywhere in the feed). A hand-maintained alias table is a separate decision
  with its own staleness risk.
- Journey planning between two arbitrary points. Explicitly rejected from the roadmap.
- Marking near-you stops from a saved home location rather than the current GPS fix.
