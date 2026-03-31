import streamlit as st
from streamlit_autorefresh import st_autorefresh
from datetime import datetime, timedelta, timezone

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
if 'selected_region' not in st.session_state:
    st.session_state.selected_region = None
if 'selected_regions_table' not in st.session_state:
    st.session_state.selected_regions_table = []

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
    page = st.radio(
        "Select View",
        ["🗺️ Live Map", "📊 Data Table", "📈 Analytics", "📍 Route Planner"],
        index=["🗺️ Live Map", "📊 Data Table", "📈 Analytics", "📍 Route Planner"].index(st.session_state.current_page),
        label_visibility="collapsed",
        key="page_radio"
    )

    if page != st.session_state.current_page:
        st.session_state.current_page = page
        st.rerun()

    st.divider()

    st.title("⚙️ Settings")

    # Theme controls
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


# Route to pages
if st.session_state.current_page == "🗺️ Live Map":
    from app_pages import live_map
    live_map.show()
elif st.session_state.current_page == "📊 Data Table":
    from app_pages import data_table
    data_table.show()
elif st.session_state.current_page == "📍 Route Planner":
    from app_pages import route_planner
    route_planner.show()
else:
    from app_pages import analytics
    analytics.show()