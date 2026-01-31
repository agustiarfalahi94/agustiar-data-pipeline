import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from utils import db, data_processor
from ingestion_rapidbus_mrtfeeder import fetch_rapid_rail_live


def show():
    # Refresh behaviour
    if st.session_state.auto_refresh:
        with st.spinner('🛰️ Auto-refreshing...'):
            fetch_rapid_rail_live()
            st.session_state.last_refresh = True
    else:
        # Manual refresh button
        if st.button("🔄 Refresh Data", type="primary"):
            with st.spinner('🛰️ Fetching...'):
                fetch_rapid_rail_live()
                st.session_state.last_refresh = True
            st.rerun()

    # Get LATEST live data for current vehicle counts only
    df_live, metrics_live, actual_sync_time = db.get_live_data_optimized()
    
    # Get ALL historical data for charts AND speed statistics
    df_historical, _, _ = db.get_historical_data()

    if df_live is None or df_live.empty or df_historical is None or df_historical.empty:
        st.info("🛰️ No data available. Please refresh.")
        return

    # Convert speed from m/s to km/h for live data
    df_live['speed'] = pd.to_numeric(df_live['speed'], errors='coerce').fillna(0) * 3.6
    df_live['speed'] = df_live['speed'].round(0).clip(upper=120)
    
    # Convert speed from m/s to km/h for historical data
    df_historical['speed'] = pd.to_numeric(df_historical['speed'], errors='coerce').fillna(0) * 3.6
    df_historical['speed'] = df_historical['speed'].round(0).clip(upper=120)

    # Show sync time
    if actual_sync_time:
        st.success(f"Data updated: {actual_sync_time}")

    # Overview metrics
    col1, col2 = st.columns(2)
    col1.metric("Regions Monitored", metrics_live['regions'])
    # Calculate avg speed from HISTORICAL data (moving vehicles only)
    avg_speed_historical = df_historical[df_historical['speed'] > 0]['speed'].mean() if len(df_historical[df_historical['speed'] > 0]) > 0 else 0
    col2.metric("Avg Speed", f"{avg_speed_historical:.2f} km/h")

    st.divider()

    # Charts - use DISTINCT vehicle counts from historical data
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.subheader("📊 Buses by Region")
        # Count DISTINCT vehicle_id per region
        region_counts = df_historical.groupby('region')['vehicle_id'].nunique().reset_index()
        region_counts.columns = ['Region', 'Count']
        region_counts = region_counts.sort_values('Count', ascending=True)

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

    with stats_col1:
        # Total unique vehicles from HISTORICAL data (distinct vehicle_id)
        total_unique_vehicles = df_historical['vehicle_id'].nunique()
        st.metric("Total Vehicles", total_unique_vehicles)
        
        # Moving vehicles from LIVE data (speed > 0, distinct vehicle_id)
        moving_count = len(df_live[df_live['speed'] > 0])
        st.metric("Moving Vehicles", moving_count)

    with stats_col2:
        # All speed stats from HISTORICAL data
        st.metric("Max Speed", f"{df_historical['speed'].max():.2f} km/h")
        st.metric("Min Speed", f"{df_historical['speed'].min():.2f} km/h")

    with stats_col3:
        # Avg speed from HISTORICAL moving vehicles
        st.metric("Avg Speed", f"{avg_speed_historical:.2f} km/h")
        
        # Median speed from HISTORICAL moving vehicles only
        moving_speeds_historical = df_historical[df_historical['speed'] > 0]['speed']
        median_speed = moving_speeds_historical.median() if len(moving_speeds_historical) > 0 else 0
        st.metric("Median Speed", f"{median_speed:.2f} km/h")