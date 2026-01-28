import streamlit as st
import duckdb
import pandas as pd
import pydeck as pdk
import numpy as np
from datetime import datetime, timedelta, timezone
from ingestion_rapidbus_mrtfeeder import fetch_rapid_rail_live

# Try to import from config.py (for local development)
# Fall back to Streamlit Secrets (for cloud deployment)
try:
    from config import (
        REGIONS, DATABASE_NAME, DATABASE_TABLE, TIMEZONE, 
        UTC_OFFSET_HOURS, DEFAULT_ZOOM, MAP_STYLE,
        ARROW_SIZE, ARROW_COLOR_RGB, CENTER_DOT_COLOR_RGB, ARROW_OPACITY
    )
except ImportError:
    # Running on Streamlit Cloud - read from secrets
    REGIONS = st.secrets["regions"]["list"]
    DATABASE_NAME = st.secrets["database"]["name"]
    DATABASE_TABLE = st.secrets["database"]["table"]
    TIMEZONE = st.secrets["timezone"]["name"]
    UTC_OFFSET_HOURS = st.secrets["timezone"]["utc_offset_hours"]
    DEFAULT_ZOOM = st.secrets["map"]["default_zoom"]
    MAP_STYLE = st.secrets["map"]["style"]
    ARROW_SIZE = st.secrets["arrow"]["size"]
    ARROW_COLOR_RGB = list(st.secrets["arrow"]["color_rgb"])
    CENTER_DOT_COLOR_RGB = list(st.secrets["arrow"]["center_dot_color_rgb"])
    ARROW_OPACITY = st.secrets["arrow"]["opacity"]

st.set_page_config(page_title="Malaysia Real-Time Transit Tracker", page_icon="🚇", layout="wide")

# Sort regions
primary = ['Rapid Bus KL']
others = sorted([r for r in REGIONS if r != 'Rapid Bus KL'])
SORTED_REGIONS = primary + others

# Initialize session state
if 'selected_region' not in st.session_state:
    st.session_state.selected_region = SORTED_REGIONS[0]
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = None

# Create timestamp
now_utc = datetime.now(timezone.utc)
now_kl = now_utc + timedelta(hours=UTC_OFFSET_HOURS)
current_date = now_kl.strftime('%d %b %Y')
current_sync_time = now_kl.strftime('%H:%M:%S')

st.write(f"📅 {current_date} | 🕒 {current_sync_time} (GMT+8)")

# Database connection
con = duckdb.connect(DATABASE_NAME)
con.execute(f"SET TimeZone='{TIMEZONE}'")

st.title("🚇 Malaysia Real-Time Transit Tracker")
st.markdown("Monitoring live bus positions across Malaysia.")

# Refresh button
col_button, col_status = st.columns([1, 4], vertical_alignment="center")

with col_button:
    if st.button("🔄 Refresh Data", type="primary", use_container_width=True):
        with st.spinner('🛰️ Fetching latest positions...'):
            fetch_rapid_rail_live()
            st.session_state.last_refresh = datetime.now()
        st.rerun()

with col_status:
    if st.session_state.last_refresh:
        st.info(f"Last refresh: {current_date} {current_sync_time}")
    else:
        st.info("Click 'Refresh Data' to fetch the latest bus positions")

def create_arrow_paths(lat, lon, bearing, size=ARROW_SIZE):
    """Create arrow path coordinates based on position and bearing"""
    angle_rad = np.radians(90 - bearing)
    arrow_length = size * 2
    arrow_width = size * 0.8
    
    tip_lat = lat + arrow_length * np.sin(angle_rad)
    tip_lon = lon + arrow_length * np.cos(angle_rad)
    
    left_angle = angle_rad - np.radians(150)
    left_lat = lat + arrow_width * np.sin(left_angle)
    left_lon = lon + arrow_width * np.cos(left_angle)
    
    right_angle = angle_rad + np.radians(150)
    right_lat = lat + arrow_width * np.sin(right_angle)
    right_lon = lon + arrow_width * np.cos(right_angle)
    
    return [[lon, lat], [tip_lon, tip_lat], [left_lon, left_lat], 
            [tip_lon, tip_lat], [right_lon, right_lat], [tip_lon, tip_lat]]

