"""
gtfs_static.py
--------------
Utilities for downloading, caching, and reading Malaysia GTFS Static data from
https://api.data.gov.my/gtfs-static/<agency>

The ZIP is cached locally for 24 hours to avoid hammering the API on every
Streamlit rerun.  All ZIP parsing is done in-memory via zipfile + io.BytesIO.
"""

import io
import os
import time
import zipfile
import csv
import requests

# ---------------------------------------------------------------------------
# Agency slugs — mirrors API_SOURCES in ingestion.py
# Key: display name used in selected_region, Value: GTFS static slug (single)
# ---------------------------------------------------------------------------
STATIC_API_SOURCES = {
    'Rapid Bus KL':              'prasarana?category=rapid-bus-kl',
    'Rapid Bus MRT Feeder':      'prasarana?category=rapid-bus-mrtfeeder',
    'Rapid Bus Kuantan':         'prasarana?category=rapid-bus-kuantan',
    'Rapid Bus Penang':          'prasarana?category=rapid-bus-penang',
    'KTM Berhad':                'ktmb',
    'myBAS Kangar':              'mybas-kangar',
    'myBAS Alor Setar':          'mybas-alor-setar',
    'myBAS Kota Bharu':          'mybas-kota-bharu',
    'myBAS Kuala Terengganu':    'mybas-kuala-terengganu',
    'myBAS Ipoh':                'mybas-ipoh',
    'myBAS Seremban':            'mybas-seremban-a',   # primary slug for this region
    'myBAS Melaka':              'mybas-melaka',
    'myBAS Johor':               'mybas-johor',
    'myBAS Kuching':             'mybas-kuching',
}

STATIC_API_BASE_URL = 'https://api.data.gov.my/gtfs-static/'
CACHE_TTL_SECONDS = 86400          # 24 hours
REQUEST_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _slug_safe(agency_slug: str) -> str:
    """Return a filesystem-safe version of the slug (strip query params)."""
    return agency_slug.replace('?', '_').replace('=', '_').replace('&', '_').replace('-', '_')


def get_cached_path(agency_slug: str) -> str:
    """Return the local file path where the ZIP for *agency_slug* is cached."""
    return f"/tmp/gtfs_static_{_slug_safe(agency_slug)}.zip"


def is_cache_fresh(agency_slug: str) -> bool:
    """Return True if a cached ZIP exists and is less than 24 hours old."""
    path = get_cached_path(agency_slug)
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < CACHE_TTL_SECONDS


def download_static_gtfs(agency_slug: str) -> str:
    """
    Download the GTFS Static ZIP for *agency_slug* and save to the cache path.

    Returns the cache path on success, raises on HTTP or IO errors.
    """
    url = f"{STATIC_API_BASE_URL}{agency_slug}"
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    path = get_cached_path(agency_slug)
    with open(path, 'wb') as fh:
        fh.write(response.content)
    return path


# ---------------------------------------------------------------------------
# In-memory ZIP reading helpers
# ---------------------------------------------------------------------------

def _load_zip(agency_slug: str) -> zipfile.ZipFile:
    """
    Return an open ZipFile object for *agency_slug*.
    Downloads first if the cache is stale or missing.
    """
    if not is_cache_fresh(agency_slug):
        download_static_gtfs(agency_slug)

    path = get_cached_path(agency_slug)
    return zipfile.ZipFile(path, 'r')


