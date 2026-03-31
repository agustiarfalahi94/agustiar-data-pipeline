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


def get_stops(agency_slug: str) -> list:
    """
    Return a list of stops for *agency_slug* read from stops.txt in the cached ZIP.

    Each element is a dict with keys:
        stop_id   (str)
        stop_name (str)
        stop_lat  (float)
        stop_lon  (float)

    Rows with missing or non-numeric lat/lon are skipped.
    Returns an empty list on any error.
    """
    if not agency_slug:
        return []

    try:
        with _load_zip(agency_slug) as zf:
            rows = _read_csv_from_zip(zf, 'stops.txt')
            if not rows:
                return []

            stops = []
            for row in rows:
                try:
                    lat = float(row.get('stop_lat', '').strip())
                    lon = float(row.get('stop_lon', '').strip())
                except (ValueError, AttributeError):
                    continue

                stops.append({
                    'stop_id':   row.get('stop_id', '').strip(),
                    'stop_name': row.get('stop_name', '').strip(),
                    'stop_lat':  lat,
                    'stop_lon':  lon,
                })

            return stops

    except Exception:
        return []


def get_routes_for_stop(agency_slug: str, stop_id: str) -> list:
    """
    Return a sorted, deduplicated list of route name strings that serve *stop_id*
    within *agency_slug*.

    Steps:
      1. Scan stop_times.txt for rows matching stop_id — collect unique trip_ids.
      2. Look up each trip_id in trips.txt to get route_id(s).
      3. Look up each route_id in routes.txt to build a human-readable name
         (route_short_name + route_long_name, same pattern as get_route_name).

    stop_times.txt can be very large; a set is used to deduplicate trip_ids on
    the fly and the file is streamed row-by-row to avoid loading it all into RAM.

    Returns an empty list on any error.
    """
    if not agency_slug or not stop_id:
        return []

    try:
        with _load_zip(agency_slug) as zf:
            # ---- Step 1: collect trip_ids that visit this stop ----
            trip_ids = set()

            names = zf.namelist()
            stop_times_match = next((n for n in names if n.endswith('stop_times.txt')), None)
            if stop_times_match is None:
                return []

            with zf.open(stop_times_match) as raw:
                content = io.TextIOWrapper(raw, encoding='utf-8-sig')
                reader = csv.DictReader(content)
                for row in reader:
                    if row.get('stop_id', '').strip() == stop_id.strip():
                        tid = row.get('trip_id', '').strip()
                        if tid:
                            trip_ids.add(tid)

            if not trip_ids:
                return []

            # ---- Step 2: map trip_id → route_id ----
            trips = _read_csv_from_zip(zf, 'trips.txt')
            route_ids = set()
            for row in trips:
                if row.get('trip_id', '').strip() in trip_ids:
                    rid = row.get('route_id', '').strip()
                    if rid:
                        route_ids.add(rid)

            if not route_ids:
                return []

            # ---- Step 3: map route_id → human-readable name ----
            routes = _read_csv_from_zip(zf, 'routes.txt')
            route_names = set()
            for row in routes:
                if row.get('route_id', '').strip() in route_ids:
                    short = row.get('route_short_name', '').strip()
                    long_ = row.get('route_long_name', '').strip()
                    if short and long_:
                        name = f"{short} — {long_}"
                    else:
                        name = short or long_
                    if name:
                        route_names.add(name)

            return sorted(route_names)

    except Exception:
        return []
