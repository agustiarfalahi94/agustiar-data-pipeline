import streamlit as st
import plotly.express as px
from utils import db, data_processor
from utils.ingestion import fetch_and_store_transit_data
from utils import background_fetch

# Read individually: a config.py copied from config.example.py before these
# knobs existed lacks them, and an all-or-nothing tuple import would fail
# entirely rather than falling back one knob at a time. See utils/db.py.
try:
    import config as _config
except ImportError:
    _config = None

LIVE_FRESH_SECONDS = getattr(_config, 'LIVE_FRESH_SECONDS', 60)
LIVE_HIDDEN_SECONDS = getattr(_config, 'LIVE_HIDDEN_SECONDS', 900)


def show():
    # Refresh behaviour
    if st.session_state.auto_refresh:
        # Behind the page, and once per timer tick rather than once per rerun.
        # Blocking here froze this page for the 3-10 seconds the agency's
        # server takes to answer, and it ran again on every interaction, not
        # only when the 20 seconds were up. See `utils/background_fetch.py`.
        background_fetch.maybe_start_tick_fetch(st.session_state)
    else:
        # Manual refresh button
        if st.button("🔄 Refresh Data", type="primary"):
            with st.spinner('🛰️ Fetching...'):
                fetch_and_store_transit_data()
                st.session_state.last_refresh = True
            st.rerun()

    # Get LATEST live data for current vehicle counts
    df_live, _, actual_sync_time = db.get_live_data_optimized()
    
    # Get ALL historical data for charts and speed statistics
    df_historical, _, _ = db.get_historical_data()

    live_empty = df_live is None or df_live.empty
    historical_empty = df_historical is None or df_historical.empty

    if live_empty or historical_empty:
        # An outage and an empty database both produce an empty live frame. The
        # sync time survives an empty window precisely so this branch can say
        # which one it is instead of implying nothing was ever ingested.
        if not actual_sync_time:
            st.info("🛰️ No data available. Please refresh.")
        elif live_empty:
            st.warning(
                f"⏳ No vehicle has reported in the last "
                f"{data_processor.format_duration(LIVE_HIDDEN_SECONDS)}. "
                f"The feed looks stale — data was last seen at {actual_sync_time}."
            )
        else:
            st.info(
                "🛰️ No history inside the retention window yet, so the charts have "
                f"nothing to plot. Data was last seen at {actual_sync_time}."
            )
        return

    # Convert speed using helper function
    df_live = data_processor.convert_speed_to_kmh(df_live.copy())
    df_historical = data_processor.convert_speed_to_kmh(df_historical.copy())

    # Show sync time
    if actual_sync_time:
        st.success(f"Data updated: {actual_sync_time}")

    # Charts - use DISTINCT vehicle counts from historical data
    col_chart1, col_chart2 = st.columns(2)

    # Count DISTINCT vehicle_id per region (from the dbt mart). Shared by the
    # bar chart (below) and the pie chart (further down the page) - both must
    # be skipped together when the mart has no data yet.
    region_counts = db.get_region_vehicle_counts()
    region_counts_available = not region_counts.empty
    if region_counts_available:
        region_counts = region_counts.sort_values('Count', ascending=True)

    with col_chart1:
        st.subheader("📊 Buses by Region")
        if not region_counts_available:
            st.info(
                "🛰️ Regional vehicle counts are not available yet. "
                "Click **Refresh Data** to build them."
            )
        else:
            fig1 = px.bar(
                region_counts,
                x='Count',
                y='Region',
                orientation='h',
                color_discrete_sequence=['#3399FF']
            )
            fig1.update_layout(
                height=400,
                showlegend=False,
            )
            st.plotly_chart(fig1, use_container_width=True)

    with col_chart2:
        st.subheader("🏃 Speed Distribution")
        # Calculate average speed per vehicle (not raw data points)
        avg_speed_per_vehicle = df_historical.groupby('vehicle_id')['speed'].mean().reset_index()
        avg_speed_per_vehicle.columns = ['vehicle_id', 'avg_speed']
        # Filter out zero speeds
        speed_data = avg_speed_per_vehicle[avg_speed_per_vehicle['avg_speed'] > 0]

        fig2 = px.histogram(
            speed_data,
            x='avg_speed',
            nbins=30,
            labels={'avg_speed': 'Avg Speed per Vehicle (km/h)', 'count': 'Number of Vehicles'}
        )
        fig2.update_layout(
            height=400,
            showlegend=False,
        )
        st.plotly_chart(fig2, use_container_width=True)

    # Pie chart for distribution (DISTINCT vehicle count)
    st.subheader("🎯 Regional Distribution")

    if not region_counts_available:
        st.info(
            "🛰️ Regional distribution is not available yet. "
            "Click **Refresh Data** to build it."
        )
    else:
        fig3 = px.pie(
            region_counts,
            values='Count',
            names='Region',
            hole=0.4
        )
        fig3.update_layout(height=500)
        st.plotly_chart(fig3, use_container_width=True)

    # Speed by region box plot (using average speed per vehicle)
    st.subheader("📈 Speed Analysis by Region")
    
    # Calculate avg speed per vehicle with region info - INCLUDE ALL VEHICLES (even speed=0)
    vehicle_avg_speeds = df_historical.groupby(['vehicle_id', 'region'])['speed'].mean().reset_index()
    vehicle_avg_speeds.columns = ['vehicle_id', 'region', 'avg_speed']
    # DON'T filter out zero speeds - show all regions with data

    fig4 = px.box(
        vehicle_avg_speeds,
        x='region',
        y='avg_speed',
        labels={'region': 'Region', 'avg_speed': 'Avg Speed per Vehicle (km/h)'}
    )
    fig4.update_layout(
        height=500,
        xaxis_tickangle=-45,
    )
    st.plotly_chart(fig4, use_container_width=True)

    # Summary statistics
    st.subheader("📋 Summary Statistics")

    stats_col1, stats_col2, stats_col3 = st.columns(3)
    
    # Filter to moving vehicles only (speed > 0) for consistent speed metrics
    moving_vehicles_historical = df_historical[df_historical['speed'] > 0]

    with stats_col1:
        # Total unique vehicles from HISTORICAL data (distinct vehicle_id)
        total_unique_vehicles = df_historical['vehicle_id'].nunique()
        st.metric("Total Vehicles", total_unique_vehicles)
        
        # Moving vehicles from LIVE data (speed > 0), fresh rows only. The live
        # frame widened to LIVE_HIDDEN_SECONDS in 2.4.0 and now carries stale and
        # hidden rows too; counting all of them would silently turn this metric
        # into "moved at some point in the last 15 minutes".
        df_moving_source = (
            df_live[df_live['freshness'] == 'fresh']
            if 'freshness' in df_live.columns else df_live
        )
        moving_count = int((df_moving_source['speed'] > 0).sum())
        st.metric(
            "Moving Vehicles", moving_count,
            help=(
                "Vehicles reporting a non-zero speed in their latest position, "
                f"within the last {data_processor.format_duration(LIVE_FRESH_SECONDS)}."
            ),
        )

    with stats_col2:
        # All speed stats from HISTORICAL moving vehicles (excludes stopped buses)
        if len(moving_vehicles_historical) > 0:
            st.metric("Max Speed", f"{moving_vehicles_historical['speed'].max():.2f} km/h")
            st.metric("Min Speed", f"{moving_vehicles_historical['speed'].min():.2f} km/h")
        else:
            st.metric("Max Speed", "0.00 km/h")
            st.metric("Min Speed", "0.00 km/h")

    with stats_col3:
        # Avg and Median speed from HISTORICAL moving vehicles
        if len(moving_vehicles_historical) > 0:
            st.metric("Avg Speed", f"{moving_vehicles_historical['speed'].mean():.2f} km/h")
            st.metric("Median Speed", f"{moving_vehicles_historical['speed'].median():.2f} km/h")
        else:
            st.metric("Avg Speed", "0.00 km/h")
            st.metric("Median Speed", "0.00 km/h")