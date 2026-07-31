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

### Live measurement, 2026-08-01

The premise was tested against the real API before planning, from KL1743 GREEN AVENUE
CONDOMINIUM (3.05866, 101.67398) across all 15 stops within 800 m. HTTP 200; Malaysian footpath
coverage in OSM is sufficient, with no null entries.

| Stop | Straight | Routed | App today | Routed |
|---|---|---|---|---|
| KL1291 KM1 BUKIT JALIL | 60 m | 634 m | 1 min | ~10 min |
| KL2019 ANJUNG HIJAU GREENFIELDS | 332 m | 344 m | 5 min | ~5 min |
| KL1289 TAMAN ESPLANADE | 485 m | 957 m | 7 min | ~15 min |
| KL2324 LRT AWAN BESAR | 585 m | 864 m | 8 min | ~13 min |

**Circuity across the 15: min 1.04, median 1.62, max 10.60.**

Two conclusions, both load-bearing:

1. A spread of 1.04 to 10.60 *inside one neighbourhood* rules out any tuned multiplier. This is
   the evidence for routing, and it is stronger than the two hand-collected data points that
   prompted the work.
2. KL1291 is the failure the current code cannot see. A stop 60 m away across an uncrossable
   barrier is quoted as a one-minute walk. A user told a bus is three minutes out would believe
   they had time; the walk is ten.

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

**The caller resolves the key; the module only receives it.** `walk_times` never reads
configuration. This keeps the module Streamlit-free and makes every test a pure function call.

This matters more than it looks. `src/config.py` is gitignored, so it does not exist on Streamlit
Cloud — and **no code in the repository currently reads `st.secrets`**, despite the README
documenting it as the cloud configuration mechanism (a past refactor replaced those reads with
hardcoded defaults). A key placed in Secrets today would be silently ignored. `live_map.py`, which
already imports Streamlit, therefore gains:

```python
def _ors_api_key():
    """config.py for local dev, Streamlit Secrets for cloud. None when unset."""
    key = getattr(_config, 'ORS_API_KEY', None)
    if key:
        return key
    try:
        return st.secrets['routing']['api_key'] or None
    except Exception:
        return None
```

The broad `except` is deliberate: Streamlit raises varied exception types when no secrets file
exists at all, which is the normal case for a fresh clone, and a missing optional key must never
break a render.

### Routed path

One POST to `https://api.openrouteservice.org/v2/matrix/foot-walking`, one origin, N destinations,
requesting the `distance` metric. Timeout **5 seconds** — this call sits inside a page render and
a slow dependency must not hold the map hostage.

**ORS supplies the distance; the pace stays ours.** The obvious approach — display ORS's own
`duration` — was tested against the live API and rejected. On the reported Stop B, ORS returns a
correct routed distance of 957 m but a duration of 11 minutes, implying 5.2 km/h; Google says 16
minutes, implying ~3.6 km/h. ORS models the path, not the crossings, waiting, stairs, or the pace
of a person who is not in a hurry. Taking ORS's distance at our own pace yields ~15 minutes against
Google's 16.

```python
WALK_PACE_M_PER_MIN = 67    # 4.0 km/h, matching observed Google walking estimates
minutes = max(1, ceil(routed_distance_m / WALK_PACE_M_PER_MIN))
```

One constant governs walking speed everywhere, routed or fallback, so the two paths cannot drift
into quoting different speeds for the same walk.

### Fallback path

Any of: no key configured, network error, non-200, malformed body, timeout, quota exhaustion.
Every one collapses to the same behaviour — straight-line estimate, `routed=False`, and **never an
exception escaping into the render**.

```python
estimate_minutes(distance_m) = ceil(distance_m * DETOUR_FACTOR / WALK_PACE_M_PER_MIN)
DETOUR_FACTOR = 1.4     # measured median 1.62 in Bukit Jalil; walkable grids run ~1.2
```

The factor is a deliberate compromise between the two regimes, not a fit to either. Against the
measured stops it turns 585 m into 13 minutes where routing says 13, and 485 m into 11 where
routing says 15 — a large improvement on today's 8 and 7, and still short.

**The fallback cannot detect a severed connection.** KL1291 sits 60 m away and 634 m by foot; no
multiplier applied to 60 m will ever yield ten minutes. This is the ceiling on what any
straight-line estimate can do, and precisely why the fallback is a degraded mode rather than the
design.

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

- Key: `(snapped_lat, snapped_lon, agency_slug, stop_id)` where coordinates are snapped to a
  **0.0005° grid (~55 m)**. GPS jitter while standing still resolves to the same cell, so
  auto-refresh costs nothing. A 55 m cell bounds the induced error at roughly one minute of
  walking — below the resolution the UI displays.
- **Keyed per stop, not per stop set.** Two different sets are looked up from the same location:
  the nearby stops, and the trip stops of a tapped bus. A set-keyed cache would answer one from
  the other's entry and leave every stop it had not seen on the fallback indefinitely. Per-stop
  keying also means a request asks only for the stops not already known.
- TTL 24h. Footpaths do not move.
- Only routed results are cached. Caching a fallback would pin a degraded answer in place for a
  day after a transient blip.
- `_clear_cache()` for tests, mirroring the existing `_clear_indexes()`.

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
- **Request shape:** one request for N stops, correct origin/destination ordering, `distance`
  requested — ordering matters because results map back to stops positionally.
- **Null distances:** ORS returns `null` for an unreachable destination. That stop falls back to
  its straight-line estimate individually, without discarding the rest of the response.
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
