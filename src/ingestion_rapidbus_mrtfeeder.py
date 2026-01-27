import requests
import pandas as pd
from google.transit import gtfs_realtime_pb2
from google.protobuf.json_format import MessageToDict
import duckdb
import time

def fetch_rapid_rail_live():
    # List of categories you want to track
    regions = {
        'Rapid Bus KL': 'rapid-bus-kl',
        'Rapid Bus MRT Feeder': 'rapid-bus-mrtfeeder',
        'Rapid Bus Kuantan': 'rapid-bus-kuantan',
        'Rapid Bus Penang' : 'rapid-bus-penang'
    }
    
    all_vehicle_data = []

    for name, cat in regions.items():
        url = f'https://api.data.gov.my/gtfs-realtime/vehicle-position/prasarana?category={cat}'
        try:
            response = requests.get(url)
            if response.status_code == 200:
                feed = gtfs_realtime_pb2.FeedMessage()
                feed.ParseFromString(response.content)
                for entity in feed.entity:
                    if entity.HasField('vehicle'):
                        v = MessageToDict(entity.vehicle)
                        all_vehicle_data.append({
                            'region': name, # Track which city the bus belongs to
                            'latitude': v.get('position', {}).get('latitude'),
                            'longitude': v.get('position', {}).get('longitude'),
                            'timestamp': v.get('timestamp')
                        })
        except Exception as e:
            print(f"Error fetching {name}: {e}")

    df = pd.DataFrame(all_vehicle_data)
    if not df.empty:
        # 1. Convert timestamp, latitude and longitude column to numeric just in case
        df['timestamp'] = pd.to_numeric(df['timestamp'])
        df['latitude'] = pd.to_numeric(df['latitude'])
        df['longitude'] = pd.to_numeric(df['longitude'])
        
        # 2. Get CURRENT Unix timestamp
        current_unix = int(time.time())
        
        # 3. FILTER: Only keep data that is NOT in the future
        # We add a 60-second buffer just in case of slight clock drifts
        df = df[
            (df['timestamp'] <= (current_unix + 60)) & 
            (df['latitude'] != 0) & 
            (df['longitude'] != 0) &
            (df['latitude'].notna()) &
            (df['longitude'].notna())
        ]

        try:
            con = duckdb.connect('agustiar_analytics.duckdb')
            # 'CREATE OR REPLACE' is good, but let's ensure it's clean
            con.execute("DROP TABLE IF EXISTS live_buses")
            con.execute("CREATE TABLE live_buses AS SELECT * FROM df")
            con.close()
            print(f"Synced {len(df)} valid records (removed future noise).")
        except Exception as db_e:
            print(f"Database Write Error: {db_e}")

if __name__ == "__main__":
    fetch_rapid_rail_live()