import time
import streamlit as st
import pydeck as pdk
import numpy as np
import pandas as pd
from streamlit_js_eval import get_geolocation as js_get_geolocation
from utils import db, data_processor, eta
from utils.ingestion import fetch_and_store_transit_data
from utils import gtfs_static
from utils import route_view
from utils import walking

try:
    from config import DEFAULT_ZOOM, ARROW_SIZE
except ImportError:
    DEFAULT_ZOOM = 13
    ARROW_SIZE = 0.001

# Read individually, not through the tuple import above: a config.py copied from
# config.example.py before these knobs existed lacks them, and naming them there
# would fail the whole import and throw away the user's DEFAULT_ZOOM/ARROW_SIZE
# along with them. See the same note in utils/db.py.
try:
    import config as _config
except ImportError:
    _config = None

LIVE_FRESH_SECONDS = getattr(_config, 'LIVE_FRESH_SECONDS', 60)
LIVE_STALE_SECONDS = getattr(_config, 'LIVE_STALE_SECONDS', 300)
LIVE_HIDDEN_SECONDS = getattr(_config, 'LIVE_HIDDEN_SECONDS', 900)
UTC_OFFSET_HOURS = getattr(_config, 'UTC_OFFSET_HOURS', 8)


def _ors_api_key():
    """
    The OpenRouteService key: config.py for local dev, Streamlit Secrets for
    cloud. None when unset, in which case walk times degrade to estimates.

    Both are needed. config.py is gitignored so it never reaches Streamlit
    Cloud, and no other code in this repository reads st.secrets — a past
    refactor replaced those reads with hardcoded defaults, so a key placed in
    Secrets was silently ignored until this function existed.

    The except is broad because Streamlit raises varied types when no secrets
    file exists at all, which is the normal case for a fresh clone. A missing
    optional key must never break a render.
    """
    key = getattr(_config, 'ORS_API_KEY', None)
    if key:
        return key
    try:
        return st.secrets['routing']['api_key'] or None
    except Exception:
        return None


def _walk_label(entry):
    """
    "~9 min walk", or "~5 min walk (estimated)" when routing was unavailable.

    The suffix is not decoration. A routed figure follows the real footpath; an
    estimate cannot see that KL1291 is 60 m away and 634 m on foot. Saying
    which one you are reading is the difference between an estimate and a
    claim.
    """
    suffix = '' if entry['routed'] else ' (estimated)'
    return f"~{entry['minutes']} min walk{suffix}"


# One definition of "within walking distance", shared by both arrival panels and
# by the copy that explains them, so the number in the message can never drift
# from the number actually applied.
NEARBY_STOP_RADIUS_M = 800

# Evaluate the nearest NEARBY_STOP_SCAN_LIMIT stops in range, then show the
# useful ones. Truncating by distance first hid a stop that had a bus inbound
# behind five closer stops that had none.
#
# The scan cap is a bound on work, not a claim of completeness: measured
# against the real feed, the largest 800 m neighbourhood in the whole Rapid KL
# network is 41 stops and the median is 13, so 40 covers effectively every real
# case — but "effectively every" is not "every", and in that one neighbourhood
# the farthest stop is never looked at. Nothing in the UI may say otherwise.
NEARBY_STOP_SCAN_LIMIT = 40     # candidates evaluated
NEARBY_STOP_DISPLAY = 5         # stops rendered
NEARBY_STOP_MIN_SHOWN = 3       # filled with unserved stops when few are served

# Arrivals listed under a single stop, in both panels. One constant because the
# two panels answer the same question about the same stop: a tapped stop that
# listed six buses where the panel below it listed three read as a
# contradiction rather than as two views.
ARRIVALS_PER_STOP = 3

# The same caveat wherever an arrival is shown.
ARRIVAL_ACCURACY_NOTE = (
    "Estimated from the published timetable — accurate to about one stop."
)


def format_route_heading(parts, fallback=''):
    """
    A route's identity as labelled lines rather than one run-on string.

    Rapid KL's short name is frequently a place — "PAVILION BUKIT JALIL
    (PAVBJ)" — and the long name is the path between two places, one of which
    is that same place. Joined with a dash and no labels, it reads as the name
    printed twice and leaves no way to tell which part is the route. Labelling
    answers that; the repetition itself is in the source data and is not ours
    to strip.

    The path line is omitted when it adds nothing: when it is identical to
    the short name, or when there is no short name at all, since without one
    the long name is promoted to the Route line above and repeating it as a
    Runs line would be the same duplication this task exists to remove.
    """
    short = (parts or {}).get('short') or ''
    long_ = (parts or {}).get('long') or ''
    name = short or long_ or fallback or '—'
    lines = [f"**Route:** {name.replace('~', '↔')}"]
    if short and long_ and long_ != short:
        lines.append(f"**Runs:** {long_.replace('~', '↔')}")
    return lines


def format_arrival(arrival, fresh_seconds=LIVE_FRESH_SECONDS):
    """
    One arrival as a single line, under a stop heading in the "Arrivals near
    you" panel.

    Both the stop-centric panel and the tap-a-bus panel show the same estimate
    for the same bus, so they must state the same facts about it — where it is
    going, when it gets there, how late it is, how old its position is, and the
    one-stop caveat — and qualify them identically. Only the shape differs:
    here they are one line beneath the stop's name, because the stop is already
    the heading; the tap-a-bus panel says the same things as labelled lines,
    because there the bus is the subject and the stop is one of the facts.

    They have twice drifted apart. The secondary first rendered a bare green
    box — a four-minute-old position reading as a confident "~6 min" — and
    then, once rebuilt as labelled lines, silently dropped the lateness and the
    destination, so a bus shown as 6 minutes late in one panel was shown as
    merely due in the other.

    A delay of None means "not knowable" — the trip runs to a headway rather
    than to the clock — and is stated as nothing at all, never as zero.

    "Route" is unconditional. Rapid KL names routes after places — PAVILION
    BUKIT JALIL is a route and a mall — so without the label a reader cannot
    tell which fact they are looking at.
    """
    line = f"Route {arrival.get('route_display') or '—'}"
    if arrival.get('headsign'):
        line += f" → {arrival['headsign']}"
    line += f" · arrives **~{max(1, round(arrival['eta_seconds'] / 60))} min**"

    delay = arrival.get('delay_seconds')
    if delay is not None and delay >= 60:
        line += f" · {round(delay / 60)} min late"

    age = arrival.get('age_seconds')
    if age and age > fresh_seconds:
        line += f" · position {round(age / 60)} min old"
    return line


def _tip_text(label, value):
    """
    One labelled line of plain tooltip text — no HTML tags, and the value is
    never escaped.

    Streamlit escapes HTML found inside pydeck tooltip *interpolations*
    (Streamlit bug fix #15820, an injection defence) but not markup written
    into the template itself. That means markup belongs in the template or
    nowhere: putting it in a per-row value like this one gets it escaped once
    by us and left alone by Streamlit, or — as shipped in 2.7.0 — escaped by
    both, so a stop named "A & B" rendered as the literal text "A &amp; B".
    This function deliberately picks nowhere. Stop and route names come from
    third-party feeds and are used exactly as given.
    """
    return f"{label}: {value}"


