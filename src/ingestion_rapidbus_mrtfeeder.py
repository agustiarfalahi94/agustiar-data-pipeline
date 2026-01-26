import requests
import pandas as pd
from google.transit import gtfs_realtime_pb2
from google.protobuf.json_format import MessageToDict
import duckdb

def fetch_rapid_rail_live():
    # List of categories you want to track
    regions = {
        'KL MRT Feeder': 'rapid-bus-mrtfeeder',
        'Alor Setar': 'mybas-alorsetar',
        'Kota Bharu': 'mybas-kotabharu'
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
        con = duckdb.connect('alenna_analytics.duckdb')
        con.execute("CREATE OR REPLACE TABLE live_buses AS SELECT * FROM df")
        con.close()

if __name__ == "__main__":
    fetch_rapid_rail_live()