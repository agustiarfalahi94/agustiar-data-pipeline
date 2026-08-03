# Clickable Stop Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every nearby-stop ring clickable across its whole face, and make a stop's name in *Arrivals near you* the way to select that stop.

**Architecture:** Two changes to one file. The `nearby-stops` layer gains a faint fill so deck.gl picks the disc rather than only the outline, plus a per-row colour so the selected stop is visibly the one described below the map. The arrivals panel's stop heading splits into a link-styled button (selects the stop) and a separate Google Maps link.

**Tech Stack:** Python 3, Streamlit, pydeck, pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-08-04-clickable-stop-names-design.md`

## Global Constraints

- Target version **2.11.0**; `CHANGELOG.md`, `pyproject.toml` and `README.md` must agree.
- **No new dependency.** `requirements.txt`, `requirements-dev.txt` and the `pyproject.toml` dependency list are unchanged. Verify, do not edit.
- **No new selection state.** Clicking a name must set the *same* `st.session_state['selected_stop_id']` a ring tap sets, and take the same path. The last-tap-wins rule, `cleared_stop_id` one-shot suppression and `deck_generation` bump took two fix rounds in 2.7.0 — this adds a route into that machinery, not a parallel one.
- **The ring stays a ring.** The fill is a hit target, not a redesign: a low, non-zero alpha. A dot was rejected because 2.6.0 chose the ring so a stop could not read as a smaller bus.
- Nothing may raise into a render.
- Tests make no network calls; suite green under **both** pandas string dtypes.

---

### Task 1: A hittable, highlightable ring and a clickable name

**Files:**
- Modify: `src/app_pages/live_map.py` (stops layer ~`:831-850`; arrivals-panel stop heading ~`:1461-1470`)
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: the existing `st.session_state['selected_stop_id']`.
- Produces: no new public function. The layer's data rows gain `line_color` and `line_width`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script.py`. Reuse the existing page fixtures — read `_render_live_map` and the deck-inspection tests before writing, and do not invent a parallel harness.

```python
def test_stop_rings_are_clickable_across_their_whole_face(monkeypatch):
    # deck.gl only picks drawn pixels. With filled=False the hollow centre was
    # dead space, so a cursor inside the ring missed the stop entirely.
    st_stub = _render_live_map(monkeypatch)
    deck = st_stub.pydeck_chart.call_args_list[0][0][0]
    stops = next(l for l in deck.layers if l.id == 'nearby-stops')
    assert stops.filled is True, "the centre of the ring is not pickable"
    assert stops.stroked is True, "the ring outline must survive the fill"


def test_the_stop_ring_fill_is_faint_but_not_invisible(monkeypatch):
    # A fully transparent fill invites a later reader to delete a fill that
    # appears to do nothing -- and deleting it silently restores the dead centre.
    st_stub = _render_live_map(monkeypatch)
    deck = st_stub.pydeck_chart.call_args_list[0][0][0]
    stops = next(l for l in deck.layers if l.id == 'nearby-stops')
    alpha = stops.get_fill_color[3]
    assert 0 < alpha < 120, alpha


def test_the_selected_stop_ring_is_drawn_differently(monkeypatch):
    st_stub = _render_live_map(monkeypatch, selected_stop_id='S1')
    deck = st_stub.pydeck_chart.call_args_list[0][0][0]
    rows = next(l for l in deck.layers if l.id == 'nearby-stops').data
    picked = [r for r in rows if r['stop_id'] == 'S1']
    others = [r for r in rows if r['stop_id'] != 'S1']
    assert picked, "fixture must include the selected stop"
    assert others, "fixture must include at least one other stop"
    assert picked[0]['line_color'] != others[0]['line_color'] \
        or picked[0]['line_width'] != others[0]['line_width']


def test_stop_rings_match_when_nothing_is_selected(monkeypatch):
    st_stub = _render_live_map(monkeypatch)
    rows = next(l for l in st_stub.pydeck_chart.call_args_list[0][0][0].layers
                if l.id == 'nearby-stops').data
    assert len({tuple(r['line_color']) for r in rows}) == 1


def test_clicking_a_stop_name_selects_that_stop(monkeypatch):
    # The name used to be a Google Maps link and nothing else.
    st_stub = _render_live_map(monkeypatch, pressed_button='KL1743 GREEN AVENUE CONDOMINIUM')
    assert st_stub.session_state['selected_stop_id'] == 'S1'


def test_the_google_maps_link_survives_and_is_no_longer_the_name(monkeypatch):
    st_stub = _render_live_map(monkeypatch)
    said = _texts(st_stub.markdown)
    assert 'google.com/maps' in said
    assert '[KL1743 GREEN AVENUE CONDOMINIUM](' not in said, \
        "the name should be a control now, not the link"


def test_each_stop_button_has_its_own_key(monkeypatch):
    # Streamlit collides same-keyed widgets; five stops need five keys.
    st_stub = _render_live_map(monkeypatch)
    keys = [c.kwargs.get('key') for c in st_stub.button.call_args_list
            if c.kwargs.get('key')]
    assert len(keys) == len(set(keys)), keys
```

`_render_live_map` needs two new optional parameters — `selected_stop_id` (seeds session state) and `pressed_button` (makes `st_stub.button` return `True` only for that label). Extend the existing fixture; do not copy it.

