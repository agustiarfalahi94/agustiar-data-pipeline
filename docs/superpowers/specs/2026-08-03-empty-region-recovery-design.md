# Design: The App Stops Hiding Things When a Region Is Empty

**Date:** 2026-08-03
**Target version:** 2.10.0 (behaviour change + new lookup → minor bump)
**Status:** Approved design, pending implementation plan

---

## Problem

Rapid Bus KL's realtime feed went quiet — confirmed upstream on 2026-08-03 at 12:49 on a Monday:
`rapid-bus-kl` returned HTTP 200 with a 15-byte body and zero entities, while
`rapid-bus-mrtfeeder` returned 102 vehicles and `rapid-bus-penang` 147 in the same second. The
slug is still correct: every plausible alternative returns 404.

The feed being empty is Prasarana's business. What the app did with that emptiness is ours, and it
was wrong in four connected ways.

### 1. An empty vehicle frame deletes the map

`live_map.py` returns early in three places before the map is ever drawn:

| Line | Condition |
|---|---|
| 276 | no rows at all in the window |
| 448 | the selected region reported nothing, or nothing with usable coordinates |
| 462 | every vehicle in the region is older than the drawn window |

Each `return` takes the map with it — and with the map go the user's own location marker, the
nearby stop rings, and the tapped-stop panel that 2.8.0 built. Reported verbatim: *"i cannot test
to tap a bus stop in rapid KL area because the map disappear entirely as the data is empty."*

**Stops come from the published timetable.** They do not depend on a live vehicle existing. Hiding
them because no bus is moving discards data the app already has, at exactly the moment the user is
trying to work out what is going on.

### 2. The dead end offers no way out

Standing in Bukit Jalil with **KTM Berhad** selected, the panel said only:

> No stops found within 800 m of you in KTM Berhad.

True, and useless. Rapid Bus KL has 15 stops within 800 m of that same spot. The app knew that and
did not say it. Reported verbatim: *"which is not a solution."*

### 3. 800 m is drawn too tight

The user's own judgement: *"maybe user can still tolerate that long walk."* A rider willing to walk
a kilometre is told nothing exists, because the radius was chosen conservatively and never revisited.

### 4. Selecting a region you are not in silently empties the stop list

*"if my location is not inside the selected region, the bus stop near me also disappear."* Same
root cause as (1) and (2): the app scopes stops to the selected region and then says nothing when
that scope is empty.

### And one configuration trap of our own making

The owner pasted a working ORS key into Streamlit Secrets as:

```toml
api_key = 'eyJ...'
```

`_ors_api_key` reads `st.secrets['routing']['api_key']` (`live_map.py:52`), so a flat key is
invisible to it, and the deliberately broad `except` swallows the miss. A missing section header
therefore fails **silently and indistinguishably from having no key at all** — the walk times stay
`(estimated)` and nothing says why.

---

## Decisions

Taken with the project owner.

**The map renders whenever the user's location is known.** Rejected: drawing it only when stops are
nearby. The behaviour would still change based on data the user cannot see or predict, which is the
complaint. A "no vehicles reporting" note moves above the map rather than replacing it.

**The radius widens progressively: 800 m, then 1500 m if nothing was found.** Rejected: a single
1000 m radius (simpler, but tells a rider with a stop at 1100 m the same unhelpful nothing) and a
user-facing slider (a setting on a phone-first UI that most people will never touch, and it does
not fix the dead end on its own). The panel always names the radius actually applied, so the
message cannot drift from the number.

**When neither radius finds a stop, the app scans the other regions automatically.** Rejected: a
button (an extra tap at the moment the app has already failed to help) and scanning only
already-cached timetables (instant, but silent on a fresh deploy — precisely when a new user needs
it). It runs only at the dead end, behind a spinner, never on a normal render.

---

## Part 1 — The map survives an empty frame

The three early returns become a single flag rather than three exits.

```python
no_vehicles = df_map.empty        # after the hidden-vehicle filter
```

The warning each branch already writes is kept verbatim — the wording distinguishes "the region
reported nothing" from "it reported, but the coordinates were unusable" from "everything is too
old", and those are different causes with different fixes. Only the `return` goes.

Downstream, the vehicle-derived work is skipped when `no_vehicles`:

- the vehicles `ScatterplotLayer`, the arrow `PathLayer`, and the `tip_html` column they need
- the tapped-vehicle panel and its selection handling
- the "Showing N active vehicles" caption

What still renders:

- the user's location marker and accuracy circle
- the nearby-stop rings, from the timetable
- the tapped-stop panel, including `Serves:` and the stop sequences — none of which reads a live
  vehicle
- `Arrivals near you`, which correctly reports that no bus is en route

The deck is still created, so `selection` still exists and the stop-selection state machine — the
one that took two fix rounds in 2.7.0 — is untouched.

`df_live` being empty **network-wide** (line 276) keeps its own return: with no data at all there
is no region context, and the existing message already tells the user to refresh.

## Part 2 — Progressive radius

```python
NEARBY_STOP_RADIUS_M      = 800     # unchanged: the primary search
NEARBY_STOP_WIDE_RADIUS_M = 1500    # only when the primary finds nothing
```

`get_stops_near` is called at 800 m. If it returns nothing, it is called again at 1500 m and the
panel says so:

> Nothing within 800 m — showing stops up to 1500 m.

Every message that names a radius takes it from the radius actually used, never from a literal.
2.6.0 already fixed a version of this bug, where the copy and the applied number disagreed.

