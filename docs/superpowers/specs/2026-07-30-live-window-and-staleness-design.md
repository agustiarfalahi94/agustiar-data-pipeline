# Design: Live Window Anchoring + Vehicle Staleness Tiers

**Date:** 2026-07-30
**Target version:** 2.4.0 (new user-visible behaviour → minor bump)
**Status:** Approved design, pending implementation plan

---

## Problem

Regions intermittently vanish from the Live Map. Observed live: the header read
*"Total Active Buses 101 · Regions Monitored 7"* while Rapid Bus KL — the largest network —
showed **"No valid data for Rapid Bus KL"**. A refresh later it returned, then vanished again.

### Root cause

`get_live_data_optimized` anchors its freshness window to the newest row **anywhere in the
table**, not to wall-clock time:

```sql
SELECT MAX(timestamp) FROM live_buses     -- global max across all 14 regions
WHERE timestamp >= max_timestamp - 60     -- 60s window anchored to it
```

Ingestion accepts timestamps up to `DATA_FUTURE_TOLERANCE = 300` seconds **into the future**. So
a single future-dated vehicle, in any region, drags the anchor forward:

- one vehicle reports `now + 250`
- `max_timestamp` becomes `now + 250`
- the window becomes `[now + 190, now + 250]`
- **every** vehicle reporting an honest "now" falls outside it and disappears

This is why the failure is intermittent and hits whole regions at once: it depends on whether some
feed happened to publish a future-dated timestamp that cycle.

### Evidence

Sampling five feeds directly (2026-07-30):

| Feed | Newest timestamp | Note |
|---|---|---|
| Rapid Bus MRT Feeder | `1886017556` | **≈ year 2029** — ~3.2 years in the future |
| Rapid Bus KL | `1785421208` | now − 14s |
| myBAS Johor | `1785421202` | now − 20s |
| myBAS Kuching | `1785421225` | now **+ 3s** |
| myBAS Melaka | `1785421232` | now **+ 10s** |

The MRT Feeder outlier is rejected at ingestion (beyond the 300s tolerance) so it never reaches
the table. But Kuching and Melaka were *already* mildly future-dated, confirming that future-dated
timestamps are routine in these feeds, not exotic. Anything between +60s and +300s is accepted and
will shift the window.

### Why widening alone does not fix it

Stretching the window to 300s while keeping the global-max anchor still breaks: a feed reporting
`now + 300` yields a window of `[now, now + 300]`, which excludes a bus that reported 10 seconds
ago. **The anchor is the defect; the width is a separate concern.**

## Non-goals

- **No change to `DATA_FUTURE_TOLERANCE`.** Once ages are clamped (below), a future-dated row can
  no longer distort the window, so tightening the tolerance is not required to fix this. If these
  feeds turn out to publish future timestamps routinely enough to matter, that is a separate
  change with its own evidence.
- **No ETA or arrival prediction.** Requested separately; see Follow-ups.
- **No change to the ingestion path.** This is a read-path fix.

---

## Part 1 — Anchor the window to now

The window is computed from wall-clock time, and each vehicle's age is clamped at zero so a
mildly-future timestamp reads as fresh rather than being dropped:

```
age_seconds = max(0, now - vehicle_timestamp)
include if age_seconds <= LIVE_STALE_SECONDS
```

Anchoring to `now` makes the query structurally immune to future-dated rows: no other region's
clock can move the window. This alone fixes the vanishing-region bug; Parts 2 and 3 are the
enhancement layered on top.

Two new config knobs alongside the existing `DATA_MAX_AGE` / `DATA_FUTURE_TOLERANCE`:

| Knob | Default | Meaning |
|---|---|---|
| `LIVE_FRESH_SECONDS` | `60` | At or under this age, a vehicle is *fresh* |
| `LIVE_STALE_SECONDS` | `300` | Beyond this age, a vehicle is hidden from the map |

Deduplication is unchanged — `ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY timestamp DESC)`
already yields one row per vehicle and is unrelated to the window.

## Part 2 — Three freshness tiers

| Age | Treatment |
|---|---|
| ≤ 60s | Solid arrow, exactly as today |
| 60s – 5min | **Dimmed** arrow; tooltip gains *"Last update 22:14:02 (3m ago)"* |
| > 5min | Hidden from the map, but counted in a caption |

Dimming reuses the existing arrow colour at reduced opacity, so a stale vehicle reads as
present-but-uncertain rather than as a different category of thing.

Nothing disappears silently: when any vehicle exceeds the stale bound, the map carries a caption —
*"4 vehicles hidden — no update in over 5 min"*. That preserves the intent of "don't remove it
entirely" without letting a 7-day retention window fill the map with buses parked at depots
overnight.

## Part 3 — Metrics stay honest

`get_live_data_optimized`'s metrics dict gains a `stale` count. `total` continues to mean
*reporting right now* (≤ 60s), so the headline number stays comparable to what it showed before
this change. The Live Map header gains a fourth metric, **Stale**, beside the existing three.

A vehicle counted as stale is on the map; a vehicle counted in the hidden caption is not. The two
counts never overlap.

## Part 4 — Where the logic lives

Freshness classification is a pure function in `data_processor`, mirroring how `filter_by_route`
was done in 2.3.0:

`data_processor.classify_freshness(df, now, fresh_seconds, stale_seconds) -> DataFrame`

Returns the frame with two added columns: `age_seconds` (integer, clamped at 0) and `freshness`
(one of `fresh` / `stale`). Rows beyond `stale_seconds` are **not** dropped here — the caller
needs their count for the caption — so the function also marks them `hidden`.

This keeps the split clean: `db.py` widens the query, `data_processor` decides tiers, `live_map.py`
renders. Each is testable without the others; only the renderer needs Streamlit.

## Interaction with route search

Route search (2.3.0) filters on `route_display` and is applied after freshness classification, so
searching `T580` shows its fresh **and** stale buses. That is the desired behaviour for
"should I wait or walk?" — a bus that reported 90 seconds ago is still useful, and now the map
says so rather than implying it is current.

---

## Testing

- `classify_freshness` — boundary cases at exactly 60s and exactly 300s; a future-dated timestamp
  clamping to age 0 and classifying as fresh; an empty frame; a frame missing `timestamp`.
- The window query — a fixture containing one future-dated row proves that row no longer shifts
  the window or excludes normally-timestamped vehicles in other regions. This is the regression
  test for the reported bug and must fail against the current `MAX(timestamp)` implementation.
- Metrics — `total` counts only fresh; `stale` counts only the middle tier; hidden rows appear in
  neither.
- The existing 63 tests must stay green.

## Follow-ups (not in this version)

- **Bus ETA and nearest stops** — requested separately and scoped as its own project. Feasible
  from data already downloaded (`shapes.txt`, `stops.txt`, live positions, browser GPS), but the
  provider publishes **no trip updates**, so arrival predictions must be derived locally. That
  needs distance measured *along the route shape* rather than straight-line, a way to determine
  which direction a vehicle is travelling along that shape, and smoothing — instantaneous speed is
  0 km/h at a red light. The strongest available approach is deriving observed segment travel
  times from the project's own 7-day position history. It will be an estimate and must present as
  one.
