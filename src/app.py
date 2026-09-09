import streamlit as st
from streamlit_autorefresh import st_autorefresh
from datetime import datetime, timedelta, timezone
from utils import ai_transit, db

# Import config
try:
    from config import REGIONS, DATABASE_NAME, TIMEZONE, UTC_OFFSET_HOURS
except ImportError:
    REGIONS = [
        'Rapid Bus KL', 'Rapid Bus MRT Feeder', 'Rapid Bus Kuantan', 'Rapid Bus Penang',
        'KTM Berhad', 'myBAS Kangar', 'myBAS Alor Setar', 'myBAS Kota Bharu',
        'myBAS Kuala Terengganu', 'myBAS Ipoh', 'myBAS Seremban',
        'myBAS Melaka', 'myBAS Johor', 'myBAS Kuching',
    ]
    DATABASE_NAME = 'agustiar_analytics.duckdb'
    TIMEZONE = 'Asia/Kuala_Lumpur'
    UTC_OFFSET_HOURS = 8

# Page config
st.set_page_config(
    page_title="Malaysia Transit Tracker",
    page_icon="🚇",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if 'map_theme' not in st.session_state:
    st.session_state.map_theme = 'light'
if 'auto_refresh' not in st.session_state:
    st.session_state.auto_refresh = False
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = None
if 'current_page' not in st.session_state:
    st.session_state.current_page = "🗺️ Live Map"
if 'health_selected_region' not in st.session_state:
    st.session_state.health_selected_region = None
if 'selected_region' not in st.session_state:
    st.session_state.selected_region = None
if 'selected_regions_table' not in st.session_state:
    st.session_state.selected_regions_table = []


def _show_global_ai_panel():
    """Ask grounded questions from whichever dashboard page is open."""
    st.subheader("🤖 Ask the Network")
    st.caption("Ask about the current page or the wider network.")
    question = st.text_area(
        "Question",
        placeholder="What is happening in the selected view?",
        max_chars=ai_transit.MAX_QUESTION_CHARS,
        key="global_ai_question",
    )
    if not st.button("Ask Gemini", type="secondary", key="global_ask_gemini"):
        return
    used = st.session_state.get("ai_transit_queries", 0)
    if used >= 5:
        st.warning("This session has reached the five-question limit.")
        return
    if not question.strip():
        st.info("Enter a question first.")
        return

    health = db.get_network_health_summary()
    alerts = db.get_active_service_alerts()
    live, _, sync_time = db.get_live_data_optimized()
    page = st.session_state.current_page
    extra = {"CURRENT VIEW": page}

    if page == "📈 Analytics":
        extra["ANALYTICS SNAPSHOT"] = (
            f"Regional vehicle counts: {db.get_region_vehicle_counts().to_json(orient='records')}\n"
            f"Speed statistics: {db.get_moving_speed_stats()}"
        )
    elif page == "📊 Data Table":
        regions, table_sync = db.get_table_regions()
        selected = st.session_state.selected_regions_table or regions
        table_df, total = db.get_table_page(selected, limit=100)
        extra["DATA TABLE SNAPSHOT"] = (
            f"Selected regions: {selected}; matching rows: {total}; last seen: {table_sync}\n"
            f"Rows shown: {table_df.to_json(orient='records')}"
        )
    elif page == "🗺️ Live Map":
        extra["MAP CONTEXT"] = f"Selected region: {st.session_state.selected_region or 'not selected'}"

    with st.spinner("Reading the selected data..."):
        answer = ai_transit.ask_network(
            question, health, alerts, live, extra_sections=extra
        )
    st.session_state.ai_transit_queries = used + 1
    if answer is None:
        st.warning("The AI summary is unavailable. Check GEMINI_API_KEY and try again.")
    else:
        st.markdown(answer)
        if sync_time:
            st.caption(f"Grounded in stored vehicle data from {sync_time}.")

# Auto refresh MUST be at the top before any other widgets
if st.session_state.auto_refresh:
    # Trigger a rerun every 20s when auto refresh is enabled
    st_autorefresh(interval=20_000, key="auto_refresh_counter")

# Frozen header CSS
st.markdown("""
    <style>
    .main-header {
        position: sticky;
        top: 0;
        z-index: 999;
        background-color: inherit;
        padding: 1rem 0;
        border-bottom: 1px solid rgba(128, 128, 128, 0.2);
        margin-bottom: 1rem;
    }
    </style>
""", unsafe_allow_html=True)

# Current time
now_utc = datetime.now(timezone.utc)
now_kl = now_utc + timedelta(hours=UTC_OFFSET_HOURS)
current_time = now_kl.strftime('%d %b %Y %H:%M:%S')

# Frozen header
st.markdown(f"""
    <div class="main-header">
        <h2>🚇 Malaysia Real-Time Transit Tracker</h2>
        <p style="margin-top: -10px;">📅 {current_time} (GMT+8)</p>
    </div>
""", unsafe_allow_html=True)

# Sidebar controls
with st.sidebar:
    # Page navigation (moved to the top of the sidebar)
    st.subheader("📍 Navigation")
    _pages = ["🗺️ Live Map", "📊 Data Table", "📈 Analytics", "📡 Network Health"]
    page = st.radio(
        "Select View",
        _pages,
        index=_pages.index(st.session_state.current_page) if st.session_state.current_page in _pages else 0,
        label_visibility="collapsed",
        key="page_radio"
    )

    if page != st.session_state.current_page:
        st.session_state.current_page = page
        st.rerun()

    st.divider()

    st.title("⚙️ Settings")

    # Theme controls, on the one page that has a map to theme.
    #
    # `map_theme` is read only by live_map.py, so on every other page this
    # button was a control the user could press and watch do nothing. It is
    # offered where it applies and hidden where it does not; the chosen theme
    # still persists in session state while the user is away, so returning to
    # the Live Map finds it as they left it.
    if st.session_state.current_page == "🗺️ Live Map":
        st.subheader("🎨 Appearance")

        # Only map theme button (page theme uses Streamlit's built-in settings)
        map_theme_btn = st.button(
            "🗺️ Map: Dark" if st.session_state.map_theme == 'light' else "🗺️ Map: Light",
            use_container_width=True,
            key="map_theme_btn"
        )
        if map_theme_btn:
            st.session_state.map_theme = 'dark' if st.session_state.map_theme == 'light' else 'light'
            st.rerun()

        st.divider()

    # Refresh controls
    st.subheader("🔄 Refresh Mode")
    refresh_mode = st.radio(
        "Mode",
        ["Manual", "Auto (20s)"],
        index=1 if st.session_state.auto_refresh else 0,
        horizontal=True,
        key="refresh_radio"
    )

    if (refresh_mode == "Auto (20s)") != st.session_state.auto_refresh:
        st.session_state.auto_refresh = (refresh_mode == "Auto (20s)")
        st.rerun()

    st.divider()
    _show_global_ai_panel()


# Route to pages
if st.session_state.current_page == "🗺️ Live Map":
    from app_pages import live_map
    live_map.show()
elif st.session_state.current_page == "📊 Data Table":
    from app_pages import data_table
    data_table.show()
elif st.session_state.current_page == "📈 Analytics":
    from app_pages import analytics
    analytics.show()
else:
    from app_pages import network_health
    network_health.show()
