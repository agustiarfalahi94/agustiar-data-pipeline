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
    
    # Summaries, not rows. This page draws counts, averages and a box plot; it
    # never shows an individual reading. It used to load the whole retention
    # window to get them — 602,950 rows and 6.5 seconds, measured, growing with
    # the table — where the database answers the same questions in about 30 ms.
    #
    # The summary is one row per (vehicle, region) carrying a *sum* and a
    # *count* rather than an average, so regrouping it by vehicle alone stays
    # exact. Averaging averages would weight a vehicle's quiet region the same
    # as its busy one.
    speed_summary = db.get_vehicle_speed_summary()
    moving = db.get_moving_speed_stats()

    live_empty = df_live is None or df_live.empty
    historical_empty = speed_summary.empty

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

    # Convert speed using helper function. Only the live frame needs it now —
    # the historical summary is already in km/h, converted inside the query so
    # that the rounding and the 120 cap are applied before anything is
    # averaged, exactly as this page did in pandas.
    df_live = data_processor.convert_speed_to_kmh(df_live.copy())

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
        _per_vehicle = speed_summary.groupby('vehicle_id')[['speed_sum', 'n_rows']].sum()
        avg_speed_per_vehicle = (_per_vehicle['speed_sum'] / _per_vehicle['n_rows']
                                 ).reset_index(name='avg_speed')
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
    vehicle_avg_speeds = speed_summary.assign(
        avg_speed=speed_summary['speed_sum'] / speed_summary['n_rows']
    )[['vehicle_id', 'region', 'avg_speed']]
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
    
    # Moving-vehicle speed stats come from the database (`moving`), measured
    # over rows whose *converted* speed exceeds zero — a bus at 0.1 m/s rounds
    # to 0 km/h and counts as stopped, which is what the pandas filter did.

    with stats_col1:
        # Total unique vehicles from HISTORICAL data (distinct vehicle_id)
        total_unique_vehicles = speed_summary['vehicle_id'].nunique()
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
        if moving['rows'] > 0:
            st.metric("Max Speed", f"{moving['max']:.2f} km/h")
            st.metric("Min Speed", f"{moving['min']:.2f} km/h")
        else:
            st.metric("Max Speed", "0.00 km/h")
            st.metric("Min Speed", "0.00 km/h")

    with stats_col3:
        # Avg and Median speed from HISTORICAL moving vehicles
        if moving['rows'] > 0:
            st.metric("Avg Speed", f"{moving['avg']:.2f} km/h")
            st.metric("Median Speed", f"{moving['median']:.2f} km/h")
        else:
            st.metric("Avg Speed", "0.00 km/h")
            st.metric("Median Speed", "0.00 km/h")