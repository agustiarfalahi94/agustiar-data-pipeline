import streamlit as st
from utils import db, data_processor
from utils.ingestion import fetch_and_store_transit_data
from utils import background_fetch

# A screenful by default. Loading every row is offered, not forced: the whole
# retention window is 600k rows and 6.5 seconds, and nobody reading a table
# needs all of it to answer a question.
TABLE_PAGE_CHOICES = {
    "Newest 1,000": 1000,
    "Newest 10,000": 10000,
    "All rows": None,
}


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

    # Only what this page shows, and only what it needs to decide what to show.
    #
    # This used to be `get_historical_data()` — `SELECT *` over the retention
    # window, measured at 602,950 rows and 6.5 seconds, on every visit, to
    # display one screenful. The cost grew with the table, because it is
    # append-only. The page of rows is fetched from the database already
    # filtered, sorted and limited (~50 ms), and the region list comes from a
    # cheap DISTINCT rather than from scanning every row in pandas.
    available_regions, actual_sync_time = db.get_table_regions()

    if not available_regions:
        st.info("🛰️ No data. Click 'Refresh Data' to fetch.")
        return

    # Show sync time
    if actual_sync_time:
        st.success(f"Data updated: {actual_sync_time}")

    # No metrics - just pure table

    # Hardcoded region list to prevent dropdown changes during auto-refresh
    try:
        from utils.ingestion import API_SOURCES
        all_regions = list(API_SOURCES.keys())
        # Sort with Rapid Bus KL first
        hardcoded_regions = ['Rapid Bus KL'] + sorted([r for r in all_regions if r != 'Rapid Bus KL'])
    except ImportError:
        # Fallback to dynamic list if import fails
        hardcoded_regions = available_regions

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

    # How many rows to pull is the reader's call. The default is a screenful
    # and costs about 50 ms; "All rows" is offered rather than removed, because
    # the CSV below exports exactly what was asked for and a silently shrunken
    # export would be worse than a slow one.
    choice = st.selectbox(
        "Rows to load", options=list(TABLE_PAGE_CHOICES), index=0,
        key='table_page_rows',
        help="The newest rows first. The CSV download contains exactly these rows.",
    )
    page_df, total_rows = db.get_table_page(
        selected_regions, limit=TABLE_PAGE_CHOICES[choice])
    display_df = data_processor.format_table_page(page_df)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=600
    )

    # Said plainly rather than left for the reader to notice. The table is the
    # newest rows, not all of them; the CSV below is the whole selection.
    if total_rows > len(display_df):
        st.caption(
            f"Showing the newest {len(display_df):,} of {total_rows:,} rows for "
            f"the selected region(s) — choose **All rows** above to load every one. "
            f"The CSV below contains exactly the rows shown."
        )
    else:
        st.caption(f"Showing all {total_rows:,} rows for the selected region(s).")

    # Download button
    csv = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download CSV",
        data=csv,
        file_name=f"transit_data_{actual_sync_time.replace(' ', '_').replace(':', '-')}.csv",
        mime="text/csv"
    )