try:
    table_check = con.execute(f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{DATABASE_TABLE}'").fetchone()[0]
    
    if table_check > 0:
        df_live = con.execute(f"SELECT * FROM {DATABASE_TABLE}").df()
        
        if not df_live.empty:
            latest_ts = con.execute(f"SELECT MAX(timestamp) FROM {DATABASE_TABLE}").fetchone()[0]
            if latest_ts:
                actual_sync_time = con.execute(f"SELECT strftime(to_timestamp(MAX(timestamp)::BIGINT), '%-d %b %Y %H:%M:%S') FROM {DATABASE_TABLE}").fetchone()[0]
                st.success(f"Data updated at: {actual_sync_time}")
            
            df_live['timestamp_formatted'] = pd.to_datetime(df_live['timestamp'], unit='s', utc=True).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')

            # Metrics
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Active Buses", len(df_live))
            with col2:
                st.metric("Regions Monitored", len(df_live['region'].unique()))
            with col3:
                st.metric("Busiest Region", df_live['region'].value_counts().idxmax())

            # Region filter
            available_regions = [r for r in SORTED_REGIONS if r in df_live['region'].unique()]
            
            if available_regions:
                if st.session_state.selected_region not in available_regions:
                    st.session_state.selected_region = available_regions[0]
                
                try:
                    current_index = available_regions.index(st.session_state.selected_region)
                except ValueError:
                    current_index = 0
                
                def update_region():
                    st.session_state.selected_region = st.session_state.region_selector
                
                selected_region = st.selectbox(
                    "Select Region to View", 
                    options=available_regions,
                    index=current_index,
                    key='region_selector',
                    on_change=update_region
                )
                         
                df_filtered = df_live[df_live['region'] == st.session_state.selected_region]
                df_filtered = df_filtered.fillna({'bearing': 0, 'speed': 0})

                if not df_filtered.empty:
                    df_map = df_filtered.copy()
                    df_map['latitude'] = pd.to_numeric(df_map['latitude'], errors='coerce')
                    df_map['longitude'] = pd.to_numeric(df_map['longitude'], errors='coerce')
                    df_map['bearing'] = pd.to_numeric(df_map['bearing'], errors='coerce').fillna(0)
                    df_map['speed'] = pd.to_numeric(df_map['speed'], errors='coerce').fillna(0)
                    
                    df_map = df_map.dropna(subset=['latitude', 'longitude'])
                    df_map = df_map[(df_map['latitude'] != 0) & (df_map['longitude'] != 0)]

                    if not df_map.empty:
                        df_map['path'] = df_map.apply(
                            lambda row: create_arrow_paths(row['latitude'], row['longitude'], row['bearing']), 
                            axis=1
                        )
                        
                        path_layer = pdk.Layer(
                            "PathLayer",
                            data=df_map,
                            get_path='path',
                            get_color=ARROW_COLOR_RGB + [ARROW_OPACITY],
                            width_min_pixels=2,
                            width_max_pixels=4,
                            pickable=True,
                        )
                        
                        scatter_layer = pdk.Layer(
                            "ScatterplotLayer",
                            data=df_map,
                            get_position=['longitude', 'latitude'],
                            get_color=CENTER_DOT_COLOR_RGB + [ARROW_OPACITY],
                            get_radius=80,
                            radius_min_pixels=3,
                            radius_max_pixels=8,
                            pickable=True
                        )

                        view_state = pdk.ViewState(
                            latitude=df_map['latitude'].mean(),
                            longitude=df_map['longitude'].mean(),
                            zoom=DEFAULT_ZOOM,
                            pitch=0
                        )

                        st.pydeck_chart(pdk.Deck(
                            map_style=MAP_STYLE,
                            initial_view_state=view_state,
                            layers=[path_layer, scatter_layer],
                            tooltip={
                                "html": "<b>Vehicle:</b> {vehicle_id}<br/><b>Speed:</b> {speed} m/s<br/><b>Bearing:</b> {bearing}°", 
                                "style": {"backgroundColor": "steelblue", "color": "white"}
                            }
                        ))

                        st.caption(f"Showing {len(df_map)} active vehicles in {st.session_state.selected_region}")
                    else:
                        st.warning(f"No valid coordinates for buses in {st.session_state.selected_region}")

                    with st.expander("View Raw GTFS-Realtime Data"):
                        display_df = df_filtered.sort_values('timestamp', ascending=False)
                        display_df = display_df[['region', 'latitude', 'longitude', 'bearing', 'speed', 'vehicle_id', 'timestamp_formatted']]
                        display_df = display_df.rename(columns={'timestamp_formatted': 'timestamp_readable', 'bearing': 'Heading (Degrees)'})
                        display_df = display_df.reset_index(drop=True)
                        st.dataframe(display_df, use_container_width=True, hide_index=True)
                else:
                    st.warning(f"No active buses in {st.session_state.selected_region} at this time.")
            else:
                st.info("No active buses detected in any region.")
                
    else:
        st.info("🛰️ No data found. Click 'Refresh Data' button to fetch initial data.")
        st.caption("The database table will be created on first refresh.")

except Exception as e:
    st.error(f"Pipeline Error: {e}")
    import traceback
    st.code(traceback.format_exc())

con.close()