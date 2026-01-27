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