def _read_csv_from_zip(zf: zipfile.ZipFile, filename: str):
    """
    Read *filename* from an open ZipFile and return a list of dicts (csv.DictReader).
    Returns an empty list if the file is absent in the ZIP.
    """
    # Names in the ZIP may have a directory prefix — find a match
    names = zf.namelist()
    match = next((n for n in names if n.endswith(filename)), None)
    if match is None:
        return []

    with zf.open(match) as raw:
        content = io.TextIOWrapper(raw, encoding='utf-8-sig')
        reader = csv.DictReader(content)
        return list(reader)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_shapes_for_trip(agency_slug: str, trip_id: str) -> list:
    """
    Return an ordered list of [lon, lat] pairs representing the planned route
    shape for *trip_id* within *agency_slug*.

    Steps:
      1. Look up shape_id from trips.txt using trip_id.
      2. Read shapes.txt and collect all points for that shape_id, sorted by
         shape_pt_sequence.
      3. Return [[lon, lat], ...] in pydeck format.

    Returns an empty list if:
      - trip_id is empty / not found in trips.txt
      - shapes.txt is absent from the ZIP
      - shape_id has no points
      - any download or parsing error occurs
    """
    if not trip_id:
        return []

    try:
        with _load_zip(agency_slug) as zf:
            # ---- Step 1: resolve shape_id from trips.txt ----
            trips = _read_csv_from_zip(zf, 'trips.txt')
            if not trips:
                return []

            shape_id = None
            for row in trips:
                if row.get('trip_id', '').strip() == trip_id.strip():
                    shape_id = row.get('shape_id', '').strip()
                    break

            if not shape_id:
                return []

            # ---- Step 2: read shapes.txt ----
            shapes = _read_csv_from_zip(zf, 'shapes.txt')
            if not shapes:
                return []

            # Collect points for the matching shape_id
            points = []
            for row in shapes:
                if row.get('shape_id', '').strip() == shape_id:
                    try:
                        seq = int(row.get('shape_pt_sequence', 0))
                        lat = float(row['shape_pt_lat'])
                        lon = float(row['shape_pt_lon'])
                        points.append((seq, lon, lat))
                    except (KeyError, ValueError):
                        continue

            if not points:
                return []

            # Sort by sequence and return [lon, lat] pairs
            points.sort(key=lambda x: x[0])
            return [[lon, lat] for _, lon, lat in points]

    except Exception:
        return []


def get_route_name(agency_slug: str, route_id: str) -> str:
    """
    Return a human-readable route name string for *route_id* within *agency_slug*.

    Combines route_short_name and route_long_name from routes.txt.
    Returns an empty string if not found or on any error.
    """
    if not route_id:
        return ''

    try:
        with _load_zip(agency_slug) as zf:
            routes = _read_csv_from_zip(zf, 'routes.txt')
            if not routes:
                return ''

            for row in routes:
                if row.get('route_id', '').strip() == route_id.strip():
                    short = row.get('route_short_name', '').strip()
                    long_ = row.get('route_long_name', '').strip()
                    if short and long_:
                        return f"{short} — {long_}"
                    return short or long_

    except Exception:
        return ''

    return ''


# ---------------------------------------------------------------------------
# Route lookup
#
# These exist so the Live Map can tell two very different situations apart:
# "this route does not run in the region you are looking at" and "it runs here
# but nothing is reporting right now". Conflating them produced a search that
# looked broken when it was working correctly against the wrong region.
# ---------------------------------------------------------------------------

# region name -> set of upper-cased route_short_name. Populated lazily and kept
# for the life of the process; the underlying ZIPs already carry a 24h cache.
_ROUTE_REGION_INDEX = {}


def _route_short_names(agency_slug: str) -> set:
    """Upper-cased route_short_name values for one agency. Empty set on any error."""
    names = set()
    try:
        with _load_zip(agency_slug) as zf:
            for row in _read_csv_from_zip(zf, 'routes.txt') or []:
                short = (row.get('route_short_name') or '').strip()
                if short:
                    names.add(short.upper())
    except Exception:
        return set()
    return names


def region_has_route(agency_slug: str, query: str) -> bool:
    """
    True if *query* names a route published by *agency_slug*.

    Matching is case-insensitive against route_short_name, mirroring how the
    Live Map's search box is used ("t580" and "T580" are the same route).
    """
    q = (query or '').strip().upper()
    if not q:
        return False
    return q in _route_short_names(agency_slug)


def find_regions_for_route(query: str) -> list:
    """
    Region names that publish a route called *query*, in STATIC_API_SOURCES order.

    Used to answer "you searched the wrong region" with something actionable.
    An agency whose feed is unavailable is skipped rather than sinking the whole
    lookup — rapid-bus-kuantan currently 404s, and one dead feed must not stop
    the others from answering.
    """
    q = (query or '').strip().upper()
    if not q:
        return []

    found = []
    for region, slug in STATIC_API_SOURCES.items():
        if region not in _ROUTE_REGION_INDEX:
            _ROUTE_REGION_INDEX[region] = _route_short_names(slug)
        if q in _ROUTE_REGION_INDEX[region]:
            found.append(region)
    return found


# ---------------------------------------------------------------------------
# Timetable lookups
#
# stop_times.txt is ~88,000 rows for Rapid Bus KL, so it is parsed once per
# agency into a trip-keyed index and kept for the life of the process. The
# underlying ZIPs already carry a 24h cache.
# ---------------------------------------------------------------------------

# agency slug -> {trip_id: [stop dict, ...]} ordered by stop_sequence
_TRIP_STOPS_INDEX = {}
# agency slug -> {trip_id: headsign}
_TRIP_HEADSIGN_INDEX = {}


