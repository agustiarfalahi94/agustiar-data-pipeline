import pandas as pd
import pytest
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.data_processor import convert_speed_to_kmh, prepare_map_data, get_sorted_regions
from unittest.mock import patch, MagicMock
from utils.ingestion import _fetch_endpoint


# ── convert_speed_to_kmh ──────────────────────────────────────────────────────

def test_speed_conversion_basic():
    df = pd.DataFrame({'speed': [10, 20, 30]})
    result = convert_speed_to_kmh(df)
    assert result['speed'].tolist() == [36, 72, 108]


def test_speed_conversion_zero():
    df = pd.DataFrame({'speed': [0]})
    result = convert_speed_to_kmh(df)
    assert result['speed'].tolist() == [0]


def test_speed_conversion_caps_at_120():
    df = pd.DataFrame({'speed': [40]})  # 40 m/s = 144 km/h → capped at 120
    result = convert_speed_to_kmh(df)
    assert result['speed'].tolist() == [120]


def test_speed_conversion_null_becomes_zero():
    df = pd.DataFrame({'speed': [None, float('nan')]})
    result = convert_speed_to_kmh(df)
    assert result['speed'].tolist() == [0, 0]


# ── prepare_map_data ──────────────────────────────────────────────────────────

def _make_df(rows):
    return pd.DataFrame(rows)


def test_prepare_map_data_filters_zero_coords():
    df = _make_df([
        {'region': 'Rapid Bus KL', 'vehicle_id': 'A', 'latitude': 3.1, 'longitude': 101.7, 'bearing': 0, 'speed': 5},
        {'region': 'Rapid Bus KL', 'vehicle_id': 'B', 'latitude': 0.0, 'longitude': 0.0, 'bearing': 0, 'speed': 0},
    ])
    result = prepare_map_data(df, 'Rapid Bus KL')
    assert len(result) == 1
    assert result.iloc[0]['vehicle_id'] == 'A'


def test_prepare_map_data_filters_null_coords():
    df = _make_df([
        {'region': 'Rapid Bus KL', 'vehicle_id': 'A', 'latitude': None, 'longitude': 101.7, 'bearing': 0, 'speed': 0},
        {'region': 'Rapid Bus KL', 'vehicle_id': 'B', 'latitude': 3.1, 'longitude': None, 'bearing': 0, 'speed': 0},
        {'region': 'Rapid Bus KL', 'vehicle_id': 'C', 'latitude': 3.1, 'longitude': 101.7, 'bearing': 0, 'speed': 0},
    ])
    result = prepare_map_data(df, 'Rapid Bus KL')
    assert len(result) == 1
    assert result.iloc[0]['vehicle_id'] == 'C'


def test_prepare_map_data_filters_by_region():
    df = _make_df([
        {'region': 'Rapid Bus KL', 'vehicle_id': 'A', 'latitude': 3.1, 'longitude': 101.7, 'bearing': 0, 'speed': 0},
        {'region': 'KTM Berhad', 'vehicle_id': 'B', 'latitude': 3.2, 'longitude': 101.6, 'bearing': 0, 'speed': 0},
    ])
    result = prepare_map_data(df, 'KTM Berhad')
    assert len(result) == 1
    assert result.iloc[0]['vehicle_id'] == 'B'


def test_prepare_map_data_empty_region():
    df = _make_df([
        {'region': 'Rapid Bus KL', 'vehicle_id': 'A', 'latitude': 3.1, 'longitude': 101.7, 'bearing': 0, 'speed': 0},
    ])
    result = prepare_map_data(df, 'Nonexistent Region')
    assert result.empty


def test_prepare_map_data_converts_speed_to_kmh():
    df = _make_df([
        {'region': 'Rapid Bus KL', 'vehicle_id': 'A', 'latitude': 3.1, 'longitude': 101.7, 'bearing': 0, 'speed': 10},
    ])
    result = prepare_map_data(df, 'Rapid Bus KL')
    assert result.iloc[0]['speed'] == 36


# ── get_sorted_regions ────────────────────────────────────────────────────────

def test_get_sorted_regions_primary_first():
    df = _make_df([
        {'region': 'KTM Berhad'},
        {'region': 'Rapid Bus KL'},
        {'region': 'myBAS Johor'},
    ])
    result = get_sorted_regions(df)
    assert result[0] == 'Rapid Bus KL'


def test_get_sorted_regions_without_primary():
    df = _make_df([
        {'region': 'KTM Berhad'},
        {'region': 'myBAS Johor'},
    ])
    result = get_sorted_regions(df)
    assert 'Rapid Bus KL' not in result
    assert result == sorted(result)


def test_get_sorted_regions_others_alphabetical():
    df = _make_df([
        {'region': 'myBAS Johor'},
        {'region': 'KTM Berhad'},
        {'region': 'Rapid Bus KL'},
        {'region': 'Rapid Bus Penang'},
    ])
    result = get_sorted_regions(df)
    assert result[0] == 'Rapid Bus KL'
    assert result[1:] == sorted(result[1:])


# ── _fetch_endpoint ───────────────────────────────────────────────────────────

def test_fetch_endpoint_returns_tuple_on_network_error():
    """_fetch_endpoint must return (list, int) even when the request fails."""
    with patch('utils.ingestion.requests.get', side_effect=ConnectionError("timeout")):
        result = _fetch_endpoint("Test Region", "test-endpoint")
    assert isinstance(result, tuple), "should return a tuple"
    assert isinstance(result[0], list), "first element should be a list"
    assert isinstance(result[1], int), "second element should be duration_ms int"
    assert result[0] == [], "vehicle list should be empty on error"
    assert result[1] >= 0, "duration should be non-negative"


def test_fetch_endpoint_returns_tuple_on_non_200():
    """_fetch_endpoint must return ([], duration_ms) for non-200 responses."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    with patch('utils.ingestion.requests.get', return_value=mock_response):
        result = _fetch_endpoint("Test Region", "test-endpoint")
    assert isinstance(result, tuple)
    assert result[0] == []
    assert isinstance(result[1], int)
