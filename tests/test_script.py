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


# ── _build_quality_stats ──────────────────────────────────────────────────────

from utils.ingestion import _build_quality_stats


def test_build_quality_stats_basic():
    received = {'Rapid Bus KL': 50, 'KTM Berhad': 30}
    valid = {'Rapid Bus KL': 45, 'KTM Berhad': 28}
    inserted = {'Rapid Bus KL': 40, 'KTM Berhad': 25}
    lag = {
        'Rapid Bus KL': {'avg': 20.0, 'max': 60.0},
        'KTM Berhad': {'avg': 35.0, 'max': 90.0},
    }
    duration = {'Rapid Bus KL': 800, 'KTM Berhad': 600}
    ts = 1000000

    stats = _build_quality_stats(received, valid, inserted, lag, duration, ts)

    assert len(stats) == 2
    kl = next(s for s in stats if s['region'] == 'Rapid Bus KL')
    assert kl['fetch_timestamp'] == ts
    assert kl['vehicles_received'] == 50
    assert kl['vehicles_rejected'] == 5
    assert kl['vehicles_inserted'] == 40
    assert kl['avg_data_lag_seconds'] == 20.0
    assert kl['max_data_lag_seconds'] == 60.0
    assert kl['total_dropout'] is False
    assert kl['fetch_duration_ms'] == 800


def test_build_quality_stats_dropout():
    received = {'myBAS Johor': 0}
    valid = {}
    inserted = {}
    lag = {}
    duration = {'myBAS Johor': 500}
    ts = 1000000

    stats = _build_quality_stats(received, valid, inserted, lag, duration, ts)

    assert len(stats) == 1
    assert stats[0]['total_dropout'] is True
    assert stats[0]['vehicles_received'] == 0
    assert stats[0]['vehicles_rejected'] == 0
    assert stats[0]['vehicles_inserted'] == 0


def test_build_quality_stats_rejected_never_negative():
    """Dedup may mean inserted < valid — rejected should never go below 0."""
    received = {'Rapid Bus KL': 10}
    valid = {'Rapid Bus KL': 10}
    inserted = {'Rapid Bus KL': 3}
    lag = {'Rapid Bus KL': {'avg': 5.0, 'max': 10.0}}
    duration = {'Rapid Bus KL': 300}
    ts = 1000000

    stats = _build_quality_stats(received, valid, inserted, lag, duration, ts)
    assert stats[0]['vehicles_rejected'] == 0


# ── fetch guard ───────────────────────────────────────────────────────────────

import tempfile
import duckdb as _duckdb


def test_fetch_guard_skips_when_recent_fetch_exists():
    """
    If fetch_quality_log has a row within the last 15 seconds,
    fetch_and_store_transit_data should return early without calling requests.get.
    """
    db_path = os.path.join(tempfile.mkdtemp(), 'test_guard.duckdb')

    try:
        con = _duckdb.connect(db_path)
        con.execute("""
            CREATE TABLE fetch_quality_log (
                fetch_timestamp BIGINT, region VARCHAR,
                vehicles_received INTEGER, vehicles_rejected INTEGER,
                vehicles_inserted INTEGER, avg_data_lag_seconds DOUBLE,
                max_data_lag_seconds DOUBLE, total_dropout BOOLEAN,
                fetch_duration_ms INTEGER
            )
        """)
        con.execute(
            f"INSERT INTO fetch_quality_log VALUES ({int(time.time()) - 5}, 'Test', 0, 0, 0, 0, 0, false, 0)"
        )
        con.close()

        call_count = {'n': 0}

        def fake_get(*args, **kwargs):
            call_count['n'] += 1
            raise AssertionError("requests.get should not be called")

        from utils import ingestion as _ing
        with patch('utils.ingestion.DATABASE_NAME', db_path), \
             patch('utils.ingestion.requests.get', side_effect=fake_get):
            _ing.fetch_and_store_transit_data()

        assert call_count['n'] == 0, "requests.get was called despite recent fetch"
    finally:
        os.unlink(db_path)


# ── DB health functions ───────────────────────────────────────────────────────

def _make_temp_quality_db(rows):
    """Create a temp DuckDB with fetch_quality_log populated."""
    db_path = os.path.join(tempfile.mkdtemp(), 'test_quality.duckdb')
    con = _duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE fetch_quality_log (
            fetch_timestamp BIGINT, region VARCHAR,
            vehicles_received INTEGER, vehicles_rejected INTEGER,
            vehicles_inserted INTEGER, avg_data_lag_seconds DOUBLE,
            max_data_lag_seconds DOUBLE, total_dropout BOOLEAN,
            fetch_duration_ms INTEGER
        )
    """)
    for row in rows:
        con.execute(
            "INSERT INTO fetch_quality_log VALUES (?,?,?,?,?,?,?,?,?)",
            [row['fetch_timestamp'], row['region'], row['vehicles_received'],
             row['vehicles_rejected'], row['vehicles_inserted'],
             row['avg_data_lag_seconds'], row['max_data_lag_seconds'],
             row['total_dropout'], row['fetch_duration_ms']]
        )
    con.close()
    return db_path


def test_get_network_health_summary_returns_one_row_per_region():
    now = int(time.time())
    db_path = _make_temp_quality_db([
        {'fetch_timestamp': now - 100, 'region': 'Rapid Bus KL',
         'vehicles_received': 50, 'vehicles_rejected': 5, 'vehicles_inserted': 40,
         'avg_data_lag_seconds': 20.0, 'max_data_lag_seconds': 60.0,
         'total_dropout': False, 'fetch_duration_ms': 800},
        {'fetch_timestamp': now - 100, 'region': 'KTM Berhad',
         'vehicles_received': 30, 'vehicles_rejected': 2, 'vehicles_inserted': 25,
         'avg_data_lag_seconds': 35.0, 'max_data_lag_seconds': 90.0,
         'total_dropout': False, 'fetch_duration_ms': 600},
    ])
    try:
        with patch('utils.db.DATABASE_NAME', db_path):
            from utils import db as _db
            result = _db.get_network_health_summary(window_hours=24)
        assert len(result) == 2
        assert set(result['region'].tolist()) == {'Rapid Bus KL', 'KTM Berhad'}
        assert 'reliability_score' in result.columns
        assert 'reporting_rate' in result.columns
        assert 'dropout_count' in result.columns
        assert 'total_fetches' in result.columns
        assert all(result['reliability_score'].between(0, 100))
    finally:
        os.unlink(db_path)


def test_get_network_health_summary_returns_empty_when_no_table():
    db_path = os.path.join(tempfile.mkdtemp(), 'test_empty.duckdb')
    try:
        with patch('utils.db.DATABASE_NAME', db_path):
            from utils import db as _db
            result = _db.get_network_health_summary()
        assert result.empty
    finally:
        # db may not exist if never opened
        if os.path.exists(db_path):
            os.unlink(db_path)