def parse_gtfs_time(value):
    """
    "HH:MM:SS" to seconds since service-day midnight. Returns -1 if unparseable.

    GTFS hours legitimately exceed 24 — "25:30:00" means 01:30 the following
    day on the same service day. Formatting these as clock times silently
    breaks late-night arrivals, so they stay integer seconds throughout.
    """
    try:
        h, m, s = (int(part) for part in str(value).strip().split(':'))
    except (ValueError, AttributeError):
        return -1
    return h * 3600 + m * 60 + s


def _build_trip_index(agency_slug):
    """Populate the trip-stops and headsign indexes for one agency."""
    stops_by_id = {}
    trip_stops = {}
    headsigns = {}
    try:
        with _load_zip(agency_slug) as zf:
            for row in _read_csv_from_zip(zf, 'stops.txt') or []:
                try:
                    stops_by_id[row['stop_id'].strip()] = {
                        'stop_id': row['stop_id'].strip(),
                        'stop_name': (row.get('stop_name') or '').strip(),
                        'stop_lat': float(row['stop_lat']),
                        'stop_lon': float(row['stop_lon']),
                    }
                except (KeyError, ValueError, TypeError):
                    continue

            rows = []
            for row in _read_csv_from_zip(zf, 'stop_times.txt') or []:
                stop = stops_by_id.get((row.get('stop_id') or '').strip())
                if stop is None:
                    continue
                try:
                    seq = int(row['stop_sequence'])
                except (KeyError, ValueError, TypeError):
                    continue
                rows.append((row.get('trip_id', '').strip(), seq,
                             parse_gtfs_time(row.get('arrival_time')), stop))

            for trip_id, seq, arrival_seconds, stop in rows:
                if not trip_id or arrival_seconds < 0:
                    continue
                entry = dict(stop)
                entry['arrival_seconds'] = arrival_seconds
                trip_stops.setdefault(trip_id, []).append((seq, entry))

            for trip_id in trip_stops:
                trip_stops[trip_id] = [e for _, e in sorted(trip_stops[trip_id],
                                                            key=lambda pair: pair[0])]

            for row in _read_csv_from_zip(zf, 'trips.txt') or []:
                tid = (row.get('trip_id') or '').strip()
                if tid:
                    headsigns[tid] = (row.get('trip_headsign') or '').strip()
    except Exception:
        trip_stops, headsigns = {}, {}

    _TRIP_STOPS_INDEX[agency_slug] = trip_stops
    _TRIP_HEADSIGN_INDEX[agency_slug] = headsigns


def get_trip_stops(agency_slug: str, trip_id: str) -> list:
    """Ordered stops for *trip_id*, each with coordinates and arrival_seconds."""
    if not trip_id:
        return []
    if agency_slug not in _TRIP_STOPS_INDEX:
        _build_trip_index(agency_slug)
    return _TRIP_STOPS_INDEX.get(agency_slug, {}).get(trip_id.strip(), [])


def get_trip_headsign(agency_slug: str, trip_id: str) -> str:
    """Destination text for *trip_id* — what a rider reads on the front of the bus."""
    if not trip_id:
        return ''
    # Both indexes are always populated together by _build_trip_index, so
    # _TRIP_STOPS_INDEX is the single source of truth for "already built" —
    # checking _TRIP_HEADSIGN_INDEX independently here would let a stale
    # (possibly empty, on a failed build) headsign entry survive a rebuild
    # of the stops index for the same agency.
    if agency_slug not in _TRIP_STOPS_INDEX:
        _build_trip_index(agency_slug)
    return _TRIP_HEADSIGN_INDEX.get(agency_slug, {}).get(trip_id.strip(), '')


def get_stops_near(agency_slug: str, lat: float, lon: float,
                   radius_m: float = 800, limit: int = 5) -> list:
    """Nearest stops to (lat, lon) within *radius_m*, closest first."""
    from utils.eta import haversine_m

    found = []
    try:
        with _load_zip(agency_slug) as zf:
            for row in _read_csv_from_zip(zf, 'stops.txt') or []:
                try:
                    slat, slon = float(row['stop_lat']), float(row['stop_lon'])
                except (KeyError, ValueError, TypeError):
                    continue
                d = haversine_m(lat, lon, slat, slon)
                if d <= radius_m:
                    found.append({
                        'stop_id': row['stop_id'].strip(),
                        'stop_name': (row.get('stop_name') or '').strip(),
                        'stop_lat': slat,
                        'stop_lon': slon,
                        'distance_m': d,
                    })
    except Exception:
        return []

    found.sort(key=lambda s: s['distance_m'])
    return found[:limit]
