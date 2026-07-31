# Design: Real Walk Times, Tappable Stops, Legible Arrival Rows

**Date:** 2026-08-01
**Target version:** 2.7.0 (new routing capability + new interaction → minor bump)
**Status:** Approved design, pending implementation plan

---

## Problem

2.6.0 made the arrivals panels legible and drew nearby stops on the map. Using it on the real
commute from Green Avenue Condominium, Bukit Jalil surfaced three follow-ups. Two were left open
by 2.6.0 by deliberate escalation; the third is new and is the significant one.

### 1. Walk times are wrong by a factor of ~2.5 — the significant one

Reported verbatim: *"in our app it says one of the bus stop is 3 minute walk, but when i click it,
directed to google maps, directions, walk, boom 8 minutes. second one says 7 min walk boom 16
minutes."*

| Stop | App | Google Maps | Implied ratio |
|---|---|---|---|
| A | 3 min | 8 min | ~2.7× |
| B | 7 min | 16 min | ~2.4× |

Two compounding causes:

- `gtfs_static.get_stops_near` measures **straight-line haversine distance** (`gtfs_static.py:516`).
  A pedestrian in Bukit Jalil walks around gated blocks, along highway frontage, and over
  pedestrian bridges. None of that is in a straight line.
- `eta.DEFAULT_PACE_M_PER_MIN = 80` (`eta.py:17`) is 4.8 km/h — a brisk, unobstructed walk with no
  crossings, no waiting, no stairs.

The error is not a constant. Bukit Jalil's ~2.5× reflects severed pedestrian connectivity; a
walkable grid such as George Town or central KL runs closer to 1.2×. **A single tuned multiplier
cannot serve both** — calibrating on Bukit Jalil would make George Town wrong in the other
direction. This is why the fix is real routing rather than a corrected constant.

The consequence is not cosmetic. The whole point of the arrivals panel is deciding *whether you can
make it*. A stop quoted at 3 minutes with a bus 6 minutes out reads as comfortable; at the true 8
minutes you have already missed it.

### 2. Nearby stops are drawn but inert

2.6.0 draws nearby stops as hollow rings. Tapping one does nothing and the map never says which
stop is which. The original design wanted a hover tooltip; that was escalated and **not
implemented**, for two stated reasons — hover does not exist on touch and this is a phone-first
app, and the map has a single shared tooltip template hard-coded to vehicle fields, so making
stops pickable risked degrading the working vehicle tooltip.

The user's counter-proposal, which supersedes the tooltip idea: *"i would like to tap one of the
bus stops in the map and i expect the page would be dragged down to 'arrivals near you' section and
shows the name of the bus stop we tap."* The intent — tap a ring, learn about that stop — is
adopted. The scroll mechanism is not; see Decisions.

### 3. `Arrivals near you` never received the legibility fix

2.6.0's design (Part 2, line 114) called for the labelled treatment on both panels. Only the
tapped-bus panel got it. `format_arrival` (`live_map.py:80-110`) still emits the run-on form on the
panel that is **expanded by default and seen first**:

```
PAVBJ → Pavilion Bukit Jalil · ~6 min · 2 min late
```

The ambiguity reported against the tapped panel applies here unchanged: `PAVILION BUKIT JALIL` is
both a route name and a shopping mall, and nothing marks which role it is playing.

---

## Decisions

Four decisions were taken with the project owner before design.

**Walk times use real routing, not a corrected constant.** Rejected: a tuned circuity multiplier
(cannot serve both Bukit Jalil and George Town from one number) and dropping the time in favour of
bare distance (least useful for the "can I make this bus" question the panel exists to answer).

**The routing provider is OpenRouteService with a registered free key.** Rejected: the public OSRM
demo server, whose usage policy excludes production apps and which carries no uptime guarantee —
unsuitable for a portfolio piece a reader may open months from now.

**A tapped stop answers under the map, not by scrolling.** Rejected: scrolling to `Arrivals near
you`, which Streamlit cannot do without a JavaScript injection that fights the auto-refresh rerun
and can overshoot on mobile; and pinning the stop to the top of that list, which still requires the
user to scroll to see the effect they asked to avoid. Answering in the slot the tapped-bus panel
already occupies keeps the answer where the user just tapped.

**Arrival rows are route-first and labelled.** Rejected: time-first (better for "what can I catch",
worse for "where is my T580", and the stated use case is the latter) and a compact bold-only form
(shortest, but leaves the PAVBJ ambiguity intact — bold does not tell you the first item is a route
name).

---

## Verified constraints

OpenRouteService free tier, checked 2026-08-01 rather than recalled:

