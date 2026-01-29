import pandas as pd

try:
    from config import REGIONS
except ImportError:
    import streamlit as st
    REGIONS = st.secrets["regions"]["list"]

def get_sorted_regions(df):
    """Get available regions sorted with Rapid Bus KL first"""
    primary = ['Rapid Bus KL']
    available = df['region'].unique().tolist()
    others = sorted([r for r in available if r != 'Rapid Bus KL'])
    return [r for r in primary if r in available] + others

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
    
    # Convert speed from m/s to km/h, round to whole number, and cap at reasonable max (120 km/h for urban areas)
    df_filtered['speed'] = df_filtered['speed'] * 3.6
    df_filtered['speed'] = df_filtered['speed'].round(0).clip(upper=120)
    
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
    
    # Convert speed from m/s to km/h, round to whole number, and cap at reasonable max
    display_df['speed'] = pd.to_numeric(display_df['speed'], errors='coerce').fillna(0) * 3.6
    display_df['speed'] = display_df['speed'].round(0).clip(upper=120)
    
    # Calculate average speed per vehicle (for grouping)
    avg_speed = display_df.groupby('vehicle_id')['speed'].mean().round(0)
    display_df['avg_speed'] = display_df['vehicle_id'].map(avg_speed)
    
    # Select and rename columns
    display_df = display_df[[
        'region', 'vehicle_id', 'latitude', 'longitude', 
        'bearing', 'speed', 'avg_speed', 'timestamp_formatted'
    ]]
    
    display_df = display_df.rename(columns={
        'region': 'Region',
        'vehicle_id': 'Vehicle ID',
        'latitude': 'Latitude',
        'longitude': 'Longitude',
        'bearing': 'Heading (°)',
        'speed': 'Speed (km/h)',
        'avg_speed': 'Avg Speed (km/h)',
        'timestamp_formatted': 'Timestamp'
    })
    
    # Round numeric columns
    display_df['Latitude'] = display_df['Latitude'].round(6)
    display_df['Longitude'] = display_df['Longitude'].round(6)
    display_df['Heading (°)'] = display_df['Heading (°)'].round(1)
    display_df['Speed (km/h)'] = display_df['Speed (km/h)'].round(0).astype(int)
    display_df['Avg Speed (km/h)'] = display_df['Avg Speed (km/h)'].round(0).astype(int)
    
    return display_df.reset_index(drop=True)
