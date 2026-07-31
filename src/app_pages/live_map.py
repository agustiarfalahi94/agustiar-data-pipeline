import time
import streamlit as st
import pydeck as pdk
import numpy as np
import pandas as pd
from streamlit_js_eval import get_geolocation as js_get_geolocation
from utils import db, data_processor, eta
from utils.ingestion import fetch_and_store_transit_data
from utils import gtfs_static

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

    # One fallback for a frame that never carried the freshness columns (an
    # older cached frame, or a caller that skipped classify_freshness): treat
    # every row as fresh and full-strength.
    freshness_col = df_map.get('freshness', pd.Series('fresh', index=df_map.index))
    age_col = df_map.get('age_seconds', pd.Series(0, index=df_map.index))

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

    df_map['freshness_display'] = [
        f"{int(a)}s ago" if f == 'fresh' else f"⚠️ last update {int(a) // 60}m {int(a) % 60}s ago"
        for a, f in zip(age_col, freshness_col)
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
            matched = sorted(df_map['route_display'].unique())
            matched_label = ', '.join(matched[:3]) + ('…' if len(matched) > 3 else '')
            st.success(f"Showing {len(df_map)} vehicle(s) on {matched_label}")

    # Map style based on theme
    map_style = 'dark' if st.session_state.map_theme == 'dark' else 'light'

    # Create bus icon layer
    icon_layer = pdk.Layer(
        "ScatterplotLayer",
        data=df_map,
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
        data=df_map,
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

    # ===== ADD USER LOCATION MARKER TO MAP =====
    layers = [icon_layer, arrow_layer]
    
    if 'user_location' in st.session_state and st.session_state.user_location:
        user_loc = st.session_state.user_location
        
        # Create user location marker (red dot)
        user_marker_data = pd.DataFrame([{
            'lat': user_loc['lat'],
            'lon': user_loc['lon']
        }])
        
        user_marker = pdk.Layer(
            "ScatterplotLayer",
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

    st.pydeck_chart(
        pdk.Deck(
            map_style=map_style,
            initial_view_state=view_state,
            layers=layers,
            tooltip={
                "html": "<b>Vehicle:</b> {vehicle_id}<br/><b>Route:</b> {route_display}<br/><b>Speed:</b> {speed_display} km/h<br/><b>Bearing:</b> {bearing_display}°<br/><b>Updated:</b> {freshness_display}",
                "style": {"backgroundColor": "steelblue", "color": "white"},
            },
        )
    )

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

    # ===== ROUTE VIEWER SECTION =====
    # Maps selected_region display names to GTFS static agency slugs
    REGION_TO_SLUG = gtfs_static.STATIC_API_SOURCES

    # ── What can I catch from here? ─────────────────────────────────────────
    with st.expander("📍 Arrivals near you", expanded=True):
        loc = st.session_state.get('user_location')
        if not loc:
            st.info("Tap **📍 Locate Me** above to see what is arriving near you.")
        elif not agency_slug:
            st.info(f"No timetable is published for {selected_region}.")
        else:
            nearby = gtfs_static.get_stops_near(
                agency_slug, loc['lat'], loc['lon'], radius_m=800, limit=5)
            if not nearby:
                st.info(
                    f"No stops found within 800 m of you in {selected_region}."
                )
            else:
                vehicles = df_map.to_dict('records')
                arrivals, skipped = eta.arrivals_for_stops(
                    vehicles, nearby,
                    lambda t: gtfs_static.get_trip_stops(agency_slug, t),
                    int(time.time()), UTC_OFFSET_HOURS,
                    headsign_lookup=lambda t: gtfs_static.get_trip_headsign(agency_slug, t),
                )

                any_arrival = False
                for stop in nearby:
                    walk = eta.walking_minutes(stop['distance_m'])
                    st.markdown(
                        f"**{stop['stop_name']}** · {int(stop['distance_m'])} m "
                        f"· ~{walk} min walk"
                    )
                    rows = arrivals.get(stop['stop_id'], [])
                    if not rows:
                        st.caption("  nothing inbound right now")
                        continue
                    any_arrival = True
                    for a in rows[:3]:
                        mins = max(1, round(a['eta_seconds'] / 60))
                        line = f"  {a['route_display']}"
                        if a['headsign']:
                            line += f" → {a['headsign']}"
                        line += f" · **~{mins} min**"
                        if a['delay_seconds'] >= 60:
                            line += f" · {round(a['delay_seconds'] / 60)} min late"
                        if a.get('age_seconds') and a['age_seconds'] > LIVE_FRESH_SECONDS:
                            line += f" · position {round(a['age_seconds'] / 60)} min old"
                        st.caption(line)

                st.caption(
                    "Estimated from the published timetable and each bus's "
                    "measured delay — accurate to about one stop."
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
                            f"{skipped['bad_position']} with an unusable position")
                    st.caption("Not shown: " + ", ".join(reasons) + ".")
                if not any_arrival:
                    st.caption("No buses are currently inbound to these stops.")

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

                # ---- Determine agency slug for the selected region ----
                agency_slug = REGION_TO_SLUG.get(selected_region, '')

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