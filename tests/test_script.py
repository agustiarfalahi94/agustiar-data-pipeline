# tests/test_data_processor.py
from utils.data_processor import convert_speed_to_kmh
import pandas as pd

def test_speed_conversion():
    df = pd.DataFrame({'speed': [10, 20, 30]})
    result = convert_speed_to_kmh(df)
    assert result['speed'].tolist() == [36, 72, 108]