import duckdb
import pandas as pd
from datetime import datetime, timedelta, timezone

try:
    from config import DATABASE_NAME, DATABASE_TABLE, TIMEZONE, UTC_OFFSET_HOURS
except ImportError:
    import streamlit as st
    DATABASE_NAME = st.secrets["database"]["name"]
    DATABASE_TABLE = st.secrets["database"]["table"]
    TIMEZONE = st.secrets["timezone"]["name"]
    UTC_OFFSET_HOURS = st.secrets["timezone"]["utc_offset_hours"]

def get_connection():
    """Get database connection with timezone set"""
    con = duckdb.connect(DATABASE_NAME)
    con.execute(f"SET TimeZone='{TIMEZONE}'")
    return con

def table_exists():
    """Check if table exists"""
    con = get_connection()
    result = con.execute(
        f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{DATABASE_TABLE}'"
    ).fetchone()[0]
    con.close()
    return result > 0

def get_live_data_optimized():
    """
    Get only the LATEST data (most recent insert_timestamp) for live map
    Returns: (dataframe, metrics_dict, sync_time_string)
    """
    if not table_exists():
        return None, {}, None
    
    con = get_connection()
    
    try:
        # Get the most recent insert_timestamp
        max_insert_ts = con.execute(f"SELECT MAX(insert_timestamp) FROM {DATABASE_TABLE}").fetchone()[0]
        
        if max_insert_ts is None:
            con.close()
            return pd.DataFrame(), {}, None
        
        # Get only data from the most recent insert
        df = con.execute(
            f"SELECT * FROM {DATABASE_TABLE} WHERE insert_timestamp = {max_insert_ts}"
        ).df()
        
        if df.empty:
            con.close()
            return df, {}, None
        
        # Get sync time (from the vehicle timestamp, not insert_timestamp)
        sync_time_str = con.execute(
            f"SELECT strftime(to_timestamp(MAX(timestamp)::BIGINT), '%-d %b %Y %H:%M:%S') FROM {DATABASE_TABLE} WHERE insert_timestamp = {max_insert_ts}"
        ).fetchone()[0]
        
        con.close()
        
        # Format timestamp column once - fix deprecation warning by converting to numeric first
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_numeric(df['timestamp'], errors='coerce')
            df['timestamp_formatted'] = pd.to_datetime(
                df['timestamp'], unit='s', utc=True
            ).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # Calculate metrics
        metrics = {
            'total': len(df),
            'regions': len(df['region'].unique()),
            'busiest': df['region'].value_counts().idxmax() if len(df) > 0 else 'N/A'
        }
        
        return df, metrics, sync_time_str
        
    except Exception as e:
        con.close()
        raise e

def get_historical_data():
    """
    Get ALL historical data (for analytics and data table pages)
    Returns: (dataframe, metrics_dict, sync_time_string)
    """
    if not table_exists():
        return None, {}, None
    
    con = get_connection()
    
    try:
        # Get all historical data
        df = con.execute(f"SELECT * FROM {DATABASE_TABLE}").df()
        
        if df.empty:
            con.close()
            return df, {}, None
        
        # Get sync time (most recent timestamp)
        sync_time_str = con.execute(
            f"SELECT strftime(to_timestamp(MAX(timestamp)::BIGINT), '%-d %b %Y %H:%M:%S') FROM {DATABASE_TABLE}"
        ).fetchone()[0]
        
        con.close()
        
        # Format timestamp column once - fix deprecation warning by converting to numeric first
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_numeric(df['timestamp'], errors='coerce')
            df['timestamp_formatted'] = pd.to_datetime(
                df['timestamp'], unit='s', utc=True
            ).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # Format insert_timestamp for display
        if 'insert_timestamp' in df.columns:
            df['insert_timestamp'] = pd.to_numeric(df['insert_timestamp'], errors='coerce')
            df['insert_timestamp_formatted'] = pd.to_datetime(
                df['insert_timestamp'], unit='s', utc=True
            ).dt.tz_convert(TIMEZONE).dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # Calculate metrics (only regions for historical data)
        metrics = {
            'regions': len(df['region'].unique())
        }
        
        return df, metrics, sync_time_str
        
    except Exception as e:
        con.close()
        raise e