- [ ] **Step 2: Run tests to verify they fail**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "ring or stop_name or maps_link or button_has" -q
```

Expected: FAIL — `filled` is `False` and the name is still a markdown link.

- [ ] **Step 3: Make the ring hittable and highlightable**

Replace the stops-layer construction. Keep the existing comment's reasoning and correct the half that is now wrong:

```python
                data=[{'stop_id': s['stop_id'],
                       'stop_name': s['stop_name'],
                       'stop_lat': s['stop_lat'],
                       'stop_lon': s['stop_lon'],
                       'tip_text': _tip_text('Stop', s['stop_name']),
                       'line_color': ([255, 255, 255, 255]
                                      if s['stop_id'] == _selected_stop_id
                                      else [255, 200, 60, 220]),
                       'line_width': 4 if s['stop_id'] == _selected_stop_id else 2}
                      for s in _nearby_stops],
                get_position=['stop_lon', 'stop_lat'],
                # Rings, not dots: buses are filled circles, so a stop must
                # differ in shape and not only in colour — a smaller coloured
                # dot reads as a smaller bus.
                #
                # Filled all the same, faintly. deck.gl discards the fragments
                # inside an unfilled circle and picking follows the same
                # discard, so a hollow ring is only clickable on its outline —
                # a cursor resting in the middle of a stop missed it. The fill
                # is a hit target, not a shape: the bright stroke is still what
                # the eye reads. The alpha is deliberately non-zero, because a
                # fully transparent fill looks like it does nothing and invites
                # a later reader to remove it, restoring the dead centre.
                stroked=True,
                filled=True,
                get_fill_color=[255, 200, 60, 40],
                get_line_color='line_color',
                get_line_width='line_width',
                line_width_min_pixels=2,
                get_radius=40,
                radius_min_pixels=5,
                radius_max_pixels=10,
                pickable=True,
```

Read the selection once, above the layer:

```python
    _selected_stop_id = st.session_state.get('selected_stop_id')
```

- [ ] **Step 4: Make the name a control**

Replace the heading in the arrivals panel:

```python
                    maps_url = (
                        "https://www.google.com/maps/search/?api=1&query="
                        f"{stop['stop_lat']},{stop['stop_lon']}"
                    )
                    # The name selects the stop; the Maps link keeps its old
                    # job beside it. Same session key a ring tap sets, so this
                    # is a second route into one selection, not a second
                    # selection — the last-tap-wins rule and the one-shot clear
                    # suppression both key off that single value.
                    if st.button(stop['stop_name'], type="tertiary",
                                 key=f"pick_stop_{stop['stop_id']}"):
                        st.session_state['selected_stop_id'] = stop['stop_id']
                        st.rerun()
                    st.markdown(
                        f"[Google Maps]({maps_url}) "
                        f"· ~{int(walk['distance_m'])} m · {_walk_label(walk)}"
                    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest tests/test_script.py -k "ring or stop_name or maps_link or button_has" -q
```

- [ ] **Step 6: Run the full suite under both dtypes and the network spy**

```bash
export PATH="$(pwd)/.venv/bin:$PATH"
python -m pytest -q tests/ 2>&1 | tail -3
python -c "import pandas as pd; pd.set_option('future.infer_string', True); import pytest,sys; sys.exit(pytest.main(['-q','tests/']))" 2>&1 | tail -3
```

Existing tests asserting the stop name is a markdown link must be updated to the new shape — the name is a control now. Report which you changed.

- [ ] **Step 7: Commit**

```bash
git add src/app_pages/live_map.py tests/test_script.py
git commit -m "feat: hit a stop ring anywhere, and select a stop from its name"
```

---

### Task 2: Documentation and version

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml`
- Verify unchanged: `requirements.txt`, `requirements-dev.txt`

- [ ] **Step 1: Update `README.md`**

In the Live Map feature list, record that a stop's name in *Arrivals near you* selects that stop and highlights its ring, with the Google Maps link now beside the name rather than being it.

**State the trade plainly:** the map sits above the list, so selecting a stop from the list means scrolling up to the panel. The app does not scroll for you — Streamlit cannot do it reliably, and the JavaScript workaround was rejected in 2.7.0 because it fights the auto-refresh rerun.

- [ ] **Step 2: Update `CHANGELOG.md`**

Open `## [2.11.0] - 2026-08-04`:

- The nearby-stop rings were `stroked` but not `filled`, and deck.gl picks only drawn pixels — so the hollow centre of every ring was dead space and a cursor resting inside a stop missed it. The ring now carries a faint fill, making the whole disc a hit target while the bright stroke keeps the shape that distinguishes a stop from a bus. A filled dot was rejected: 2.6.0 chose the ring precisely so a stop could not read as a smaller bus.
- A stop's name in *Arrivals near you* now selects that stop and highlights its ring, opening the same panel a ring tap opens. The Google Maps link moves beside the name. The panel appears under the map, above the list, so this means scrolling up — the app does not scroll for you.

- [ ] **Step 3: Bump `pyproject.toml` to 2.11.0**

- [ ] **Step 4: Verify the manifests**

```bash
git diff --stat requirements.txt requirements-dev.txt
grep -n "version" pyproject.toml | head -2
head -8 CHANGELOG.md
```

- [ ] **Step 5: Run the full suite under both dtypes, then commit**

```bash
git add README.md CHANGELOG.md pyproject.toml
git commit -m "docs: document clickable stop names, bump to 2.11.0"
```

---

## Manual verification

Run locally (`streamlit run src/app.py`), click **Refresh Data**, then **Locate Me**:

1. Click the **middle** of a stop ring on the map — it selects. That is the bug this fixes.
2. Click a stop name in *Arrivals near you* — its ring changes appearance and the panel opens under the map.
3. The Google Maps link beside the name still opens the right place.
4. Tapping a bus still replaces the stop panel, and **Clear stop selection** still sticks through an auto-refresh.
