import streamlit as st
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

    # Get historical data (all data, not just latest)
    df_historical, metrics, actual_sync_time = db.get_historical_data()

    if df_historical is None or df_historical.empty:
        st.info("🛰️ No data. Click 'Refresh Data' to fetch.")
        return

    # Show sync time
    if actual_sync_time:
        st.success(f"Data updated: {actual_sync_time}")

    # Metrics (Regions Monitored and Avg Speed for Data Table)
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Regions Monitored", metrics['regions'])
    with col2:
        # Calculate average speed across all filtered data
        avg_speed = df_historical['speed'].mean() * 3.6  # Convert to km/h
        st.metric("Avg Speed", f"{avg_speed:.2f} km/h")

    st.divider()

    # Hardcoded region list to prevent dropdown changes during auto-refresh
    try:
        from ingestion_rapidbus_mrtfeeder import API_SOURCES
        all_regions = list(API_SOURCES.keys())
        # Sort with Rapid Bus KL first
        hardcoded_regions = ['Rapid Bus KL'] + sorted([r for r in all_regions if r != 'Rapid Bus KL'])
    except ImportError:
        # Fallback to dynamic list if import fails
        hardcoded_regions = data_processor.get_sorted_regions(df_historical)
    
    # Get available regions from current data
    available_regions = data_processor.get_sorted_regions(df_historical)

    if not available_regions:
        st.info("No active buses.")
        return

    # Preserve selected regions during auto-refresh
    if not st.session_state.selected_regions_table or not all(
        r in hardcoded_regions for r in st.session_state.selected_regions_table
    ):
        # Initialize with first 3 available regions or all if less than 3
        init_regions = available_regions[:3] if len(available_regions) >= 3 else available_regions
        st.session_state.selected_regions_table = [r for r in init_regions if r in hardcoded_regions]

    # Multi-select for regions - use hardcoded list
    selected_regions = st.multiselect(
        "Filter by Regions",
        options=hardcoded_regions,  # Use hardcoded list instead of dynamic
        default=st.session_state.selected_regions_table,
        key='regions_multiselect_table'
    )
    
    # Update session state
    st.session_state.selected_regions_table = selected_regions

    if not selected_regions:
        st.warning("Please select at least one region")
        return

    # Filter data
    df_filtered = df_historical[df_historical['region'].isin(selected_regions)]

    # Format and display
    display_df = data_processor.format_display_dataframe(df_filtered)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=600
    )

    # Download button
    csv = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download CSV",
        data=csv,
        file_name=f"transit_data_{actual_sync_time.replace(' ', '_').replace(':', '-')}.csv",
        mime="text/csv"
    )

