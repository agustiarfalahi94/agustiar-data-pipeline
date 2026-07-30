import pandas as pd

try:
    from config import REGIONS, PRIMARY_REGION
except ImportError:
    REGIONS = [
        'Rapid Bus KL', 'Rapid Bus MRT Feeder', 'Rapid Bus Kuantan', 'Rapid Bus Penang',
        'KTM Berhad', 'myBAS Kangar', 'myBAS Alor Setar', 'myBAS Kota Bharu',
        'myBAS Kuala Terengganu', 'myBAS Ipoh', 'myBAS Seremban',
        'myBAS Melaka', 'myBAS Johor', 'myBAS Kuching',
    ]
    PRIMARY_REGION = 'Rapid Bus KL'

def convert_speed_to_kmh(df, speed_column='speed'):
    """
    Convert speed from m/s to km/h and cap at reasonable maximum
    
    Args:
        df: DataFrame with speed column
        speed_column: Name of the speed column (default: 'speed')
    
    Returns:
        DataFrame with converted speed
    """
    df[speed_column] = pd.to_numeric(df[speed_column], errors='coerce').fillna(0)
    df[speed_column] = df[speed_column] * 3.6  # m/s to km/h
    df[speed_column] = df[speed_column].round(0).clip(upper=120)  # Cap at 120 km/h
    return df

def get_sorted_regions(df):
    """Get available regions sorted with PRIMARY_REGION first"""
    available = df['region'].unique().tolist()
    others = sorted([r for r in available if r != PRIMARY_REGION])
    return ([PRIMARY_REGION] if PRIMARY_REGION in available else []) + others

def prepare_map_data(df, region):
    """
    Optimized data preparation for map display
    Filters, cleans, and validates in one pass
    """
    # Filter by region
    df_filtered = df[df['region'] == region].copy()
    
    if df_filtered.empty:
        return pd.DataFrame()
    
    # Convert to numeric and fill defaults in one step
    df_filtered['latitude'] = pd.to_numeric(df_filtered['latitude'], errors='coerce')
    df_filtered['longitude'] = pd.to_numeric(df_filtered['longitude'], errors='coerce')
    df_filtered['bearing'] = pd.to_numeric(df_filtered['bearing'], errors='coerce').fillna(0)
    df_filtered['speed'] = pd.to_numeric(df_filtered['speed'], errors='coerce').fillna(0)
    
    # Convert speed using helper function
    df_filtered = convert_speed_to_kmh(df_filtered)
    
    # Filter invalid coordinates in one operation
    df_filtered = df_filtered[
        (df_filtered['latitude'].notna()) &
        (df_filtered['longitude'].notna()) &
        (df_filtered['latitude'] != 0) &
        (df_filtered['longitude'] != 0)
    ]
    
    return df_filtered

def format_display_dataframe(df):
    """Format dataframe for display in data table"""
    display_df = df.sort_values('timestamp', ascending=False).copy()
    
    # Convert speed using helper function
    display_df = convert_speed_to_kmh(display_df)
    
    # Calculate average speed per vehicle
    avg_speed = display_df.groupby('vehicle_id')['speed'].mean().round(0)
    display_df['avg_speed'] = display_df['vehicle_id'].map(avg_speed)
    
    # Select and rename columns (include created_at_formatted if present)
    base_cols = [
        'region', 'vehicle_id', 'latitude', 'longitude',
        'bearing', 'speed', 'avg_speed', 'timestamp_formatted'
    ]
    if 'created_at_formatted' in display_df.columns:
        base_cols.append('created_at_formatted')
    display_df = display_df[base_cols]

    rename_map = {
        'region': 'Region',
        'vehicle_id': 'Vehicle ID',
        'latitude': 'Latitude',
        'longitude': 'Longitude',
        'bearing': 'Heading (°)',
        'speed': 'Speed (km/h)',
        'avg_speed': 'Avg Speed (km/h)',
        'timestamp_formatted': 'Timestamp',
        'created_at_formatted': 'Created At',
    }
    display_df = display_df.rename(columns=rename_map)
    
    # Round numeric columns
    display_df['Latitude'] = display_df['Latitude'].round(6)
    display_df['Longitude'] = display_df['Longitude'].round(6)
    display_df['Heading (°)'] = display_df['Heading (°)'].round(1)
    display_df['Speed (km/h)'] = display_df['Speed (km/h)'].astype(int)
    display_df['Avg Speed (km/h)'] = display_df['Avg Speed (km/h)'].astype(int)

    return display_df.reset_index(drop=True)


