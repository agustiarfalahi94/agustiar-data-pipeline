import streamlit as st
import pydeck as pdk
import numpy as np
import pandas as pd
from streamlit_js_eval import get_geolocation as js_get_geolocation
from utils import db, data_processor
from utils.ingestion import fetch_and_store_transit_data
from utils import gtfs_static

try:
    from config import DEFAULT_ZOOM, ARROW_SIZE
except ImportError:
    DEFAULT_ZOOM = 13
    ARROW_SIZE = 0.001


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

    if df_live is None or df_live.empty:
        st.info("🛰️ No data. Click 'Refresh Data' to fetch.")
        return

    # Show sync time
    if actual_sync_time:
        st.success(f"Data updated: {actual_sync_time}")

    # Metrics (Total Active Buses, Regions Monitored, and Busiest Region for Live Map)
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Active Buses", metrics['total'])
    col2.metric("Regions Monitored", metrics['regions'])
    col3.metric("Busiest Region", metrics['busiest'])

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
        if st.session_state.selected_region is None or st.session_state.selected_region not in hardcoded_regions:
            # Try to use first available, otherwise use first hardcoded
            st.session_state.selected_region = available_regions[0] if available_regions else hardcoded_regions[0]

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
        # Update session state only if changed
        if selected_region != st.session_state.selected_region:
            st.session_state.selected_region = selected_region
    
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
    df_map = data_processor.prepare_map_data(df_live, selected_region)

    if df_map.empty:
        st.warning(f"No valid data for {selected_region}")
        return
    
    # Create formatted columns for tooltip display
    df_map['speed_display'] = df_map['speed'].round(0).astype(int).astype(str)
    df_map['bearing_display'] = df_map['bearing'].round(0).astype(int).astype(str)

    # Map style based on theme
    map_style = 'dark' if st.session_state.map_theme == 'dark' else 'light'

    # Create bus icon layer
    icon_layer = pdk.Layer(
        "ScatterplotLayer",
        data=df_map,
        get_position=['longitude', 'latitude'],
        get_fill_color=[51, 153, 255, 255],
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
        get_color=[255, 255, 255, 255],
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
    
    # Only update view state if region changed
    if st.session_state.selected_region != st.session_state.get('last_viewed_region', None):
        st.session_state.map_view_state = {
            'latitude': df_map['latitude'].mean(),
            'longitude': df_map['longitude'].mean(),
            'zoom': DEFAULT_ZOOM,
            'pitch': 0,
        }
        st.session_state.last_viewed_region = st.session_state.selected_region
    
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
                get_radius=30,
                radius_min_pixels=8,
                radius_max_pixels=14,
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
                "html": "<b>Vehicle:</b> {vehicle_id}<br/><b>Speed:</b> {speed_display} km/h<br/><b>Bearing:</b> {bearing_display}°",
                "style": {"backgroundColor": "steelblue", "color": "white"},
            },
        )
    )

    st.caption(f"Showing {len(df_map)} active vehicles in {selected_region}")

    # ===== ROUTE VIEWER SECTION =====
    # Maps selected_region display names to GTFS static agency slugs
    REGION_TO_SLUG = gtfs_static.STATIC_API_SOURCES

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
                    display_trail['speed'] = display_trail['speed'].round(1)
                    display_trail['bearing'] = display_trail['bearing'].round(1)
                    display_trail = display_trail.rename(columns={
                        'timestamp': 'Timestamp',
                        'latitude': 'Latitude',
                        'longitude': 'Longitude',
                        'speed': 'Speed (m/s)',
                        'bearing': 'Bearing (°)',
                    })
                    st.dataframe(display_trail, use_container_width=True)