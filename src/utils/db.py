import time
import duckdb
import pandas as pd
from datetime import datetime, timedelta, timezone

try:
    from config import DATABASE_NAME, DATABASE_TABLE, TIMEZONE, UTC_OFFSET_HOURS, DATA_RETENTION_DAYS
except ImportError:
    DATABASE_NAME = 'agustiar_analytics.duckdb'
    DATABASE_TABLE = 'live_buses'
    TIMEZONE = 'Asia/Kuala_Lumpur'
    UTC_OFFSET_HOURS = 8
    DATA_RETENTION_DAYS = 7


def get_connection():
    con = duckdb.connect(DATABASE_NAME)
    con.execute(f"SET TimeZone='{TIMEZONE}'")
    return con


def _format_sync_time(unix_ts):
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc) + timedelta(hours=UTC_OFFSET_HOURS)
    return f"{dt.day} {dt.strftime('%b %Y %H:%M:%S')}"


def table_exists():
    con = get_connection()
    try:
        result = con.execute(
            f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{DATABASE_TABLE}'"
        ).fetchone()[0]
        return result > 0
    finally:
        con.close()


def prune_old_data():
    """Delete rows older than DATA_RETENTION_DAYS to keep the database bounded."""
    if not table_exists():
        return
    cutoff = int(time.time()) - DATA_RETENTION_DAYS * 86400
    con = get_connection()
    try:
        con.execute(f"DELETE FROM {DATABASE_TABLE} WHERE insert_timestamp < {cutoff}")
    finally:
        con.close()


def _quality_log_exists():
    con = get_connection()
    try:
        result = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'fetch_quality_log'"
        ).fetchone()[0]
        return result > 0
    finally:
        con.close()


def get_network_health_summary():
    """
    Returns one row per region with reliability_score and component metrics,
    best-scoring region first.

    Reads the dbt mart, which bakes in a fixed 24h window (see the
    `health_window_hours` dbt var) - hence no window argument here.

    Columns: region, reliability_score, reporting_rate, availability,
             avg_data_lag_seconds, dropout_count, total_fetches, last_fetch_timestamp
    """
    if not _quality_log_exists():
        return pd.DataFrame()

    con = get_connection()
    try:
        if con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'mart_network_health'"
        ).fetchone()[0] == 0:
            return pd.DataFrame()
        # Order explicitly: the page renders scorecards in row order, and a
        # view's inner ORDER BY is not a guaranteed property of a SELECT.
        return con.execute(
            "SELECT * FROM main.mart_network_health "
            "ORDER BY reliability_score DESC NULLS LAST"
        ).df()
    finally:
        con.close()


def get_region_health_trend(region, window_hours=24):
    """
    Returns time-series rows from fetch_quality_log for one region,
    oldest first, within window_hours. Includes per-row reliability_score
    and a 'datetime' column converted to the configured timezone.
    """
    if not _quality_log_exists():
        return pd.DataFrame()

    cutoff = int(time.time()) - int(window_hours * 3600)
    con = get_connection()
    try:
        if con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'mart_region_health_trend'"
        ).fetchone()[0] == 0:
            return pd.DataFrame()
        query = """
        SELECT fetch_timestamp, vehicles_received, vehicles_rejected, vehicles_inserted,
               avg_data_lag_seconds, max_data_lag_seconds, total_dropout, reliability_score
        FROM main.mart_region_health_trend
        WHERE region = ? AND fetch_timestamp >= ?
        ORDER BY fetch_timestamp ASC
        """
        df = con.execute(query, [region, cutoff]).df()
    finally:
        con.close()

    if not df.empty:
        df['datetime'] = pd.to_datetime(
            df['fetch_timestamp'], unit='s', utc=True
        ).dt.tz_convert(TIMEZONE)
    return df


def get_region_fetch_log(region, limit=100):
    """
    Returns the most recent raw fetch_quality_log rows for one region,
    newest first. Adds a human-readable 'datetime' column.
    """
    if not _quality_log_exists():
        return pd.DataFrame()

    con = get_connection()
    try:
        query = f"""
        SELECT *
        FROM fetch_quality_log
        WHERE region = ?
        ORDER BY fetch_timestamp DESC
        LIMIT {int(limit)}
        """
        df = con.execute(query, [region]).df()
    finally:
        con.close()

    if not df.empty:
        df['datetime'] = pd.to_datetime(
            df['fetch_timestamp'], unit='s', utc=True
        ).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')
    return df