- ~2,500 requests/day, 40,000/month, 40 concurrent
- Matrix: max 3,500 origin × destination pairs per request
- Directions: max 50 waypoints

Worst case here is 1 origin × 40 stops = **40 pairs in one request**, two orders of magnitude
inside the per-request ceiling and comfortably inside the daily one before caching. `requests` is
already a dependency, and the REST call is made directly — **no new package**, so
`requirements.txt` and `pyproject.toml` dependency lists are unchanged.

---

## Part 1 — `src/utils/walking.py` (new module)

One responsibility: given a location and a set of stops, return walking minutes.

```python
walk_times(user_lat, user_lon, stops, agency_slug, api_key=None)
    → {stop_id: {'minutes': int, 'distance_m': float, 'routed': bool}}
```

`stops` are the dicts `get_stops_near` already returns. `agency_slug` is carried solely to
namespace the cache — stop ids are unique within an agency but may collide across the fourteen
feeds. The module knows nothing about Streamlit, GTFS, or arrivals; it is unit-testable without a
network or a browser.

### Routed path

One POST to `https://api.openrouteservice.org/v2/matrix/foot-walking`, one origin, N destinations,
requesting both `distance` and `duration` metrics. `duration` is ORS's pedestrian estimate along
the real footpath network and is what the UI displays. Timeout **5 seconds** — this call sits
inside a page render and a slow dependency must not hold the map hostage.

### Fallback path

Any of: no key configured, network error, non-200, malformed body, timeout, quota exhaustion.
Every one collapses to the same behaviour — straight-line estimate, `routed=False`, and **never an
exception escaping into the render**.

```python
estimate_minutes(distance_m) = ceil(distance_m * DETOUR_FACTOR / FALLBACK_PACE_M_PER_MIN)
DETOUR_FACTOR = 1.35
FALLBACK_PACE_M_PER_MIN = 75    # 4.5 km/h, a real walk with crossings
```

This replaces today's `80` m/min crow-flight. On the reported stops it yields ~5 min (true 8) and
~10 min (true 16) — still short, and honestly so. It is a floor on wrongness, not a fix; the fix is
the routed path.

**Disposition of `eta.walking_minutes`.** It is superseded, not duplicated. Its two call sites
(`live_map.py:808` and `live_map.py:878`) move to `walk_times`, and the function itself is removed
along with `DEFAULT_PACE_M_PER_MIN`, so no second walking-pace constant survives in the codebase to
drift from this one. `eta.py` keeps `haversine_m`, which the fallback and the radius filter both
still need.

When no key is configured the module makes **zero network calls**. A fresh clone stays fast and
works offline.

### Cache

Module-level dict, following the `_TRIP_INDEX_MTIME` / `_ROUTE_PARTS_MTIME` pattern already in
`gtfs_static.py`. The repo uses no Streamlit caching and this introduces none.

- Key: `(snapped_lat, snapped_lon, agency_slug)` where coordinates are snapped to a **0.0005°
  grid (~55 m)**. GPS jitter while standing still resolves to the same cell, so auto-refresh costs
  nothing. A 55 m cell bounds the induced error at roughly one minute of walking — below the
  resolution the UI displays.
- TTL 24h. Footpaths do not move.
- Only `routed=True` results are cached. Caching a fallback would pin a degraded answer in place
  for a day after a transient blip.
- `_clear_caches()` for tests, mirroring the existing `_clear_indexes()`.

### What does not change

The 800 m radius selecting *which* stops count as nearby stays straight-line. Re-filtering on
routed distance would make stops appear and disappear as the API succeeded or failed, so "nearby"
stays a stable idea and only the quoted walk gets smarter. This is a deliberate limitation and is
documented as one.

---

## Part 2 — Tapping a stop

Three changes in `live_map.py`.

### Stops become pickable

The layer gains `id="nearby-stops"` and `pickable=True`.

### The shared tooltip is made layer-agnostic

This is what blocked the feature in 2.6.0. The Deck-level tooltip is a single HTML template
interpolating `{vehicle_id}`, `{route_display}`, `{speed_display}`, `{bearing_display}`,
`{last_report_display}` (`live_map.py:661`), and pydeck renders unmatched keys literally — so a
stop hover would show raw `{vehicle_id}` text.

Fix: both datasets gain a **`tip_html`** column and the template collapses to `{"html":
"{tip_html}"}`. Vehicles render exactly the markup they render today; stops render their name.
No unmatched field is reachable from either layer, and the working vehicle tooltip is preserved
rather than risked.

### Selection and the panel

