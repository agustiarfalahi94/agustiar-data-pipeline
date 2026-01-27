import streamlit as st
import duckdb
import pandas as pd
from datetime import datetime, timedelta, timezone
from ingestion_rapidbus_mrtfeeder import fetch_rapid_rail_live

st.set_page_config(page_title="Malaysia Real-Time Transit Tracker", page_icon="🚇", layout="wide")

# HARDCODED REGIONS - matching the ingestion script exactly
REGIONS = [
    'Rapid Bus KL',
    'Rapid Bus MRT Feeder',
    'Rapid Bus Kuantan',
    'Rapid Bus Penang'
]

# Create a GMT+8 timestamp
# This adds 8 hours to the server's UTC time
now_utc = datetime.now(timezone.utc)
now_kl = now_utc + timedelta(hours=8)

# Format to show both Date and Time
current_date = now_kl.strftime('%d %b %Y') # e.g., 27 Jan 2026
current_sync_time = now_kl.strftime('%H:%M:%S')

# Display in Streamlit (Example)
st.write(f"📅 {current_date} | 🕒 {current_sync_time} (GMT+8)")

# Initialize session state
if 'selected_region' not in st.session_state:
    st.session_state.selected_region = REGIONS[0]
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = None

# Renamed DB to match project identity
con = duckdb.connect('agustiar_analytics.duckdb')

# FORCE DuckDB to use Malaysia Time regardless of server location
con.execute("SET TimeZone='Asia/Kuala_Lumpur'")

st.title("🚇 Malaysia Real-Time Transit Tracker")
st.markdown("Monitoring live bus positions across Kuala Lumpur and Penang.")

# MANUAL REFRESH BUTTON
col_button, col_status = st.columns([1, 4], vertical_alignment="center")

with col_button:
    if st.button("🔄 Refresh Data", type="primary", use_container_width=True):
        with st.spinner('🛰️ Fetching latest positions...'):
            fetch_rapid_rail_live()
            st.session_state.last_refresh = datetime.now()
        st.rerun()

with col_status:
    if st.session_state.last_refresh:
        st.info(f"Last refresh attempt at: {current_date} {current_sync_time}")
    else:
        st.info("Click 'Refresh Data' to fetch the latest bus positions")

try:
    # Check if table exists
    table_check = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'live_buses'").fetchone()[0]
    
    if table_check > 0:
        df_live = con.execute("SELECT * FROM live_buses").df()
        
        if not df_live.empty:
            # Get the actual sync time from the data
            latest_ts = con.execute("SELECT MAX(timestamp) FROM live_buses").fetchone()[0]
            if latest_ts:
                # Convert timestamp to Malaysia time (UTC+8)
                from datetime import timezone, timedelta
                utc_time = datetime.fromtimestamp(int(latest_ts), tz=timezone.utc)
                malaysia_time = utc_time + timedelta(hours=8)
                actual_sync_time = con.execute("SELECT strftime(to_timestamp(MAX(timestamp)::BIGINT), '%d-%m-%Y %H:%M:%S') FROM live_buses").fetchone()[0]
                st.success(f"Actual data updated at: {actual_sync_time}")
            
            # Add formatted timestamp column to dataframe for display (Malaysia time)
            df_live['timestamp_formatted'] = pd.to_datetime(df_live['timestamp'], unit='s', utc=True).dt.tz_convert('Asia/Kuala_Lumpur').dt.strftime('%Y-%m-%d %H:%M:%S')

            # METRICS
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Active Buses", len(df_live))
            with col2:
                regions_count = len(df_live['region'].unique())
                st.metric("Regions Monitored", regions_count)
            with col3:
                if not df_live.empty:
                    top_region = df_live['region'].value_counts().idxmax()
                    st.metric("Busiest Region", top_region)

            # REGION FILTER - Using hardcoded list
            available_regions = [r for r in REGIONS if r in df_live['region'].unique()]
            
            if available_regions:
                # Make sure selected region is still available
                if st.session_state.selected_region not in available_regions:
                    st.session_state.selected_region = available_regions[0]
                
                # Get current index
                try:
                    current_index = available_regions.index(st.session_state.selected_region)
                except ValueError:
                    current_index = 0
                
                # Selectbox with callback
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
                
                if not df_filtered.empty:
                    st.map(df_filtered.rename(columns={'latitude': 'lat', 'longitude': 'lon'}))
                    
                    with st.expander("View Raw GTFS-Realtime Data"):
                        # Sort by timestamp (newest first) and reorder columns
                        display_df = df_filtered.sort_values('timestamp', ascending=False)
                        display_df = display_df[['region', 'latitude', 'longitude', 'timestamp_formatted']]
                        display_df = display_df.rename(columns={'timestamp_formatted': 'timestamp_readable'})
                        # Reset index and hide it
                        display_df = display_df.reset_index(drop=True)
                        st.dataframe(display_df, use_container_width=True, hide_index=True)
                else:
                    st.warning(f"No active buses in {st.session_state.selected_region} at this time.")
            else:
                st.info("No active buses detected in any region.")
                
    else:
        # This shows if the table hasn't been created yet by the ingestion script
        st.info("🛰️ No data found. Click 'Refresh Data' button to fetch initial data.")
        st.caption("The database table will be created on first refresh.")

except Exception as e:
    st.error(f"Pipeline Error: {e}")
    import traceback
    st.code(traceback.format_exc())

con.close()