**This widens an existing inaccuracy and must be documented as such.** The radius is measured
straight-line. At 1500 m the gap between crow-flight and footpath is larger than at 800 m —
KL1291 sits 60 m away by crow and 634 m on foot. Routing corrects the walk time *displayed*; it
does not correct which stops are *selected*. A stop listed at 1400 m may be a 3 km walk.

## Part 3 — Which region should I be looking at?

New in `gtfs_static.py`:

```python
find_regions_with_stops_near(lat, lon, radius_m=1500, exclude_slug=None, limit=3)
    -> [{'region': str, 'count': int, 'nearest_m': float}, ...]     # nearest first
```

It walks `STATIC_API_SOURCES`, calls the existing `get_stops_near` per agency, and returns the
regions that have any, sorted by their closest stop. Any agency that raises or has no cached
timetable is skipped — a single unavailable feed must not deny the user the other twelve answers.

Called **only** when both radii found nothing, wrapped in `st.spinner`. A first run on a fresh
deploy downloads timetables it has not seen; they are then cached for 24 hours by the existing ZIP
cache.

The panel then reads:

```
No stops within 1500 m of you in KTM Berhad.

→ Rapid Bus KL — 15 stops, nearest ~152 m
→ Rapid Bus MRT Feeder — 3 stops, nearest ~1.2 km
Switch region above to see them.
```

When no region has stops nearby, it says that plainly rather than showing an empty list.

## Part 4 — The secrets lookup accepts both spellings

```python
st.secrets['routing']['api_key']    # documented form
st.secrets['api_key']               # flat form, accepted
```

The flat form is what a reader reaches for when copying a single line, and the failure mode is
invisible. Accepting both costs three lines and removes a trap that already cost the owner a day of
`(estimated)` walk times. The README continues to document the sectioned form as canonical.

## Part 5 — Route aliases

A rider reads `GOKL14` on the front of the bus. The feed publishes that route as
`PAVILION BUKIT JALIL (PAVBJ)`, `route_id` `S6060`. Searched across all 137 Rapid KL routes, **no
`GOKL` route exists anywhere in the data** — the connection lives only on the vehicle's livery.

```python
# gtfs_static.py — hand-maintained, not from any feed.
ROUTE_ALIASES = {'GOKL14': 'PAVILION BUKIT JALIL (PAVBJ)'}
```

### Where the alias is applied

A search currently reaches two independent matchers:

- `data_processor.filter_by_route(df, query)` — substring match against `route_display`, the
  resolved `"SHORT — Long"` string, which decides **which vehicles are drawn**
- `gtfs_static.region_has_route(agency_slug, query)` — decides **which message** is shown when the
  search finds nothing ("this route does not run in this region" versus "it runs here but nothing
  is reporting")

Resolving the alias inside either one would leave the other disagreeing with it. So resolution
happens **once, in `live_map`, before the query is used**:

```python
resolve_route_alias(query) -> (resolved_query, alias_source | None)
```

`resolved_query` is passed to both matchers; `alias_source` is non-`None` only when the alias table
did the work, and drives the disclosure line. `data_processor` stays unchanged and unaware of
aliases, which keeps it free of a `gtfs_static` import it has never needed.

**When a match comes through an alias the app says so** — *"GOKL14 is published as PAVILION BUKIT
JALIL (PAVBJ)"* — so a local guess is never presented as feed data. A query that matches a real
feed name is never rewritten: the table is consulted only when the query is one of its keys.

Documented as unofficial and hand-maintained, with the standing risk stated: route branding changes
and this table will not notice.

## Part 6 — Airflow leaves the backlog

Recorded as decided-against rather than deleted: it cannot run on Streamlit Cloud, needs a
server running 24/7, and the owner has decided not to pay for one. The reasoning is worth keeping
so it is not re-proposed.

## Part 7 — Documentation for riders

The README is written for someone reading the code. It needs a section written for someone using
the app:

- what each page answers, in one line
- what `(estimated)` on a walk time means, and how to make it go away
- why a region can show a green **Reliable** score and an empty map at the same time — feed health
  and service activity are different questions
- what to do when a region has no stops near you
- that journey times come from the timetable, not from live positions

Plus the cosmetic items deferred across the 2.7–2.9 reviews, swept in one pass.

---

## Testing

- **Map survival:** a region with zero vehicles still renders the deck, the user marker and the
  stop rings; the tapped-stop panel still opens; the correct one of the three warnings appears; no
  vehicle layer is built.
- **Progressive radius:** a location with stops at 900 m finds none at 800 m and reports the widened
  search; a location with stops at 200 m never widens; the wording names the radius used.
- **Region scan:** returns regions sorted by nearest stop; excludes the selected one; skips an
  agency whose timetable raises; returns `[]` when nothing is near; is not called when stops were
  found.
- **Secrets:** sectioned form read; flat form read; sectioned wins when both present; neither
  present returns `None` and makes no network call.
- **Aliases:** `GOKL14` matches the PAVBJ route; the disclosure text names both; an unknown query
  still misses; the alias table never overrides a real feed name.

No network calls in tests. Suite green under both pandas string dtypes.

## Documentation

README, CHANGELOG and `pyproject.toml` to **2.10.0**; `requirements.txt` and `requirements-dev.txt`
verified unchanged — no dependency is added.

## Out of scope

- Correcting *which* stops are selected using routed distance. The radius stays straight-line; only
  the quoted walk time is routed. Documented, not fixed.
- Auto-switching the region on the user's behalf. The app names the alternatives; the choice stays
  with the rider.
- Any further route aliases beyond `GOKL14` until someone reports one.
