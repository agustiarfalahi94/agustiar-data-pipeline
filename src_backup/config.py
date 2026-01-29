# config.py
# Configuration file for Malaysia Real-Time Transit Tracker
# This file contains constants and configuration variables

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
ARROW_SIZE = 0.001  # Size multiplier for arrow paths
ARROW_COLOR_RGB = [0, 122, 255]  # Blue color for arrows
CENTER_DOT_COLOR_RGB = [255, 69, 0]  # Orange color for center dots
ARROW_OPACITY = 200  # Alpha value (0-255)

# Data freshness settings (in seconds)
DATA_MAX_AGE = 3600  # 1 hour - filter out data older than this
DATA_FUTURE_TOLERANCE = 300  # 5 minutes - filter out data timestamped too far in future

# API endpoints mapping
# Format: 'Display Name': ['endpoint1', 'endpoint2', ...]
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

# API base URL
API_BASE_URL = 'https://api.data.gov.my/gtfs-realtime/vehicle-position/'

# Request timeout (in seconds)
REQUEST_TIMEOUT = 10