def _picked_stop_id(selection):
    """
    The tapped stop's id, or None.

    Mirrors the vehicle path exactly, including coercing to str before the id
    can meet a pandas comparison and a broad except for a payload that does not
    match the assumed shape.

    An empty id is rejected rather than returned. get_stops_near takes stop_id
    straight from stops.txt with only a .strip(), so a feed row with a blank id
    really does produce '' — and '' is falsy but not None, so returning it made
    the two guards downstream disagree: the selection was stored, the sticky
    bus selection was cleared to make room for it, and then the panel's own
    truthiness check declined to render anything. A dead tap that ate the
    previous selection.
    """
    try:
        objects = selection.selection.objects.get("nearby-stops", [])
        raw = objects[0].get("stop_id") if objects else None
    except (AttributeError, KeyError, IndexError, TypeError):
        return None
    return str(raw) if isinstance(raw, (str, int)) and str(raw) else None


def create_arrow_paths(lat, lon, bearing, size=ARROW_SIZE):
    """
    Generate arrow path geometry for pydeck PathLayer
    
    Args:
        lat: Latitude of vehicle position
        lon: Longitude of vehicle position
        bearing: Direction heading in degrees (0-360)
        size: Arrow size multiplier (default from config)
    
    Returns:
        list: Path coordinates [[lon, lat], ...] forming arrow shape
    """
    angle_rad = np.radians(90 - bearing)
    arrow_length, arrow_width = size * 2, size * 0.8

    sin_angle, cos_angle = np.sin(angle_rad), np.cos(angle_rad)
    tip_lat, tip_lon = lat + arrow_length * sin_angle, lon + arrow_length * cos_angle

    left_angle = angle_rad - np.radians(150)
    left_lat, left_lon = lat + arrow_width * np.sin(left_angle), lon + arrow_width * np.cos(left_angle)

    right_angle = angle_rad + np.radians(150)
    right_lat, right_lon = lat + arrow_width * np.sin(right_angle), lon + arrow_width * np.cos(right_angle)

    return [
        [lon, lat],
        [tip_lon, tip_lat],
        [left_lon, left_lat],
        [tip_lon, tip_lat],
        [right_lon, right_lat],
        [tip_lon, tip_lat],
    ]



