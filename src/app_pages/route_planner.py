"""
route_planner.py
----------------
Route Planner page — Phase 1.

Lets the user locate themselves (via browser geolocation), pick a transit
region, and find the 10 nearest bus/rail stops.  For each stop it shows the
routes that serve it and allows the user to draw any trip's planned shape on
a pydeck map.
"""

import streamlit as st
import pydeck as pdk
import pandas as pd
import numpy as np
from streamlit_js_eval import get_geolocation as js_get_geolocation
from utils import gtfs_static
from utils.ingestion import API_SOURCES

try:
    from config import DEFAULT_ZOOM
except ImportError:
    DEFAULT_ZOOM = 13


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _haversine_m(lat1, lon1, lat2_arr, lon2_arr):
    """Vectorised haversine — returns distances in metres from (lat1,lon1) to arrays of points."""
    R = 6_371_000
    phi1, phi2 = np.radians(lat1), np.radians(lat2_arr)
    dphi = phi2 - phi1
    dlambda = np.radians(lon2_arr - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------------------
# Page entry-point
# ---------------------------------------------------------------------------

def show():
    st.title("📍 Route Planner")

    # ------------------------------------------------------------------ #
    # Session-state initialisation
    # ------------------------------------------------------------------ #
    if 'rp_location' not in st.session_state:
        st.session_state.rp_location = None
    if 'rp_getting_location' not in st.session_state:
        st.session_state.rp_getting_location = False
    if 'rp_nearest_stops' not in st.session_state:
        st.session_state.rp_nearest_stops = []
    if 'rp_selected_region' not in st.session_state:
        st.session_state.rp_selected_region = None

    # ------------------------------------------------------------------ #
    # Step 1 — Locate Me
    # ------------------------------------------------------------------ #
    st.subheader("Step 1: Your Location")

    if not st.session_state.rp_getting_location and st.session_state.rp_location is None:
        if st.button("📍 Locate Me", key="rp_locate_btn"):
            st.session_state.rp_getting_location = True
            st.rerun()

    if st.session_state.rp_getting_location:
        with st.spinner("🌍 Getting your location…"):
            location_data = js_get_geolocation(component_key="rp_geolocation")
            if location_data and isinstance(location_data, dict):
                coords = location_data.get('coords', {})
                if coords and 'latitude' in coords and 'longitude' in coords:
                    st.session_state.rp_location = {
                        'lat': coords['latitude'],
                        'lon': coords['longitude'],
                    }
                    st.session_state.rp_getting_location = False
                    st.rerun()

    if st.session_state.rp_location:
        lat = st.session_state.rp_location['lat']
        lon = st.session_state.rp_location['lon']
        col_loc, col_clear = st.columns([4, 1])
        with col_loc:
            st.success(f"📍 {lat:.5f}, {lon:.5f}")
        with col_clear:
            if st.button("Clear", key="rp_clear_loc"):
                st.session_state.rp_location = None
                st.session_state.rp_nearest_stops = []
                st.rerun()

    # ------------------------------------------------------------------ #
    # Step 2 — Region selector
    # ------------------------------------------------------------------ #
    st.subheader("Step 2: Select Region")

    all_regions = list(API_SOURCES.keys())
    sorted_regions = ['Rapid Bus KL'] + sorted([r for r in all_regions if r != 'Rapid Bus KL'])

    # Default to previously selected or first in list
    default_idx = 0
    if st.session_state.rp_selected_region in sorted_regions:
        default_idx = sorted_regions.index(st.session_state.rp_selected_region)

    selected_region = st.selectbox(
        "Region",
        options=sorted_regions,
        index=default_idx,
        key="rp_region_selector",
        label_visibility="collapsed",
    )
    st.session_state.rp_selected_region = selected_region

    # ------------------------------------------------------------------ #
    # Step 3 — Find Nearest Stops
    # ------------------------------------------------------------------ #
    st.subheader("Step 3: Find Nearest Stops")

    find_disabled = st.session_state.rp_location is None
    find_clicked = st.button(
        "🔍 Find Nearest Stops",
        key="rp_find_btn",
        disabled=find_disabled,
    )

    if find_disabled:
        st.caption("Please locate yourself first (Step 1).")

    if find_clicked and st.session_state.rp_location:
        agency_slug = gtfs_static.STATIC_API_SOURCES.get(selected_region, '')
        if not agency_slug:
            st.warning("No GTFS static data configured for this region.")
        else:
            with st.spinner("Loading stops…"):
                stops = gtfs_static.get_stops(agency_slug)

            if not stops:
                st.warning("No stop data available for this region.")
            else:
                user_lat = st.session_state.rp_location['lat']
                user_lon = st.session_state.rp_location['lon']

                lats = np.array([s['stop_lat'] for s in stops])
                lons = np.array([s['stop_lon'] for s in stops])
                dists = _haversine_m(user_lat, user_lon, lats, lons)

                # Attach distances and sort
                for i, s in enumerate(stops):
                    s['distance_m'] = float(dists[i])

                nearest = sorted(stops, key=lambda x: x['distance_m'])[:10]
                st.session_state.rp_nearest_stops = nearest
                # Persist the agency slug used so the results section can reuse it
                st.session_state.rp_agency_slug = agency_slug

    # ------------------------------------------------------------------ #
    # Results
    # ------------------------------------------------------------------ #
    nearest_stops = st.session_state.rp_nearest_stops
    agency_slug = st.session_state.get('rp_agency_slug', '')

    if nearest_stops and st.session_state.rp_location:
        st.divider()
        st.subheader("Results")

        user_lat = st.session_state.rp_location['lat']
        user_lon = st.session_state.rp_location['lon']

        # ---- Map ----
        user_df = pd.DataFrame([{'lat': user_lat, 'lon': user_lon}])
        stops_df = pd.DataFrame([
            {'lat': s['stop_lat'], 'lon': s['stop_lon'], 'name': s['stop_name']}
            for s in nearest_stops
        ])

        user_layer = pdk.Layer(
            "ScatterplotLayer",
            data=user_df,
            get_position='[lon, lat]',
            get_fill_color=[255, 0, 0, 255],
            get_line_color=[255, 255, 255, 255],
            line_width_min_pixels=2,
            get_radius=20,
            radius_min_pixels=6,
            radius_max_pixels=10,
            pickable=False,
        )

        stops_layer = pdk.Layer(
            "ScatterplotLayer",
            data=stops_df,
            get_position='[lon, lat]',
            get_fill_color=[0, 120, 255, 200],
            get_radius=20,
            radius_min_pixels=5,
            radius_max_pixels=8,
            pickable=True,
        )

        view = pdk.ViewState(
            latitude=user_lat,
            longitude=user_lon,
            zoom=15,
            pitch=0,
        )

        map_style = 'dark' if st.session_state.get('map_theme', 'light') == 'dark' else 'light'

        st.pydeck_chart(
            pdk.Deck(
                map_style=map_style,
                initial_view_state=view,
                layers=[stops_layer, user_layer],
                tooltip={"html": "<b>{name}</b>", "style": {"backgroundColor": "steelblue", "color": "white"}},
            )
        )

        # ---- Stop list ----
        st.subheader("Nearest Stops")
        for stop in nearest_stops:
            with st.expander(f"{stop['stop_name']} — {stop['distance_m']:.0f}m"):
                st.write(f"**Lat:** {stop['stop_lat']:.6f}  **Lon:** {stop['stop_lon']:.6f}")
                if agency_slug:
                    with st.spinner("Loading routes…"):
                        routes = gtfs_static.get_routes_for_stop(agency_slug, stop['stop_id'])
                    if routes:
                        st.markdown("**Routes:** " + ", ".join(routes))
                    else:
                        st.caption("No route info available.")

        # ---- Route shape viewer ----
        st.subheader("🗺️ View a Route")
        trip_id_input = st.text_input(
            "Enter a trip ID to view its route shape",
            placeholder='trip IDs are visible in the Route Viewer on the Live Map page',
            key="rp_trip_id_input",
        )

        if trip_id_input and agency_slug:
            with st.spinner("Fetching route shape…"):
                shapes = gtfs_static.get_shapes_for_trip(agency_slug, trip_id_input.strip())

            if len(shapes) >= 2:
                shape_df = pd.DataFrame([{'path': shapes, 'color': [0, 200, 100, 200]}])

                shape_layer = pdk.Layer(
                    "PathLayer",
                    data=shape_df,
                    get_path='path',
                    get_color='color',
                    width_min_pixels=3,
                    width_max_pixels=6,
                    pickable=False,
                )

                mid_idx = len(shapes) // 2
                centre_lon, centre_lat = shapes[mid_idx]

                shape_view = pdk.ViewState(
                    latitude=centre_lat,
                    longitude=centre_lon,
                    zoom=DEFAULT_ZOOM,
                    pitch=0,
                )

                st.pydeck_chart(
                    pdk.Deck(
                        map_style=map_style,
                        initial_view_state=shape_view,
                        layers=[shape_layer],
                    )
                )
            else:
                st.warning("No shape data found for that trip ID.")
