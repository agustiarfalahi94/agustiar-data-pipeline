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
import re
import tempfile
import time
import zipfile
import csv
import requests

from utils.eta import haversine_m
# The app's one coordinate-grid rule, shared rather than re-derived here — see
# _REGION_STOPS_INDEX. walking imports nothing from this module (it knows
# nothing about GTFS by design), so this direction cannot cycle.
from utils.walking import snap_to_grid

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

# Hand-maintained, and not from any feed. A rider reads "GOKL14" on the front
# of the bus; Prasarana publishes that route as PAVILION BUKIT JALIL (PAVBJ),
# route_id S6060. Searched across all 137 Rapid KL routes, no GOKL route
# exists anywhere in the data — the connection lives only on the vehicle's
# livery, so nothing in the feed can be derived from it.
#
# The standing risk: route branding changes and this table will not notice.
# Every match made through it is disclosed to the user for that reason.
ROUTE_ALIASES = {
    'GOKL14': 'PAVILION BUKIT JALIL (PAVBJ)',
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
    return os.path.join(tempfile.gettempdir(), f"gtfs_static_{_slug_safe(agency_slug)}.zip")


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

    A strict subset of `get_route_parts`, and expressed in terms of it so the
    two can never disagree about a route and so both share its cache — this is
    called once per unique route_id on every render of the map.
    """
    parts = get_route_parts(agency_slug, route_id)
    short, long_ = parts['short'], parts['long']
    if short and long_:
        return f"{short} — {long_}"
    return short or long_


# agency slug -> {route_id: {'short': ..., 'long': ...}}. Populated lazily and
# kept for the life of the process, exactly like _ROUTE_REGION_INDEX below and
# for the same reason: both read routes.txt, and re-reading it per lookup meant
# opening a 1.7 MB ZIP on every render — every 20 seconds with auto-refresh on.
# A failed read is deliberately not cached, so one outage cannot blank every
# route name until the process restarts.
_ROUTE_PARTS_INDEX = {}
# agency slug -> mtime of the ZIP the index above was built from. Keyed the
# same way as _TRIP_INDEX_MTIME and for the same reason: the ZIP cache rolls
# every 24 hours, and an index keyed on presence alone lets a long-running
# process serve a superseded routes.txt for its lifetime — a renamed route
# would keep showing its old name, and a route added in the new release would
# fall back to displaying its raw route_id.
_ROUTE_PARTS_MTIME = {}


def _route_parts_index(agency_slug: str) -> dict:
    """
    {route_id: {'short', 'long'}} for one agency. Empty dict on any error.

    First row wins for a duplicated route_id, matching the scan this replaced.
    routes.txt keys on route_id, so a duplicate is malformed data either way.
    """
    index = {}
    try:
        with _load_zip(agency_slug) as zf:
            for row in _read_csv_from_zip(zf, 'routes.txt') or []:
                rid = (row.get('route_id') or '').strip()
                if rid and rid not in index:
                    index[rid] = {
                        'short': (row.get('route_short_name') or '').strip(),
                        'long': (row.get('route_long_name') or '').strip(),
                    }
    except Exception:
        return {}
    return index


def get_route_parts(agency_slug: str, route_id: str) -> dict:
    """
    A route's short and long names, kept separate.

    `get_route_name` joins them for a tooltip, which reads badly in a panel:
    Rapid KL's short name is often a place ("PAVILION BUKIT JALIL (PAVBJ)")
    and the long name is the path between two places, one of which is that
    same place. Joined, it looks like the name was printed twice. Returned
    separately, the UI can label which is which.

    Never raises: an unknown route or an unavailable feed both come back as
    {'short': '', 'long': ''}. The returned dict is a copy, so a caller that
    mutates it cannot poison the cached index.
    """
    empty = {'short': '', 'long': ''}
    if not route_id:
        return empty

    mtime = _zip_mtime(agency_slug)
    index = _ROUTE_PARTS_INDEX.get(agency_slug)
    if not index or _ROUTE_PARTS_MTIME.get(agency_slug) != mtime:
        index = _route_parts_index(agency_slug)
        if index:
            _ROUTE_PARTS_INDEX[agency_slug] = index
            _ROUTE_PARTS_MTIME[agency_slug] = mtime

    parts = index.get(route_id.strip())
    return dict(parts) if parts else empty


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
# agency slug -> {trip_id} for trips published in frequencies.txt
_TRIP_FREQUENCY_INDEX = {}
# agency slug -> {stop_id: {route_id, ...}}
_STOP_ROUTES_INDEX = {}
# agency slug -> {route_id: [trip_id, ...]}
#
# Both are filled by _build_trip_index alongside the three above, never
# independently: they are derived from the same stop_times.txt pass, and an
# index that could be rebuilt on its own would let a stale entry survive a
# refresh of its siblings and serve a superseded timetable.
_ROUTE_TRIPS_INDEX = {}
# agency slug -> mtime of the ZIP the index above was built from
_TRIP_INDEX_MTIME = {}


def _zip_mtime(agency_slug):
    """Modification time of the cached ZIP, or None when there is no cache yet."""
    try:
        return os.path.getmtime(get_cached_path(agency_slug))
    except OSError:
        return None


def _trip_index_is_current(agency_slug):
    """
    True when an index exists for *agency_slug* and was built from the ZIP
    currently on disk.

    The ZIP cache refreshes every 24 hours; a new static release brings new
    trip_ids. Keying the index on presence alone let a process serve a
    superseded timetable indefinitely, which degrades silently — every vehicle
    on a new trip_id simply counts as "not in the schedule".
    """
    return (agency_slug in _TRIP_STOPS_INDEX
            and _TRIP_INDEX_MTIME.get(agency_slug) == _zip_mtime(agency_slug))


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
    """
    Populate the trip-stops, headsign and frequency indexes for one agency.

    On any failure nothing is stored, so the next call retries. Caching an
    empty result would turn one network blip into a permanent "this agency has
    no timetable" for the life of the process — every vehicle counted as not in
    the schedule, the arrivals panel empty, forever. get_stops_near already
    behaves this way; this now matches it.
    """
    stops_by_id = {}
    trip_stops = {}
    headsigns = {}
    frequency_trips = set()
    stop_routes = {}
    route_trips = {}
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
                    rid = (row.get('route_id') or '').strip()
                    if rid:
                        route_trips.setdefault(rid, []).append(tid)

            # Derived from trip_stops, which is already built: no second parse
            # of stop_times.txt, which is 87,935 rows for Rapid Bus KL alone.
            trip_route = {tid: rid for rid, tids in route_trips.items() for tid in tids}
            for trip_id, entries in trip_stops.items():
                rid = trip_route.get(trip_id)
                if not rid:
                    continue
                for entry in entries:
                    stop_routes.setdefault(entry['stop_id'], set()).add(rid)

            # frequencies.txt marks trips that run to a headway rather than to
            # the clock. For these the times in stop_times.txt are a
            # travel-time template, so no absolute start time is published and
            # no delay can be measured against one.
            for row in _read_csv_from_zip(zf, 'frequencies.txt') or []:
                tid = (row.get('trip_id') or '').strip()
                if tid:
                    frequency_trips.add(tid)
    except Exception:
        # Deliberately no write: leave the slug unindexed so the next call
        # retries rather than caching the failure.
        return

    _TRIP_STOPS_INDEX[agency_slug] = trip_stops
    _TRIP_HEADSIGN_INDEX[agency_slug] = headsigns
    _TRIP_FREQUENCY_INDEX[agency_slug] = frequency_trips
    _STOP_ROUTES_INDEX[agency_slug] = stop_routes
    _ROUTE_TRIPS_INDEX[agency_slug] = route_trips
    # Recorded after the load, which may itself have downloaded a fresh ZIP.
    _TRIP_INDEX_MTIME[agency_slug] = _zip_mtime(agency_slug)


def get_trip_stops(agency_slug: str, trip_id: str) -> list:
    """Ordered stops for *trip_id*, each with coordinates and arrival_seconds."""
    if not trip_id:
        return []
    if not _trip_index_is_current(agency_slug):
        _build_trip_index(agency_slug)
    return _TRIP_STOPS_INDEX.get(agency_slug, {}).get(trip_id.strip(), [])


def get_trip_headsign(agency_slug: str, trip_id: str) -> str:
    """Destination text for *trip_id* — what a rider reads on the front of the bus."""
    if not trip_id:
        return ''
    # All five indexes -- trip stops, headsigns, frequency trips, stop→routes
    # and route→trips -- are always populated together by _build_trip_index, so
    # _trip_index_is_current is the single source of truth for "already built
    # from the ZIP on disk" — checking _TRIP_HEADSIGN_INDEX independently here
    # would let a stale headsign entry survive a rebuild of the stops index for
    # the same agency.
    if not _trip_index_is_current(agency_slug):
        _build_trip_index(agency_slug)
    return _TRIP_HEADSIGN_INDEX.get(agency_slug, {}).get(trip_id.strip(), '')


def is_frequency_based(agency_slug: str, trip_id: str) -> bool:
    """
    True if *trip_id* is published in frequencies.txt — a headway service.

    Its stop_times.txt rows are then a travel-time template repeated across an
    operating window, not scheduled wall-clock times, so no start time exists
    to measure lateness against. Callers must report the delay as unknown; the
    arrival itself stays valid, since it uses only differences between stop
    times. 2,099 of the 2,102 Rapid Bus KL trips are published this way.
    """
    if not trip_id:
        return False
    if not _trip_index_is_current(agency_slug):
        _build_trip_index(agency_slug)
    return trip_id.strip() in _TRIP_FREQUENCY_INDEX.get(agency_slug, set())


_DIGIT_RUN = re.compile(r'(\d+)')


def _natural_key(text: str) -> list:
    """
    Sort key that orders embedded digit runs numerically.

    Riders read bus numbers as numbers. Sorted as text, '10' comes before '2'
    and a `Serves:` line reads as though it were shuffled. Splitting on digit
    runs and comparing those as integers puts '2' before '10' while the
    letters around them still order as text, so T580 stays among the T routes.

    Every part is the same 3-tuple shape, so an int is never compared against
    a str — that would raise on a name like '10' beside 'PAVBJ'.

    The digit branch is chosen by re-matching the split pattern, not by
    str.isdigit(). isdigit() is True for characters int() cannot parse —
    superscripts like '³', circled digits like '④' — and \\d does not match
    those, so they arrive here inside a *text* chunk that isdigit() would
    nonetheless claim. int() then raises, in a sort that runs inside the
    tapped-stop panel where nothing may raise into the render. Matching the
    pattern keeps Arabic-Indic digits numeric, since those do match \\d and
    int() does parse them.
    """
    parts = []
    for chunk in _DIGIT_RUN.split(text or ''):
        if _DIGIT_RUN.fullmatch(chunk):
            parts.append((0, int(chunk), ''))
        elif chunk:
            parts.append((1, 0, chunk.casefold()))
    return parts


def get_routes_at_stop(agency_slug: str, stop_id: str) -> list:
    """
    Every route whose timetable calls at *stop_id*, sorted by display name.

    The sort is natural, not lexicographic — '2' before '10', the way a rider
    reads bus numbers — and falls back to route_id so the order is the same on
    every process.

    This is the timetable's answer, not the live feed's. The arrivals panels
    show buses currently en route, which is a different and much smaller set —
    a rider at LRT Awan Besar saw three routes arriving and could not learn
    that a fourth, the only one reaching their destination, serves the stop at
    all. Empty list on any missing or malformed data; never raises.
    """
    if not stop_id:
        return []
    if not _trip_index_is_current(agency_slug):
        _build_trip_index(agency_slug)
    route_ids = _STOP_ROUTES_INDEX.get(agency_slug, {}).get(stop_id.strip(), set())

    routes = []
    for route_id in route_ids:
        parts = get_route_parts(agency_slug, route_id)
        routes.append({'route_id': route_id,
                       'short': parts['short'],
                       'long': parts['long']})
    # route_id breaks the tie: two route_ids can share a route_short_name, and
    # without it the order falls out of set iteration, which varies between
    # processes — the same stop would list its routes differently on a rerun.
    routes.sort(key=lambda r: (_natural_key(r['short'] or r['long'] or r['route_id']),
                               r['route_id']))
    return routes


def get_route_patterns(agency_slug: str, route_id: str, stop_id: str = None) -> list:
    """
    The distinct stop sequences *route_id* runs, optionally only those calling
    at *stop_id*.

    A route can run more than one pattern — 37 of Rapid KL's 136 routes run
    two and one runs three — so picking a single "the" sequence would be wrong
    for a quarter of the network, in a feature whose whole purpose is to stop
    the app misdirecting someone. Trips that visit the same stops in the same
    order are one pattern however many times a day they run.

    Each result carries a representative trip_id, because the headsign and
    whether the service runs to a headway are per-trip facts the caller needs
    and would otherwise have to re-derive.

    Caveat — the times are one arbitrary trip's. De-duplication keys on the
    stop-id tuple alone, so a pattern's stop times come from whichever of its
    trips appears first in trips.txt. A route with different peak and off-peak
    running times therefore shows that one representative trip's offsets, not
    the ones for the time of day the rider is standing there. The impact is
    small on this network — 2,099 of Rapid Bus KL's 2,102 trips are headway
    templates, where the stop times *are* a template repeated across the
    operating window rather than a time-of-day-specific schedule — but it is a
    real limit on a route that publishes genuinely distinct trip timings.

    Empty list on any missing or malformed data; never raises.
    """
    if not route_id:
        return []
    if not _trip_index_is_current(agency_slug):
        _build_trip_index(agency_slug)

    trip_ids = _ROUTE_TRIPS_INDEX.get(agency_slug, {}).get(route_id.strip(), [])
    all_stops = _TRIP_STOPS_INDEX.get(agency_slug, {})
    wanted = stop_id.strip() if stop_id else None

    seen = set()
    patterns = []
    for trip_id in trip_ids:
        stops = all_stops.get(trip_id) or []
        if not stops:
            continue
        key = tuple(s['stop_id'] for s in stops)
        if key in seen:
            continue
        # Recorded before the filter, so de-duplication does not depend on
        # which stop was asked for.
        seen.add(key)
        if wanted and wanted not in key:
            continue
        patterns.append({'trip_id': trip_id, 'stops': stops})
    return patterns


def get_stops_near(agency_slug: str, lat: float, lon: float,
                   radius_m: float = 800, limit: int = 5) -> list:
    """Nearest stops to (lat, lon) within *radius_m*, closest first."""
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

    # A feed is free to repeat a stop_id across stops.txt rows, and some do.
    # Callers treat the id as the identity of a stop: live_map keys one
    # st.button per stop on it, and Streamlit raises StreamlitDuplicateElementKey
    # on a repeated widget key -- straight into the render, taking the whole
    # Live Map down. The panel, the walk-time matrix and the map layer would
    # each show the same stop twice as well. De-duplicated here rather than at
    # any one caller so all four are fixed once.
    #
    # Before the limit slice, not after, or a duplicated row would spend one of
    # the `limit` places and silently cost the rider a real stop. The list is
    # already sorted by distance, so the occurrence kept is the nearest one.
    seen = set()
    unique = []
    for s in found:
        if s['stop_id'] in seen:
            continue
        seen.add(s['stop_id'])
        unique.append(s)
    return unique[:limit]


def resolve_route_alias(query):
    """
    Map a rider's wording onto the name the feed publishes.

    Returns (resolved_query, alias_source). alias_source is the matched alias
    when the table did the work and None otherwise, so the caller can disclose
    the substitution — a hand-written guess must never be presented as feed
    data.

    A query that is not a key passes through untouched: the table is consulted,
    never imposed, so a real route name can never be rewritten by it.
    """
    text = (query or '').strip()
    if not text:
        return '', None
    canonical = ROUTE_ALIASES.get(text.upper())
    if canonical is None:
        return text, None
    return canonical, text.upper()


# (snapped_lat, snapped_lon, radius_m, exclude_slug) -> (stored_at, [row, ...])
#
# This is the only all-agency walk in the module that was not memoised, and the
# dead end it runs at is sticky: the user has not moved, so every 20-second
# auto-refresh re-entered it. Warm that costs a re-read of fourteen cached
# ZIPs; cold it goes _load_zip -> is_cache_fresh -> download_static_gtfs, up to
# thirteen synchronous HTTP fetches inside a page render, and the 24-hour TTL
# re-arms that daily. An agency endpoint that hangs costs REQUEST_TIMEOUT (30s)
# and was retried on every refresh, indefinitely, because the exception is
# swallowed per-agency and nothing remembered it.
#
# Keyed on the location grid rather than raw coordinates so GPS jitter while
# standing still resolves to the same entry — the same reasoning, and the same
# rule, as walking's cache. The scan itself still runs on the true coordinates;
# only the key is snapped. `limit` is deliberately not part of the key: it only
# slices an already-sorted result, so two callers asking for different numbers
# of suggestions share one scan.
_REGION_STOPS_INDEX = {}

# Unlike _ROUTE_REGION_INDEX, this expires. That index answers "does this
# agency publish route X", which changes only when a feed is republished; this
# one bakes in *which agencies answered at all*, and an agency skipped because
# its feed was briefly unreadable would otherwise stay missing from the hint
# for the life of the process — the same failure _ROUTE_PARTS_INDEX avoids by
# refusing to cache a failed read. Five minutes serves fourteen of every
# fifteen auto-refreshes from memory while keeping recovery inside the span of
# a wait at a bus stop.
#
# No separate negative-cache window (walking._FAIL_UNTIL) is needed here, and
# adding one would be redundant machinery: walking suppresses *requests* after
# a failure because its failure path produces no cacheable value — a fallback
# distance must never be stored. This function's failure path produces a
# perfectly good value, `[]` or a shorter list, so memoising the result already
# stops the retry storm at the dead end, which is exactly what a negative cache
# would have been for.
REGION_SCAN_TTL_SECONDS = 300

# Checked only past this many keys, so an ordinary lookup never walks the dict.
# Entries are tiny and only created at a dead end, but this process never
# restarts between deploys and serves every visitor, so without eviction every
# grid cell anyone ever hit a dead end in would stay resident forever — the
# same defect _WALK_CACHE was given eviction for. Generous headroom, not a
# tuned figure.
_REGION_STOPS_EVICT_THRESHOLD = 500


def _evict_expired_region_scans(now):
    """Drop region-scan entries past REGION_SCAN_TTL_SECONDS."""
    expired = [key for key, (stored_at, _rows) in _REGION_STOPS_INDEX.items()
               if (now - stored_at) >= REGION_SCAN_TTL_SECONDS]
    for key in expired:
        del _REGION_STOPS_INDEX[key]


def find_regions_with_stops_near(lat, lon, radius_m=1500, exclude_slug=None,
                                 limit=3):
    """
    Regions with stops near (lat, lon), closest stop first.

    Called only when the selected region has no stops near the user — a dead
    end where the app has already failed to help. Standing in Bukit Jalil with
    KTM Berhad selected, the panel said only "no stops found", while Rapid Bus
    KL had 15 within 800 m and the app knew it.

    An agency whose timetable is missing or unreadable is skipped rather than
    raising: one dead feed must not cost the user the other twelve answers.

    Memoised per location grid cell for REGION_SCAN_TTL_SECONDS — see
    _REGION_STOPS_INDEX for why the dead end made that necessary. Rows are
    copied out, so a caller that mutates the result cannot poison the cache.
    """
    now = time.time()
    try:
        key = (snap_to_grid(lat), snap_to_grid(lon), radius_m, exclude_slug)
    except (TypeError, ValueError, OverflowError):
        # An unusable coordinate must not raise into a render. Uncached, this
        # falls through to the scan below, where get_stops_near rejects it
        # per-agency exactly as it did before this cache existed.
        key = None

    if key is not None:
        cached = _REGION_STOPS_INDEX.get(key)
        if cached and (now - cached[0]) < REGION_SCAN_TTL_SECONDS:
            return [dict(row) for row in cached[1][:limit]]

    found = []
    for region, slug in STATIC_API_SOURCES.items():
        if exclude_slug and slug == exclude_slug:
            continue
        try:
            # get_stops_near already filters to radius_m and sorts before it
            # truncates to `limit` — a small limit here would silently cap
            # `count` below, understating exactly the dense-agency case this
            # function exists for. Ask for effectively "all of them"; the
            # radius filter already did the real work, so this costs nothing.
            stops = get_stops_near(slug, lat, lon, radius_m=radius_m, limit=100_000)
        except Exception:
            continue
        if not stops:
            continue
        found.append({
            'region': region,
            'slug': slug,
            'count': len(stops),
            'nearest_m': min(s['distance_m'] for s in stops),
        })

    found.sort(key=lambda r: r['nearest_m'])

    if key is not None:
        # The whole sorted list is stored, not the slice: `limit` shapes the
        # answer, not the scan.
        #
        # Stamped when the scan FINISHES, not when it started. Using the entry
        # time meant a scan slower than the TTL was stored already expired —
        # with two agency endpoints hanging, the walk outlasts
        # REGION_SCAN_TTL_SECONDS, the cache can never serve the entry, and
        # every render re-scans. That is the refresh storm this cache exists
        # to prevent, defeated in precisely the case where it matters most.
        _REGION_STOPS_INDEX[key] = (time.time(), found)
        if len(_REGION_STOPS_INDEX) > _REGION_STOPS_EVICT_THRESHOLD:
            _evict_expired_region_scans(time.time())

    return [dict(row) for row in found[:limit]]
