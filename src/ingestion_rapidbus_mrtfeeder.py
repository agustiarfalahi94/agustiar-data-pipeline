import requests
import pandas as pd
from google.transit import gtfs_realtime_pb2
from google.protobuf.json_format import MessageToDict
import duckdb
import time

# Constants
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
    'myBAS Kuching': ['mybas-kuching']
}

API_BASE_URL = 'https://api.data.gov.my/gtfs-realtime/vehicle-position/'
REQUEST_TIMEOUT = 10

try:
    from config import DATABASE_NAME, DATABASE_TABLE, DATA_MAX_AGE, DATA_FUTURE_TOLERANCE
except ImportError:
    DATABASE_NAME = 'agustiar_analytics.duckdb'
    DATABASE_TABLE = 'live_buses'
    DATA_MAX_AGE = 3600
    DATA_FUTURE_TOLERANCE = 300

def fetch_rapid_rail_live():
    """
    Fetch live transit data from Malaysia GTFS API and store in DuckDB
    - Fetches data from all configured regions
    - Filters invalid/stale data
    - Deduplicates before inserting
    """
    all_vehicle_data = []
    current_unix = int(time.time())

    # ===== Step 1: Fetch data from all API endpoints =====
    for name, endpoints in API_SOURCES.items():
        for endpoint in endpoints:
            url = f'{API_BASE_URL}{endpoint}'
            try:
                response = requests.get(url, timeout=REQUEST_TIMEOUT)
                if response.status_code == 200:
                    feed = gtfs_realtime_pb2.FeedMessage()
                    feed.ParseFromString(response.content)
                    
                    for entity in feed.entity:
                        if entity.HasField('vehicle'):
                            v = MessageToDict(entity.vehicle)
                            pos = v.get('position', {})
                            vehicle_info = v.get('vehicle', {})
                            
                            all_vehicle_data.append({
                                'region': name,
                                'latitude': pos.get('latitude'),
                                'longitude': pos.get('longitude'),
                                'bearing': pos.get('bearing', 0),
                                'speed': pos.get('speed', 0),
                                'vehicle_id': vehicle_info.get('id', 'Unknown'),
                                'timestamp': v.get('timestamp')
                            })
            except Exception as e:
                print(f"Error fetching {name} ({endpoint}): {e}")

    if not all_vehicle_data:
        print("No vehicle data fetched")
        return

    # ===== Step 2: Clean and filter data =====
    df = pd.DataFrame(all_vehicle_data)
    
    # Convert to numeric for filtering
    df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
    df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
    df['timestamp_num'] = pd.to_numeric(df['timestamp'], errors='coerce')
    
    # Filter invalid coordinates and timestamps
    df = df[
        (df['latitude'] != 0) &
        (df['longitude'] != 0) &
        (df['timestamp_num'].notna()) &
        (df['timestamp_num'] <= current_unix + DATA_FUTURE_TOLERANCE) &
        (df['timestamp_num'] >= current_unix - DATA_MAX_AGE)
    ].drop(columns=['timestamp_num']).copy()

    if df.empty:
        print("No valid vehicle data after filtering")
        return

    df['insert_timestamp'] = current_unix

    # ===== Step 3: Store in database with deduplication =====
    try:
        con = duckdb.connect(DATABASE_NAME)
        
        table_exists = con.execute(
            f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{DATABASE_TABLE}'"
        ).fetchone()[0] > 0
        
        if not table_exists:
            con.execute(f"CREATE TABLE {DATABASE_TABLE} AS SELECT * FROM df")
            print(f"✓ Created table and synced {len(df)} vehicles")
        else:
            # Ensure insert_timestamp column exists (for migration)
            columns = con.execute(
                f"SELECT column_name FROM information_schema.columns WHERE table_name = '{DATABASE_TABLE}'"
            ).df()['column_name'].tolist()
            
            if 'insert_timestamp' not in columns:
                con.execute(f"ALTER TABLE {DATABASE_TABLE} ADD COLUMN insert_timestamp BIGINT")
                con.execute(f"UPDATE {DATABASE_TABLE} SET insert_timestamp = {current_unix} WHERE insert_timestamp IS NULL")
            
            # Insert only non-duplicate records
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
                print(f"⚠ No new data inserted (all records were duplicates)")
        
        con.close()
    except Exception as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    fetch_rapid_rail_live()