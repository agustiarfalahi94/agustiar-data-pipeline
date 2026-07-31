# config.example.py
# Template configuration file - Copy this to config.py and customize as needed
# This file is safe to commit to version control

# List of all transit regions monitored by the application
REGIONS = [
    'Rapid Bus KL',
    'Rapid Bus MRT Feeder',
    'Rapid Bus Kuantan',
    'Rapid Bus Penang',
    'KTM Berhad',
    'myBAS Kangar',
    'myBAS Alor Setar',
    'myBAS Kota Bharu',
    'myBAS Kuala Terengganu',
    'myBAS Ipoh',
    'myBAS Seremban',
    'myBAS Melaka',
    'myBAS Johor',
    'myBAS Kuching'
]

# Database configuration
DATABASE_NAME = 'agustiar_analytics.duckdb'
DATABASE_TABLE = 'live_buses'

# Timezone configuration
TIMEZONE = 'Asia/Kuala_Lumpur'
UTC_OFFSET_HOURS = 8

# Map configuration
DEFAULT_ZOOM = 13
MAP_STYLE = 'light'

# Arrow visualization settings
ARROW_SIZE = 0.001
ARROW_COLOR_RGB = [0, 122, 255]
CENTER_DOT_COLOR_RGB = [255, 69, 0]
ARROW_OPACITY = 200

# Data freshness settings (in seconds)
DATA_MAX_AGE = 3600
DATA_FUTURE_TOLERANCE = 300

# Live Map freshness tiers (seconds). Ages are measured from wall-clock now and
# clamped at zero, so a feed whose clock runs fast cannot shift the window.
LIVE_FRESH_SECONDS = 60     # at or under this age a vehicle is drawn solid
LIVE_STALE_SECONDS = 300    # up to this age it is drawn dimmed with a "last update" note
LIVE_HIDDEN_SECONDS = 900   # up to this age it is counted as hidden; older is not fetched

# Data retention — rows older than this are pruned from DuckDB on each ingestion run.
# Increase if you need more history for analytics; decrease to save disk space.
DATA_RETENTION_DAYS = 7

# Primary region displayed first in dropdowns
PRIMARY_REGION = 'Rapid Bus KL'

# API endpoints mapping
API_SOURCES = {
    'Rapid Bus KL': ['prasarana?category=rapid-bus-kl'],
    'Rapid Bus MRT Feeder': ['prasarana?category=rapid-bus-mrtfeeder'],
    'Rapid Bus Kuantan': ['prasarana?category=rapid-bus-kuantan'],
    'Rapid Bus Penang': ['prasarana?category=rapid-bus-penang'],
    'KTM Berhad': ['ktmb'],
    'myBAS Kangar': ['mybas-kangar'],
    'myBAS Alor Setar': ['mybas-alor-setar'],
    'myBAS Kota Bharu': ['mybas-kota-bharu'],
    'myBAS Kuala Terengganu': ['mybas-kuala-terengganu'],
    'myBAS Ipoh': ['mybas-ipoh'],
    'myBAS Seremban': ['mybas-seremban-a', 'mybas-seremban-b'],
    'myBAS Melaka': ['mybas-melaka'],
    'myBAS Johor': ['mybas-johor'],
    'myBAS Kuching': ['mybas-kuching']
}

# API configuration
API_BASE_URL = 'https://api.data.gov.my/gtfs-realtime/vehicle-position/'
REQUEST_TIMEOUT = 10

# OpenRouteService API key, for real walking distances to nearby stops.
# Free key from https://account.heigit.org. Optional: without it, walk times
# fall back to a straight-line estimate and are labelled "(estimated)".
ORS_API_KEY = ''