def show():
    # Refresh behaviour
    if st.session_state.auto_refresh:
        # When auto-refresh is enabled, fetch data on every rerun
        with st.spinner('🛰️ Auto-refreshing...'):
            fetch_and_store_transit_data()
            st.session_state.last_refresh = True
    else:
        # Manual refresh button (only show if not auto-refresh)
        if st.button("🔄 Refresh Data", type="primary", use_container_width=False):
            with st.spinner('🛰️ Fetching...'):
                fetch_and_store_transit_data()
                st.session_state.last_refresh = True
            st.rerun()

    # Get data - single optimized query for current state
    df_live, metrics, actual_sync_time = db.get_live_data_optimized()

    window_label = data_processor.format_duration(LIVE_HIDDEN_SECONDS)
    drawn_label = data_processor.format_duration(LIVE_STALE_SECONDS)
    fresh_label = data_processor.format_duration(LIVE_FRESH_SECONDS)

    if df_live is None or df_live.empty:
        # An outage and an empty database look identical from an empty frame, so
        # say which one it is. actual_sync_time survives an empty window
        # precisely so this branch can name when data was last seen.
        if actual_sync_time:
            st.warning(
                f"⏳ No vehicle has reported in the last {window_label}. "
                f"The feed looks stale — data was last seen at {actual_sync_time}."
            )
        else:
            st.info("🛰️ No data. Click 'Refresh Data' to fetch.")
        return

    # Show sync time
    if actual_sync_time:
        st.success(f"Data updated: {actual_sync_time}")

    # Metrics. All four are network-wide, across every region — the map below
    # shows one region. "Active" means the same here as in the caption under the
    # map: reporting within LIVE_STALE_SECONDS, i.e. everything that gets drawn.
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "Active Buses", metrics['total'],
        help=f"Reported within the last {drawn_label} — every vehicle drawn on the map.",
    )
    col2.metric(
        "Stale", metrics['stale'],
        help=f"Of those, the ones last reporting over {fresh_label} ago. Drawn dimmed.",
    )
    col3.metric("Regions Monitored", metrics['regions'])
    col4.metric("Busiest Region", metrics['busiest'])
    st.caption(
        f"Network-wide across all regions. Active = reported within the last {drawn_label}; "
        f"the map below shows the selected region only."
    )

    # Hardcoded region list to prevent dropdown changes during auto-refresh
    try:
        from utils.ingestion import API_SOURCES
        all_regions = list(API_SOURCES.keys())
        # Sort with Rapid Bus KL first
        hardcoded_regions = ['Rapid Bus KL'] + sorted([r for r in all_regions if r != 'Rapid Bus KL'])
    except ImportError:
        # Fallback to dynamic list if import fails
        hardcoded_regions = data_processor.get_sorted_regions(df_live)
    
    # Get available regions from current data
    available_regions = data_processor.get_sorted_regions(df_live)

    if not available_regions:
        st.info("No active buses.")
        return

    # ===== LOCATE ME BUTTON SECTION =====
    st.markdown("### 🗺️ Map Controls")
    
    col_region, col_locate = st.columns([3, 1])
    
    with col_region:
        # Initialize selected region - preserve during auto-refresh
        auto_picked_region = None
        if st.session_state.selected_region is None or st.session_state.selected_region not in hardcoded_regions:
            # Try to use first available, otherwise use first hardcoded.
            # get_sorted_regions only puts the primary region first when it has
            # live vehicles, so when it is quiet this silently lands the user in
            # an unrelated region. Remember that so it can be surfaced below —
            # searching a route against a region you never chose looks like a
            # broken search, not a wrong region.
            st.session_state.selected_region = available_regions[0] if available_regions else hardcoded_regions[0]
            if st.session_state.selected_region != data_processor.PRIMARY_REGION:
                auto_picked_region = st.session_state.selected_region

        # Get current index safely from hardcoded list
        try:
            current_index = hardcoded_regions.index(st.session_state.selected_region)
        except ValueError:
            current_index = 0
            st.session_state.selected_region = hardcoded_regions[0]

        selected_region = st.selectbox(
            "Select Region",
            options=hardcoded_regions,
            index=current_index,
            key='region_selector_live_map',
        )
        # Clear a stale search only on a *genuine* region change, judged against
        # the selectbox's own previous value.
        #
        # This used to compare the widget against st.session_state.selected_region,
        # a parallel mirror of the same value. The selectbox carries both `index`
        # and `key`, and a keyed widget's stored value wins over `index`, so the
        # two could drift apart without the user touching anything — and every
        # drift silently wiped an active search for exactly one render. That is
        # the "first auto-refresh shows every bus, then it behaves" symptom.
        previous_region = st.session_state.get('_region_for_search')
        if previous_region is not None and previous_region != selected_region:
            # Safe here: this runs before the text_input below is instantiated
            # for this run, so clearing its key does not raise.
            st.session_state.pop('route_search_live_map', None)
            auto_picked_region = None
        st.session_state['_region_for_search'] = selected_region
        st.session_state.selected_region = selected_region

        if auto_picked_region == selected_region:
            st.caption(
                f"Showing **{selected_region}** — {data_processor.PRIMARY_REGION} "
                f"had no vehicles reporting, so the first region with live data "
                f"was selected."
            )

        # Route search — hidden for KTM, whose realtime feed carries no route_id
        if selected_region == 'KTM Berhad':
            route_query = ''
            st.caption("Route search is not yet available for KTM Berhad.")
        else:
            route_query = st.text_input(
                "Search route (e.g. T580)",
                value='',
                placeholder='Route number or name',
                key='route_search_live_map',
            )

    with col_locate:
        # Locate Me button
        st.markdown("<br>", unsafe_allow_html=True)  # Vertical alignment
        if st.button("📍 Locate Me", use_container_width=True, type="secondary", key="locate_btn"):
            st.session_state.getting_location = True
            st.rerun()
    
    # Get location if button was clicked
    if st.session_state.get('getting_location', False):
        with st.spinner("🌍 Getting your location..."):
            location_data = js_get_geolocation(component_key="geolocation")

            if location_data and isinstance(location_data, dict):
                coords = location_data.get('coords', {})
                if coords and 'latitude' in coords and 'longitude' in coords:
                    st.session_state.user_location = {
                        'lat': coords['latitude'],
                        'lon': coords['longitude'],
                        'accuracy': coords.get('accuracy', 0)
                    }
                    st.session_state.map_view_state = {
                        'latitude': coords['latitude'],
                        'longitude': coords['longitude'],
                        'zoom': 15,
                        'pitch': 0,
                    }
                    st.success(f"📍 Location found: {coords['latitude']:.4f}, {coords['longitude']:.4f}")
                    st.session_state.getting_location = False
                    st.rerun()
    
    # Display current user location if available
    if 'user_location' in st.session_state and st.session_state.user_location:
        with st.expander("📍 Your Location", expanded=False):
            loc = st.session_state.user_location
            col_loc1, col_loc2 = st.columns(2)
            with col_loc1:
                st.write(f"**Latitude:** {loc['lat']:.6f}")
                st.write(f"**Longitude:** {loc['lon']:.6f}")
            with col_loc2:
                st.write(f"**Accuracy:** ±{loc['accuracy']:.0f}m")
                if st.button("🗑️ Clear Location", type="secondary", use_container_width=True, key="clear_loc_btn"):
                    del st.session_state.user_location
                    st.rerun()

    # Filter and process data
    region_row_count = int((df_live['region'] == selected_region).sum())
    df_map = data_processor.prepare_map_data(df_live, selected_region)

    if df_map.empty:
        # "No valid data" was the original bug report's symptom and explains
        # nothing. Separate "the region reported nothing" from "it reported, but
        # the coordinates were unusable" — different causes, different fixes.
        if region_row_count == 0:
            st.warning(
                f"No vehicle in {selected_region} has reported in the last {window_label}."
            )
        else:
            st.warning(
                f"{region_row_count} vehicle(s) reported for {selected_region}, but none "
                "carried usable coordinates."
            )
        return

    # Hidden vehicles are counted but not drawn — a 7-day retention window would
    # otherwise fill the map with buses parked at depots overnight.
    hidden_count = 0
    if 'freshness' in df_map.columns:
        hidden_count = int((df_map['freshness'] == 'hidden').sum())
        df_map = df_map[df_map['freshness'] != 'hidden'].copy()

    if df_map.empty:
        st.warning(
            f"No recent data for {selected_region} — "
            f"{hidden_count} vehicle(s) last reported over {drawn_label} ago."
        )
        return

    # Create formatted columns for tooltip display
    df_map['speed_display'] = df_map['speed'].round(0).astype(int).astype(str)
    df_map['bearing_display'] = df_map['bearing'].round(0).astype(int).astype(str)

    # One fallback for a frame that never carried the freshness column (an
    # older cached frame, or a caller that skipped classify_freshness): treat
    # every row as fresh and full-strength.
    freshness_col = df_map.get('freshness', pd.Series('fresh', index=df_map.index))

    # Counted here, before any route search narrows df_map, so the caption under
    # the map describes the region rather than the search result.
    region_stale_count = int((freshness_col == 'stale').sum())

    # Stale vehicles keep their colour but drop to ~35% alpha, so they read as
    # present-but-uncertain rather than as a different kind of thing.
    is_stale = freshness_col == 'stale'
    df_map['dot_color'] = [
        [51, 153, 255, 90] if s else [51, 153, 255, 255] for s in is_stale
    ]
    df_map['arrow_color'] = [
        [255, 255, 255, 90] if s else [255, 255, 255, 255] for s in is_stale
    ]

    # The tooltip states *when* the vehicle last reported, not how long ago.
    # A relative age ("12s ago") is recomputed on every render, and the deck
    # spec — data included — is hashed into the chart's widget id, so a
    # per-second string churns that id and loses any map selection with it.
    # An absolute local clock time changes only when the bus genuinely reports
    # again, which is the only time the deck should change identity.
    df_map['last_report_display'] = [
        (time.strftime('%H:%M:%S', time.gmtime(int(t) + int(UTC_OFFSET_HOURS) * 3600))
         if pd.notna(t) else 'unknown')
        + ('' if f == 'fresh' else ' ⚠️ stale')
        for t, f in zip(pd.to_numeric(df_map['timestamp'], errors='coerce'), freshness_col)
    ]

    # Resolve human-readable route names from GTFS Static for all unique route_ids
    agency_slug = gtfs_static.STATIC_API_SOURCES.get(selected_region, '')
    if agency_slug and 'route_id' in df_map.columns:
        unique_routes = df_map['route_id'].dropna().unique()
        route_name_cache = {
            rid: gtfs_static.get_route_name(agency_slug, rid)
            for rid in unique_routes if rid
        }
        df_map['route_display'] = df_map['route_id'].map(
            lambda rid: route_name_cache.get(rid) or rid or '—'
        )
    else:
        df_map['route_display'] = df_map.get('route_id', '—').fillna('—')

    # Filter to the searched route. Applied after route_display is resolved and
    # before layers are built, so the layers, the view centring below, the
    # caption and the Route Viewer all reflect the filtered set.
    filter_active = False
    # What the user typed, in their own case, kept for the copy that has to
    # narrow its claims to the filtered frame. `current_query` below is the
    # same value lower-cased for a session-state comparison; echoing "t580"
    # back at someone who typed "T580" is a small wrongness this copy does not
    # need to make.
    filter_label = ''
    if route_query and route_query.strip():
        df_filtered = data_processor.filter_by_route(df_map, route_query)
        if df_filtered.empty:
            # Leave the map unfiltered: a blank map cannot be told apart from
            # a bad search term.
            #
            # Naming the region matters here. "No live vehicles found on 't580'"
            # is indistinguishable from a broken search when the region silently
            # defaulted to somewhere T580 does not run.
            q = route_query.strip()
            if agency_slug and gtfs_static.region_has_route(agency_slug, q):
                st.warning(
                    f"**{q}** runs in {selected_region}, but no vehicles are "
                    f"reporting on it right now."
                )
            else:
                # Cold path reads routes.txt from every agency — measured ~5s
                # the first time, then memoised. Worth a spinner so it does not
                # read as a frozen page.
                with st.spinner(f"Looking for '{q}' in other regions…"):
                    elsewhere = gtfs_static.find_regions_for_route(q)
                elsewhere = [r for r in elsewhere if r != selected_region]
                if elsewhere:
                    st.warning(
                        f"**{q}** is not a route in {selected_region} — "
                        f"it runs in **{', '.join(elsewhere)}**. "
                        f"Change the region above to see it."
                    )
                else:
                    st.warning(
                        f"**{q}** is not a route in {selected_region}, and no "
                        f"other region publishes it either."
                    )
        else:
            df_map = df_filtered
            filter_active = True
            filter_label = route_query.strip()
            matched = sorted(df_map['route_display'].unique())
            matched_label = ', '.join(matched[:3]) + ('…' if len(matched) > 3 else '')
            st.success(f"Showing {len(df_map)} vehicle(s) on {matched_label}")

    # Resolved once per render and passed down. It was being re-resolved at
    # each of the three walk-time call sites, re-reading config and st.secrets
    # every time for a value that cannot change within a render.
    ors_key = _ors_api_key()

    # One arrivals lookup, shared by the tapped-stop panel and the "Arrivals
    # near you" panel below.
    #
    # The two call sites were character-for-character identical — six
    # arguments including four identical lambdas — and both ran in full when a
    # stop was selected, so df_map.to_dict('records') materialised the whole
    # frame twice and arrivals_for_stops walked every vehicle twice, inside a
    # 20-second auto-refresh. The records are built at most once per render and
    # only when something actually asks for them.
    #
    # The tapped-*bus* panel is deliberately not routed through here: it asks
    # about one vehicle against the stops of its own trip, which is a different
    # question with different inputs.
    _records = {}

    def _arrivals(stops):
        if 'v' not in _records:
            _records['v'] = df_map.to_dict('records')
        return eta.arrivals_for_stops(
            _records['v'], stops,
            lambda t: gtfs_static.get_trip_stops(agency_slug, t),
            int(time.time()), UTC_OFFSET_HOURS,
            headsign_lookup=lambda t: gtfs_static.get_trip_headsign(agency_slug, t),
            frequency_lookup=lambda t: gtfs_static.is_frequency_based(agency_slug, t),
        )

    # Map style based on theme
    map_style = 'dark' if st.session_state.map_theme == 'dark' else 'light'

    # ---- Layer identity ----------------------------------------------------
    # Two rules govern every layer in the deck below, and tap-a-bus is dead
    # without both:
    #
    #   1. Once on_select is anything but "ignore", Streamlit requires *every*
    #      layer to declare an explicit `id`. A pdk.Layer built without one is
    #      assigned a fresh uuid4 on each construction, so an unnamed layer
    #      changes the deck on every render.
    #   2. st.pydeck_chart hashes the whole deck spec — layers, and the data
    #      inside them — into the chart's widget id. Anything in that data
    #      that is recomputed per render (a relative "12s ago" age, say)
    #      rewrites the id continuously, so the selection is written under one
    #      id and read back under another and never survives.
    #
    # Hence: stable ids everywhere, and each layer is handed only the columns
    # it actually draws or shows in its tooltip.
    # Each row carries its own rendered tooltip. A single Deck-level template
    # can only name fields of one layer, and pydeck prints unmatched keys
    # literally — so a shared vehicle template made every other layer
    # unpickable. Per-row text lets stops be tapped without touching this.
    #
    # Plain text, not markup: Streamlit escapes HTML found inside pydeck
    # tooltip interpolations but not markup written into the template, so
    # markup belongs in the template or nowhere — and this design chooses
    # nowhere. See `_tip_text` for the double-escaping bug that shipping HTML
    # here caused in 2.7.0. The five facts are joined with real newlines;
    # deck.gl assigns tooltip.text via innerText, whose setter turns '\n'
    # into line breaks on its own, and the Deck's `white-space: pre-line`
    # style is belt-and-braces in case that ever changes.
    df_map['tip_text'] = (
        df_map['vehicle_id'].map(lambda v: _tip_text('Vehicle', v))
        + '\n' + df_map['route_display'].map(lambda v: _tip_text('Route', v))
        + '\n' + df_map['speed_display'].map(lambda v: _tip_text('Speed', v + ' km/h'))
        + '\n' + df_map['bearing_display'].map(lambda v: _tip_text('Bearing', v + '°'))
        + '\n' + df_map['last_report_display'].map(lambda v: _tip_text('Last reported', v))
    )
    vehicle_columns = [
        'longitude', 'latitude', 'dot_color', 'vehicle_id', 'tip_text',
    ]
    vehicle_data = df_map[[c for c in vehicle_columns if c in df_map.columns]].copy()

    icon_layer = pdk.Layer(
        "ScatterplotLayer",
        id="vehicles",
        data=vehicle_data,
        get_position=['longitude', 'latitude'],
        get_fill_color='dot_color',
        get_radius=100,
        radius_min_pixels=8,
        radius_max_pixels=15,
        get_line_color=[255, 255, 255, 200],
        line_width_min_pixels=2,
        pickable=True,
    )

    # Create arrow layer
    df_map['arrow_path'] = df_map.apply(
        lambda row: create_arrow_paths(row['latitude'], row['longitude'], row['bearing'], size=0.0003),
        axis=1,
    )

    arrow_layer = pdk.Layer(
        "PathLayer",
        id="vehicle-arrows",
        data=df_map[['arrow_path', 'arrow_color']].copy(),
        get_path='arrow_path',
        get_color='arrow_color',
        width_min_pixels=3,
        width_max_pixels=5,
        pickable=False,
    )

    # Preserve map view state during auto-refresh
    if 'map_view_state' not in st.session_state:
        st.session_state.map_view_state = {
            'latitude': df_map['latitude'].mean(),
            'longitude': df_map['longitude'].mean(),
            'zoom': DEFAULT_ZOOM,
            'pitch': 0,
        }
    
    # Re-centre on a *change of what is being shown* — a different region, or a
    # different route search — and only then, so auto-refresh never yanks the
    # viewport away from wherever the user panned. Without the search half, a
    # search made while parked elsewhere in the region filtered the layers but
    # left the camera behind, i.e. a "Showing 3 vehicle(s)" banner over a map
    # with nothing on it.
    # A search that matched nothing leaves df_map unfiltered, so recentring on it
    # would jump the camera to the region mean for what is usually a typo — the
    # opposite of the sticky-viewport intent. Only a search that actually filtered
    # the map is worth moving for.
    current_query = (route_query or '').strip().lower() if filter_active else ''
    region_changed = st.session_state.selected_region != st.session_state.get('last_viewed_region', None)
    query_changed = current_query != st.session_state.get('last_route_query', '')
    if region_changed or query_changed:
        st.session_state.map_view_state = {
            'latitude': df_map['latitude'].mean(),
            'longitude': df_map['longitude'].mean(),
            'zoom': DEFAULT_ZOOM,
            'pitch': 0,
        }
        st.session_state.last_viewed_region = st.session_state.selected_region
        st.session_state.last_route_query = current_query
    
    view_state = pdk.ViewState(
        latitude=st.session_state.map_view_state['latitude'],
        longitude=st.session_state.map_view_state['longitude'],
        zoom=st.session_state.map_view_state['zoom'],
        pitch=st.session_state.map_view_state['pitch'],
    )

    # Nearby stops, drawn beneath the vehicles. Resolved once here and reused
    # by the "Arrivals near you" panel below, so the map and the panel can
    # never disagree about which stops are in range, and stops.txt is parsed
    # only once per render instead of twice.
    # Only static columns reach the layer: a per-render-volatile field here
    # would churn the deck spec hash and break map selection, as it did in
    # 2.5.0.
    stops_layer = None
    _loc = st.session_state.get('user_location')
    _nearby_stops = None
    if _loc and agency_slug:
        _nearby_stops = gtfs_static.get_stops_near(
            agency_slug, _loc['lat'], _loc['lon'],
            radius_m=NEARBY_STOP_RADIUS_M, limit=NEARBY_STOP_SCAN_LIMIT)
        if _nearby_stops:
            stops_layer = pdk.Layer(
                "ScatterplotLayer",
                id="nearby-stops",
                data=[{'stop_id': s['stop_id'],
                       'stop_name': s['stop_name'],
                       'stop_lat': s['stop_lat'],
                       'stop_lon': s['stop_lon'],
                       'tip_text': _tip_text('Stop', s['stop_name'])}
                      for s in _nearby_stops],
                get_position=['stop_lon', 'stop_lat'],
                # Hollow rings, not dots: buses are filled circles, so a stop
                # must differ in shape and not only in colour — a smaller
                # coloured dot reads as a smaller bus.
                stroked=True,
                filled=False,
                get_line_color=[255, 200, 60, 220],
                line_width_min_pixels=2,
                get_radius=40,
                radius_min_pixels=5,
                radius_max_pixels=10,
                pickable=True,
            )

    # ===== ADD USER LOCATION MARKER TO MAP =====
    layers = ([stops_layer] if stops_layer else []) + [icon_layer, arrow_layer]
    
    if 'user_location' in st.session_state and st.session_state.user_location:
        user_loc = st.session_state.user_location
        
        # Create user location marker (red dot)
        user_marker_data = pd.DataFrame([{
            'lat': user_loc['lat'],
            'lon': user_loc['lon']
        }])
        
        user_marker = pdk.Layer(
            "ScatterplotLayer",
            id="user-location",
            data=user_marker_data,
            get_position='[lon, lat]',
            get_radius=20,
            radius_min_pixels=6,
            radius_max_pixels=10,
            get_fill_color=[255, 0, 0, 255],  # Red marker
            get_line_color=[255, 255, 255, 255],  # White border
            line_width_min_pixels=2,
            pickable=False,
        )

        # Add accuracy circle
        if user_loc['accuracy'] > 0:
            accuracy_circle_data = pd.DataFrame([{
                'lat': user_loc['lat'],
                'lon': user_loc['lon'],
                'accuracy': user_loc['accuracy']
            }])

            accuracy_circle = pdk.Layer(
                "ScatterplotLayer",
                id="user-accuracy",
                data=accuracy_circle_data,
                get_position='[lon, lat]',
                get_radius='accuracy',   # metres, matches the GPS accuracy value
                radius_min_pixels=10,
                radius_max_pixels=500,
                get_fill_color=[255, 0, 0, 40],
                pickable=False,
            )
            
            layers.extend([accuracy_circle, user_marker])
        else:
            layers.append(user_marker)

    selection = st.pydeck_chart(
        pdk.Deck(
            map_style=map_style,
            initial_view_state=view_state,
            layers=layers,
            # "text", not "html": deck.gl assigns tooltip.html via innerHTML
            # (markup renders) but tooltip.text via innerText (markup would
            # show as literal characters, which is exactly what plain text
            # wants). white-space: pre-line is belt-and-braces — innerText's
            # setter already turns the '\n' in tip_text into line breaks, and
            # this CSS guarantees it even if that assignment ever changes.
            tooltip={
                "text": "{tip_text}",
                "style": {"backgroundColor": "steelblue", "color": "white",
                          "white-space": "pre-line"},
            },
        ),
        selection_mode="single-object",
        on_select="rerun",
        # The generation counter is bumped when the user clears a selection, so
        # the chart becomes a *new* widget and Streamlit stops handing back the
        # stale payload. Without it, clearing appeared to do nothing whenever
        # the deck spec was unchanged between renders.
        key=f"live_map_deck_{st.session_state.get('deck_generation', 0)}",
    )

    # Secondary view: one tapped vehicle, against the user's nearest stop on
    # its own trip.
    #
    # A tap is reported for exactly one render; every auto-refresh afterwards
    # returns an empty selection. The selection is therefore held in session
    # state and the vehicle re-resolved from the current frame each render, so
    # the bus advances without the panel disappearing. The broad except guards
    # against a real selection payload not matching this assumed shape.
    picked = None
    try:
        objects = selection.selection.objects.get("vehicles", [])
        raw = objects[0].get("vehicle_id") if objects else None
        # Coerce to str before it ever meets a pandas comparison below. A
        # non-string id against an Arrow-backed string column raises
        # NotImplementedError rather than comparing false, so an unexpected
        # payload type would take the whole page down instead of matching
        # nothing.
        #
        # An empty id is rejected for the same reason as in _picked_stop_id:
        # `if picked:` below and `is not None` in the stop path must never be
        # able to disagree about the same value. ingestion.py defaults a
        # missing vehicle id to 'Unknown', so '' is not reachable here today —
        # this keeps the two parse sites identical so it cannot become so.
        picked = str(raw) if isinstance(raw, (str, int)) and str(raw) else None
    except (AttributeError, KeyError, IndexError, TypeError):
        picked = None

    # Belt and braces on clearing. Bumping the widget key should be enough to
    # stop a stale payload coming back, but the payload is Streamlit's to
    # deliver, so the dismissed vehicle is also ignored for exactly one render.
    #
    # One render, not forever: a permanent block would mean dismissing a bus
    # silently cost the user the ability to tap it again.
    cleared = st.session_state.pop('cleared_vehicle_id', None)
    if cleared is not None and picked == cleared:
        picked = None

    # Same belt and braces for the stop path: the widget-key bump on clear is
    # not trusted alone (see above), so the dismissed stop is also ignored for
    # exactly one render. Read here, before `_picked_stop_id` runs below, so a
    # repeated payload naming the just-cleared stop cannot be re-adopted.
    cleared_stop = st.session_state.pop('cleared_stop_id', None)

    # Captured before the sticky re-resolution below overwrites `picked` with
    # whatever vehicle is *currently* selected. Last-tap-wins must compare a
    # fresh tap against a fresh tap -- comparing it against a sticky selection
    # meant a bus stayed "picked" on every render after the one it was tapped
    # on, so a stop tap could never win against it for as long as any bus
    # remained selected.
    fresh_vehicle_tap = picked

    if picked:
        st.session_state['selected_vehicle_id'] = picked
    else:
        picked = st.session_state.get('selected_vehicle_id')

    # Last tap wins. Without this the slot below the map could hold a bus panel
    # and a stop panel at once, each answering a question the user did not ask
    # most recently.
    picked_stop = _picked_stop_id(selection)
    if cleared_stop is not None and picked_stop == cleared_stop:
        picked_stop = None
    if fresh_vehicle_tap is not None:
        st.session_state['selected_stop_id'] = None
    elif picked_stop is not None:
        st.session_state['selected_stop_id'] = picked_stop
        st.session_state['selected_vehicle_id'] = None
        # Suppress the bus panel for this render too, not just next render's
        # session state -- otherwise the sticky bus resolved above still
        # renders alongside the stop panel just tapped into existence.
        picked = None
    selected_stop_id = st.session_state.get('selected_stop_id')

    if picked:
        if st.button("✕ Clear bus selection", key="clear_vehicle_selection"):
            st.session_state['cleared_vehicle_id'] = picked
            st.session_state.pop('selected_vehicle_id', None)
            st.session_state['deck_generation'] = (
                st.session_state.get('deck_generation', 0) + 1
            )
            st.rerun()

        row = df_map[df_map['vehicle_id'] == picked]
        if row.empty:
            # df_map has already been through the region filter, the stale/
            # hidden-freshness filter, and any active route search — a
            # selection surviving from before one of those changed can miss
            # here while the vehicle is still very much live in df_live.
            # Asserting "no longer reporting" in that case would be a
            # confidently wrong claim, which this feature must never make.
            if picked in df_live['vehicle_id'].values:
                st.info(
                    f"Vehicle {picked} is still reporting, but is not shown in "
                    f"the current view — it may be filtered out by a route "
                    f"search, belong to another region, or be too stale to draw."
                )
            else:
                st.info(f"Vehicle {picked} is no longer reporting.")
        elif not st.session_state.get('user_location'):
            st.info("Tap **📍 Locate Me** to see when this bus reaches you.")
        else:
            loc = st.session_state['user_location']
            v = row.iloc[0].to_dict()
            stops = gtfs_static.get_trip_stops(agency_slug, str(v.get('trip_id') or ''))
            if not stops:
                st.info(
                    f"Vehicle {picked} has no timetable entry for its current trip, "
                    f"so its arrival cannot be estimated."
                )
            else:
                # Bounded by the same radius as the panel below. Unbounded,
                # "your nearest stop on this trip" could name one 6 km away
                # and quote a 75-minute walk to it, which is not an answer to
                # the question being asked.
                nearby = [
                    dict(s, distance_m=d) for s, d in (
                        (s, eta.haversine_m(loc['lat'], loc['lon'],
                                            s['stop_lat'], s['stop_lon']))
                        for s in stops)
                    if d <= NEARBY_STOP_RADIUS_M
                ]
                if not nearby:
                    st.info(
                        f"Vehicle {picked} does not come within "
                        f"{NEARBY_STOP_RADIUS_M} m of you on its current trip."
                    )
                else:
                    arrivals, _ = eta.arrivals_for_stops(
                        [v], nearby,
                        lambda t: stops, int(time.time()), UTC_OFFSET_HOURS,
                        headsign_lookup=lambda t: gtfs_static.get_trip_headsign(agency_slug, t),
                        frequency_lookup=lambda t: gtfs_static.is_frequency_based(agency_slug, t),
                    )
                    best = None
                    for s in sorted(nearby, key=lambda s: s['distance_m']):
                        rows = arrivals.get(s['stop_id'], [])
                        if rows:
                            best = (s, rows[0])
                            break
                    if best is None:
                        st.info(
                            f"Vehicle {picked} has already passed the stops nearest you."
                        )
                    else:
                        s, a = best
                        # Deliberately not st.success: a green confirmation box
                        # reads as certainty, and this is an estimate that may
                        # rest on a position several minutes old.
                        parts = gtfs_static.get_route_parts(
                            agency_slug, a.get('route_id', ''))
                        heading = format_route_heading(
                            parts, fallback=a.get('route_display', ''))
                        mins = max(1, round(a['eta_seconds'] / 60))
                        body = list(heading)
                        # Same facts as format_arrival, in the shape that fits
                        # this panel. The headsign belongs with the route — it
                        # is where this bus is going — and the lateness with
                        # the arrival it qualifies. Dropping either made the
                        # two panels contradict each other about the same bus.
                        if a.get('headsign'):
                            body.append(f"**Towards:** {a['headsign']}")
                        arrives = f"**Arrives** {s['stop_name']} in **~{mins} min**"
                        # None means "not knowable" — a headway trip has no
                        # published start time to be late against. It is never
                        # zero and never "on time"; it is silence.
                        delay = a.get('delay_seconds')
                        if delay is not None and delay >= 60:
                            arrives += f" · {round(delay / 60)} min late"
                        body.append(arrives)
                        walk = walking.walk_times(
                            loc['lat'], loc['lon'], [s], agency_slug,
                            api_key=ors_key)[s['stop_id']]
                        body.append(
                            f"That stop is ~{int(walk['distance_m'])} m from you "
                            f"({_walk_label(walk)})")
                        age = a.get('age_seconds')
                        if age and age > LIVE_FRESH_SECONDS:
                            body.append(
                                f"⚠️ This bus last reported "
                                f"{round(age / 60)} min ago")
                        st.info("  \n".join(body))
                        st.caption(ARRIVAL_ACCURACY_NOTE)

    loc = st.session_state.get('user_location')
    if selected_stop_id:
        # The confirmation and the clearing are one decision, not two. The
        # guard used to require _nearby_stops, agency_slug and loc *before*
        # looking the stop up, so walking out of range entirely (an empty
        # nearby list) or clearing the location short-circuited past the
        # self-heal below and left the stale id in session state — and the
        # panel then reappeared without a tap the moment the user walked back
        # into range or pressed Locate Me again.
        stop = (next((s for s in (_nearby_stops or [])
                      if s['stop_id'] == selected_stop_id), None)
                if (agency_slug and loc) else None)
        if stop is None:
            # The user walked out of range of a stop they had selected, or the
            # selection can no longer be confirmed at all.
            st.session_state['selected_stop_id'] = None
        else:
            walk = walking.walk_times(
                loc['lat'], loc['lon'], [stop], agency_slug,
                api_key=ors_key)[stop['stop_id']]
            body = [f"📍 **{stop['stop_name']}**",
                    f"~{int(walk['distance_m'])} m · {_walk_label(walk)}"]

            arrivals, _ = _arrivals([stop])
            rows = arrivals.get(stop['stop_id'], [])
            if rows:
                # Same cap as the panel below. Uncapped, a tapped stop could
                # list six buses where the panel underneath listed three for
                # that same stop.
                body += [format_arrival(r) for r in rows[:ARRIVALS_PER_STOP]]
            else:
                # A route search narrowed df_map before this panel looked, so
                # with one active the only honest claim is about that route.
                body.append(
                    f"No bus matching '{filter_label}' is currently en route "
                    f"to this stop"
                    if filter_active else
                    "No bus is currently en route to this stop"
                )
            st.info("  \n".join(body))
            st.caption(ARRIVAL_ACCURACY_NOTE)

            # Every route the timetable says calls here, not only those with a
            # bus running right now. A rider at this stop saw three arrivals
            # and could not learn that a fourth route — the only one reaching
            # their destination — serves the stop at all.
            routes = gtfs_static.get_routes_at_stop(agency_slug, stop['stop_id'])
            if routes:
                st.markdown("**Serves:** " + " · ".join(
                    r['short'] or r['long'] or r['route_id'] for r in routes))

                # Resolve every pattern first, so the walk-time lookup below
                # can be narrowed to the stops that will actually carry a
                # near-you mark. Patterns come from an in-memory index built
                # from the ZIP already on disk, so this costs no I/O.
                by_route = []
                pattern_stop_ids = set()
                for route in routes:
                    patterns = gtfs_static.get_route_patterns(
                        agency_slug, route['route_id'], stop['stop_id'])
                    if not patterns:
                        continue
                    by_route.append((route, patterns))
                    for pattern in patterns:
                        pattern_stop_ids.update(
                            s.get('stop_id') for s in pattern['stops'])

                # The marks reuse the nearby-stop scan this render already ran
                # — not walk data already computed, which on a cold grid cell
                # does not exist yet. Asking for all ~40 nearby stops would
                # make *this* the request that creates it, 8x larger and
                # earlier than the Arrivals-near-you panel below that used to;
                # and a failure here arms walking._FAIL_UNTIL for 60 s, which
                # would drop that panel to "(estimated)" for stops its own
                # smaller, likelier-to-succeed request would have routed. So
                # ask only about the stops a rendered pattern actually calls
                # at — the rest could not show a mark anyway.
                marked_stops = [s for s in (_nearby_stops or [])
                                if s['stop_id'] in pattern_stop_ids]
                nearby_walks = walking.walk_times(
                    loc['lat'], loc['lon'], marked_stops, agency_slug,
                    api_key=ors_key) if marked_stops else {}
                nearby_by_id = {
                    s['stop_id']: {
                        'distance_m': nearby_walks[s['stop_id']]['distance_m'],
                        'walk_label': _walk_label(nearby_walks[s['stop_id']]),
                    }
                    for s in marked_stops
                    if s['stop_id'] in nearby_walks
                }

                for route, patterns in by_route:
                    headsigns = [gtfs_static.get_trip_headsign(agency_slug, p['trip_id'])
                                 for p in patterns]
                    titles = route_view.pattern_titles(patterns, headsigns)
                    label = route['short'] or route['long'] or route['route_id']

                    for pattern, title in zip(patterns, titles):
                        with st.expander(f"{label} — {title}"):
                            # Streamlit re-runs this whole function body on
                            # every auto-refresh whether or not the expander
                            # is open -- collapsing is only visual disclosure,
                            # not deferred work. One st.markdown call per row
                            # (~35 stops per pattern, times every route
                            # serving the stop) meant ~140 widget calls per
                            # 20-second refresh for a panel nobody had opened.
                            # Building the lines and joining them into a
                            # single call costs the same rendered text at a
                            # small fraction of the widget count.
                            lines = []
                            for row in route_view.build_stop_rows(
                                    pattern['stops'], stop['stop_id'], nearby_by_id):
                                # A hard break ("  \n") is an inline <br>, not
                                # a new markdown block, so inline constructs
                                # parse across the whole joined string. An
                                # unescaped stop name -- untrusted GTFS feed
                                # text, same class of problem the tooltip fix
                                # in 2.7.0 addressed -- could pair an
                                # unmatched *, _ or ` with a matching
                                # character several rows away and swallow the
                                # rows between into unintended formatting.
                                name = route_view.escape_markdown(row['stop_name'])
                                line = f"`{row['seq']:>2}`  {name}"
                                offset = row['offset_minutes']
                                if row['is_tapped']:
                                    line += "  ← you tapped this"
                                    if offset:
                                        # Non-zero only, so the first
                                        # occurrence (offset 0, the anchor)
                                        # stays a bare mark. On a loop the
                                        # tapped stop appears at both ends,
                                        # and the returning row is where the
                                        # circuit's length becomes visible:
                                        # without this, rows 1 and 35 render
                                        # as identical text and the +40 min
                                        # closure -- the one thing this panel
                                        # exists to show -- is thrown away.
                                        line += f"  · +{offset} min"
                                elif offset is not None and offset < 0:
                                    # Stops the bus passes before reaching the
                                    # tapped one. "+-5 min" is not a time.
                                    line += f"  · {abs(offset)} min earlier"
                                elif offset is not None:
                                    line += f"  · +{offset} min"
                                lines.append(line)
                                if row['near'] and not row['is_tapped']:
                                    # Not on the tapped row: it is in
                                    # _nearby_stops too, so its mark would
                                    # repeat verbatim the "~560 m · ~9 min
                                    # walk" the panel header printed two lines
                                    # above. The mark exists to point out
                                    # *other* stops within walking distance.
                                    #
                                    # Italics plus an indent glyph, not
                                    # leading spaces: HTML collapses runs of
                                    # spaces to one, and moving this line out
                                    # of its own st.caption (the widget
                                    # collapse above) also lost the smaller
                                    # muted styling that used to mark it as
                                    # subordinate. Without a visual cue here
                                    # it would read as a peer stop rather
                                    # than a note about one. These are our
                                    # own formatted numbers and label, not
                                    # feed text, so nothing here needs
                                    # escaping.
                                    lines.append(
                                        f"*↳ ~{int(row['near']['distance_m'])} m "
                                        f"from you · {row['near']['walk_label']}*")
                            st.markdown("  \n".join(lines))
                            # Differences between timetabled stop times, not a
                            # live prediction, and for a headway service there
                            # is no published departure to state at all.
                            note = ("Journey times from the published timetable, "
                                    "measured from the stop you tapped.")
                            if gtfs_static.is_frequency_based(agency_slug, pattern['trip_id']):
                                note += " This route runs to a headway, not a fixed timetable."
                            st.caption(note)

            if st.button("Clear stop selection", key="clear_stop_selection"):
                st.session_state['cleared_stop_id'] = selected_stop_id
                st.session_state['selected_stop_id'] = None
                # Bump the widget key so Streamlit stops handing back the stale
                # payload. Without it, clearing appears to do nothing whenever
                # the deck spec is unchanged between renders — the same bug
                # that made "Clear bus selection" inert.
                st.session_state['deck_generation'] = (
                    st.session_state.get('deck_generation', 0) + 1)
                st.rerun()

    # While a search is filtering the frame, len(df_map) is the match count, not
    # the region total — the success banner above already states it, so don't
    # restate the same number as though it were the whole region.
    if hidden_count:
        st.caption(
            f"🚫 {hidden_count} vehicle(s) in {selected_region} hidden — "
            f"no update in over {drawn_label}."
        )

    if not filter_active:
        # "active" here means what it means in the header metric: reported
        # within LIVE_STALE_SECONDS, i.e. drawn. The stale share is counted from
        # this region's frame, not network-wide, so it matches the dimmed dots
        # actually on screen.
        stale_note = (
            f" — {region_stale_count} of them dimmed, last reporting over {fresh_label} ago"
            if region_stale_count else ""
        )
        st.caption(
            f"Showing {len(df_map)} active vehicles in {selected_region}{stale_note}"
        )

    # ── What can I catch from here? ─────────────────────────────────────────
    with st.expander("📍 Arrivals near you", expanded=True):
        loc = st.session_state.get('user_location')
        if not loc:
            st.info("Tap **📍 Locate Me** above to see what is arriving near you.")
        elif not agency_slug:
            st.info(f"No timetable is published for {selected_region}.")
        else:
            # Reuse the single resolution of get_stops_near computed above
            # (same agency, same lat/lon, same radius/limit) so the panel and
            # the map layer can never list a different set of stops.
            nearby = _nearby_stops
            if not nearby:
                st.info(
                    f"No stops found within {NEARBY_STOP_RADIUS_M} m of you "
                    f"in {selected_region}."
                )
            else:
                arrivals, skipped = _arrivals(nearby)

                # Rank by usefulness: stops with a bus actually coming, nearest
                # first, then fill with the nearest unserved ones so the panel
                # is never empty and "nothing anywhere" is distinguishable from
                # "the app found nothing".
                served = [s for s in nearby if arrivals.get(s['stop_id'])]
                unserved = [s for s in nearby if not arrivals.get(s['stop_id'])]
                shown = served[:NEARBY_STOP_DISPLAY]
                if len(shown) < NEARBY_STOP_MIN_SHOWN:
                    shown += unserved[:NEARBY_STOP_MIN_SHOWN - len(shown)]

                walks = walking.walk_times(
                    loc['lat'], loc['lon'], shown, agency_slug,
                    api_key=ors_key)

                any_arrival = bool(served)
                for stop in shown:
                    walk = walks[stop['stop_id']]
                    maps_url = (
                        "https://www.google.com/maps/search/?api=1&query="
                        f"{stop['stop_lat']},{stop['stop_lon']}"
                    )
                    st.markdown(
                        f"**[{stop['stop_name']}]({maps_url})** "
                        f"· ~{int(walk['distance_m'])} m · {_walk_label(walk)}"
                    )
                    rows = arrivals.get(stop['stop_id'], [])
                    if not rows:
                        # A route search narrowed df_map before this panel ever
                        # looked, so with one active the only honest claim is
                        # about the route searched. "No bus is coming here" on
                        # the evidence of one route may be flatly untrue.
                        st.caption(
                            f"  no bus matching '{filter_label}' currently en "
                            f"route to this stop"
                            if filter_active else
                            "  no bus currently en route to this stop"
                        )
                        continue
                    any_arrival = True
                    for a in rows[:ARRIVALS_PER_STOP]:
                        st.caption("  " + format_arrival(a))

                st.caption(ARRIVAL_ACCURACY_NOTE)
                # A stop the app evaluated, found a bus inbound for, and then
                # did not show is the reported bug in miniature — the median
                # 800 m neighbourhood here holds 13 stops, so overflowing the
                # display cap is ordinary rather than exceptional. Say how many
                # were left out; silence is what made the original bug invisible.
                if len(served) > NEARBY_STOP_DISPLAY:
                    st.caption(
                        f"{len(served) - NEARBY_STOP_DISPLAY} more nearby stop(s) "
                        f"also have buses coming — only the {NEARBY_STOP_DISPLAY} "
                        f"nearest of them are listed."
                    )
                # skipped has three keys — no_trip_id, trip_not_in_schedule and
                # bad_position. Report every non-zero one; a vehicle omitted
                # without explanation is indistinguishable from one that simply
                # is not coming, which is the whole reason these are counted.
                if any(skipped.values()):
                    reasons = []
                    if skipped.get('no_trip_id'):
                        reasons.append(f"{skipped['no_trip_id']} without trip info")
                    if skipped.get('trip_not_in_schedule'):
                        reasons.append(
                            f"{skipped['trip_not_in_schedule']} on a trip missing "
                            f"from the timetable")
                    if skipped.get('bad_position'):
                        reasons.append(
                            f"{skipped['bad_position']} with unusable telemetry")
                    st.caption("Not shown: " + ", ".join(reasons) + ".")
                if not any_arrival:
                    # Same rule as the per-stop line above: the claim may only
                    # be as wide as the evidence behind it.
                    if filter_active:
                        st.caption(
                            f"No buses matching '{filter_label}' are currently "
                            f"en route to any stop within {NEARBY_STOP_RADIUS_M} m "
                            f"of you. Other routes are hidden while the route "
                            f"search is active."
                        )
                    else:
                        st.caption(
                            f"No buses are currently en route to any stop within "
                            f"{NEARBY_STOP_RADIUS_M} m of you."
                        )

    with st.expander("🚌 Route Viewer", expanded=False):
        vehicle_options = sorted(df_map['vehicle_id'].unique().tolist())

        if not vehicle_options:
            st.info("No vehicles available for the selected region.")
        else:
            selected_vehicle = st.selectbox(
                "Select Vehicle",
                options=vehicle_options,
                key="route_viewer_vehicle_select",
            )

            if selected_vehicle:
                # ---- Resolve trip_id / route_id from live snapshot ----
                vehicle_row = df_map[df_map['vehicle_id'] == selected_vehicle]
                trip_id = ''
                route_id = ''
                if not vehicle_row.empty:
                    trip_id = str(vehicle_row.iloc[0].get('trip_id', '') or '')
                    route_id = str(vehicle_row.iloc[0].get('route_id', '') or '')

                # agency_slug was already resolved from selected_region above.

                # ---- Try to fetch planned route shapes from GTFS Static ----
                planned_shapes = []
                if agency_slug and trip_id:
                    with st.spinner('Fetching planned route from GTFS Static...'):
                        planned_shapes = gtfs_static.get_shapes_for_trip(agency_slug, trip_id)

                # ---- Show route name caption ----
                if agency_slug and route_id:
                    route_name = gtfs_static.get_route_name(agency_slug, route_id)
                    if route_name:
                        st.caption(f"Route: {route_name}")

                # ---- Fetch historical trail for fallback / table ----
                trail_df = db.get_vehicle_trail(selected_vehicle, selected_region)

                if len(planned_shapes) >= 2:
                    # --- PRIMARY: draw planned route from GTFS Static shapes ---
                    planned_data = pd.DataFrame([{
                        'path': planned_shapes,
                        'color': [0, 200, 100, 200],
                    }])

                    planned_layer = pdk.Layer(
                        "PathLayer",
                        data=planned_data,
                        get_path='path',
                        get_color='color',
                        width_min_pixels=3,
                        width_max_pixels=6,
                        pickable=False,
                    )

                    # Centre view on midpoint of planned shape
                    mid_idx = len(planned_shapes) // 2
                    centre_lon, centre_lat = planned_shapes[mid_idx]

                    planned_view = pdk.ViewState(
                        latitude=centre_lat,
                        longitude=centre_lon,
                        zoom=DEFAULT_ZOOM,
                        pitch=0,
                    )

                    st.markdown("**Planned Route**")
                    st.pydeck_chart(
                        pdk.Deck(
                            map_style=map_style,
                            initial_view_state=planned_view,
                            layers=[planned_layer],
                        )
                    )

                elif trail_df is not None and len(trail_df) >= 2:
                    # --- FALLBACK: historical breadcrumb trail ---
                    st.info("No planned route available — showing historical trail.")

                    path_coords = trail_df[['longitude', 'latitude']].values.tolist()

                    trail_data = pd.DataFrame([{
                        'path': path_coords,
                        'color': [255, 165, 0, 200],
                    }])

                    trail_layer = pdk.Layer(
                        "PathLayer",
                        data=trail_data,
                        get_path='path',
                        get_color='color',
                        width_min_pixels=3,
                        width_max_pixels=6,
                        pickable=False,
                    )

                    trail_view = pdk.ViewState(
                        latitude=trail_df['latitude'].mean(),
                        longitude=trail_df['longitude'].mean(),
                        zoom=DEFAULT_ZOOM,
                        pitch=0,
                    )

                    st.pydeck_chart(
                        pdk.Deck(
                            map_style=map_style,
                            initial_view_state=trail_view,
                            layers=[trail_layer],
                        )
                    )

                else:
                    st.info("No route data available.")

                # ---- Always show historical position table if trail exists ----
                if trail_df is not None and not trail_df.empty:
                    display_trail = trail_df[['timestamp', 'latitude', 'longitude', 'speed', 'bearing']].copy()
                    display_trail['speed'] = (display_trail['speed'] * 3.6).round(1)  # m/s → km/h
                    display_trail['bearing'] = display_trail['bearing'].round(1)
                    display_trail = display_trail.rename(columns={
                        'timestamp': 'Timestamp',
                        'latitude': 'Latitude',
                        'longitude': 'Longitude',
                        'speed': 'Speed (km/h)',
                        'bearing': 'Bearing (°)',
                    })
                    st.dataframe(display_trail, use_container_width=True)