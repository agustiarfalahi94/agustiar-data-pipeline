# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.11.1] - 2026-08-05

Four things reported from local testing, all of them about the map: taps on a stop ring did
nothing, taps on a bus did nothing, the selected ring's highlight was invisible on the light map,
and changing the map theme froze the page for about five seconds.

### Fixed
- **Tapping a stop ring or a bus on the map now works.** It never did, except by accident. The
  cause: `get_live_data_optimized` measures its window from wall-clock now, so two calls a second
  apart disagree even when no new data has arrived — the cutoff has moved, so a bus can fall out of
  the window, and every remaining bus is a second older, so one can cross from fresh to stale. All
  of that is drawn: a stale bus is dimmed and its tooltip gains a "stale" line. So the deck spec on
  the rerun a tap triggers was not the spec the tap was made against, and Streamlit folds the deck
  spec into the chart's identity — a changed spec is a *new* chart, and a new chart has no tap
  recorded against it. Every tap was discarded before any code could read it. The live frame is now
  read once per data refresh instead of once per rerun, so the spec is byte-identical across the
  rerun a tap causes. This also explains the one case that did work, reported as *"if i apply a
  route name filter, then clicked the available bus, it works"* — a filter leaves too few buses for
  any of them to change state in that instant, so the spec happened to stay put
- **Changing the map theme no longer pauses the page for several seconds.** With *Auto (20s)*
  selected, the page refetched every agency feed on *every* rerun, not just on the timer — so
  toggling the theme, or touching any other control, triggered a full network fetch and the page sat
  waiting for it. Reported as *"if i change the map appearance, my location got reset but would
  restored after waiting for like 5 seconds"*. Auto-refresh now fetches only when its own 20-second
  counter advances. The stored location was never actually lost; the wait was the refetch
- **The selected stop's ring is magenta, not white.** The map has a light theme as well as a dark
  one, and white disappears into the light one — the highlight added in 2.11.0 was invisible exactly
  half the time. Magenta reads against both a pale background and a near-black one, and is not
  already in use: stop rings are gold, buses are blue, the location dot is red

### Changed
- **A stop's name in "Arrivals near you" is drawn in blue.** It is a tertiary button, which renders
  in the ordinary body colour, so nothing told the reader it could be tapped. Blue is the same
  signal the Google Maps link under it already gives

## [2.11.0] - 2026-08-04

Two complaints from testing in a Mac browser: *"sometimes i missed the click because my cursor is
inside the ring, not in the ring line"*, and a nearby stop's name in "Arrivals near you" was a
Google Maps link and nothing else — no way to say "this one, show me it" without first finding its
ring on the map.

### Added
- **A stop's name in "Arrivals near you" now selects it.** It's a button that writes
  `st.session_state['selected_stop_id']` — the identical key a ring tap writes — so it is a second
  route into one selection, not a second selection: the last-tap-wins rule and the one-shot
  `cleared_stop_id` suppression both still apply unchanged. It also clears
  `selected_vehicle_id`, the same way a ring tap already does (`:1043`) — otherwise clicking a name
  while a bus is selected leaves the sticky vehicle id behind, and the next render (no fresh tap of
  either kind) shows both the bus panel and the stop panel at once, exactly what the last-tap-wins
  comment at `:1033-1035` exists to forbid. The Google Maps link is kept rather than lost, on its
  own line directly under the name (`st.button` and `st.markdown` are block elements, so it stacks
  below rather than sitting alongside — see the note in the design table). The new panel opens between the map and the "Arrivals near you" list, so clicking a
  name from the list means scrolling *up* to see it — this does not auto-scroll (a JS workaround was
  rejected in 2.7.0 for fighting the auto-refresh rerun)
- **The selected stop's name is drawn as a selected control in the list too.** Until now the only
  feedback for clicking a name was the highlighted ring and the panel, both of which sit *above* the
  list — on a phone, above the fold. Clicking a name and staying where you are read as nothing
  having happened. The selected stop's button now renders `primary` where the others render
  `tertiary`; no new state, it is styled from the same `selected_stop_id` the ring is
