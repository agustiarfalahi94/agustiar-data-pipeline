"""
Walking time from a location to a set of stops.

Straight-line distance is not a walk. Measured from KL1743 GREEN AVENUE
CONDOMINIUM across the 15 stops within 800 m, the ratio of routed to
straight-line distance ranges from 1.04 to 10.60 — KL1291 KM1 BUKIT JALIL
sits 60 m away and 634 m on foot, and the app used to call that a one-minute
walk. No single multiplier survives that spread, which is why real routing is
asked for and a corrected constant was rejected.

OpenRouteService supplies the distance; the pace stays ours. ORS returns its
own duration, but it walks 957 m in 11 minutes (5.2 km/h) where Google implies
16 (~3.6 km/h) — it models the path, not the crossings, the waiting, the
stairs, or a person who is not in a hurry.

This module knows nothing about Streamlit, GTFS, or arrivals, and never reads
configuration: the caller passes the key in. That keeps every test a plain
function call with no network.
"""
import math
import time

import requests

ORS_MATRIX_URL = 'https://api.openrouteservice.org/v2/matrix/foot-walking'

# The one walking pace in the codebase. 4.0 km/h, matching observed Google
# walking estimates rather than an unobstructed stride.
WALK_PACE_M_PER_MIN = 67

# Applied only when routing is unavailable. Measured median in Bukit Jalil is
# 1.62; a walkable grid runs ~1.2. This is a deliberate compromise between the
# two regimes, not a fit to either — and no multiplier applied to KL1291's 60 m
# will ever yield its true ten minutes, which is why the fallback is a degraded
# mode rather than the design.
DETOUR_FACTOR = 1.4

# ~55 m. GPS jitter while standing still resolves to the same cell, so
# auto-refresh costs nothing. A cell this size bounds the induced error below
# the one-minute resolution the UI displays.
GRID_DEGREES = 0.0005

CACHE_TTL_SECONDS = 86400       # footpaths do not move
REQUEST_TIMEOUT = 5             # this call sits inside a page render

# How long to stop asking after a failed lookup.
#
# This is not the negative caching that was deliberately rejected. Caching a
# *fallback distance* for CACHE_TTL_SECONDS would pin a degraded answer to a
# stop for a day after one transient blip; that remains forbidden, and no
# fallback is ever written to _WALK_CACHE. This only suppresses the *request*
# for a minute, and the fallback is recomputed fresh on every render meanwhile.
#
# The alternative to a long negative cache is a short one, not none. With
# auto-refresh on, a render happens every 20 seconds and issues up to two
# lookups at REQUEST_TIMEOUT each — so an ORS outage, an exhausted quota, or
# ordinary mobile flakiness meant up to 10 s of blocking I/O in the Streamlit
# script thread three times a minute, and a 429 became a tight retry loop
# against an endpoint that was rate-limiting us precisely to stop that. This
# app is phone-first on mobile data; that is the common degraded case, not an
# exotic one. Sixty seconds is short enough that recovery is still felt as
# immediate and long enough that three renders out of every four cost nothing.
FAIL_BACKOFF_SECONDS = 60

# Wall-clock time before which no request is attempted. Reset by _clear_cache().
_FAIL_UNTIL = 0.0

# (snapped_lat, snapped_lon, agency_slug, stop_id) -> (stored_at, routed_m)
# Module-level dict, following _TRIP_INDEX_MTIME in gtfs_static.py. The repo
# uses no Streamlit caching and this introduces none.
#
# Keyed per stop, not per stop set. Two different sets are looked up from the
# same location — the nearby stops, and the trip stops of a tapped bus — and a
# set-keyed cache would answer one from the other's entry, silently leaving the
# stops it had never seen on the straight-line fallback forever.
_WALK_CACHE = {}


def _clear_cache():
    """Drop every cached lookup and any failure backoff. For tests."""
    global _FAIL_UNTIL
    _WALK_CACHE.clear()
    _FAIL_UNTIL = 0.0


def _now():
    """Indirection so tests can age the cache without patching the stdlib clock."""
    return time.time()