def get_live_data_optimized():
    """
    Get latest live data for display (last 60 seconds, deduplicated by vehicle)

    Returns:
        tuple: (dataframe, metrics_dict, sync_time_string)
            - dataframe: Latest position for each vehicle
            - metrics_dict: {'total': int, 'regions': int, 'busiest': str}
            - sync_time_string: Formatted timestamp of most recent data
    """
    if not table_exists():
        return None, {}, None

    con = get_connection()

    try:
        max_timestamp_raw = con.execute(f"SELECT MAX(timestamp) FROM {DATABASE_TABLE}").fetchone()[0]

        if max_timestamp_raw is None:
            return pd.DataFrame(), {}, None

        max_timestamp = int(max_timestamp_raw)
        sixty_seconds_ago = max_timestamp - 60

        query = f"""
        SELECT * FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY CAST(timestamp AS BIGINT) DESC) as rn
            FROM {DATABASE_TABLE}
            WHERE CAST(timestamp AS BIGINT) >= {sixty_seconds_ago}
        ) WHERE rn = 1
        """

        df = con.execute(query).df()

        if df.empty:
            return df, {}, None

        if 'rn' in df.columns:
            df = df.drop(columns=['rn'])

        sync_time_str = _format_sync_time(max_timestamp)

    finally:
        con.close()

    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_numeric(df['timestamp'], errors='coerce')
        df['timestamp_formatted'] = pd.to_datetime(
            df['timestamp'], unit='s', utc=True
        ).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')

    for col in ('trip_id', 'route_id'):
        if col not in df.columns:
            df[col] = ''
        else:
            df[col] = df[col].fillna('').astype(str)

    metrics = {
        'total': len(df),
        'regions': len(df['region'].unique()),
        'busiest': df['region'].value_counts().idxmax() if len(df) > 0 else 'N/A'
    }

    return df, metrics, sync_time_str


def get_vehicle_trail(vehicle_id, region, limit=50):
    """
    Get historical positions for a specific vehicle in a region, ordered by timestamp ASC.

    Args:
        vehicle_id: The vehicle ID to query
        region: The region to filter by
        limit: Maximum number of records to return (default 50)

    Returns:
        DataFrame with columns: vehicle_id, latitude, longitude, bearing, speed, timestamp
    """
    if not table_exists():
        return pd.DataFrame()

    con = get_connection()

    try:
        query = f"""
        SELECT vehicle_id, latitude, longitude, bearing, speed, timestamp
        FROM {DATABASE_TABLE}
        WHERE vehicle_id = ? AND region = ?
        ORDER BY CAST(timestamp AS BIGINT) ASC
        LIMIT {int(limit)}
        """
        df = con.execute(query, [vehicle_id, region]).df()
    finally:
        con.close()

    if df.empty:
        return df

    df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
    df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
    df['bearing'] = pd.to_numeric(df['bearing'], errors='coerce').fillna(0)
    df['speed'] = pd.to_numeric(df['speed'], errors='coerce').fillna(0)
    df['timestamp'] = pd.to_numeric(df['timestamp'], errors='coerce')

    df['timestamp'] = pd.to_datetime(
        df['timestamp'], unit='s', utc=True
    ).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')

    df = df[
        df['latitude'].notna() & df['longitude'].notna() &
        (df['latitude'] != 0) & (df['longitude'] != 0)
    ]

    return df


def get_historical_data():
    """
    Get historical data for analytics and data table (rolling DATA_RETENTION_DAYS window).

    Returns:
        tuple: (dataframe, metrics_dict, sync_time_string)
            - dataframe: Records within the retention window
            - metrics_dict: {'regions': int}
            - sync_time_string: Formatted timestamp of most recent data
    """
    if not table_exists():
        return None, {}, None

    cutoff = int(time.time()) - DATA_RETENTION_DAYS * 86400
    con = get_connection()

    try:
        df = con.execute(
            f"SELECT * FROM {DATABASE_TABLE} WHERE insert_timestamp >= {cutoff}"
        ).df()

        if df.empty:
            return df, {}, None

        max_ts_raw = con.execute(f"SELECT MAX(timestamp) FROM {DATABASE_TABLE}").fetchone()[0]
        sync_time_str = _format_sync_time(int(max_ts_raw)) if max_ts_raw else None

    finally:
        con.close()

    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_numeric(df['timestamp'], errors='coerce')
        df['timestamp_formatted'] = pd.to_datetime(
            df['timestamp'], unit='s', utc=True
        ).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')

    if 'insert_timestamp' in df.columns:
        df['insert_timestamp'] = pd.to_numeric(df['insert_timestamp'], errors='coerce')
        df['insert_timestamp_formatted'] = pd.to_datetime(
            df['insert_timestamp'], unit='s', utc=True
        ).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')

    if 'created_at' in df.columns:
        df['created_at_formatted'] = pd.to_datetime(
            df['created_at'], utc=True, errors='coerce'
        ).dt.tz_convert(TIMEZONE).dt.strftime('%-d %b %Y, %H:%M')

    metrics = {
        'regions': len(df['region'].unique())
    }

    return df, metrics, sync_time_str


def get_region_vehicle_counts():
    """Unique vehicles per region from the dbt mart. Columns: Region, Count."""
    if not table_exists():
        return pd.DataFrame(columns=['Region', 'Count'])
    con = get_connection()
    try:
        if con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'mart_region_vehicle_counts'"
        ).fetchone()[0] == 0:
            return pd.DataFrame(columns=['Region', 'Count'])
        df = con.execute(
            "SELECT region AS \"Region\", unique_vehicles AS \"Count\" "
            "FROM main.mart_region_vehicle_counts"
        ).df()
    finally:
        con.close()
    return df
