import requests
import pandas as pd
from google.transit import gtfs_realtime_pb2
from google.protobuf.json_format import MessageToDict
import duckdb
import time
from config import (
    API_SOURCES, API_BASE_URL, REQUEST_TIMEOUT,
    DATABASE_NAME, DATABASE_TABLE, 
    DATA_MAX_AGE, DATA_FUTURE_TOLERANCE
)

def fetch_rapid_rail_live():
    """
    Fetch live vehicle positions from Malaysia's GTFS Realtime API
    """
    all_vehicle_data = []

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
                            
                            # Extract details
                            trip = v.get('trip', {})
                            vehicle_info = v.get('vehicle', {})
                            pos = v.get('position', {})

                            all_vehicle_data.append({
                                'region': name,
                                'latitude': pos.get('latitude'),
                                'longitude': pos.get('longitude'),
                                'bearing': pos.get('bearing', 0),
                                'speed': pos.get('speed', 0),
                                'vehicle_id': vehicle_info.get('id', 'Unknown'),
                                'timestamp': v.get('timestamp')
                            })
                else:
                    print(f"Skipping {endpoint}: Status {response.status_code}")
            except Exception as e:
                print(f"Error fetching {name} ({endpoint}): {e}")

    df = pd.DataFrame(all_vehicle_data)

    if not df.empty:
        # Ensure coordinates and time are numeric
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
        df['timestamp_num'] = pd.to_numeric(df['timestamp'], errors='coerce')
        
        current_unix = int(time.time())
        
        # Filter out invalid data:
        # 1. No 0/0 coordinates
        # 2. No future data (> current + tolerance)
        # 3. No extremely old data (< current - max age)
        df = df[
            (df['latitude'] != 0) & 
            (df['longitude'] != 0) &
            (df['timestamp_num'] <= (current_unix + DATA_FUTURE_TOLERANCE)) &
            (df['timestamp_num'] >= (current_unix - DATA_MAX_AGE))
        ].copy()
        
        df = df.drop(columns=['timestamp_num'])

        try:
            con = duckdb.connect(DATABASE_NAME)
            con.execute(f"DROP TABLE IF EXISTS {DATABASE_TABLE}")
            con.execute(f"CREATE TABLE {DATABASE_TABLE} AS SELECT * FROM df")
            con.close()
            print(f"✓ Synced {len(df)} vehicles to database")
        except Exception as db_e:
            print(f"Database Write Error: {db_e}")
    else:
        print("No vehicle data fetched")

if __name__ == "__main__":
    fetch_rapid_rail_live()