import streamlit as st
import duckdb
import pandas as pd
from datetime import datetime
from streamlit_autorefresh import st_autorefresh
from ingestion_rapidbus_mrtfeeder import fetch_rapid_rail_live

st.set_page_config(page_title="Malaysia Real-Time Transit Tracker", page_icon="🚇", layout="wide")

# 1. AUTO-REFRESH: Keep the dashboard live
st_autorefresh(interval=10000, key="transit_refresh")

con = duckdb.connect('alenna_analytics.duckdb') # You can rename this .duckdb file later if you want

st.title("🚇 Malaysia Real-Time Transit Tracker")
st.markdown("Monitoring live bus positions across Klang Valley, Alor Setar, and Kota Bharu.")

# 2. TRIGGER INGESTION
# Define current_time before using it to avoid the 'name not defined' error
current_sync_time = datetime.now().strftime('%H:%M:%S')

with st.spinner('🛰️ Updating satellite positions...'):
    fetch_rapid_rail_live()

try:
    df_live = con.execute("SELECT * FROM live_buses").df()
    
    if not df_live.empty:
        # Success message with the current time we defined above
        st.success(f"✅ Last Sync: {current_sync_time} | Auto-refreshing every 10 seconds")

        # 3. ADVANCED METRICS (Maximizing the Dashboard)
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Active Buses", len(df_live))
        with col2:
            regions_count = df_live['region'].nunique()
            st.metric("Regions Monitored", regions_count)
        with col3:
            # Show which region has the most traffic
            top_region = df_live['region'].value_counts().idxmax()
            st.metric("Busiest Region", top_region)

        # 4. REGION FILTER & MAP
        selected_region = st.selectbox("🗺️ Select Region to View", df_live['region'].unique())
        df_filtered = df_live[df_live['region'] == selected_region]
        
        # Display Map
        st.map(df_filtered.rename(columns={'latitude': 'lat', 'longitude': 'lon'}))
        
        # 5. RAW DATA TABLE (For the 'Data Engineer' look)
        with st.expander("🔍 View Raw GTFS-Realtime Data"):
            st.dataframe(df_filtered, use_container_width=True)

    else:
        st.warning("No active vehicles found. The API might be in maintenance or it's late night.")

except Exception as e:
    st.error(f"Pipeline Error: {e}")

con.close()