- **A selected stop's ring is drawn brighter and thicker** (white, 4px vs. the default gold, 2px).
  `get_line_width` on this layer is in the same units as `get_radius` (metres) by default, where a
  2m/4m stroke on a 40m-radius, 5-10px-on-screen ring both round under `line_width_min_pixels`'s 2px
  floor — so without also setting `line_width_units='pixels'`, the two widths would have rendered
  identically and only colour would have actually distinguished them; that unit switch is included
  here. Selecting by name shows this instantly (the click's own `st.rerun()` rebuilds the layer with
  the new selection already applied); tapping the ring itself can lag up to one auto-refresh cycle
  (≤20s) before its own ring updates, because the stops layer is built from session state before
  that same tap's payload is read back — left to self-heal on the next render rather than forced
  with a bare `st.rerun()` after adopting the tap, since that would risk replaying a stale selection
  payload in the one case where it can recur (re-tapping the ring already selected, where the deck
  spec — and so Streamlit's element id — does not change); a guarded rerun would close it safely but
  was not judged worth the extra branch for a highlight that already self-heals (see
  `test_a_ring_tap_selects_instantly_but_its_own_highlight_lags_one_render`). The stop *panel* itself
  is never affected by this lag — it opens instantly from either path

### Fixed
- **A nearby-stop ring is now clickable across its whole face, not only its outline.** deck.gl only
  picks pixels a layer actually draws; with the ring drawn hollow (`filled=False`), a tap landing in
  its centre hit nothing. The ring is now filled too, at a low alpha (40 of 255) — visible enough
  that a later reader can't mistake it for doing nothing and delete it, faint enough that the gold
  stroke, not the fill, is still what the eye reads. The ring stays a ring: 2.6.0's reasoning for a
  hollow shape over a filled dot (a stop must not read as a smaller bus) still holds and is
  unchanged by this fix, only its "unfilled" half is corrected — the comment in `live_map.py` now
  says both
- **A stale sticky vehicle selection can no longer sit alongside a newly-selected stop.** Clicking
  a stop's name while a bus was still selected used to leave `selected_vehicle_id` untouched, so the
  very next render — the one the click's own `st.rerun()` produces — resolved a fresh tap of
  neither kind, fell back to the sticky bus id, and rendered the bus panel and the just-opened stop
  panel together. The click now also arms `cleared_vehicle_id`, the same one-shot "ignore this
  vehicle for exactly one render" token "Clear bus selection" has always set. Clearing the selection
  alone is not enough: if the pydeck payload naming that bus survives the rerun, the next render
  reads it as a *fresh* vehicle tap, and a fresh vehicle tap sets `selected_stop_id` to None — the
  click does nothing at all. It happens not to survive today, but only because the selected ring's
  own `line_color`/`line_width` change the deck spec and Streamlit hashes the spec into the element
  id; style the highlight some other way and the click silently breaks. The token makes that a
  guarantee the code states rather than one it inherits from how a ring is drawn, and it cannot
  strand a bus as untappable — it is popped unconditionally on the next render, quiet feed or not
- **A feed that repeats a `stop_id` no longer takes the Live Map down.** `get_stops_near` appended
  one entry per matching `stops.txt` row, so a repeated id came back twice. Under 2.10.0 that was a
  duplicated line in "Arrivals near you"; now that each stop's name is a button keyed on its id, it
  is two widgets with one key and a `StreamlitDuplicateElementKey` raised into the render — the
  whole page, not one row. `get_stops_near` now de-duplicates by `stop_id`, keeping the nearest
  occurrence, before it truncates to `limit` (after would let a duplicate row spend one of the five
  places a real stop needed). That fixes the button, the panel, the walk-time matrix and the map
  layer in one place
- **The declared Streamlit floor now matches what the new button actually needs.** The name button
  passes `type="tertiary"`, added in Streamlit 1.41.0; `pyproject.toml` and `requirements.txt` still
  named `>=1.40.0`, so a fresh install resolving to 1.40.x would raise `StreamlitAPIException`
  straight into the arrivals render. Both files now require `>=1.41.0`

## [2.10.0] - 2026-08-03

Rapid Bus KL's realtime feed went quiet upstream — confirmed at 12:49 on a Monday: `rapid-bus-kl`
returned HTTP 200 with a 15-byte body and zero entities, while `rapid-bus-mrtfeeder` returned 102
vehicles and `rapid-bus-penang` 147 in the same second. The outage was Prasarana's. What the app
did with it was ours.

### Fixed
- **An empty vehicle frame no longer deletes the map.** Three early returns in `live_map.py` — no
  rows in the window, the selected region reporting nothing usable, or everything on hand too old
  to draw — each took the whole map with them, including the user's own location marker, the
  nearby stop rings, and the tapped-stop panel, none of which reads a live vehicle. Reported
  verbatim: *"i cannot test to tap a bus stop in rapid KL area because the map disappear entirely
  as the data is empty."* The three returns are now a single `no_vehicles` flag: the deck, the
  location marker, the stop rings and the tapped-stop panel still render; only the vehicle layer,
  its tooltip column, and the "Showing N active vehicles" caption are skipped, replaced by a
  warning naming which of the three causes applied. `df_live` being empty network-wide keeps its
  own return — with no data at all there is no region context to render into
- **Three latent crashes surfaced by that change, fixed along the way.** `prepare_map_data` returns
  a frame with *no columns at all* on an empty region, so anything still reading a vehicle column
  needed to skip wholesale rather than being guarded row-by-row: the view-state centring read
  `df_map['latitude'].mean()` and `df_map['longitude'].mean()`, the tooltip column read
  `df_map['vehicle_id']`, and the Route Viewer's vehicle picker read `df_map['vehicle_id']` too —
  all three now short-circuit on `no_vehicles` instead of raising `KeyError`
- **The camera re-centres when a quiet region's vehicles come back.** Removing the early return
  meant a quiet render started consuming the "something changed, move the camera" trigger by
  itself — it correctly centres on the user, the only anchor there is, but with the region and
  search unchanged, the *next* render (the one where the feed recovered) saw no change at all, left
  the camera parked on the user, and drew the recovered buses off-screen under a "Showing N active
  vehicles" caption. Gaining or losing vehicles is now itself a third re-centre trigger alongside a
  region switch and a route search, with its own remembered flag so an ordinary auto-refresh during
  the outage does not fight a pan the user made
- **Streamlit Secrets now accepts a flat `api_key`, not only the sectioned `[routing]` form.**
  `_ors_api_key` reads `st.secrets['routing']['api_key']` only, so a key pasted as a bare top-level
  `api_key = '...'` — what a reader reaches for when pasting one copied line — was invisible to it,
  and the deliberately broad `except` swallowed the miss. The sectioned form stays canonical and
  wins when both are present; a missing key in either form still returns `None` with no network
  call. Previously this failed silently and indistinguishably from having no key configured at all
  — every walk time stayed `(estimated)` with nothing saying why
- **Route search now finds a route by the name painted on the bus, not just the name the feed
  publishes.** `GOKL14` is Rapid KL's livery for the route the feed calls `PAVILION BUKIT JALIL
  (PAVBJ)`; no `GOKL` route exists anywhere in the feed. The Live Map now resolves the query once,
  via `gtfs_static.resolve_route_alias`, before it reaches either the vehicle filter or the
  region-lookup that decides why a search found nothing — so the two can never disagree about what
  was actually searched. A match through the hand-maintained alias table always discloses itself
  with a caption naming both the searched term and the resolved route, so a guess from that table
  is never presented as feed data. A real feed name (e.g. `T580`) is never rewritten and produces
  no such caption
- **A dead end now names a region that works.** Standing in Bukit Jalil with KTM Berhad selected,
  the app said only *"No stops found within 800 m of you in KTM Berhad"* — true, and useless. Rapid
  Bus KL has 15 stops within that same 800 m and the app already knew it. The panel now lists the
  regions `find_regions_with_stops_near` found, nearest first, e.g. *"Rapid Bus KL — 15 stops,
  nearest ~152 m"*, so there is somewhere to go instead of a dead end
- **The tapped-bus panel now follows the widened radius too.** The progressive search below widens
  to 1500 m when 800 m finds nothing, but this panel kept its own hard-coded 800 m — so with stops
  found at 1200 m, the map drew rings there, *"Arrivals near you"* listed a bus en route to one of
  them, and tapping that same bus answered *"Vehicle V1 does not come within 800 m of you on its
  current trip"*. Two panels, one bus, opposite answers. The panel now reads the radius that was
  actually applied, so the bound it enforces and the number it quotes are the same by construction
- **The cross-region scan no longer re-runs every 20 seconds at a sticky dead end.**
  `find_regions_with_stops_near` was the only all-agency walk in the codebase with no memoisation,
  and the dead end it runs at does not clear itself — the user's location has not changed, so every
  auto-refresh walked straight back into it. Warm that re-read fourteen cached ZIPs; cold it goes
  `_load_zip → is_cache_fresh → download_static_gtfs`, up to thirteen synchronous HTTP fetches
  inside a page render, and the 24-hour ZIP TTL re-arms that daily. Worse, an agency endpoint that
  hangs costs `REQUEST_TIMEOUT` (30 s) and was retried on every refresh forever, because the
  per-agency exception is swallowed and nothing remembered it. The result is now memoised on the
  same ~55 m location grid `walking` already uses (now a shared `walking.snap_to_grid` rather than a
  second snapping rule), following `_ROUTE_REGION_INDEX`'s module-dict pattern. It expires after
  five minutes rather than lasting the process, because the cached answer bakes in *which agencies
  replied* — one skipped for a momentary unreadable feed must not stay missing from the hint until
  the process restarts. No separate negative-cache window was added: unlike `walking`, whose
  failure path has no value worth storing, this function's failure path returns a perfectly good
  `[]`, so memoising the result already is the negative cache
- **The alias caption now says whose mapping it is.** It read *"'GOKL14' is the name on the bus;
  the feed publishes this route as PAVILION BUKIT JALIL (PAVBJ)"* — two statements of fact, with
  the one part that is actually a guess, that these are the same route, left unattributed between
  them, so the sentence read as though the app had looked the link up. Nothing in the feed supports
  it. The caption now names the link as this app's own hand-maintained one and says plainly that
  the feed does not confirm it
- **A dismissed bus is no longer un-tappable for the length of a feed outage.** The one-render
  token that ignores a just-cleared vehicle was popped *inside* the "are any vehicles reporting"
  guard, so if the feed went quiet on the render right after a clear, the token survived the whole
  outage and the first re-tap of that bus after recovery was swallowed. It is now popped outside
  the guard, matching the stop-side token, which always was

### Added
- `gtfs_static.find_regions_with_stops_near(lat, lon, radius_m, exclude_slug, limit)` — which other
  regions have stops near a point, nearest first. Walks every agency's cached timetable and skips
  any whose feed raises or is unreadable, so one dead feed cannot cost the user the other twelve
  answers. Called only at the dead end described above, behind a spinner — never on a normal render
- `gtfs_static.resolve_route_alias` and a hand-maintained `ROUTE_ALIASES` table, currently holding
  exactly one entry: `GOKL14 → PAVILION BUKIT JALIL (PAVBJ)`, the route's `route_id` is `S6060` and
  no `GOKL` route exists anywhere in the feed — the connection lives only on the vehicle's livery
- **Progressive nearby-stop radius.** The search that used to stop at a fixed 800 m now widens once
  to 1500 m when 800 m finds nothing, and the panel always names the radius it actually applied
  (2.6.0 already fixed one version of copy and applied number disagreeing; this reuses that
  discipline rather than reintroducing the bug)

### Changed — Airflow leaves the backlog
Recorded as decided against, not deleted, so it is not re-proposed without new information: Airflow
cannot run on Streamlit Cloud, it needs a server running 24/7, and the owner has decided not to pay
for one just to orchestrate this project's ingestion. The actual blocker is hosting, not the DAG
design — `fetch_and_store_transit_data()` is already a clean entry point a DAG could call. A
zero-cost alternative worth evaluating first if the hosting constraint ever changes: a GitHub
Actions scheduled workflow can run ingestion and `dbt run` on a free cron, using the CI setup that
already exists, at the cost of no backfill UI and weaker retry semantics than Airflow.

### Cosmetic sweep
Deferred across the 2.7–2.10 reviews, swept in one pass:
- `route_view.pattern_titles`'s docstring claimed its titles were "guaranteed unique"; softened to
  the actual guarantee — unique among the patterns passed in a single call, not globally
- A single-stop pattern has `first == last` trivially, the same condition `pattern_label` uses to
  detect a loop, so it was labelled `loop from X` for a circuit that never happens. Single-stop
  patterns are now named for their one stop instead
- A route listed under `Serves:` whose `get_route_patterns` returns `[]` got no expander and no
  explanation — it simply vanished from the loop that builds them. It now gets a one-line note
  saying no stop sequence is available for it at that stop
- `_WALK_CACHE` had no eviction. This process auto-refreshes every ~20s and never restarts between
  deploys, so every (grid cell, agency, stop) anyone had ever asked about stayed in memory forever,
  long after `CACHE_TTL_SECONDS` made the entry useless. Entries older than the TTL are now dropped
  once the dict exceeds a few thousand keys — checked only past that threshold, so an ordinary
  render pays nothing for it
- The over-long README line introduced in 2.8.1 (the 24h sparklines bullet, one unwrapped ~200
  character line) is re-wrapped to match the rest of the document

### Documentation
- Added a "How to Use This App" section to the README, written for a rider rather than a developer
  and placed before the developer setup section: what each page answers, why **Locate Me** should
  be tapped first, what `(estimated)` on a walk time means and how to remove it, why a green
  **Reliable** score and an empty map are not a contradiction, what to do when a region has no
  stops near you, that journey times come from the timetable rather than live positions, and that
  route search accepts a hand-maintained, unofficial livery alias where one is known

### Known Limitations
- **The widened 1500 m radius is still straight-line, and more wrong than the 800 m one was.** The
  gap between crow-flight and footpath grows with distance — one measured stop sits 60 m away by
  crow and 634 m on foot, over ten times the straight-line figure. Routing corrects the walk time
  *displayed* for a stop once it is selected; it does not correct *which* stops get selected in the
  first place. A stop listed at 1400 m may be a much longer walk than the number suggests

## [2.9.0] - 2026-08-02

### Added
- **Each reliability scorecard now says when that region last reported a bus.** The score answers
  one question — is the feed answering? — and an `EMPTY` cycle, the feed correctly reporting that
  no service is running, is deliberately not counted against it. So Rapid Bus KL could show a
  green **100 · Reliable** at 23:00 while the Live Map said *"No vehicle in Rapid Bus KL has
  reported in the last 15 minutes"*. Both statements were true and the card gave a reader no way
  to see that they were answering different questions. Cards now carry a second line —
  `🚌 buses last seen 3h ago`, or `🚌 no buses reported in this window` — so feed health and
  service activity are stated separately instead of one being read as the other
- `mart_network_health` gains `last_vehicle_timestamp`: the newest cycle that actually carried
  vehicles, which is not the same as `last_fetch_timestamp`, the newest cycle that answered. It is
  `NULL` — never `0`, never the fetch time — when the window holds no such cycle, because
  inventing a moment a bus was seen is precisely the false reassurance the column exists to
  prevent
- The dbt schema sentinel gains the new column, so a database created by an earlier release
  rebuilds its mart views instead of serving the old SQL forever. Without it the card would have
  read "no buses reported in this window" for every region, permanently and silently

## [2.8.1] - 2026-08-02

### Fixed
- **Hovering a health sparkline showed an array index.** The tooltip read `(1035, 100)`. The
  second number was the reliability score; the first was the point's position in the underlying
  array — the 1,036th fetch cycle counting from zero — an internal offset with no meaning outside
  the process that produced it. The chart supplied only a `y` series, and Plotly substitutes the
  row index when no `x` is given. `get_region_health_trend` already returned a timezone-converted
  `datetime` for every row; the chart was ignoring it. The hover now reads `18:20 · score 100`,
  naming the clock time the score was measured at. Where a trend genuinely carries no timestamps
  the number is labelled `cycle 1035` rather than printed bare, because an unlabelled number is
  the original fault

## [2.8.0] - 2026-08-01

### Added
- **The tapped-stop panel now names every route the timetable says calls there, not only the
  routes with a bus currently running.** The arrival list above it reads the live feed, which is a
  much smaller set — at KL2324 LRT AWAN BESAR that meant three routes on screen, `651`, `652` and
  `PAVILION BUKIT JALIL (PAVBJ)`, none of which reach KL1743 GREEN AVENUE CONDOMINIUM. The one
  route that does, `T580`, was invisible for as long as no T580 vehicle happened to be moving. A
  new `Serves:` line lists all four, built from `get_routes_at_stop`, a `{stop_id: {route_id,
  ...}}` index constructed inside the same pass over `stop_times.txt` that already builds the
  trip-stops index — no second parse of an 87,935-row file for Rapid Bus KL. The list is ordered
  the way a rider reads bus numbers, `2` before `10` rather than the other way round, and ties
  between two routes sharing a short name settle on `route_id` so the order does not change
  between reruns
- **Tapping a served route opens its full stop sequence, timed from the stop you tapped.** On
  T580 — a 35-stop, 40-minute loop — anchored at LRT Awan Besar: KM1 BUKIT JALIL reads `+1 min`,
  GREEN AVENUE CONDOMINIUM reads `+32 min`. The two stops sit about 60 m apart on the ground, on
  opposite legs of the same loop. A rider trusting the stop name rides 32 minutes instead of 1, and
  T580's next bus is about 50 minutes behind (headway ~50 min, 07:30–23:20; ~40 min 06:00–06:40).
  100 of the 136 Rapid KL routes with timetable data are loops, so this is the network's ordinary
  shape, not one route's quirk. `get_route_patterns` supplies the distinct stop sequences and
  `route_view.build_stop_rows` turns one into display rows, marking every occurrence of the tapped
  stop rather than only the nearest one, so both ends of a loop are visible together — the
  returning row also states how far round the circuit it is (`+40 min` on T580), which is what
  distinguishes it from the row you actually tapped
- **A route with more than one stop pattern is shown once per pattern — never merged.** 37 of the
  136 routes run two distinct stop sequences and one runs three; picking a single "the" sequence
  would misrepresent a quarter of the network, exactly the failure this feature exists to remove.
  `route_view.pattern_titles` gives each pattern a distinct heading — stop count and running time
  separate most collisions, a numbered suffix settles the rest — so two openable panels never read
  as the same route twice
- **Stops within walking distance are marked inside the sequence**, reusing the nearby-stop scan
  this render already ran rather than searching again. The walk times themselves are resolved for
  the marked stops only — those a rendered pattern actually calls at — so the request stays no
  larger than the marks it can draw, and `walking`'s per-stop cache means an already-resolved stop
  costs nothing
- Each pattern's stop sequence renders inside a single collapsed `st.expander`, closed on arrival:
  a 35-row list opened by default would push the map off a phone screen before anyone asked for it.
  Streamlit still re-runs the whole page body on every auto-refresh regardless of whether the
  expander is open, so the rows are built once and joined into a single `st.markdown` call rather
  than one call per row, keeping the widget count the same as before this feature shipped
- Stop names come from a third-party feed and are joined into one markdown block with hard line
  breaks; `route_view.escape_markdown` escapes the characters that could open unmatched
  bold/italic/code-span formatting across rows, the same class of fix 2.7.1 made to map tooltips.
  The same escape covers the other two new sites that interpolate feed text into markdown: the
  `Serves:` line, which joins several route names into one call, and each expander's label, built
  from a route name and a published headsign

### Known Limitations
- **Journey times are differences between timetabled stop times, not live predictions.** They
  describe the schedule, not where a bus actually is right now — for that, the arrival list and the
  tapped-bus panel remain the places to look
- **A route running to a headway is flagged as one, never given a fabricated departure time.**
  2,099 of Rapid Bus KL's 2,102 trips publish no absolute start time in `frequencies.txt`, so there
  is nothing to compute a specific departure from; the stop sequence's caption says *"This route
  runs to a headway, not a fixed timetable."* rather than inventing a time. It does not state the
  headway interval either — the app does not read `headway_secs`, so it has no figure to give
- **A pattern's journey times come from one representative trip.** Patterns are de-duplicated on
  the stop-id sequence alone, so the times shown are those of whichever of the pattern's trips
  appears first in `trips.txt` — not the trip running at the time of day the rider is standing
  there. On this network the effect is small, since 2,099 of Rapid Bus KL's 2,102 trips are headway
  templates whose stop times already *are* a template rather than a time-of-day schedule, but a
  route publishing genuinely different peak and off-peak running times would show only one of them
- **A route with more than one stop pattern appears once per pattern; patterns are never merged.**
  Choosing to keep them separate means a route serving a stop twice a day on two different
  sequences shows two expanders, not one
- **This is still not a journey planner.** It answers "does this bus stop at X, and how far along
  is it" for one route at a time — not "how do I get from A to B". Origin→destination planning
  remains explicitly out of scope (see Roadmap)

## [2.7.1] - 2026-08-01

### Fixed
- **Every map tooltip rendered literal markup — the vehicle tooltip too, not only stops.**
  2.7.0 moved tooltip markup out of the Deck-level template and into a per-row `tip_html` data
  field so that stops, once pickable, wouldn't show a vehicle-only template's unmatched
  `{vehicle_id}` as raw text. What that change missed: Streamlit escapes HTML found inside pydeck
  tooltip *interpolations* as an injection defence (Streamlit bug fix #15820), but not markup
  written into the template itself. deck.gl assigns `tooltip.html` via `innerHTML`, so template
  markup renders — but the `<b>` and `<br/>` moved into `tip_html` were substituted values, and
  came out on screen as the literal text `<b>Stop:</b> KL1743 GREEN AVENUE CONDOMINIUM`. Since the
  vehicle tooltip was rebuilt the same way, hovering a bus showed the same raw tags where it had
  worked correctly before 2.7.0. The fix drops HTML from the tooltip entirely: `_tip_html` is now
  `_tip_text`, returns `"Label: value"` with no tags, and the Deck tooltip switches from
  `{"html": "{tip_html}"}` to `{"text": "{tip_text}"}`, which deck.gl assigns via `innerText` —
  markup cannot render there even by accident. `white-space: pre-line` is set on the tooltip style
  so the vehicle's five facts, joined with real `\n` characters, still show one per line
- **A stop or route name containing `&` displayed as the literal text `A &amp; B`.** `_tip_html`
  called `html.escape()` on every value on top of Streamlit's own escaping of the interpolation, so
  an `&` was escaped twice. `_tip_text` never escapes: Streamlit escaping the value is now the only
  escaping that happens, and it belongs there because the value only ever reaches the page through
  an interpolation, never through markup this code writes
- **The network guard in `tests/test_script.py` was inert.** It raised `AssertionError` when a page
  test reached OpenRouteService, but `walking._routed_distances` wraps its request in a bare
  `except Exception`, which catches `AssertionError` and returns the fallback distance in silence —
  the guard fired and nothing ever saw it fire. It now raises `PageTestReachedNetwork`, defined in
  the test module and deriving from `BaseException`, which passes straight through that handler.
  Verified by temporarily making a page test call `walking.walk_times` with a key: the test failed
  loudly with a `PageTestReachedNetwork` traceback, confirming the guard now surfaces a violation
  instead of swallowing it; the probe was then removed

## [2.7.0] - 2026-08-01

### Added
- **Walk times now come from real footpaths, not a straight line.** Measured live from KL1743
  GREEN AVENUE CONDOMINIUM across all 15 stops within 800 m, circuity — routed distance divided
  by straight-line distance — ranges from 1.04 to 10.60. The extreme case is KL1291 KM1 BUKIT
  JALIL: 60 m away in a straight line, 634 m on foot, because something uncrossable sits between
  the user and the stop. The app quoted that as a one-minute walk; it is about ten. That spread is
  why no corrected constant was used — a multiplier tuned to Bukit Jalil would badly overestimate
  in a walkable grid like George Town. `utils/walking.py` now asks OpenRouteService's Matrix API
  for the footpath distance to every nearby stop in one request, cached per stop on a ~55 m
  location grid for 24h so auto-refresh and GPS jitter cost nothing further. Two more measured
  examples: KL2324 LRT AWAN BESAR was quoted 8 min, is ~13 (585 m straight, 864 m routed); KL1289
  TAMAN ESPLANADE was quoted 7 min, is ~15 (485 m straight, 957 m routed)
- **ORS supplies distance; the pace stays ours.** ORS's own `duration` was rejected — it walks
  957 m in 11 minutes (5.2 km/h), where the same distance implies 16 minutes (~3.6 km/h) by
  Google's estimate. It models the path, not the crossings, the waiting, the stairs, or a person
  who isn't in a hurry. Only the routed distance is taken from ORS; minutes are computed from it
  at this app's own walking pace
- **Streamlit Secrets are read for the first time.** `live_map._ors_api_key()` reads
  `ORS_API_KEY` from `config.py` for local dev, falling back to `st.secrets['routing']['api_key']`
  for Streamlit Cloud. No other code in this repository reads `st.secrets` at all — a past
  refactor had replaced those reads with hardcoded defaults, so a key placed in Secrets was
  silently ignored until now. `.streamlit/secrets.toml` is gitignored as of `b3a3af2`; the README
  had already documented it as the cloud config mechanism, but only `config.py` and `secrets.py`
  were actually ignored before this release introduced a real key worth protecting
- **Stops on the map are tappable.** Tapping a stop ring opens a panel below the map naming that
  stop, its distance, its walk time, and the buses en route to it — the same information the
  "Arrivals near you" list already showed, reachable directly from the map. Tapping a bus replaces
  the stop panel and vice versa: last tap wins, matching the existing bus-tap behaviour. Tap is
  the designed interaction, because hover does not exist on the touch devices this app is actually
  used on; what was rejected was hover as *the* way in, not a tooltip as such. Since every map row
  now carries its own rendered `tip_html`, a desktop pointer hovering a stop does also show
  `Stop: <name>` — a side effect of that work, and stated here so the documentation does not
  understate what the code does

### Changed
- **Arrival rows are route-first and labelled.** `format_arrival` now renders `Route T580 → Awan
  Besar · arrives ~6 min · 2 min late · position 3 min old` instead of running the same facts
  together with hedging prose (`, so less certain`). The route is unconditional — Rapid KL names
  routes after places, so without the label a reader can't tell a route from a destination
- Walk times state whether they are real. A routed figure reads `~9 min walk`; a fallback figure
  reads `~5 min walk (estimated)`, because a straight-line estimate cannot see that KL1291 is 60 m
  away and 634 m on foot
- **A failed routing lookup stops further requests for 60 seconds.** Auto-refresh renders every
  20 s and each render can issue two lookups at a 5 s timeout, so an ORS outage, an exhausted
  quota, or ordinary mobile flakiness meant up to 10 s of blocking I/O in the page thread three
  times a minute — and a 429 became a tight retry loop against the endpoint rate-limiting us to
  prevent exactly that. This is not negative caching of the *answer*, which remains rejected: no
  fallback distance is ever stored, distances already routed are still served throughout the
  window, and the estimate is recomputed fresh on every render. Sixty seconds, not a day, so
  recovery is still felt as immediate
- Both arrival panels list at most three buses for one stop. A tapped stop previously rendered
  every arrival while the list below it capped at three, so the same stop could show six above and
  three below — a contradiction rather than two views

### Known Limitations
- **Without an `ORS_API_KEY`, walk times remain straight-line estimates**, labelled
  `(estimated)`. A stop across an uncrossable barrier will read as far nearer than it actually is
  — the fallback has no way to know the barrier exists. This is the same gap the routing feature
  exists to close; it is not closed for anyone who has not configured a key
- **The 800 m radius that decides which stops count as "nearby" is still straight-line**, even
  when a key is configured and routing is available for the stops it selects. Only the walk time
  quoted for each already-selected stop is routed — a stop 750 m away on the map could be well
  over 800 m on foot and still appear in the list, or a stop just past 800 m by the crow that is
  genuinely closer by footpath will not appear at all

## [2.6.0] - 2026-07-31

### Fixed
- **"Nothing inbound" no longer hides a bus that is on its way.** Nearby stops were truncated to
  the five closest *before* any arrival was computed. Measured from the reported location: 15 stops
  sit within 800 m, and the only one with a bus inbound ranked 9th at 585 m — so every stop on
  screen reported nothing while a bus was en route to one the panel never evaluated. The nearest 40
  stops in range are now evaluated rather than the nearest 5, and those with a bus coming are shown
  first. Forty is a bound on work, not a claim of completeness — the largest 800 m neighbourhood in
  the network is 41 stops and the median is 13, so it covers every real case but one, and the
  README and the code now say so instead of claiming every stop is evaluated
- **Nearby stops with a bus coming are no longer dropped in silence.** At most five stops are
  listed, and a neighbourhood with more than five served stops is ordinary — the median holds 13
  stops in total. The panel now says how many further stops also have buses coming. A stop the app
  evaluated, found a bus inbound for, and then never mentioned is the original bug in miniature
- The wording said "nothing inbound right now", which reads as *no bus ever serves this stop*. It
  now says "no bus currently en route to this stop", and the panel-level message names the radius
- **"Nothing is coming" is no longer claimed on the evidence of one route.** The arrivals panel
  reads the same frame the map draws, which a route search narrows to the searched route. Someone
  searching T580 was then told "no buses are currently en route to any stop within 800 m of you" —
  a statement about every route, made from evidence about one, and quite possibly untrue. With a
  search active both the per-stop and the panel-level wording now name the route being searched
  and say that other routes are hidden
- **The tapped-bus panel is legible.** It ran six facts into one sentence, and the staleness note
  welded itself onto the stop name — *"position 1 min old, so less certain at KL2324 LRT AWAN
  BESAR"*. Route, path, arrival, walk and staleness are now labelled lines
- **The tapped-bus panel no longer drops the lateness or the destination.** Rebuilding it as
  labelled lines quietly lost both. Only Rapid Bus KL runs to a headway; twelve of the thirteen
  agency feeds publish no `frequencies.txt` at all, so lateness is a real, knowable number for
  roughly 18,500 trips — KTM, myBAS Johor, MRT Feeder, Penang. Outside KL the stop list showed a
  bus as six minutes late while the tapped panel, for the same bus, showed it as merely due. Both
  panels state the same facts again, each in the shape that fits it, and a delay that is genuinely
  not knowable is still rendered as nothing at all — never as zero, never as "on time"
- **The test guarding the headway-honesty rule was vacuous.** It asserted that the tapped panel
  says nothing about lateness on a headway trip — but that panel had lost the ability to say
  anything about lateness at all, so the assertion passed unconditionally and would have kept
  passing with the frequency-suppression logic deleted outright. Nothing anywhere asserted the
  positive rendering either. `format_arrival` is now tested directly on all three cases: a measured
  delay renders "6 min late", an unknowable one renders no lateness text, and a sub-minute one is
  treated as noise and stays silent
- **`get_route_parts` re-opened a 1.7 MB ZIP on every render** — every 20 seconds with a bus
  selected and auto-refresh on. `routes.txt` is now parsed once per agency and kept, the same way
  this module already treats its trip and route-name indexes. A failed read is deliberately not
  cached, so one feed outage cannot blank every route name until the process restarts

### Added
- Nearby stops are drawn on the map beneath the vehicles, so their position is visible rather than
  only named
- Stop names link to Google Maps for walking directions, which this app deliberately does not
  compute itself
- `gtfs_static.get_route_parts` — a route's short and long names kept separate. `get_route_name` is
  a strict subset of it and is now expressed in terms of it, so the two can never disagree about a
  route and both share the one parse of `routes.txt`

### Known Limitations
- **Nearby-stop markers have no hover tooltip.** The layer is deliberately `pickable=False` — hover
  does not exist on the touch devices this app is actually used on, and making the layer pickable
  risked polluting the working vehicle tooltip with a field that couldn't be filled in reliably.
  Stop names remain visible and linked to Google Maps in the panel below the map

### Notes
- The reported "why is Pavilion Bukit Jalil written twice" is inherent to the feed, not a bug:
  `route_short_name` is `PAVILION BUKIT JALIL (PAVBJ)` — a place — and `route_long_name` is
  `Stesen LRT Awan Besar ~ Pavilion Bukit Jalil`, the path through that same place. Labelling the
  two makes it readable; stripping either would lose information

## [2.5.3] - 2026-07-31

### Fixed
- **"Clear bus selection" now actually clears.** The chart's selection lives in Streamlit widget
  state keyed by an id derived from the deck spec, so when nothing on the map had changed between
  renders the id was unchanged, Streamlit handed back the same payload, and the code re-adopted the
  vehicle it had just dismissed — the panel stayed stuck on the chosen bus. Clearing now bumps a
  generation counter folded into the chart key, making it a new widget, and additionally ignores
  the dismissed vehicle for exactly one render in case a stale payload still arrives. Reported from
  live use; predicted by the 2.5.0 review and previously deferred
- The one-render guard is deliberately not permanent: a first attempt remembered the dismissed
  vehicle indefinitely, which silently cost the user the ability to tap that same bus again. A test
  pins both halves — clearing sticks, and reselecting still works

## [2.5.2] - 2026-07-31

### Documentation
- Added `eta.py` and `dbt_runner.py` to the README's project-structure tree. `eta.py` is the
  module this release is built on and was missing entirely; `dbt_runner.py` had been absent since
  2.2.0
- The "ETAs from the timetable" design-decision row said arrivals are shifted "by its measured
  delay" without qualification, which the row immediately below it contradicts — for 99% of Rapid
  Bus KL trips there is no measurable delay. It now says "where one can be measured"

## [2.5.1] - 2026-07-31

### Fixed
- **Three tests passed locally and failed on CI**, breaking the build on `main`. The page stub let
  `st.pydeck_chart` return a bare `MagicMock`, so the selection parsing produced a `MagicMock`
  vehicle id which reached `df_map['vehicle_id'] == picked`. Whether that survives depends on the
  pandas string dtype: an object-dtype column quietly compares `False`, an Arrow-backed one raises
  `NotImplementedError`. Local pandas infers object, CI infers Arrow. The stub now defaults to
  "nothing selected", and the suite is verified green under both dtype regimes
- The selection id is now coerced to a string before it meets that comparison, so an unexpected
  payload type from Streamlit matches nothing instead of taking the page down

## [2.5.0] - 2026-07-31

### Added
- **"Arrivals near you"** — the Live Map now answers *"I am standing here; what can I catch?"*
  It finds stops within 800 m of your location and lists the next buses to each, with an estimated
  arrival and the route's destination. No route knowledge and no map reading required
- **Tap a bus** to see when that specific vehicle reaches your nearest stop on its trip. The
  selection is held in session state and re-resolved from the current frame each render, so
  auto-refresh advances the bus without dropping the panel; a control clears it
- `src/utils/eta.py` — arrival estimation as pure, testable functions: `haversine_m`,
  `nearest_stop_index`, `walking_minutes`, `service_day_epoch`, `estimate_delay_seconds`,
  `compute_eta_seconds` and `arrivals_for_stops`
- `gtfs_static.get_trip_stops`, `get_stops_near`, `get_trip_headsign`, `is_frequency_based` and
  `parse_gtfs_time` — timetable lookups, with `stop_times.txt` (~88,000 rows for Rapid Bus KL)
  parsed once per agency into a trip-keyed index rather than per interaction

### Changed
- Minimum Streamlit raised to **1.40** for map click selection (`selection_mode` / `on_select`)
- The map tooltip now names the local clock time a vehicle last reported, rather than a relative
  age. A per-second string was recomputed on every render, and the deck spec — data included — is
  hashed into the chart's widget id, so it churned that id continuously

### Notes
- **Lateness is not claimed for every bus, because it is not knowable for every bus.** 2,099 of the
  2,102 Rapid Bus KL trips are published in `frequencies.txt` with `exact_times=0`: they run to a
  headway, so their `stop_times.txt` rows are a travel-time template repeated across an operating
  window rather than scheduled wall-clock times, and no start time exists to be late against. For
  those trips `arrivals_for_stops` reports `delay_seconds` as `None` — unknown, not zero — and the
  UI renders no lateness clause at all. **The arrival is unaffected and stays correct**: the
  service-day epoch and the bus's own scheduled time cancel algebraically, so the estimate uses only
  the *differences* between stop times, which is exactly what a headway template encodes
- The provider publishes vehicle positions only — trip updates are on their 2026 roadmap — so every
  arrival here is derived locally from the published timetable. It is accurate to about one stop and
  is labelled as an estimate throughout
- GTFS times legitimately exceed 24:00:00 (`25:30:00` means 01:30 the next day). They are handled
  as integer seconds since service-day midnight, never as clock times
- Known limitation: the service day is derived from the vehicle's own timestamp because ingestion
  does not capture the trip descriptor's `startDate`. A trip that begins before midnight and runs
  past it therefore resolves against the following service day. What that corrupts is the **delay**,
  not the arrival — the epoch cancels out of the arrival entirely. A bus at 00:30 on a 23:50-start
  trip that is genuinely 40 minutes late computes as roughly −1,400 minutes, which the "late"
  threshold then suppresses, so a late bus reads as on time. Rapid KL services largely end by
  midnight, so this is accepted for now
- Walking time is a straight-line distance at a fixed pace, not a routed path
- `arrivals_for_stops` reports its skip reasons as three separate counters — `no_trip_id` (including
  a `NaN` trip_id, which would otherwise stringify to `'nan'`), `trip_not_in_schedule`, and
  `bad_position` for unusable telemetry (coordinates missing, non-finite or out of range, **or an
  unreadable timestamp**). An unreadable clock is not a missing timetable entry, and saying so
  explained the omission wrongly. The UI surfaces every non-zero counter, so a vehicle omitted from
  the list is never omitted silently
- A trip that revisits a stop — 1,003 of 2,096 Rapid Bus KL trips do, up to 8 times — is listed once
  per stop at its earliest arrival, not once per visit
- `compute_eta_seconds` returns `None` for a stop that does not exist on the trip, and a negative
  integer for a stop the bus has already passed. Those are deliberately different values —
  collapsing both to `-1` would make missing data read as "arriving now"
- The per-agency trip index is keyed on the cached ZIP's mtime, so a refreshed 24-hour cache rebuilds
  it rather than serving a superseded timetable. A failed build stores nothing, so one network blip
  no longer caches "this agency has no timetable" for the life of the process
- Verified with the automated suite (153 tests) and parse checks against the cached Rapid Bus KL
  feed; not yet exercised in a running browser

## [2.4.2] - 2026-07-31

### Fixed
- **An active route search is no longer wiped by a refresh.** The map kept the selected region in
  two places — `st.session_state.selected_region` and the selectbox's own `key` — and cleared the
  search whenever they disagreed. A keyed widget's stored value wins over its `index`, so the two
  could drift apart without the user touching anything, and every drift dropped the filter for
  exactly one render. That is the reported "first auto-refresh shows every bus in the region, then
  it behaves". The search now clears only when the selectbox's value genuinely changes from its own
  previous value

## [2.4.1] - 2026-07-31

### Fixed
- **Route search now says *why* it found nothing.** Searching `t580` returned only "No live
  vehicles found on 't580' right now", which is indistinguishable from a broken search when the
  region is wrong. The message now names the region and separates three cases: the route runs here
  but nothing is reporting; the route is not published here but *is* published elsewhere (naming
  which regions); or no region publishes it at all
- **The silently auto-selected region is now visible.** `get_sorted_regions` only puts the primary
  region first when it has live vehicles, so a quiet Rapid Bus KL left the user in an unrelated
  region — myBAS Kuching, in the reported case — with nothing on screen saying so. When the app
  picks a region rather than the user, it now says which and why

- **A flaky test that would have reddened CI at random.** `test_sync_time_is_never_in_the_future`
  captured `now` itself while the code under test reads the clock independently, so it failed by
  exactly one second whenever the wall clock ticked between the two. The clock is now frozen for
  that test; verified over 20 consecutive runs

### Added
- `gtfs_static.region_has_route` and `gtfs_static.find_regions_for_route` — case-insensitive
  lookups over `routes.txt`. The cross-region search skips an agency whose feed is unavailable
  rather than failing entirely (`rapid-bus-kuantan` currently returns 404), and memoises its index;
  measured ~5s cold, ~0ms afterwards, so the cold path shows a spinner

### Notes
- Reported alongside these: pressing Enter in the search box with an unchanged value appeared to do
  nothing. That is correct Streamlit behaviour and not a defect — the value persists, the filter
  re-runs on every render, and the screenshot's own warning text proves the search had executed. It
  read as broken only because the message did not explain itself, which is what the first fix above
  addresses

## [2.4.0] - 2026-07-30

### Fixed
- **Regions no longer vanish from the Live Map.** The freshness window was anchored to the newest
  timestamp anywhere in the table, and ingestion accepts timestamps up to 5 minutes in the future —
  so a single vehicle with a fast clock shifted the window past every bus reporting honestly, and
  whole regions blacked out for a refresh at a time. Observed live: 101 buses across 7 regions
  while Rapid Bus KL, the largest network, showed "No valid data". The window is now anchored to
  wall-clock time and vehicle ages are clamped at zero, so no feed's clock can move it
- **An ingestion outage now names when data was last seen, on screen.** Previously the app reported
  "No data. Click 'Refresh Data' to fetch." — which reads as *nothing was ever ingested*. The
  database layer was fixed to keep reporting a sync time through an empty window, but both pages
  early-returned on the empty frame *before* the banner, so the user still saw the old string. The
  Live Map and Analytics now distinguish "stale — data last seen at HH:MM:SS" from "nothing ingested
  yet", with the window wording derived from `LIVE_HIDDEN_SECONDS`
- **"Total Active Buses" no longer reads 0 above a map full of buses.** Manual refresh is the
  default, so 60 seconds after a fetch every vehicle fell out of the fresh-only headline count while
  the map still drew all of them — a screen showing *Total Active Buses 0*, a caption saying 101
  were dimmed, and a footer reading "Showing 101 active vehicles". The headline metric now counts
  what is drawn, with a separate **Stale** metric beside it, and "active" means the same thing in
  the header and in the caption under the map
- The Live Map's stale caption claimed network-wide vehicles were "shown dimmed on the map" while
  the map shows one region — "47 vehicles shown dimmed" over two dimmed buses. The header metrics
  are now labelled as network-wide, and the per-region dimmed count is reported in the caption under
  the map, where it can be checked against the dots on screen
- Analytics' **Moving Vehicles** counted every row of the live frame. That frame widened from 60
  seconds to 15 minutes in this release, so the metric had silently become "moved at some point in
  the last 15 minutes". It counts fresh rows only again
- The "Data updated:" banner is clamped to now. It was fed by `MAX(timestamp)`, and ingestion
  accepts timestamps up to `DATA_FUTURE_TOLERANCE` (300s) ahead, so a feed with a fast clock could
  make the app claim its data arrived up to five minutes in the future
- A region with no rows inside the fetch window showed the bare "No valid data for {region}" — the
  original bug report's symptom, with no explanation. It now distinguishes "nothing reported in the
  last 15 minutes" from "reported, but with unusable coordinates"
- **A `config.py` predating this release no longer loses every one of its settings.** The new
  `LIVE_*` knobs were added to the existing all-or-nothing `from config import (...)` tuples in
  `utils/db.py` and `app_pages/live_map.py`, so a config file without them raised `ImportError` for
  the *whole* tuple and the app silently fell back to hardcoded defaults for `DATABASE_NAME`,
  `DATABASE_TABLE`, `TIMEZONE`, `UTC_OFFSET_HOURS`, `DATA_RETENTION_DAYS`, `DEFAULT_ZOOM` and
  `ARROW_SIZE` — pointing a customised install at a different DuckDB file with no error. Knobs
  added after `config.example.py` was last copied are now read one at a time via `getattr`, so a
  missing one falls back alone
- `classify_freshness` no longer raises `ValueError: Bin edges must be unique` when
  `LIVE_FRESH_SECONDS` and `LIVE_STALE_SECONDS` are set equal (or inverted). Both are documented as
  user-tunable, and equal bounds simply mean "no stale band" — the tiers degrade to fresh/hidden
  instead of taking the page down. A row whose timestamp is missing or unparseable is aged past
  every bound, so it lands in `hidden` rather than being drawn as current

### Added
- **Vehicle freshness tiers on the Live Map.** Vehicles reporting within 60s are drawn as before;
  those between 60s and 5 minutes are drawn dimmed with a "last update" line in their tooltip; those
  older than 5 minutes are hidden but reported in a caption rather than silently dropped
- `data_processor.classify_freshness` — pure, testable tier assignment
- `data_processor.format_duration` — turns a configured window into UI wording, so the Live Map's
  copy tracks the `LIVE_*` knobs instead of hardcoding "5 minutes" beside a value the user can change
- `LIVE_FRESH_SECONDS` (60), `LIVE_STALE_SECONDS` (300) and `LIVE_HIDDEN_SECONDS` (900) config knobs.
  **Upgrading:** `config.py` is gitignored and generated by copying `config.example.py`, so an
  existing install's config will not have these. It does not need them — each new knob now falls
  back to its default on its own, leaving every setting you *did* customise intact. Copy the three
  `LIVE_*` lines from `config.example.py` only if you want to tune the freshness tiers
- A **Stale** metric beside the existing Live Map metrics, as the design spec asked for — a fourth
  header metric, not a caption

### Changed
- The live window widened from 60 seconds to 5 minutes of drawn vehicles, so a bus that reported
  90 seconds ago is now visible (marked stale) instead of disappearing
- `get_live_data_optimized`'s metrics gain `fresh`, `stale` and `hidden` counts, and **`total`
  changed meaning**: it now counts the drawn set (fresh + stale, i.e. everything on the map) rather
  than fresh rows only. `fresh` + `stale` = `total`. The previous number is not comparable to the
  old pre-2.4.0 headline either way — that one meant "within 60s *of the newest row in the table*",
  which under manual refresh was always populated, whereas a fresh count is measured against
  wall-clock now and decays to zero between refreshes. Read `metrics['fresh']` for the
  reporting-right-now count

### Docs
- The README pipeline diagram still labelled the Live Map "last 60s" — it is now 15 minutes fetched
  and 5 minutes drawn. It sits one screen above the Key Design Decisions table this release updated,
  so the contradiction was visible at a glance. The table gains a row spelling out the three
  windows, and the two "sub-minute freshness" asides now say what the direct query actually buys
  (up-to-the-second positions and ages), rather than describing the pre-2.4.0 window

### Notes
- Measured while diagnosing this: the Rapid Bus MRT Feeder feed published a timestamp of
  `1886017556` — roughly the year 2029. Ingestion rejects it, but `myBAS Kuching` and `myBAS Melaka`
  were simultaneously reporting +3s and +10s, so mildly future-dated timestamps are routine in
  these feeds rather than exotic
- `DATA_FUTURE_TOLERANCE` is deliberately unchanged. Clamping ages at zero removes its ability to
  distort the window, so tightening it would treat a symptom that is already fixed and would start
  rejecting real data from feeds whose clocks run slightly fast
- Verified by the automated test suite and parse checks; not yet exercised in a running browser.
  The outage banner and the "no rows in the window" message are now covered by page-level tests that
  drive `live_map.show()` / `analytics.show()` against a stubbed Streamlit — the previous db-layer
  test asserted only that `sync_time_str` was not `None`, which passed while the user-visible string
  was still the old one

## [2.3.1] - 2026-07-30

### Fixed
- A route search that matched nothing no longer recentres the map. The camera now follows what is
  actually displayed: it moves for a search that filtered the map, and for a search cleared or
  mistyped back to an unfiltered view, but a typo no longer jumps the viewport to the region mean
- Renamed the Network Health summary metric from **⚫ No Feed** to **⚫ Not scored**. That bucket is
  `scoreable_fetches = 0`, which also holds regions we rate-limited ourselves — those do have a
  feed. The per-region cards already named their own cause; only the roll-up label overclaimed

## [2.3.0] - 2026-07-30

### Added
- **Route name search on the Live Map** — type a route (e.g. `T580`) to show only the vehicles running it. Matches the route number or any part of its name (`awan besar` works), case-insensitively, within the selected region. Hidden for KTM Berhad, whose realtime feed carries no `route_id`
- `data_processor.filter_by_route` — pure, testable route filtering
- `fetch_status` column on `fetch_quality_log`, classifying every fetch as `OK`, `EMPTY`, `NO_FEED` (HTTP 404), `THROTTLED` (HTTP 429) or `ERROR`, with an additive migration for existing databases
- `mart_network_health` gains `scoreable_fetches`, `feed_unavailable`, `no_feed_count` and `throttled_count`
- `reliability_score_without_reporting` dbt macro — the score with the reporting-rate term dropped and the remaining weights renormalised, for a fetch cycle that received nothing and therefore has no reporting rate to score
- **Raw Fetch Log now shows `fetch_status`** as a `Status` column, and exports it. Without it the one screen built for forensic drill-down showed `Dropout: true` with no reason, contradicting the "Feed unavailable" card above it

### Changed
- **Reliability scores now reflect the agency, not the plumbing.** `NO_FEED` and `THROTTLED` fetches are excluded from scoring, and `EMPTY` (feed healthy, no service running) no longer counts as an outage. Availability is now `1 − errors ÷ scoreable fetches`. The `reliability_score` formula itself is unchanged — only which rows feed it
- Regions with no scoreable fetch render a neutral "Feed unavailable" card and a ⚫ Not scored count, instead of a misleading low score. The card names the actual cause from `no_feed_count`/`throttled_count` — "withdrawn upstream" is claimed only for a feed that really did return 404, never for one we rate-limited ourselves
- **The scorecard and its sparkline now agree.** Both marts score a cycle that received nothing on the terms that apply, renormalised, instead of the aggregate skipping the undefined reporting rate while the trend substituted a zero. A region with one `OK` and one `EMPTY` cycle previously read aggregate **100** above a sparkline dipping to **60** — the same region, window and macro telling two stories, most visibly on KTM Berhad's alternating in-service/out-of-service pattern
- `dropout_count` and `avg_data_lag_seconds` are computed over scoreable fetches only, so neither can contradict the score printed beside it. `dropout_count` is also relabelled on the card as *quiet cycles* — it has not been a scoring input since this release, and a bare 🚫 count next to a green score read as a bug
- The drill-down shows a "not scored" note instead of a titled, axis-labelled, entirely blank chart when a region has no scoreable fetch in the window
- Searching a route now recentres the map on the matches. Previously the view was recomputed only on a region change, so a search made while panned elsewhere filtered the layers but left the camera behind — a "Showing 3 vehicle(s)" banner over an empty map. The viewport stays sticky across auto-refresh, as before
- The caption under the map no longer reports a filtered match count as the region total while a search is active
- `_fetch_endpoint` returns `(vehicles, duration_ms, status)`; `_build_quality_stats` takes `status_by_region`
- `fetch_quality_log` inserts now name their columns explicitly rather than relying on positional order

### Fixed
- A fetch cycle in which every region fails now writes a quality-log row explaining why, instead of returning silently
- **The new marts now actually take effect on an existing database.** Marts are views, so a database that had already run 2.2.x kept the previous release's SQL while `dbt_runner.ensure_dbt_models` returned early on their mere presence: `fetch_status` was written faithfully and then ignored, and Kuantan kept scoring 20. The bootstrap now probes `mart_network_health` for this release's columns and re-runs dbt when they are missing. The never-raise guarantee and the failure cooldown are unchanged
- `data_processor.filter_by_route` returns a copy rather than a slice — the live map assigns `arrow_path` onto the result, which raises `SettingWithCopyWarning` on pandas 2.x
- The quality-log insert defaulted a missing `fetch_status` to `OK` while `_build_quality_stats` defaulted the same unknown to `ERROR`; both now record `ERROR` rather than fabricating health

### Documentation
- README: the Network Health score breakdown said "reporting rate, dropout count, average data lag". Dropout count stopped being a scoring input in this release — the breakdown is reporting rate (40%), availability (40%), data lag (20%)
- README: the `fetch_quality_log` design-decision row did not mention `fetch_status`, the column the whole honest-scoring change rests on; the schema table already listed it
- README: documented the two-macro score, the mart-currency check in the bootstrap, and that route search recentres the map
- Design spec: the "Integration point" section claimed automatic view centring followed from filtering before the layers are built. It does not — centring is deliberately sticky and needed its own trigger. Corrected, along with the mart column list and the unavailable-card copy

### Notes
- Observed upstream: `prasarana?category=rapid-bus-kuantan` returns HTTP 404 (*"feed does not exist"*), which is why that region previously scored 20. It is still listed in the provider's documentation and may return
- Rows written before this migration have `fetch_status = NULL`, which the staging model coalesces to `OK`. For a historical row that had `total_dropout = true`, the old scoring counted it as a dropout-driven outage; the new scoring does not, since a coalesced `OK` is never `ERROR`. Such rows therefore score **higher** retroactively than they did before the migration — this is an intentional side effect of no longer treating a bare dropout as proof of an outage, not a bug, and it is self-limiting: those rows age out of the 7-day retention window within a week of this release
- Dropping the reporting term for cycles that received nothing raises the score of a region that was quiet for the whole window (e.g. an all-`EMPTY` 24h now scores 100 rather than 60). That is the same judgement the release already makes — a feed that answers correctly with no service running is healthy — applied consistently to the window as well as to the individual cycle
- Known limitation: because `EMPTY` is treated as healthy, an outage where a feed responds but returns nothing during service hours no longer reduces the score. Separating that from "no service scheduled" needs GTFS `calendar.txt`

## [2.2.1] - 2026-07-30

### Changed
- Roadmap: replaced the planned **Route Planner** with **search by route name**. Origin→destination
  journey planning duplicates Google Maps without improving on it; the unmet need is the inverse —
  when you already know your route, see where those specific vehicles are right now.
- Roadmap: marked the dbt analytics layer complete.

### Documentation
- Recorded that live vehicle positions for LRT/MRT/Monorail are **not available**: the
  `prasarana?category=rapid-rail-kl` vehicle-position feed returns a 404 (*"feed does not exist"*),
  and the API currently publishes vehicle positions only — trip updates and service alerts are on
  the provider's 2026 roadmap. Rail **static** data (8 lines, stops, shapes, frequencies) is
  available, and KTM Berhad realtime is already ingested.

## [2.2.0] - 2026-07-29

### Added
- **dbt (dbt-duckdb) analytics layer** under `transform/` — bronze sources (`live_buses`, `fetch_quality_log`), silver staging views, and three gold mart views (`mart_network_health`, `mart_region_health_trend`, `mart_region_vehicle_counts`)
- `reliability_score` dbt macro — single definition of the 0–100 score formula, replacing the copy in two `db.py` functions
- dbt data tests: region `accepted_values` (the 14 canonical regions), 0–100 score and 0–1 rate range checks, key `not_null`/`unique`, and a composite-uniqueness test pinning `mart_region_health_trend` to its documented `region + fetch_timestamp` grain
- Local generic test macros `accepted_range` and `unique_combination_of_columns` in `transform/macros/` — the project has **no dbt package dependencies**, so `dbt run` works on a fresh clone and on Streamlit Cloud without a `dbt deps` step
- Model, source, and column `description:` metadata throughout, so `dbt docs generate` renders a documented project
- GitHub Actions CI running `dbt build --target ci` (seed → run → test) and pytest on every push/PR, plus a separate informational `dbt source freshness` step (`dbt build` does not run freshness; the committed fixtures carry fixed, old timestamps, so the step is `continue-on-error`)
- `dbt_runner.ensure_dbt_models` — creates the mart views once after the first ingestion (bootstraps Streamlit Cloud). It requires **all three** marts before skipping, backs off after two consecutive failed attempts and automatically retries once a 10-minute cooldown elapses (so a doomed bootstrap cannot stall every ~20s auto-refresh, but a transient failure — e.g. a DuckDB write-lock collision or the 120s subprocess timeout — cannot lock analytics out for the life of a long-running Streamlit Cloud process either), and invokes dbt via `sys.executable -m dbt.cli.main` rather than a bare `dbt` on `PATH`
- Model lineage diagram (Mermaid) in the README's new "Data Modeling (dbt)" section
- Regression test (`tests/test_dbt_marts.py`) asserting `dbt ls --target dev --resource-type seed` returns no seed nodes while `--target ci` does — guarding the seed-disabled-on-`dev` data-loss fix with an explicit check

### Changed
- Network Health reads (`get_network_health_summary`, `get_region_health_trend`) and the Analytics region charts now query dbt marts instead of inline SQL; the live-map path is unchanged
- `get_network_health_summary()` no longer takes a `window_hours` argument — the mart bakes in the 24h window, so the parameter was ignored — and now orders explicitly by `reliability_score DESC` instead of relying on a view's inner `ORDER BY`
- dbt seeds are enabled only on the `ci` target. They are CI fixtures named identically to the real app tables, so an unqualified `dbt build` against a live database would have truncated ingested history; the documented local command is now `dbt run` / `dbt test`
- The `ci` target now resolves its DuckDB path from its own `DBT_CI_DUCKDB_PATH` env var (default `ci.duckdb`), never `DBT_DUCKDB_PATH` — closing a gap where a shell that had exported `DBT_DUCKDB_PATH` to the real app database would have had `dbt build --target ci` truncate it, since seeds are enabled on that target
- Minimum Python raised to **3.9** (`pyproject.toml`, README badge) — dbt-core does not support 3.8; CI runs 3.11
- Analytics page now shows a refresh hint instead of a blank bar and pie when regional vehicle counts are unavailable

### Fixed
- Corrected two stale entries in the README's "Key Design Decisions" table that had drifted since 2.1.1: the fetch guard is a **3s** window (not 15s), and the `@st.cache_data(ttl=60)` row was removed entirely — that caching was dropped in 2.1.1 and `st.cache_data` is no longer used anywhere in `src/`
- Corrected the matching stale "last 15 seconds" comment on the fetch guard in `src/utils/ingestion.py` (the guard queries a 3-second window)

## [2.1.2] - 2026-05-14

### Added
- Map tooltip now shows human-readable route name (e.g. "T580 — Terminal Bersepadu Selatan – Klang") resolved from GTFS Static `routes.txt`, falling back to raw `route_id` if no name is found

## [2.1.1] - 2026-05-04

### Fixed
- `_write_quality_log` now uses a dedicated DuckDB connection opened after the main connection is fully closed, preventing write lock conflicts
- Parameterised `INSERT` in `_write_quality_log` replaces f-string construction, fixing a silent write failure caused by type coercion
- Fetch guard window tightened from 15 s to 3 s to reduce unnecessary skip rate while still preventing concurrent collisions
- `inserted_by_region` count now uses a `SELECT COUNT … WHERE insert_timestamp = …` query instead of the SQLite-only `changes()` call, which returned 0 on DuckDB
- Network Health page now follows the same refresh pattern as all other pages (no `st.cache_data`, manual or auto-refresh triggers a fetch)
- All `st.plotly_chart` calls given unique `key=` arguments to prevent duplicate element ID errors on re-render
- Removed debug instrumentation (`DIAGNOSTICS` dict and in-page debug panel) added during diagnosis

## [2.1.0] - 2026-05-02

### Added
- 📡 Network Health page with per-region reliability scorecards, drill-down charts, and raw fetch log export
- `fetch_quality_log` DuckDB table tracking vehicles received/rejected/inserted, data lag, dropout status, and fetch duration per region per cycle
- Fetch guard preventing concurrent write collisions when multiple users trigger refresh simultaneously
- `_build_quality_stats` pure function for testable quality stat computation
- `get_network_health_summary`, `get_region_health_trend`, `get_region_fetch_log` DB read functions
- Reliability score formula: 40% reporting rate + 40% availability + 20% data freshness (0–100 scale)
- `fetch_quality_log` pruned with the same `DATA_RETENTION_DAYS` window as `live_buses`

### Changed
- `_fetch_endpoint` now returns `(vehicles, duration_ms)` tuple to support per-region timing

## [2.0.0] - 2026-04-03

### Fixed
- Accuracy circle now uses real GPS accuracy metres instead of hardcoded 30 px radius
- `get_historical_data` now queries a rolling 7-day window instead of the full table (prevents memory exhaustion on long-running instances)
- DuckDB connection leaks fixed with `finally` blocks across all DB functions
- `API_SOURCES` consolidated into `config.py` — `ingestion.py` imports from single source of truth
- `datetime.utcnow()` replaced with `datetime.now(timezone.utc)` (deprecated in Python 3.12+)
- Trail table speed converted to km/h for consistency with the rest of the UI
- `.DS_Store` untracked from git

### Added
- `DATA_RETENTION_DAYS` config constant (default: 7 days) for rolling data retention
- `PRIMARY_REGION` config constant replacing magic string in `data_processor.py`
- `prune_old_data()` function in `db.py`
- `requirements-dev.txt` with pytest; `tests/conftest.py` for sys.path setup
- Expanded test suite from 1 to 12 tests covering speed conversion, coordinate filtering, and region sorting

### Changed
- `pyproject.toml` now has complete dependency list and correct author email
