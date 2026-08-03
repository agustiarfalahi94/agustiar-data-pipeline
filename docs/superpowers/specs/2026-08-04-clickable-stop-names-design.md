# Design: Clickable Stop Names, and Rings You Can Actually Hit

**Date:** 2026-08-04
**Target version:** 2.11.0 (new interaction + hit-target fix → minor bump)
**Status:** Approved design, pending implementation plan

---

## Problem

### 1. The stop rings are only clickable on their outline

The `nearby-stops` layer is `stroked=True, filled=False` (`live_map.py:842-843`). deck.gl makes only drawn pixels pickable, so the hollow centre of every ring is dead space. Reported verbatim: *"sometimes i missed the click because my cursor is inside the ring, not in the ring line."*

The ring shape itself is not the mistake. 2.6.0 chose it deliberately, and the comment at `:839-841` records why: buses are filled circles, so *"a stop must differ in shape and not only in colour — a smaller coloured dot reads as a smaller bus."* Replacing the ring with a dot would fix the hit target by undoing that.

### 2. A stop's name in *Arrivals near you* is a Google Maps link and nothing else

The panel lists stops the user is standing near, and the only thing their names do is leave the app. There is no way to say *"this one — show me it"* without hunting for its ring on the map.

---

## Decisions

**Keep the ring, add a faint fill.** Rejected: a filled dot. `filled=True` with a low-alpha fill makes the whole disc pickable while the bright stroke keeps the ring's identity, so the shape distinction 2.6.0 introduced survives. This is a hit-target fix, not a visual redesign.

**Clicking a stop name selects it; its detail opens under the map.** Rejected: opening the detail inline in the list (no scrolling, but the same stop's facts could then appear in two places at once, and this app has twice shipped bugs where two panels described the same bus differently), and highlighting the ring without opening anything (simplest, but leaves the routes and stop sequences a second interaction away).

**The trade is stated, not hidden:** the map sits above *Arrivals near you*, so clicking a name means scrolling up to see the panel. Streamlit cannot auto-scroll reliably — the JS injection needed was considered and rejected in 2.7.0 because it fights the auto-refresh rerun. The user chose this knowing that.

---

## Part 1 — A ring you can hit anywhere

```python
stroked=True,
filled=True,
get_fill_color=[255, 200, 60, 40],   # faint: a hit target, not a new shape
get_line_color=[255, 200, 60, 220],
```

deck.gl discards fragments inside the circle when `filled=False`, and picking follows the same discard — which is exactly why the centre was dead. A filled disc is picked regardless of how faint the fill is. The alpha is deliberately non-zero rather than fully transparent: a value of `0` invites a future reader to "tidy up" a fill that appears to do nothing.

The existing comment must be corrected rather than deleted — it explains a decision that still stands, and it is now half wrong.

## Part 2 — The selected stop's ring stands out

The layer already carries `stop_id` per row. It gains a per-row colour so the selected stop is visibly the one being described below the map:

- selected: full-strength stroke, wider line
- everything else: as today

`st.session_state['selected_stop_id']` is readable at any point in `show()`, including where the layer is built.

## Part 3 — The stop name becomes a control

In *Arrivals near you*, each stop's heading changes from a bare Google Maps link to:

```
KL1743 GREEN AVENUE CONDOMINIUM   ·  Google Maps
└ clicking the name selects the stop      └ the link that used to be the name
```

The name renders as `st.button(..., type="tertiary")`, which draws as a text link rather than a filled button — available in the pinned Streamlit and appropriate for a list of five.

Clicking sets `st.session_state['selected_stop_id']` and reruns, taking the **same** path a ring tap takes. It must not open a second panel or a parallel state: the tapped-stop panel, the last-tap-wins rule and the one-shot clear suppression all took two fix rounds in 2.7.0, and this adds a new way to reach that state, not a new state.

Each button needs a `key` unique per stop, or Streamlit will collide them.

---

## Testing

- Ring: the layer is `filled=True` with a non-zero-alpha fill, and still `stroked` with the original line colour.
- Highlight: with a stop selected, that row's colour differs from the others; with none selected, all rows match.
- Name click: sets `selected_stop_id` to that stop and nothing else; a second stop's button sets that one instead.
- The Google Maps link still points at the stop's coordinates, and is no longer the name.
- Buttons carry distinct keys.
- No new state key is introduced — the selection reached from the list is the same one a ring tap reaches.

Suite green under both pandas string dtypes; no network calls.

## Documentation

README, CHANGELOG, `pyproject.toml` to **2.11.0**; `requirements.txt` and `requirements-dev.txt` verified unchanged. The README must state plainly that clicking a name scrolls nothing — the panel opens under the map, above the list.

## Out of scope

- Auto-scrolling to the map. Rejected in 2.7.0 and still rejected.
- Any change to the tapped-stop panel's content — this adds a route into it, not a new version of it.