def _snap(value):
    """Snap a coordinate to the cache grid."""
    return round(round(value / GRID_DEGREES) * GRID_DEGREES, 6)


def estimate_minutes(distance_m):
    """
    Walking minutes from a straight-line distance, when routing is unavailable.

    Whole minutes, never less than one.
    """
    return max(1, math.ceil(distance_m * DETOUR_FACTOR / WALK_PACE_M_PER_MIN))


def _routed_distances(user_lat, user_lon, stops, agency_slug, api_key):
    """
    {stop_id: routed_metres} from ORS, or {} when routing is unavailable.

    The agency slug namespaces the cache: stop ids are unique within a feed but
    may collide across the fourteen.

    Every failure returns {} and the caller estimates. The except is broad on
    purpose — a network error, a rejected key, an exhausted quota, a changed
    response shape and unparseable JSON are all the same event here, and none
    of them may raise into a Streamlit render.

    A failure also stops the next FAIL_BACKOFF_SECONDS of requests; see the
    constant for why a short suppression is not the negative caching that was
    rejected.
    """
    global _FAIL_UNTIL
    glat, glon = _snap(user_lat), _snap(user_lon)
    now = _now()

    found = {}
    missing = []
    for stop in stops:
        cached = _WALK_CACHE.get((glat, glon, agency_slug, stop['stop_id']))
        if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
            found[stop['stop_id']] = cached[1]
        else:
            missing.append(stop)

    if not missing or not api_key:
        return found

    # Still inside the backoff window from a recent failure. Cached stops were
    # already served above; the rest fall back without touching the network.
    if now < _FAIL_UNTIL:
        return found

    locations = [[user_lon, user_lat]]
    locations += [[s['stop_lon'], s['stop_lat']] for s in missing]
    body = {
        'locations': locations,
        'sources': [0],
        'destinations': list(range(1, len(locations))),
        'metrics': ['distance'],
    }
    try:
        response = requests.post(
            ORS_MATRIX_URL,
            json=body,
            headers={'Authorization': api_key,
                     'Content-Type': 'application/json'},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            _FAIL_UNTIL = _now() + FAIL_BACKOFF_SECONDS
            return found
        row = response.json()['distances'][0]
    except Exception:
        _FAIL_UNTIL = _now() + FAIL_BACKOFF_SECONDS
        return found

    # Results map back to stops positionally, so a row of the wrong length
    # cannot be trusted at all — discarding it beats guessing an alignment.
    if not isinstance(row, list) or len(row) != len(missing):
        _FAIL_UNTIL = _now() + FAIL_BACKOFF_SECONDS
        return found

    # Only a successful lookup is cached. Caching a fallback would pin a
    # degraded answer in place for a day after a transient blip.
    for stop, distance in zip(missing, row):
        if distance is None:
            continue
        _WALK_CACHE[(glat, glon, agency_slug, stop['stop_id'])] = (
            _now(), float(distance))
        found[stop['stop_id']] = float(distance)
    return found


def walk_times(user_lat, user_lon, stops, agency_slug, api_key=None):
    """
    {stop_id: {'minutes', 'distance_m', 'routed'}} for each stop.

    'routed' is False when the figure came from the straight-line fallback, so
    the UI can say so rather than claim more than the data supports.

    *stops* are the dicts get_stops_near returns, carrying straight-line
    distance_m. Costs one request for all of them, or none when cached.
    """
    if not stops:
        return {}

    routed = _routed_distances(user_lat, user_lon, stops, agency_slug, api_key)

    out = {}
    for stop in stops:
        stop_id = stop['stop_id']
        distance = routed.get(stop_id)
        if distance is None:
            out[stop_id] = {
                'minutes': estimate_minutes(stop['distance_m']),
                'distance_m': stop['distance_m'],
                'routed': False,
            }
        else:
            out[stop_id] = {
                'minutes': max(1, math.ceil(distance / WALK_PACE_M_PER_MIN)),
                'distance_m': distance,
                'routed': True,
            }
    return out
