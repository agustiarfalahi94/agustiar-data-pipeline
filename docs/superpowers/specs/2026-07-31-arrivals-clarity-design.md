# Design: Making Arrivals Legible

**Date:** 2026-07-31
**Target version:** 2.6.0 (behaviour change + new map layer → minor bump)
**Status:** Approved design, pending implementation plan

---

## Problem

2.5.0 shipped stop-centric arrivals. Using it on a real commute in Bukit Jalil surfaced three
problems, reported with a screenshot. All three are about the feature failing to communicate, and
one of them hides working data behind a misleading message.

### 1. The tapped-bus line is unreadable

Shipped output:

> PAVILION BUKIT JALIL (PAVBJ) — Stesen LRT Awan Besar ~ Pavilion Bukit Jalil · **~19 min** ·
> position 1 min old, so less certain at **KL2324 LRT AWAN BESAR** — ~517 m from you (~7 min walk).

Everything is true and nothing is clear. Reported verbatim: *"which one is the route/bus name? why
pavilion bukit jalil written twice? what is ~18 min for?"*

- The route is **named after its destination**, so `PAVILION BUKIT JALIL (PAVBJ)` and the path
  `Stesen LRT Awan Besar ~ Pavilion Bukit Jalil` read as duplication. Routes like `T580` or `710`
  don't have this problem, so it was never noticed in testing.
- The staleness note is welded onto the stop name by accident of ordering:
  *"so less certain at KL2324 LRT AWAN BESAR"* parses as one clause.
- Six facts run together in one sentence with no labels.

### 2. Stop locations are invisible

The panel names stops (`KL1743 GREEN AVENUE CONDOMINIUM · ~185 m · ~3 min walk`) but the app never
shows where they are — on a map application whose own map is already on screen.

### 3. "nothing inbound right now" hides working data — the significant one

Every nearby stop reported nothing inbound while the user had just tapped a bus with a 19-minute
arrival. Reproduced against the real feed using the reported location:

```
stops within 800 m: 15          panel's limit: 5

KL1743 GREEN AVENUE CONDOMINIUM     0 m   considered
KL1291 KM1 BUKIT JALIL             60 m   considered
KL2020 AFC (OPP)                  303 m   considered
KL2019 ANJUNG HIJAU GREENFIELDS   332 m   considered
KL1290 ARKED ESPLANAD (OPP)       340 m   considered
        … 3 more …
KL2324 LRT AWAN BESAR             585 m   rank 9 — NEVER EVALUATED
```

`get_stops_near(radius_m=800, limit=5)` truncates by distance **before** any arrival is computed.
LRT Awan Besar is rank 9, so the one stop with a bus actually inbound was never considered, and the
five that were had nothing. The message was true and useless.

The wording compounds it: *"nothing inbound right now"* reads as *"no bus ever serves this stop"*,
which is a much stronger claim than intended.

## Non-goals

- **No walking directions of our own.** A Google Maps link delegates that; we do not route.
- **No change to the arrival arithmetic.** 2.5.x established it, including the frequency-based
  delay suppression. This release changes selection and presentation only.
- **No new radius.** 800 m stays; the defect is truncation within it, not its size.

---

## Part 1 — Select stops by usefulness, not just proximity

`get_stops_near` is called without a hard `limit`, evaluating every stop inside the radius. The
panel then renders:

1. every stop **with** at least one arrival, nearest first, capped at 5;
2. if fewer than 3 such stops exist, the nearest stops **without** arrivals fill the remainder, so
   a user always sees something and can tell "nothing is coming anywhere near me" from "the app
   found nothing".

Cost is unaffected in the way that matters: `arrivals_for_stops` iterates
`vehicles × stops-on-that-trip` and tests each against a `set` of wanted stop ids. Widening that
set from 5 to ~15 entries changes a hash lookup, not the loop bounds.

Wording changes to distinguish the two cases:

- a stop with no inbound bus → **"no bus currently en route to this stop"**
- no stop in range has any → **"No buses are currently en route to any stop within 800 m of you."**

## Part 2 — A readable tapped-bus panel

Replace the single run-on sentence with labelled lines:

```
Route PAVBJ — Pavilion Bukit Jalil
Runs: Stesen LRT Awan Besar ↔ Pavilion Bukit Jalil

Arrives KL2324 LRT AWAN BESAR in ~19 min
That stop is ~517 m from you (~7 min walk)

⚠️ This bus last reported 1 min ago
```

Rules:

- **Route line first**, short name and long name explicitly labelled as the route.
- **Path on its own line**, prefixed `Runs:`, using `↔` rather than `~` so it does not read as an
  approximation sign.
- **Arrival and walk are separate lines** — the arrival is about the bus, the walk is about you.
- **Staleness is its own line**, never adjacent to a stop name. Shown only when the position is
  older than `LIVE_FRESH_SECONDS`.
- When the route's long name and the headsign are the same string, the path line is **omitted**
  rather than repeating it. That is what produced the reported duplication.

The same labelled treatment is applied to rows in "Arrivals near you", which currently share the
run-on style.

## Part 3 — Stops on the map, and a link out

**Markers.** Nearby stops render as a distinct pydeck layer beneath the vehicle layers, visually
separable from buses (different colour and shape), with the stop name in the tooltip.

Two constraints inherited from 2.5.1, both learned the hard way:

- The layer **must** carry an explicit `id`. An unnamed `pdk.Layer` takes a fresh `uuid4()` per
  render, and Streamlit hashes the deck spec into the widget id, which is what previously stopped
  map selection working at all.
- The layer's data **must not** carry per-render-volatile columns. Stop coordinates and names are
  static, so this holds naturally — but the frame passed to the layer is restricted to the columns
  actually drawn, so a future addition cannot reintroduce spec churn.

**Link.** Each stop name in the panel links to
`https://www.google.com/maps/search/?api=1&query=<lat>,<lon>`, opening the stop's location for
walking directions. The link is on the stop name only; the arrival lines stay plain text.

---

## Testing

- **Stop selection:** a fixture with more stops in range than the display cap, where the only
  served stop is beyond the old `limit=5` — asserts it appears. This is the regression test for the
  reported bug and must fail against the current implementation.
- **Fallback:** no stop in range has an arrival → the nearest stops still render, with the
  "no bus currently en route" wording, and the panel-level message names the radius.
- **Formatting:** a route whose long name equals its headsign omits the path line; one where they
  differ shows it. A stale position renders its own line; a fresh one renders none.
- **Link:** the rendered markdown contains the stop's real coordinates.
- **Layer id:** the stops layer has a stable explicit `id`, asserted the same way the vehicle
  layers are.
- The existing 153 tests stay green.

## Follow-ups (not in this version)

- The **"✕ Clear bus selection" control can be inert** when the deck spec is unchanged between
  renders — the widget id is then unchanged, Streamlit returns the same selection, and the cleared
  session key is immediately rewritten. Carried from the 2.5.0 review; unchanged by this release.
- **Tap-a-bus has never been exercised in a browser.** The screenshot in this report is the first
  evidence it works at all — which it does.
- Ingesting the trip descriptor's `startDate` would make service-day resolution exact and allow a
  real delay for `exact_times=1` trips.
