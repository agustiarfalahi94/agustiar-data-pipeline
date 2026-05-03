import requests
import pandas as pd
from google.transit import gtfs_realtime_pb2
from google.protobuf.json_format import MessageToDict
import duckdb
import time
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# Module-level diagnostics — survive across Streamlit reruns within a session.
# Read by the Network Health debug panel.
DIAGNOSTICS = {
    'fetch_calls': 0,
    'guard_blocks': 0,
    'fetches_with_data': 0,
    'df_empty_returns': 0,
    'main_db_exceptions': 0,
    'quality_stats_built_count': 0,
    'quality_writes_attempted': 0,
    'quality_writes_succeeded': 0,
    'quality_writes_failed': 0,
    'last_main_db_error': None,
    'last_main_db_traceback': None,
    'last_quality_error': None,
    'last_quality_traceback': None,
    'last_rows_count_before_write': None,
    'last_rows_count_after_write': None,
    'last_fetch_timestamp_used': None,
}

try:
    from config import (
        DATABASE_NAME, DATABASE_TABLE, DATA_MAX_AGE, DATA_FUTURE_TOLERANCE,
        API_SOURCES, API_BASE_URL, REQUEST_TIMEOUT,
    )
except ImportError:
    DATABASE_NAME = 'agustiar_analytics.duckdb'
    DATABASE_TABLE = 'live_buses'
    DATA_MAX_AGE = 3600
    DATA_FUTURE_TOLERANCE = 300
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
        'myBAS Kuching': ['mybas-kuching'],
    }
    API_BASE_URL = 'https://api.data.gov.my/gtfs-realtime/vehicle-position/'
    REQUEST_TIMEOUT = 10


def _fetch_endpoint(name, endpoint):
    """
    Fetch vehicle data from a single API endpoint.
    Returns (vehicles, duration_ms) — vehicles is [] on any error.
    """
    url = f'{API_BASE_URL}{endpoint}'
    t0 = time.time()
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        duration_ms = int((time.time() - t0) * 1000)
        if response.status_code == 200:
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
            vehicles = []
            for entity in feed.entity:
                if entity.HasField('vehicle'):
                    v = MessageToDict(entity.vehicle)
                    pos = v.get('position', {})
                    vehicle_info = v.get('vehicle', {})
                    trip_info = v.get('trip', {})
                    vehicles.append({
                        'region': name,
                        'latitude': pos.get('latitude'),
                        'longitude': pos.get('longitude'),
                        'bearing': pos.get('bearing', 0),
                        'speed': pos.get('speed', 0),
                        'vehicle_id': vehicle_info.get('id', 'Unknown'),
                        'timestamp': v.get('timestamp'),
                        'trip_id': trip_info.get('tripId', ''),
                        'route_id': trip_info.get('routeId', ''),
                    })
            return vehicles, duration_ms
    except Exception as e:
        print(f"Error fetching {name} ({endpoint}): {e}")
    return [], int((time.time() - t0) * 1000)


def _build_quality_stats(received_by_region, valid_by_region, inserted_by_region,
                          lag_by_region, duration_by_region, fetch_timestamp):
    """
    Build one quality-log row per region from per-stage pipeline counts.

    Args:
        received_by_region:  {region: int}  raw count before filtering
        valid_by_region:     {region: int}  count after coord/timestamp filter
        inserted_by_region:  {region: int}  count actually written to live_buses
        lag_by_region:       {region: {'avg': float, 'max': float}}
        duration_by_region:  {region: int}  fetch wall-clock ms
        fetch_timestamp:     int  unix time of this fetch cycle

    Returns:
        list of dicts, one per region
    """
    all_regions = set(received_by_region) | set(duration_by_region)
    stats = []
    for region in sorted(all_regions):
        received = received_by_region.get(region, 0)
        valid = valid_by_region.get(region, 0)
        inserted = inserted_by_region.get(region, 0)
        rejected = max(0, received - valid)
        lag = lag_by_region.get(region, {'avg': 0.0, 'max': 0.0})
        stats.append({
            'fetch_timestamp': fetch_timestamp,
            'region': region,
            'vehicles_received': received,
            'vehicles_rejected': rejected,
            'vehicles_inserted': inserted,
            'avg_data_lag_seconds': float(lag['avg']),
            'max_data_lag_seconds': float(lag['max']),
            'total_dropout': received == 0,
            'fetch_duration_ms': duration_by_region.get(region, 0),
        })
    return stats