def filter_by_route(df, query):
    """
    Filter vehicles to those whose route matches *query* (case-insensitive substring).

    Matches against the 'route_display' column, which holds the resolved
    "SHORT — Long Name" string, so both "T580" and "awan besar" match.

    Returns df unchanged for an empty/whitespace query or when route_display
    is absent, so callers can pass user input straight through.

    `regex=False` is load-bearing, not defensive: a query of "." would otherwise
    match every vehicle. The returned frame is a copy because callers assign
    derived columns onto it (e.g. live_map's arrow_path), which on a slice
    raises SettingWithCopyWarning on pandas 2.x.
    """
    if not query or not query.strip():
        return df
    if 'route_display' not in df.columns:
        return df
    needle = query.strip().lower()
    mask = df['route_display'].fillna('').astype(str).str.lower().str.contains(needle, regex=False)
    return df[mask].copy()

def format_duration(seconds):
    """
    Human wording for a window length, so UI copy can be derived from the
    configured knobs instead of hardcoding "5 minutes" next to a 300 that the
    user is invited to change.

    45 -> '45 seconds', 60 -> '1 minute', 900 -> '15 minutes', 90 -> '1 minute 30 seconds'
    """
    total = int(seconds)
    if total < 0:
        total = 0
    minutes, remainder = divmod(total, 60)
    if minutes == 0:
        return f"{remainder} second{'' if remainder == 1 else 's'}"
    label = f"{minutes} minute{'' if minutes == 1 else 's'}"
    if remainder:
        label += f" {remainder} second{'' if remainder == 1 else 's'}"
    return label


def classify_freshness(df, now, fresh_seconds=60, stale_seconds=300):
    """
    Tag each vehicle with how stale its position is.

    Adds two columns:
      age_seconds  int, clamped at 0 — a future-dated timestamp reads as age 0
                   rather than a negative age, so a feed whose clock runs fast
                   is treated as current instead of being discarded.
      freshness    'fresh'  (age <= fresh_seconds)
                   'stale'  (fresh_seconds < age <= stale_seconds)
                   'hidden' (age > stale_seconds)

    Both bounds are user-tunable config knobs, so they can arrive equal (or
    inverted). That leaves no room for a 'stale' band: rather than raising
    "Bin edges must be unique" out of pd.cut, the middle tier is dropped and
    rows are split into 'fresh' and 'hidden' at the wider of the two bounds.

    A row whose timestamp is missing or non-numeric is aged past every bound,
    so it lands in 'hidden' rather than being presented as current.

    Rows are never dropped — the caller counts the hidden ones for its caption.
    A frame that is empty, or has no 'timestamp' column, is returned unchanged.
    """
    if df.empty or 'timestamp' not in df.columns:
        return df

    fresh_edge = int(fresh_seconds)
    stale_edge = int(stale_seconds)
    if stale_edge > fresh_edge:
        bins = [-1, fresh_edge, stale_edge, float('inf')]
        labels = ['fresh', 'stale', 'hidden']
    else:
        bins = [-1, max(fresh_edge, stale_edge), float('inf')]
        labels = ['fresh', 'hidden']

    out = df.copy()
    ts = pd.to_numeric(out['timestamp'], errors='coerce')
    hidden_floor = max(fresh_edge, stale_edge) + 1
    out['age_seconds'] = (now - ts).clip(lower=0).fillna(hidden_floor).astype(int)
    out['freshness'] = pd.cut(
        out['age_seconds'],
        bins=bins,
        labels=labels,
    ).astype(str)
    return out