Selection reads `objects.get("nearby-stops")` alongside the existing `objects.get("vehicles")`,
with the same defensive coercion and the same broad `except` guarding an unexpected payload shape.
The tapped stop id is held in `st.session_state.selected_stop_id` and the stop is re-resolved from
the current frame each render, so it survives auto-refresh exactly as the tapped vehicle does.

**Last tap wins.** Tapping a bus clears the stop selection and tapping a stop clears the bus, so
the slot below the map never holds two competing answers. A `Clear stop selection` button mirrors
the existing bus-clear, including the `deck_generation` bump that fixed the stale-payload bug —
without it, clearing appears to do nothing when the deck spec is unchanged between renders.

Panel content:

```
📍 KL2324 LRT AWAN BESAR
~640 m · ~9 min walk

Route T580 → Awan Besar · arrives ~6 min
Route 583 → Bandar Malaysia · arrives ~14 min

[ Clear stop selection ]
```

Arrivals for the tapped stop reuse the existing per-stop arrival computation. A stop with nothing
inbound says so in the wording 2.6.0 established, including the route-search qualifier when a
search is narrowing the frame.

---

## Part 3 — `format_arrival` becomes route-first and labelled

```
Route PAVBJ → Pavilion Bukit Jalil · arrives ~6 min · 2 min late · position 2 min old
```

- `Route` is unconditional. That single word resolves the mall-versus-route ambiguity, and it is
  the reason the compact bold-only variant was rejected.
- One logical line, wrapping only on a narrow viewport — not stacked labels. The tapped-bus panel
  keeps its labelled-lines shape, because there the bus is the subject and the stop is one fact
  among several; here the stop is already the heading.
- Facts carried are identical to the tapped-bus panel: route, destination, arrival, lateness,
  position age, and the one-stop caveat. The two panels have drifted apart twice already; the
  docstring records why they must not.
- Inapplicable segments are **omitted, not zeroed**. A delay of `None` means "runs to a headway,
  not to the clock" and renders as nothing at all — never as `0`, never as "on time".

### Walk-time provenance in the UI

Routed results render `~9 min walk`. Fallback results render `~5 min walk (estimated)`.

The suffix is deliberate and consistent with the standing rule that the app never claims more than
the code supports. It is the one element flagged to the owner as most likely to be unwanted visual
noise, and it was kept.

---

## Testing

Unit tests, no network and no browser:

- **Cache:** miss populates, hit avoids the call, jitter inside one 55 m cell hits, movement across
  cells misses, TTL expiry re-requests, fallback results are not cached.
- **Failure paths, each asserted separately:** absent key, connection error, non-200, malformed
  body, timeout. Each returns estimates with `routed=False` and raises nothing.
- **No key ⇒ zero network calls**, asserted on a mock that fails the test if called.
- **Request shape:** one request for N stops, correct origin/destination ordering, both metrics
  requested — ordering matters because results map back to stops positionally.
- **`format_arrival`:** the `Route` prefix present, missing headsign, `delay=None` rendering as
  nothing, `delay=0` likewise, stale position, route-search qualifier.
- **Stop selection:** payload round-trip, last-tap-wins in both directions, clear button, and a
  non-string id not reaching a pandas comparison.
- **Tooltip:** `tip_html` present on both layer datasets and the template referencing no other key.

Two existing tests cover the removed function —
`test_eta.py:46 test_walking_minutes_rounds_up_and_has_a_floor` and
`test_eta.py:53 test_walking_minutes_honours_a_non_default_pace`. They **migrate** to
`estimate_minutes` with the new constants rather than being deleted: the rounding-up rule and the
one-minute floor are still the intended behaviour and must keep their coverage. The
non-default-pace test drops its parameter, since the pace is no longer a caller's choice.

The suite must pass under both default and Arrow-backed pandas string dtypes, as the existing 182
tests do.

---

## Documentation

Per the standing project rule, updated at the time of the change:

- **README** — `ORS_API_KEY` in the config table and the Streamlit Secrets TOML block; a
  troubleshooting note that walk times degrade to estimates without a key; the straight-line radius
  limitation stated plainly.
- **CHANGELOG** — 2.7.0.
- **pyproject.toml** — 2.7.0.
- **requirements.txt / requirements-dev.txt** — verified unchanged and consistent with
  `pyproject.toml`; no dependency is added.

---

## Out of scope

- Learning each region's detour factor from successful routed results to improve the fallback.
  Attractive, and YAGNI until the fallback is shown to matter in practice.
- Routed distance as the nearby-stop filter. Rejected above.
- Turn-by-turn walking directions. The Google Maps deep link already added in 2.6.0 covers this.