def _write_quality_log(stats_list):
    """Write quality stats to fetch_quality_log using its own connection."""
    if not stats_list:
        return
    DIAGNOSTICS['quality_writes_attempted'] += 1
    DIAGNOSTICS['last_fetch_timestamp_used'] = stats_list[0]['fetch_timestamp']
    try:
        con = duckdb.connect(DATABASE_NAME)
    except Exception as e:
        DIAGNOSTICS['quality_writes_failed'] += 1
        DIAGNOSTICS['last_quality_error'] = f"connect: {e}"
        DIAGNOSTICS['last_quality_traceback'] = traceback.format_exc()
        return
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS fetch_quality_log (
                fetch_timestamp BIGINT,
                region VARCHAR,
                vehicles_received INTEGER,
                vehicles_rejected INTEGER,
                vehicles_inserted INTEGER,
                avg_data_lag_seconds DOUBLE,
                max_data_lag_seconds DOUBLE,
                total_dropout BOOLEAN,
                fetch_duration_ms INTEGER
            )
        """)
        before = con.execute("SELECT COUNT(*) FROM fetch_quality_log").fetchone()[0]
        DIAGNOSTICS['last_rows_count_before_write'] = before

        for s in stats_list:
            con.execute(
                "INSERT INTO fetch_quality_log VALUES (?,?,?,?,?,?,?,?,?)",
                [s['fetch_timestamp'], s['region'], s['vehicles_received'],
                 s['vehicles_rejected'], s['vehicles_inserted'],
                 s['avg_data_lag_seconds'], s['max_data_lag_seconds'],
                 bool(s['total_dropout']), s['fetch_duration_ms']]
            )

        after = con.execute("SELECT COUNT(*) FROM fetch_quality_log").fetchone()[0]
        DIAGNOSTICS['last_rows_count_after_write'] = after

        try:
            from config import DATA_RETENTION_DAYS as _DRD
        except ImportError:
            _DRD = 7
        cutoff = stats_list[0]['fetch_timestamp'] - _DRD * 86400
        con.execute(f"DELETE FROM fetch_quality_log WHERE fetch_timestamp < {cutoff}")
        DIAGNOSTICS['quality_writes_succeeded'] += 1
        DIAGNOSTICS['last_quality_error'] = None
        DIAGNOSTICS['last_quality_traceback'] = None
    except Exception as e:
        DIAGNOSTICS['quality_writes_failed'] += 1
        DIAGNOSTICS['last_quality_error'] = str(e)
        DIAGNOSTICS['last_quality_traceback'] = traceback.format_exc()
    finally:
        con.close()


def fetch_and_store_transit_data():
    """
    Fetch live transit data from Malaysia GTFS API and store in DuckDB.
    Prunes rows older than DATA_RETENTION_DAYS after each successful insert.
    """
    DIAGNOSTICS['fetch_calls'] += 1
    all_vehicle_data = []
    current_unix = int(time.time())

    # Fetch guard: skip if a fetch already ran within the last 15 seconds.
    # Prevents duplicate quality log entries and DuckDB write collisions when
    # multiple Streamlit sessions trigger refresh simultaneously.
    try:
        _guard_con = duckdb.connect(DATABASE_NAME)
        try:
            recent = _guard_con.execute(
                f"SELECT COUNT(*) FROM fetch_quality_log WHERE fetch_timestamp >= {current_unix - 3}"
            ).fetchone()[0]
            if recent > 0:
                DIAGNOSTICS['guard_blocks'] += 1
                print("⚡ Skipping fetch — already ran within last 3 seconds")
                return
        finally:
            _guard_con.close()
    except Exception:
        pass  # Table doesn't exist on first run — proceed normally

    # ===== Step 1: Fetch data from all API endpoints =====
    tasks = [
        (name, endpoint)
        for name, endpoints in API_SOURCES.items()
        for endpoint in endpoints
    ]

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_task = {
            executor.submit(_fetch_endpoint, name, endpoint): (name, endpoint)
            for name, endpoint in tasks
        }
        duration_by_region = {}
        for future in as_completed(future_to_task):
            name, endpoint = future_to_task[future]
            vehicles, duration_ms = future.result()
            all_vehicle_data.extend(vehicles)
            duration_by_region[name] = duration_by_region.get(name, 0) + duration_ms

    if not all_vehicle_data:
        print("No vehicle data fetched")
        return

    DIAGNOSTICS['fetches_with_data'] += 1

    # Count vehicles per region BEFORE filtering (ground truth for quality log)
    received_by_region = {}
    for item in all_vehicle_data:
        r = item.get('region', 'Unknown')
        received_by_region[r] = received_by_region.get(r, 0) + 1

    # ===== Step 2: Clean and filter data =====
    df = pd.DataFrame(all_vehicle_data)

    df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
    df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
    df['timestamp_num'] = pd.to_numeric(df['timestamp'], errors='coerce')

    df = df[
        (df['latitude'] != 0) &
        (df['longitude'] != 0) &
        (df['timestamp_num'].notna()) &
        (df['timestamp_num'] <= current_unix + DATA_FUTURE_TOLERANCE) &
        (df['timestamp_num'] >= current_unix - DATA_MAX_AGE)
    ].drop(columns=['timestamp_num']).copy()

    if df.empty:
        DIAGNOSTICS['df_empty_returns'] += 1
        print("No valid vehicle data after filtering")
        return

    # Count valid vehicles per region AFTER filtering
    valid_by_region = df.groupby('region').size().to_dict()

    # Compute per-region data lag before inserting
    df['_lag'] = current_unix - pd.to_numeric(df['timestamp'], errors='coerce').fillna(current_unix)
    _lag_stats = df.groupby('region')['_lag'].agg(['mean', 'max'])
    lag_by_region = {
        region: {'avg': float(row['mean']), 'max': float(row['max'])}
        for region, row in _lag_stats.iterrows()
    }
    df = df.drop(columns=['_lag'])

    df['insert_timestamp'] = current_unix
    df['created_at'] = datetime.now(timezone.utc)

    # ===== Step 3: Store in database with deduplication =====
    quality_stats = []  # collected here, written after con is closed

    try:
        con = duckdb.connect(DATABASE_NAME)
    except Exception as e:
        print(f"Database connection error: {e}")
        return

    try:
        table_exists = con.execute(
            f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{DATABASE_TABLE}'"
        ).fetchone()[0] > 0

        if not table_exists:
            con.execute(f"CREATE TABLE {DATABASE_TABLE} AS SELECT * FROM df")
            print(f"✓ Created table and synced {len(df)} vehicles")
            inserted_by_region = df.groupby('region').size().to_dict()
        else:
            columns = con.execute(
                f"SELECT column_name FROM information_schema.columns WHERE table_name = '{DATABASE_TABLE}'"
            ).df()['column_name'].tolist()

            if 'insert_timestamp' not in columns:
                con.execute(f"ALTER TABLE {DATABASE_TABLE} ADD COLUMN insert_timestamp BIGINT")
                con.execute(f"UPDATE {DATABASE_TABLE} SET insert_timestamp = {current_unix} WHERE insert_timestamp IS NULL")

            if 'created_at' not in columns:
                con.execute(f"ALTER TABLE {DATABASE_TABLE} ADD COLUMN created_at TIMESTAMP")
                con.execute(f"UPDATE {DATABASE_TABLE} SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")

            if 'trip_id' not in columns:
                con.execute(f"ALTER TABLE {DATABASE_TABLE} ADD COLUMN trip_id VARCHAR")
                con.execute(f"UPDATE {DATABASE_TABLE} SET trip_id = '' WHERE trip_id IS NULL")

            if 'route_id' not in columns:
                con.execute(f"ALTER TABLE {DATABASE_TABLE} ADD COLUMN route_id VARCHAR")
                con.execute(f"UPDATE {DATABASE_TABLE} SET route_id = '' WHERE route_id IS NULL")

            con.execute(f"""
                INSERT INTO {DATABASE_TABLE}
                SELECT df.* FROM df
                WHERE NOT EXISTS (
                    SELECT 1 FROM {DATABASE_TABLE} existing
                    WHERE existing.region = df.region
                    AND existing.vehicle_id = df.vehicle_id
                    AND existing.timestamp = df.timestamp
                    AND existing.latitude = df.latitude
                    AND existing.longitude = df.longitude
                    AND existing.bearing = df.bearing
                    AND existing.speed = df.speed
                )
            """)

            inserted_count = con.execute("SELECT changes()").fetchone()[0]
            if inserted_count > 0:
                print(f"✓ Inserted {inserted_count} new vehicles (skipped duplicates)")
            else:
                print("⚠ No new data inserted (all records were duplicates)")

            inserted_by_region = {}
            try:
                ins_df = con.execute(
                    f"SELECT region, COUNT(*) as cnt FROM {DATABASE_TABLE} "
                    f"WHERE insert_timestamp = {current_unix} GROUP BY region"
                ).df()
                inserted_by_region = ins_df.set_index('region')['cnt'].to_dict()
            except Exception:
                pass

            # Prune live_buses while connection is still open
            try:
                from config import DATA_RETENTION_DAYS
            except ImportError:
                DATA_RETENTION_DAYS = 7
            cutoff = current_unix - DATA_RETENTION_DAYS * 86400
            con.execute(f"DELETE FROM {DATABASE_TABLE} WHERE insert_timestamp < {cutoff}")

        quality_stats = _build_quality_stats(
            received_by_region, valid_by_region, inserted_by_region,
            lag_by_region, duration_by_region, current_unix
        )
        DIAGNOSTICS['quality_stats_built_count'] += 1

    except Exception as e:
        DIAGNOSTICS['main_db_exceptions'] += 1
        DIAGNOSTICS['last_main_db_error'] = str(e)
        DIAGNOSTICS['last_main_db_traceback'] = traceback.format_exc()
        print(f"Database error: {e}")
    finally:
        con.close()  # always close before touching fetch_quality_log

    # Write quality log with a fresh connection — main con is fully closed above
    _write_quality_log(quality_stats)


if __name__ == "__main__":
    fetch_and_store_transit_data()
