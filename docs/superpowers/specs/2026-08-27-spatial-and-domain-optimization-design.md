# Spatial & Domain Optimization Design Specification

## Overview & Goals
When locating the user or opening the nearby stops panel and route viewer, the application performs multiple spatial and timetable lookups:
1. `get_stops_near(agency_slug, lat, lon, radius_m)`: Unzips the GTFS package, parses `stops.txt` via CSV reader, and evaluates Haversine trigonometric equations across thousands of stops on every lookup.
2. `find_regions_with_stops_near(lat, lon, radius_m)`: Iterates across all 14 agency feeds and repeatedly calls `get_stops_near`.
3. `get_shapes_for_trip(agency_slug, trip_id)`: Unzips and scans `trips.txt` and `shapes.txt` on every route view selection.

This design introduces:
- In-memory spatial index and bounding box pre-filtering for stop searches.
- Agency stops cache with mtime-aware invalidation.
- Shapes cache with mtime-aware invalidation.

---

## 1. In-Memory Agency Stops Cache & Bounding Box Spatial Index

### Problem
GTFS `stops.txt` can contain between 500 and 5,000 stops per agency. Repeated full-table CSV parses and un-indexed Haversine scans add latency on user location changes and cross-region scans.

### Solution
1. **`_AGENCY_STOPS_INDEX`**: Dictionary mapping `agency_slug -> list[dict]`.
   Built once per ZIP file modification time (`_AGENCY_STOPS_MTIME`).
2. **Bounding Box Pre-Filtering**:
   Before running `haversine_m(lat, lon, stop_lat, stop_lon)`, filter using a planar bounding box:
   - $\Delta \text{lat} = \frac{\text{radius\_m}}{111,320.0}$
   - $\Delta \text{lon} = \frac{\text{radius\_m}}{111,320.0 \times \max(\cos(\text{radians}(\text{lat})), 0.01)}$
   - If $|stop\_lat - lat| > \Delta lat$ or $|stop\_lon - lon| > \Delta lon$, skip Haversine computation immediately.
3. This reduces trigonometric calculations by >98% and removes CSV parsing overhead on every query.

---

## 2. Route Shapes Cache

### Solution
1. **`_TRIP_SHAPES_INDEX`**: Mapping `(agency_slug, trip_id) -> list[[lon, lat]]`.
2. Cache is bounded and invalidated when the agency ZIP file's mtime changes.

---

## 3. Testing Strategy
- Unit tests verifying bounding box filtering produces identical results to brute-force haversine lookups.
- Cache invalidation tests on ZIP update.
- Performance / benchmark verification.
- Full test suite run (`pytest`).
