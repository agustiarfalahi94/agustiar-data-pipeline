import streamlit as st
from streamlit_autorefresh import st_autorefresh
from datetime import datetime, timedelta, timezone

# Import config
try:
    from config import REGIONS, DATABASE_NAME, TIMEZONE, UTC_OFFSET_HOURS
except ImportError:
    REGIONS = st.secrets["regions"]["list"]
    DATABASE_NAME = st.secrets["database"]["name"]
    TIMEZONE = st.secrets["timezone"]["name"]
    UTC_OFFSET_HOURS = st.secrets["timezone"]["utc_offset_hours"]

# Page config
st.set_page_config(
    page_title="Malaysia Transit Tracker",
    page_icon="🚇",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if 'theme_mode' not in st.session_state:
    st.session_state.theme_mode = 'light'
if 'map_theme' not in st.session_state:
    st.session_state.map_theme = 'light'
if 'auto_refresh' not in st.session_state:
    st.session_state.auto_refresh = False
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = None
if 'current_page' not in st.session_state:
    st.session_state.current_page = "🗺️ Map"
if 'selected_region' not in st.session_state:
    st.session_state.selected_region = None
if 'selected_regions_table' not in st.session_state:
    st.session_state.selected_regions_table = []

# Auto refresh MUST be at the top before any other widgets
if st.session_state.auto_refresh:
    # Trigger a rerun every 20s when auto refresh is enabled
    st_autorefresh(interval=10_000, key="auto_refresh_counter")

# Apply custom CSS for themes and frozen header
if st.session_state.theme_mode == 'dark':
    # Dark theme
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {
            background-color: #0e1117;
            color: #fafafa !important;
        }
        [data-testid="stAppViewContainer"] * {
            color: #fafafa !important;
        }
        [data-testid="stSidebar"] {
            background-color: #161a23;
        }
        [data-testid="stHeader"], [data-testid="stToolbar"] {
            background-color: #0e1117;
        }
        .main-header {
            position: sticky;
            top: 0;
            z-index: 999;
            background-color: #0e1117;
            padding: 1rem 0;
            border-bottom: 1px solid rgba(128, 128, 128, 0.2);
            margin-bottom: 1rem;
        }
        /* Inputs / tables / buttons in dark mode */
        .stDataFrame, [data-testid="stDataFrame"] {
            background-color: #0e1117;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
else:
    # Light theme (soft grey background, dark text)
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {
            background-color: #f3f4f6;
            color: #31333F !important;
        }
        [data-testid="stAppViewContainer"] * {
            color: #31333F !important;
        }
        [data-testid="stSidebar"] {
            background-color: #e5e7eb;
        }
        [data-testid="stHeader"], [data-testid="stToolbar"] {
            background-color: #f3f4f6;
        }
        .main-header {
            position: sticky;
            top: 0;
            z-index: 999;
            background-color: #f3f4f6;
            padding: 1rem 0;
            border-bottom: 1px solid rgba(148, 163, 184, 0.6);
            margin-bottom: 1rem;
        }
        /* Inputs / selectors / tables / buttons in light mode */
        .stSelectbox, .stMultiSelect, .stTextInput, .stNumberInput {
            background-color: #ffffff !important;
            color: #111827 !important;
        }
        /* Multi-select filter box styling in light mode */
        [data-baseweb="select"] {
            background-color: #f3f4f6 !important;
        }
        [data-baseweb="select"] > div {
            background-color: #f3f4f6 !important;
        }
        /* Data table styling in light mode */
        [data-testid="stDataFrame"] {
            background-color: #f9fafb !important;
        }
        [data-testid="stDataFrame"] table {
            background-color: #ffffff !important;
            color: #111827 !important;
        }
        [data-testid="stDataFrame"] th {
            background-color: #f3f4f6 !important;
            color: #111827 !important;
        }
        [data-testid="stDataFrame"] td {
            background-color: #ffffff !important;
            color: #111827 !important;
        }
        /* CSV download button in light mode */
        .stDownloadButton > button {
            background-color: #f3f4f6 !important;
            color: #111827 !important;
            border: 1px solid #d1d5db !important;
        }
        .stDownloadButton > button:hover {
            background-color: #e5e7eb !important;
        }
        .stButton > button {
            background-color: #ffffff !important;
            color: #111827 !important;
        }
        /* Fix selectbox dropdown styling */
        .stSelectbox > div > div {
            background-color: #ffffff !important;
            color: #111827 !important;
        }
        .stSelectbox > div > div > div {
            background-color: #ffffff !important;
            color: #111827 !important;
        }
        /* Fix dropdown menu items */
        [data-baseweb="select"] {
            background-color: #ffffff !important;
        }
        [data-baseweb="select"] > div {
            background-color: #ffffff !important;
            color: #111827 !important;
        }
        [data-baseweb="popover"] {
            background-color: #ffffff !important;
        }
        [data-baseweb="popover"] li {
            background-color: #ffffff !important;
            color: #111827 !important;
        }
        [data-baseweb="popover"] li:hover {
            background-color: #f3f4f6 !important;
        }
        .stButton > button {
            border-radius: 999px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

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
        ["🗺️ Map", "📊 Data Table", "📈 Analytics"],
        index=["🗺️ Map", "📊 Data Table", "📈 Analytics"].index(st.session_state.current_page),
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
    theme_col1, theme_col2 = st.columns(2)

    with theme_col1:
        page_theme_btn = st.button(
            "🌙 Dark" if st.session_state.theme_mode == 'light' else "☀️ Light",
            use_container_width=True,
            key="page_theme_btn"
        )
        if page_theme_btn:
            st.session_state.theme_mode = 'dark' if st.session_state.theme_mode == 'light' else 'light'
            st.rerun()

    with theme_col2:
        map_theme_btn = st.button(
            "🗺️ Map: D" if st.session_state.map_theme == 'light' else "🗺️ Map: L",
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
if st.session_state.current_page == "🗺️ Map":
    from app_pages import live_map
    live_map.show()
elif st.session_state.current_page == "📊 Data Table":
    from app_pages import data_table
    data_table.show()
else:
    from app_pages import analytics
    analytics.show()
