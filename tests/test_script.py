import pandas as pd
import pytest
import sys
import os
import time
import warnings
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import data_processor
from utils.data_processor import convert_speed_to_kmh, prepare_map_data, get_sorted_regions
from unittest.mock import patch, MagicMock
from utils.ingestion import _fetch_endpoint
from utils import walking
from utils import background_fetch


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
    status = {'Rapid Bus KL': 'OK', 'KTM Berhad': 'OK'}
    ts = 1000000

    stats = _build_quality_stats(received, valid, inserted, lag, duration, status, ts)

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
    status = {'myBAS Johor': 'NO_FEED'}
    ts = 1000000

    stats = _build_quality_stats(received, valid, inserted, lag, duration, status, ts)

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
    status = {'Rapid Bus KL': 'OK'}
    ts = 1000000

    stats = _build_quality_stats(received, valid, inserted, lag, duration, status, ts)
    assert stats[0]['vehicles_rejected'] == 0


# ── fetch guard ───────────────────────────────────────────────────────────────

import tempfile
import contextlib
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
            f"INSERT INTO fetch_quality_log VALUES ({int(time.time()) - 1}, 'Test', 0, 0, 0, 0, 0, false, 0)"
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
    """
    Create a temp DuckDB with fetch_quality_log populated, plus mart_network_health
    and mart_region_health_trend views exposing the same columns as the real dbt
    marts (transform/models/marts/*.sql). db.py's get_network_health_summary and
    get_region_health_trend read these view names directly, so unit tests need
    them present even without running a full dbt build. reliability_score is a
    fixed stand-in value here, not the real weighted formula — see the comment
    on the view definitions below.
    """
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
    con.execute("""
        CREATE VIEW mart_network_health AS
        SELECT
            region,
            COUNT(*) AS total_fetches,
            SUM(CASE WHEN total_dropout THEN 1 ELSE 0 END) AS dropout_count,
            COALESCE(AVG(CASE WHEN vehicles_received > 0
                THEN vehicles_inserted::DOUBLE / vehicles_received
                ELSE NULL END), 0) AS reporting_rate,
            1.0 - SUM(CASE WHEN total_dropout THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS availability,
            COALESCE(AVG(avg_data_lag_seconds), 0) AS avg_data_lag_seconds,
            MAX(fetch_timestamp) AS last_fetch_timestamp,
            -- Fixed stand-in score, not the real formula: these tests only assert
            -- range/shape (see reliability_score.between(0, 100) below), so the
            -- actual weighted arithmetic doesn't need to be duplicated here. The
            -- real formula (transform/macros/reliability_score.sql) is exercised
            -- against the dbt macro in tests/test_dbt_marts.py.
            95 AS reliability_score
        FROM fetch_quality_log
        GROUP BY region
    """)
    con.execute("""
        CREATE VIEW mart_region_health_trend AS
        SELECT
            region,
            fetch_timestamp,
            vehicles_received,
            vehicles_rejected,
            vehicles_inserted,
            avg_data_lag_seconds,
            max_data_lag_seconds,
            total_dropout,
            -- Fixed stand-in score (see comment on mart_network_health above):
            -- only range/shape is asserted here, not the real formula.
            90 AS reliability_score
        FROM fetch_quality_log
    """)
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
            result = _db.get_network_health_summary()
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


def test_get_region_health_trend_returns_time_series():
    now = int(time.time())
    rows = [
        {'fetch_timestamp': now - (i * 20), 'region': 'Rapid Bus KL',
         'vehicles_received': 50, 'vehicles_rejected': 2, 'vehicles_inserted': 45,
         'avg_data_lag_seconds': 15.0, 'max_data_lag_seconds': 40.0,
         'total_dropout': False, 'fetch_duration_ms': 700}
        for i in range(5)
    ]
    db_path = _make_temp_quality_db(rows)
    try:
        with patch('utils.db.DATABASE_NAME', db_path):
            from utils import db as _db
            result = _db.get_region_health_trend('Rapid Bus KL', window_hours=1)
        assert len(result) == 5
        assert 'reliability_score' in result.columns
        assert 'datetime' in result.columns
        assert 'avg_data_lag_seconds' in result.columns
        assert 'vehicles_received' in result.columns
    finally:
        os.unlink(db_path)


def test_get_region_fetch_log_returns_most_recent_first():
    now = int(time.time())
    rows = [
        {'fetch_timestamp': now - (i * 20), 'region': 'KTM Berhad',
         'vehicles_received': 30, 'vehicles_rejected': 1, 'vehicles_inserted': 28,
         'avg_data_lag_seconds': 25.0, 'max_data_lag_seconds': 70.0,
         'total_dropout': False, 'fetch_duration_ms': 500}
        for i in range(10)
    ]
    db_path = _make_temp_quality_db(rows)
    try:
        with patch('utils.db.DATABASE_NAME', db_path):
            from utils import db as _db
            result = _db.get_region_fetch_log('KTM Berhad', limit=5)
        assert len(result) == 5
        assert 'datetime' in result.columns
        assert result.iloc[0]['fetch_timestamp'] >= result.iloc[1]['fetch_timestamp']
    finally:
        os.unlink(db_path)


def test_get_region_vehicle_counts_returns_region_and_count():
    """
    get_region_vehicle_counts() should read distinct-vehicle counts per region
    from mart_region_vehicle_counts, exposed as columns ['Region', 'Count'].
    """
    db_path = os.path.join(tempfile.mkdtemp(), 'test_region_counts.duckdb')
    con = _duckdb.connect(db_path)
    # table_exists() checks for DATABASE_TABLE ('live_buses'); its presence
    # (not contents) is what gates get_region_vehicle_counts().
    con.execute("CREATE TABLE live_buses (vehicle_id VARCHAR, region VARCHAR)")
    con.execute("""
        CREATE VIEW mart_region_vehicle_counts AS
        SELECT * FROM (VALUES
            ('Rapid Bus KL', 3),
            ('KTM Berhad', 1)
        ) AS t(region, unique_vehicles)
    """)
    con.close()
    try:
        with patch('utils.db.DATABASE_NAME', db_path):
            from utils import db as _db
            result = _db.get_region_vehicle_counts()
        assert list(result.columns) == ['Region', 'Count']
        assert set(result['Region'].tolist()) == {'Rapid Bus KL', 'KTM Berhad'}
        kl_count = result.loc[result['Region'] == 'Rapid Bus KL', 'Count'].iloc[0]
        ktm_count = result.loc[result['Region'] == 'KTM Berhad', 'Count'].iloc[0]
        assert kl_count == 3
        assert ktm_count == 1
    finally:
        os.unlink(db_path)


# ── filter_by_route ──────────────────────────────────────────────────────────

def test_filter_by_route_matches_case_insensitively():
    df = pd.DataFrame({
        'vehicle_id': ['A', 'B', 'C'],
        'route_display': ['T580 — Awan Besar ~ TPM', 'U6000 — Klang', 'T581 — Other'],
    })
    out = data_processor.filter_by_route(df, 't580')
    assert list(out['vehicle_id']) == ['A']


def test_filter_by_route_matches_long_name():
    df = pd.DataFrame({
        'vehicle_id': ['A', 'B'],
        'route_display': ['T580 — Awan Besar ~ TPM', 'U6000 — Klang'],
    })
    out = data_processor.filter_by_route(df, 'awan besar')
    assert list(out['vehicle_id']) == ['A']


def test_filter_by_route_empty_query_returns_all():
    df = pd.DataFrame({'vehicle_id': ['A', 'B'], 'route_display': ['T580', 'U6000']})
    assert len(data_processor.filter_by_route(df, '')) == 2
    assert len(data_processor.filter_by_route(df, '   ')) == 2


def test_filter_by_route_no_match_returns_empty_with_columns():
    df = pd.DataFrame({'vehicle_id': ['A'], 'route_display': ['T580']})
    out = data_processor.filter_by_route(df, 'ZZZ999')
    assert out.empty
    assert list(out.columns) == ['vehicle_id', 'route_display']


def test_filter_by_route_missing_column_returns_unchanged():
    df = pd.DataFrame({'vehicle_id': ['A', 'B']})
    assert len(data_processor.filter_by_route(df, 'T580')) == 2


def test_filter_by_route_treats_regex_metacharacters_literally():
    """The query is user input, so `regex=False` is load-bearing, not
    defensive: as a regex, "." matches every vehicle in the region."""
    df = pd.DataFrame({
        'vehicle_id': ['A', 'B', 'C'],
        'route_display': ['T580 — Awan Besar', 'U6000 — Klang', 'No.7 — Shuttle'],
    })
    out = data_processor.filter_by_route(df, '.')
    assert list(out['vehicle_id']) == ['C']


def test_filter_by_route_returns_a_copy_not_a_slice():
    """Callers assign derived columns onto the result (live_map's arrow_path);
    on a slice that raises SettingWithCopyWarning on pandas 2.x."""
    df = pd.DataFrame({
        'vehicle_id': ['A', 'B'],
        'route_display': ['T580 — Awan Besar', 'U6000 — Klang'],
    })
    out = data_processor.filter_by_route(df, 'T580')
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out['derived'] = 1
    assert 'derived' not in df.columns


# ── fetch status classification ──────────────────────────────────────────────

from utils import ingestion


def test_classify_status_ok_and_empty():
    assert ingestion._classify_status(200, 5) == 'OK'
    assert ingestion._classify_status(200, 0) == 'EMPTY'


def test_classify_status_no_feed_and_throttled():
    assert ingestion._classify_status(404, 0) == 'NO_FEED'
    assert ingestion._classify_status(429, 0) == 'THROTTLED'


def test_classify_status_other_codes_are_errors():
    assert ingestion._classify_status(500, 0) == 'ERROR'
    assert ingestion._classify_status(None, 0) == 'ERROR'


def test_merge_status_precedence():
    # OK beats everything
    assert ingestion._merge_status('OK', 'NO_FEED') == 'OK'
    assert ingestion._merge_status('NO_FEED', 'OK') == 'OK'
    # a responding-but-empty feed beats a dead one
    assert ingestion._merge_status('EMPTY', 'NO_FEED') == 'EMPTY'
    # a real error outranks throttling and a dead feed
    assert ingestion._merge_status('ERROR', 'THROTTLED') == 'ERROR'
    assert ingestion._merge_status('NO_FEED', 'NO_FEED') == 'NO_FEED'


def test_build_quality_stats_carries_fetch_status():
    stats = ingestion._build_quality_stats(
        received_by_region={'R1': 10},
        valid_by_region={'R1': 8},
        inserted_by_region={'R1': 8},
        lag_by_region={'R1': {'avg': 1.0, 'max': 2.0}},
        duration_by_region={'R1': 120},
        status_by_region={'R1': 'OK'},
        fetch_timestamp=1750000000,
    )
    assert len(stats) == 1
    assert stats[0]['fetch_status'] == 'OK'


def test_build_quality_stats_defaults_missing_status_to_error():
    stats = ingestion._build_quality_stats(
        received_by_region={'R1': 0},
        valid_by_region={},
        inserted_by_region={},
        lag_by_region={},
        duration_by_region={'R1': 5},
        status_by_region={},
        fetch_timestamp=1750000000,
    )
    assert stats[0]['fetch_status'] == 'ERROR'


def test_early_return_logs_no_feed_status(tmp_path):
    """
    Step 8: a fully-dead fetch cycle (every endpoint fails, all_vehicle_data
    stays empty) must still write a quality-log row instead of vanishing
    silently — this is what makes a withdrawn feed like Rapid Bus Kuantan's
    HTTP 404 visible rather than indistinguishable from a passing cycle.
    """
    db_path = str(tmp_path / 'test_early_return.duckdb')
    captured = []

    with patch('utils.ingestion.DATABASE_NAME', db_path), \
         patch('utils.ingestion.API_SOURCES', {'Rapid Bus Kuantan': ['dead-endpoint']}), \
         patch('utils.ingestion._fetch_endpoint', return_value=([], 5, 'NO_FEED')), \
         patch('utils.ingestion._write_quality_log', side_effect=captured.append):
        ingestion.fetch_and_store_transit_data()

    assert len(captured) == 1
    stats_list = captured[0]
    assert len(stats_list) > 0
    row = next(s for s in stats_list if s['region'] == 'Rapid Bus Kuantan')
    assert row['fetch_status'] == 'NO_FEED'


def test_write_quality_log_persists_fetch_status(tmp_path, monkeypatch):
    import duckdb
    db = tmp_path / "q.duckdb"
    monkeypatch.setattr(ingestion, 'DATABASE_NAME', str(db))
    ingestion._write_quality_log([{
        'fetch_timestamp': 1750000000, 'region': 'R1',
        'vehicles_received': 5, 'vehicles_rejected': 0, 'vehicles_inserted': 5,
        'avg_data_lag_seconds': 1.0, 'max_data_lag_seconds': 2.0,
        'total_dropout': False, 'fetch_duration_ms': 100, 'fetch_status': 'OK',
    }])
    con = duckdb.connect(str(db))
    try:
        row = con.execute(
            "SELECT region, fetch_status FROM fetch_quality_log"
        ).fetchone()
    finally:
        con.close()
    assert row == ('R1', 'OK')


def test_write_quality_log_migrates_existing_table(tmp_path, monkeypatch):
    import duckdb
    db = tmp_path / "old.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE fetch_quality_log (
            fetch_timestamp BIGINT, region VARCHAR,
            vehicles_received INTEGER, vehicles_rejected INTEGER,
            vehicles_inserted INTEGER, avg_data_lag_seconds DOUBLE,
            max_data_lag_seconds DOUBLE, total_dropout BOOLEAN,
            fetch_duration_ms INTEGER
        )
    """)
    con.execute("INSERT INTO fetch_quality_log VALUES "
                "(1749999999,'OLD',1,0,1,0.0,0.0,false,10)")
    con.close()
    monkeypatch.setattr(ingestion, 'DATABASE_NAME', str(db))
    ingestion._write_quality_log([{
        'fetch_timestamp': 1750000000, 'region': 'NEW',
        'vehicles_received': 0, 'vehicles_rejected': 0, 'vehicles_inserted': 0,
        'avg_data_lag_seconds': 0.0, 'max_data_lag_seconds': 0.0,
        'total_dropout': True, 'fetch_duration_ms': 50, 'fetch_status': 'NO_FEED',
    }])
    con = duckdb.connect(str(db))
    try:
        rows = dict(con.execute(
            "SELECT region, fetch_status FROM fetch_quality_log"
        ).fetchall())
    finally:
        con.close()
    assert rows['NEW'] == 'NO_FEED'
    assert rows['OLD'] is None      # pre-existing row keeps NULL


# ── classify_freshness ────────────────────────────────────────────────────────

def test_classify_freshness_assigns_three_tiers():
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['fresh', 'stale', 'hidden'],
        'timestamp': [now - 10, now - 120, now - 600],
    })
    out = data_processor.classify_freshness(df, now)
    assert list(out['freshness']) == ['fresh', 'stale', 'hidden']
    assert list(out['age_seconds']) == [10, 120, 600]


def test_classify_freshness_boundaries_are_inclusive():
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['at_fresh_edge', 'just_past', 'at_stale_edge', 'just_past_stale'],
        'timestamp': [now - 60, now - 61, now - 300, now - 301],
    })
    out = data_processor.classify_freshness(df, now)
    assert list(out['freshness']) == ['fresh', 'stale', 'stale', 'hidden']


def test_classify_freshness_clamps_future_timestamps_to_fresh():
    now = 1_800_000_000
    df = pd.DataFrame({'vehicle_id': ['ahead'], 'timestamp': [now + 250]})
    out = data_processor.classify_freshness(df, now)
    assert list(out['age_seconds']) == [0]
    assert list(out['freshness']) == ['fresh']


def test_classify_freshness_never_drops_rows():
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['a', 'b', 'c'],
        'timestamp': [now - 5, now - 400, now - 800],
    })
    assert len(data_processor.classify_freshness(df, now)) == 3


def test_classify_freshness_handles_empty_and_missing_column():
    now = 1_800_000_000
    assert data_processor.classify_freshness(pd.DataFrame(), now).empty
    df = pd.DataFrame({'vehicle_id': ['a']})
    out = data_processor.classify_freshness(df, now)
    assert 'freshness' not in out.columns


def test_classify_freshness_respects_custom_bounds():
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['fresh', 'at_fresh_edge', 'stale', 'at_stale_edge', 'hidden'],
        'timestamp': [now - 5, now - 10, now - 15, now - 20, now - 30],
    })
    out = data_processor.classify_freshness(df, now, fresh_seconds=10, stale_seconds=20)
    assert list(out['freshness']) == ['fresh', 'fresh', 'stale', 'stale', 'hidden']


def test_classify_freshness_survives_equal_bounds():
    """
    Both bounds are user-tunable, so they can be set equal. pd.cut raises
    "Bin edges must be unique" on duplicate edges — the tiers degrade to
    fresh/hidden instead of the page crashing.
    """
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['under', 'at_edge', 'over'],
        'timestamp': [now - 30, now - 60, now - 61],
    })
    out = data_processor.classify_freshness(df, now, fresh_seconds=60, stale_seconds=60)
    assert list(out['freshness']) == ['fresh', 'fresh', 'hidden']
    assert 'stale' not in set(out['freshness'])


def test_classify_freshness_survives_inverted_bounds():
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['under', 'over'],
        'timestamp': [now - 30, now - 120],
    })
    out = data_processor.classify_freshness(df, now, fresh_seconds=60, stale_seconds=20)
    assert list(out['freshness']) == ['fresh', 'hidden']


def test_classify_freshness_puts_unparseable_timestamps_in_hidden():
    """A row with no usable timestamp must not be presented as current."""
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['good', 'garbage', 'missing'],
        'timestamp': [now - 10, 'not-a-timestamp', None],
    })
    out = data_processor.classify_freshness(df, now)
    assert list(out['freshness']) == ['fresh', 'hidden', 'hidden']
    assert out['age_seconds'].tolist() == [10, 301, 301]


def test_classify_freshness_unparseable_timestamp_hidden_under_custom_bounds():
    now = 1_800_000_000
    df = pd.DataFrame({'vehicle_id': ['garbage'], 'timestamp': ['n/a']})
    out = data_processor.classify_freshness(df, now, fresh_seconds=600, stale_seconds=900)
    assert list(out['freshness']) == ['hidden']


# ── format_duration ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("seconds,expected", [
    (0, '0 seconds'),
    (1, '1 second'),
    (45, '45 seconds'),
    (60, '1 minute'),
    (90, '1 minute 30 seconds'),
    (300, '5 minutes'),
    (900, '15 minutes'),
])
def test_format_duration(seconds, expected):
    assert data_processor.format_duration(seconds) == expected


def test_live_window_ignores_future_dated_rows_from_other_regions(tmp_path, monkeypatch):
    """
    A single future-dated vehicle must not black out regions reporting honestly.

    This is the 2.4.0 bug: the window used to anchor to MAX(timestamp), so one
    row timestamped now+250 shifted the window to [now+190, now+250] and every
    normally-timestamped bus fell outside it.
    """
    import duckdb
    from utils import db as db_mod

    now = int(time.time())
    dbfile = tmp_path / "live.duckdb"
    con = duckdb.connect(str(dbfile))
    con.execute("""
        CREATE TABLE live_buses (
            region VARCHAR, vehicle_id VARCHAR, latitude DOUBLE, longitude DOUBLE,
            bearing DOUBLE, speed DOUBLE, timestamp BIGINT, trip_id VARCHAR,
            route_id VARCHAR, insert_timestamp BIGINT, created_at TIMESTAMP
        )
    """)
    # An honest bus reporting 10 seconds ago...
    con.execute(
        "INSERT INTO live_buses VALUES ('Rapid Bus KL','KL1',3.14,101.68,90,10.0,?, 'T1','T5800',?,current_timestamp)",
        [now - 10, now - 10])
    # ...and a vehicle in ANOTHER region whose clock runs 250s fast.
    con.execute(
        "INSERT INTO live_buses VALUES ('myBAS Melaka','MK1',2.19,102.25,90,5.0,?, 'M1','M100',?,current_timestamp)",
        [now + 250, now])
    con.close()

    monkeypatch.setattr(db_mod, 'DATABASE_NAME', str(dbfile))
    df, metrics, _ = db_mod.get_live_data_optimized()

    regions = set(df['region'])
    assert 'Rapid Bus KL' in regions, "honest bus was excluded by another region's fast clock"
    assert 'myBAS Melaka' in regions
    assert metrics['total'] == 2   # both drawn; the future-dated one clamps to age 0

    # Ages must be measured from wall-clock now, not from the future-dated
    # MAX(timestamp). Re-anchoring to MAX(timestamp) would age the honest bus by
    # a further 250s and demote it to 'stale' — still drawn, so the drawn count
    # alone no longer catches the revert. These two assertions do.
    assert metrics['fresh'] == 2, "an honest bus was aged by another region's fast clock"
    honest_age = int(df[df['vehicle_id'] == 'KL1'].iloc[0]['age_seconds'])
    assert honest_age < 60, f"honest bus aged {honest_age}s — window is not anchored to now"


def test_live_metrics_split_fresh_from_stale(tmp_path, monkeypatch):
    import duckdb
    from utils import db as db_mod

    now = int(time.time())
    dbfile = tmp_path / "tiers.duckdb"
    con = duckdb.connect(str(dbfile))
    con.execute("""
        CREATE TABLE live_buses (
            region VARCHAR, vehicle_id VARCHAR, latitude DOUBLE, longitude DOUBLE,
            bearing DOUBLE, speed DOUBLE, timestamp BIGINT, trip_id VARCHAR,
            route_id VARCHAR, insert_timestamp BIGINT, created_at TIMESTAMP
        )
    """)
    for vid, age in [('f1', 10), ('f2', 30), ('s1', 120), ('h1', 600)]:
        con.execute(
            "INSERT INTO live_buses VALUES ('Rapid Bus KL',?,3.14,101.68,90,10.0,?, 'T1','T5800',?,current_timestamp)",
            [vid, now - age, now - age])
    con.close()

    monkeypatch.setattr(db_mod, 'DATABASE_NAME', str(dbfile))
    df, metrics, _ = db_mod.get_live_data_optimized()

    # 'total' is what the map draws: fresh + stale. It must never read 0 above a
    # map still drawing buses, which is what a fresh-only count did 60 seconds
    # after the last manual refresh.
    assert metrics['total'] == 3
    assert metrics['fresh'] == 2
    assert metrics['stale'] == 1
    assert metrics['fresh'] + metrics['stale'] == metrics['total']
    assert metrics['hidden'] == 1
    assert len(df) == 4             # all four returned; the caller decides what to draw


def test_live_total_counts_every_drawn_vehicle_after_the_fresh_window():
    """
    The manual-refresh case: data fetched 90 seconds ago is stale but still
    drawn. The headline metric must agree with the map, not report zero.
    """
    now = 1_800_000_000
    df = pd.DataFrame({
        'vehicle_id': ['a', 'b', 'c'],
        'region': ['Rapid Bus KL'] * 3,
        'timestamp': [now - 90, now - 91, now - 92],
    })
    classified = data_processor.classify_freshness(df, now)
    drawn = classified[classified['freshness'] != 'hidden']

    assert (classified['freshness'] == 'fresh').sum() == 0   # nothing is fresh...
    assert len(drawn) == 3                                   # ...but all three are drawn


def test_live_data_reports_sync_time_during_outage(tmp_path, monkeypatch):
    """
    An outage (no rows within the window) must still report when data was
    last seen, not go silent with sync_time_str=None.

    Regression guard: the empty-window early return used to fire before
    MAX(timestamp) was ever queried, so a table with only stale rows (older
    than LIVE_HIDDEN_SECONDS) returned an empty frame AND sync_time_str=None -
    indistinguishable from "nothing has ever been ingested".
    """
    import duckdb
    from utils import db as db_mod

    now = int(time.time())
    dbfile = tmp_path / "outage.duckdb"
    con = duckdb.connect(str(dbfile))
    con.execute("""
        CREATE TABLE live_buses (
            region VARCHAR, vehicle_id VARCHAR, latitude DOUBLE, longitude DOUBLE,
            bearing DOUBLE, speed DOUBLE, timestamp BIGINT, trip_id VARCHAR,
            route_id VARCHAR, insert_timestamp BIGINT, created_at TIMESTAMP
        )
    """)
    # Only row is well outside the live window - ingestion has been down.
    stale_ts = now - db_mod.LIVE_HIDDEN_SECONDS - 3600
    con.execute(
        "INSERT INTO live_buses VALUES ('Rapid Bus KL','KL1',3.14,101.68,90,10.0,?, 'T1','T5800',?,current_timestamp)",
        [stale_ts, stale_ts])
    con.close()

    monkeypatch.setattr(db_mod, 'DATABASE_NAME', str(dbfile))
    df, metrics, sync_time_str = db_mod.get_live_data_optimized()

    assert df.empty
    assert metrics == {}
    assert sync_time_str is not None


# ── config fallback ───────────────────────────────────────────────────────────

def _reload_with_config(monkeypatch, module_name, fake_config, names):
    """
    Reload *module_name* with *fake_config* standing in for `config`, snapshot
    *names* off it, then restore the module so later tests see the real state.

    Returns a plain dict — `importlib.reload` mutates the module object in
    place, so the restoring reload would otherwise overwrite what we read.
    """
    import importlib
    import types

    fake = types.ModuleType('config')
    for key, value in fake_config.items():
        setattr(fake, key, value)

    module = importlib.import_module(module_name)
    monkeypatch.setitem(sys.modules, 'config', fake)
    try:
        reloaded = importlib.reload(module)
        return {name: getattr(reloaded, name) for name in names}
    finally:
        monkeypatch.undo()
        importlib.reload(module)


def test_db_config_knobs_fall_back_individually(monkeypatch):
    """
    A config.py written before the LIVE_* knobs existed must keep every setting
    it *does* define. These names used to sit in one all-or-nothing tuple
    import, so a config missing them raised ImportError and silently reverted
    DATABASE_NAME, TIMEZONE and friends to the hardcoded defaults.
    """
    names = [
        'DATABASE_NAME', 'DATABASE_TABLE', 'TIMEZONE', 'UTC_OFFSET_HOURS',
        'DATA_RETENTION_DAYS', 'LIVE_FRESH_SECONDS', 'LIVE_STALE_SECONDS',
        'LIVE_HIDDEN_SECONDS',
    ]
    values = _reload_with_config(monkeypatch, 'utils.db', {
        'DATABASE_NAME': 'custom_transit.duckdb',
        'DATABASE_TABLE': 'custom_buses',
        'TIMEZONE': 'Asia/Tokyo',
        'UTC_OFFSET_HOURS': 9,
        'DATA_RETENTION_DAYS': 30,
        # No LIVE_FRESH_SECONDS / LIVE_STALE_SECONDS / LIVE_HIDDEN_SECONDS.
    }, names)

    # The user's settings survive...
    assert values['DATABASE_NAME'] == 'custom_transit.duckdb'
    assert values['DATABASE_TABLE'] == 'custom_buses'
    assert values['TIMEZONE'] == 'Asia/Tokyo'
    assert values['UTC_OFFSET_HOURS'] == 9
    assert values['DATA_RETENTION_DAYS'] == 30
    # ...and only the genuinely missing knobs fall back.
    assert values['LIVE_FRESH_SECONDS'] == 60
    assert values['LIVE_STALE_SECONDS'] == 300
    assert values['LIVE_HIDDEN_SECONDS'] == 900


def test_db_config_knobs_are_honoured_when_present(monkeypatch):
    values = _reload_with_config(monkeypatch, 'utils.db', {
        'DATABASE_NAME': 'custom_transit.duckdb',
        'DATABASE_TABLE': 'custom_buses',
        'TIMEZONE': 'Asia/Tokyo',
        'UTC_OFFSET_HOURS': 9,
        'DATA_RETENTION_DAYS': 30,
        'LIVE_FRESH_SECONDS': 45,
        'LIVE_STALE_SECONDS': 200,
        'LIVE_HIDDEN_SECONDS': 800,
    }, ['LIVE_FRESH_SECONDS', 'LIVE_STALE_SECONDS', 'LIVE_HIDDEN_SECONDS'])

    assert values['LIVE_FRESH_SECONDS'] == 45
    assert values['LIVE_STALE_SECONDS'] == 200
    assert values['LIVE_HIDDEN_SECONDS'] == 800


# ── outage messaging (page level) ─────────────────────────────────────────────

class _SessionState(dict):
    """Minimal stand-in for st.session_state: attribute *and* item access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


class PageTestReachedNetwork(BaseException):
    """
    Raised by the network guard in `_stub_streamlit` when a page test reaches
    OpenRouteService. Deliberately derives from BaseException, not Exception:
    `walking._routed_distances` wraps its request in a bare `except Exception`,
    which would silently swallow a plain AssertionError (itself an Exception
    subclass) and return the fallback distance — the guard would then be inert,
    and a future regression that reintroduced a real network call would pass
    the test suite quietly. BaseException is never caught by that handler, so
    a violation surfaces as a loud test failure instead.
    """


def _stub_streamlit(monkeypatch, module):
    """Replace a page module's `st` with a recorder and stop it fetching."""
    st_stub = MagicMock()
    st_stub.session_state = _SessionState(auto_refresh=False)
    st_stub.button.return_value = False        # no refresh click
    # Default to "nothing selected". Without this, pydeck_chart returns a bare
    # MagicMock, the selection parsing yields a MagicMock vehicle id, and that
    # reaches `df_map['vehicle_id'] == picked`. Whether that is survivable
    # depends on the pandas string dtype: object dtype quietly returns False,
    # but an Arrow-backed string column raises NotImplementedError. Locally
    # pandas infers object and the tests passed; on CI it infers Arrow and three
    # of them failed. Tests that want a selection supply their own payload.
    st_stub.pydeck_chart.return_value = SimpleNamespace(
        selection=SimpleNamespace(objects={})
    )
    monkeypatch.setattr(module, 'st', st_stub)
    monkeypatch.setattr(module, 'fetch_and_store_transit_data', lambda *a, **k: None)
    # No test may start the real fetch thread. It would reach
    # api.data.gov.my and write to the developer's database, and it would
    # outlive the test that spawned it. Tests that care about the tick rule
    # patch `start` again with a counter of their own.
    monkeypatch.setattr(background_fetch, 'start', lambda: False)
    monkeypatch.setattr(background_fetch, 'is_running', lambda: False)

    # ── the page must never reach OpenRouteService from a test ──────────────
    #
    # Two separate failures were live here. Locally, src/config.py holds a real
    # ORS_API_KEY, so every page test that rendered a walk time spent the
    # owner's quota against the real endpoint — a full run made 39 requests
    # carrying the real key. On CI there is no config.py, so _ors_api_key()
    # fell through to st.secrets, and `st` being a MagicMock made that return a
    # truthy Mock; requests then raised InvalidHeader, which _routed_distances'
    # broad except swallowed. CI passed for an accidental reason and only ever
    # exercised the fallback — exactly the local/CI divergence this helper
    # exists to prevent.
    #
    # So: pin the key to None on both paths, and make any attempt to reach the
    # network a loud failure rather than a silently-swallowed one. A test that
    # genuinely wants the routed path must opt in by overriding this mock with
    # a canned response of its own.
    #
    # The exception raised here must not be a plain AssertionError: that is an
    # Exception subclass, and `walking._routed_distances` wraps the request in
    # a bare `except Exception`, which would catch it and return the fallback
    # distance in silence — the guard would fire but nothing would ever see it
    # fire. PageTestReachedNetwork derives from BaseException instead, so it
    # passes straight through that handler.
    st_stub.secrets = {}
    if hasattr(module, '_config'):
        monkeypatch.setattr(module, '_config', None)
    monkeypatch.setattr(
        walking.requests, 'post',
        lambda *a, **k: (_ for _ in ()).throw(
            PageTestReachedNetwork('page test reached the network')))
    # Cached routed distances would otherwise leak between tests and let one
    # test's canned response answer another's lookup.
    walking._clear_cache()
    return st_stub


def _texts(mock_method):
    return " ".join(str(call.args[0]) for call in mock_method.call_args_list if call.args)


def test_live_map_names_last_seen_time_during_an_outage(monkeypatch):
    """
    The empty-frame early return used to fire *before* the sync banner, so a
    >15-minute outage rendered "No data. Click 'Refresh Data' to fetch." —
    exactly the string that reads as "nothing was ever ingested".
    """
    from app_pages import live_map

    st_stub = _stub_streamlit(monkeypatch, live_map)
    monkeypatch.setattr(
        live_map.db, 'get_live_data_optimized',
        lambda *a, **k: (pd.DataFrame(), {}, '30 Jul 2026 21:04:11'),
    )

    live_map.show()

    said = _texts(st_stub.warning) + _texts(st_stub.info)
    assert '30 Jul 2026 21:04:11' in said, "outage banner did not say when data was last seen"
    assert "Click 'Refresh Data' to fetch" not in said
    # Wording is derived from LIVE_HIDDEN_SECONDS, not hardcoded.
    assert data_processor.format_duration(live_map.LIVE_HIDDEN_SECONDS) in said


def test_live_map_still_says_no_data_when_nothing_was_ever_ingested(monkeypatch):
    from app_pages import live_map

    st_stub = _stub_streamlit(monkeypatch, live_map)
    monkeypatch.setattr(
        live_map.db, 'get_live_data_optimized', lambda *a, **k: (None, {}, None))

    live_map.show()

    assert "Click 'Refresh Data' to fetch" in _texts(st_stub.info)
    assert _texts(st_stub.warning) == ""


def test_analytics_names_last_seen_time_during_an_outage(monkeypatch):
    from app_pages import analytics

    st_stub = _stub_streamlit(monkeypatch, analytics)
    monkeypatch.setattr(
        analytics.db, 'get_live_data_optimized',
        lambda *a, **k: (pd.DataFrame(), {}, '30 Jul 2026 21:04:11'),
    )
    monkeypatch.setattr(
        analytics.db, 'get_historical_data', lambda *a, **k: (pd.DataFrame(), {}, None))

    analytics.show()

    said = _texts(st_stub.warning) + _texts(st_stub.info)
    assert '30 Jul 2026 21:04:11' in said
    assert 'No data available. Please refresh.' not in said
    assert data_processor.format_duration(analytics.LIVE_HIDDEN_SECONDS) in said


def test_analytics_still_says_no_data_when_nothing_was_ever_ingested(monkeypatch):
    from app_pages import analytics

    st_stub = _stub_streamlit(monkeypatch, analytics)
    monkeypatch.setattr(
        analytics.db, 'get_live_data_optimized', lambda *a, **k: (None, {}, None))
    monkeypatch.setattr(
        analytics.db, 'get_historical_data', lambda *a, **k: (None, {}, None))

    analytics.show()

    assert 'No data available. Please refresh.' in _texts(st_stub.info)


def test_sync_time_is_never_in_the_future(tmp_path, monkeypatch):
    """
    Ingestion accepts timestamps up to DATA_FUTURE_TOLERANCE (300s) ahead, so
    MAX(timestamp) can sit in the future. "Data updated: <future time>" is never
    a true statement — the banner is clamped to now.
    """
    import duckdb
    from utils import db as db_mod

    now = int(time.time())
    dbfile = tmp_path / "future.duckdb"
    con = duckdb.connect(str(dbfile))
    con.execute("""
        CREATE TABLE live_buses (
            region VARCHAR, vehicle_id VARCHAR, latitude DOUBLE, longitude DOUBLE,
            bearing DOUBLE, speed DOUBLE, timestamp BIGINT, trip_id VARCHAR,
            route_id VARCHAR, insert_timestamp BIGINT, created_at TIMESTAMP
        )
    """)
    con.execute(
        "INSERT INTO live_buses VALUES ('myBAS Melaka','MK1',2.19,102.25,90,5.0,?, 'M1','M100',?,current_timestamp)",
        [now + 280, now])
    con.close()

    monkeypatch.setattr(db_mod, 'DATABASE_NAME', str(dbfile))
    # Freeze the clock. The code reads time.time() itself when clamping, so
    # asserting exact equality against a `now` captured here races the wall
    # clock and fails by exactly one second whenever it ticks in between.
    monkeypatch.setattr(db_mod.time, 'time', lambda: float(now))

    _, _, live_sync = db_mod.get_live_data_optimized()
    _, _, historical_sync = db_mod.get_historical_data()

    assert live_sync == db_mod._format_sync_time(now)
    assert live_sync != db_mod._format_sync_time(now + 280)
    assert historical_sync == db_mod._format_sync_time(now)


# ---------------------------------------------------------------------------
# Route lookup — telling "not a route here" from "here but not running"
# ---------------------------------------------------------------------------

def _make_static_zip(tmp_path, name, routes_rows):
    """Build a minimal GTFS static ZIP containing just routes.txt."""
    import zipfile
    p = tmp_path / f"{name}.zip"
    header = "route_id,route_short_name,route_long_name\n"
    body = "".join(f"{r[0]},{r[1]},{r[2]}\n" for r in routes_rows)
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('routes.txt', header + body)
    return str(p)


def test_region_has_route_matches_short_name_case_insensitively(tmp_path, monkeypatch):
    from utils import gtfs_static
    z = _make_static_zip(tmp_path, 'kl', [('T5800', 'T580', 'Awan Besar ~ TPM')])
    monkeypatch.setattr(gtfs_static, '_load_zip',
                        lambda slug: __import__('zipfile').ZipFile(z))
    assert gtfs_static.region_has_route('anything', 't580') is True
    assert gtfs_static.region_has_route('anything', 'T580') is True
    assert gtfs_static.region_has_route('anything', 'U6000') is False


def test_region_has_route_is_false_on_error(monkeypatch):
    from utils import gtfs_static
    def boom(slug):
        raise OSError("network down")
    monkeypatch.setattr(gtfs_static, '_load_zip', boom)
    assert gtfs_static.region_has_route('anything', 'T580') is False


def test_find_regions_for_route_reports_where_it_lives(tmp_path, monkeypatch):
    import zipfile
    from utils import gtfs_static
    kl = _make_static_zip(tmp_path, 'kl', [('T5800', 'T580', 'Awan Besar ~ TPM')])
    kch = _make_static_zip(tmp_path, 'kch', [('K1', 'K1', 'Kuching Line')])

    def fake_load(slug):
        return zipfile.ZipFile(kl if 'rapid-bus-kl' in slug else kch)

    monkeypatch.setattr(gtfs_static, '_load_zip', fake_load)
    monkeypatch.setattr(gtfs_static, 'STATIC_API_SOURCES', {
        'Rapid Bus KL': 'prasarana?category=rapid-bus-kl',
        'myBAS Kuching': 'mybas-kuching',
    })
    gtfs_static._ROUTE_REGION_INDEX.clear()

    assert gtfs_static.find_regions_for_route('t580') == ['Rapid Bus KL']
    assert gtfs_static.find_regions_for_route('K1') == ['myBAS Kuching']
    assert gtfs_static.find_regions_for_route('ZZZ') == []


def test_find_regions_for_route_ignores_a_failing_agency(tmp_path, monkeypatch):
    import zipfile
    from utils import gtfs_static
    kl = _make_static_zip(tmp_path, 'kl', [('T5800', 'T580', 'Awan Besar ~ TPM')])

    def fake_load(slug):
        if 'rapid-bus-kl' in slug:
            return zipfile.ZipFile(kl)
        raise OSError("feed withdrawn")

    monkeypatch.setattr(gtfs_static, '_load_zip', fake_load)
    monkeypatch.setattr(gtfs_static, 'STATIC_API_SOURCES', {
        'Rapid Bus KL': 'prasarana?category=rapid-bus-kl',
        'Rapid Bus Kuantan': 'prasarana?category=rapid-bus-kuantan',
    })
    gtfs_static._ROUTE_REGION_INDEX.clear()
    # one dead agency must not sink the whole lookup
    assert gtfs_static.find_regions_for_route('T580') == ['Rapid Bus KL']


# ---------------------------------------------------------------------------
# Region change must not silently wipe an active route search
# ---------------------------------------------------------------------------

def _live_map_with_one_region(monkeypatch, selectbox_returns):
    """Drive live_map.show() far enough to exercise the region/search block."""
    from app_pages import live_map

    st_stub = _stub_streamlit(monkeypatch, live_map)
    st_stub.selectbox.return_value = selectbox_returns
    st_stub.text_input.return_value = ''
    st_stub.session_state['map_theme'] = 'dark'
    st_stub.session_state['getting_location'] = False
    # st.columns(n) / st.columns([w, ...]) must unpack to that many objects.
    st_stub.columns.side_effect = lambda spec, *a, **k: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    df = pd.DataFrame({
        'region': ['Rapid Bus KL'], 'vehicle_id': ['V1'],
        'latitude': [3.14], 'longitude': [101.68], 'bearing': [90.0],
        'speed': [10.0], 'timestamp': [int(time.time())],
        'trip_id': ['T1'], 'route_id': ['T5800'],
        'freshness': ['fresh'], 'age_seconds': [5],
    })
    monkeypatch.setattr(
        live_map.db, 'get_live_data_optimized',
        lambda *a, **k: (df, {'total': 1, 'stale': 0, 'hidden': 0,
                              'regions': 1, 'busiest': 'Rapid Bus KL'}, 'now'),
    )
    return live_map, st_stub


def test_route_search_survives_a_rerun_without_a_region_change(monkeypatch):
    """
    The search used to be cleared whenever the selectbox value disagreed with a
    parallel `selected_region` mirror. Any transient drift between those two
    wiped an active search for one render — the reported "first auto-refresh
    shows every bus" symptom.
    """
    live_map, st_stub = _live_map_with_one_region(monkeypatch, 'Rapid Bus KL')
    # Mirror deliberately disagrees with the widget, as it did in the wild.
    st_stub.session_state['selected_region'] = 'myBAS Kuching'
    st_stub.session_state['_region_for_search'] = 'Rapid Bus KL'
    st_stub.session_state['route_search_live_map'] = 't580'

    live_map.show()

    assert st_stub.session_state.get('route_search_live_map') == 't580', \
        "an active search was wiped even though the user never changed region"


def test_route_search_is_cleared_on_a_real_region_change(monkeypatch):
    """The clearing behaviour itself must survive: a genuine switch still resets."""
    live_map, st_stub = _live_map_with_one_region(monkeypatch, 'myBAS Johor')
    st_stub.session_state['selected_region'] = 'Rapid Bus KL'
    st_stub.session_state['_region_for_search'] = 'Rapid Bus KL'
    st_stub.session_state['route_search_live_map'] = 't580'

    live_map.show()

    assert 'route_search_live_map' not in st_stub.session_state, \
        "switching region should drop a search that belonged to the old region"


def test_arrivals_panel_prompts_for_location_when_unknown(monkeypatch):
    live_map, st_stub = _live_map_with_one_region(monkeypatch, 'Rapid Bus KL')
    st_stub.session_state['selected_region'] = 'Rapid Bus KL'
    st_stub.session_state['_region_for_search'] = 'Rapid Bus KL'
    st_stub.session_state.pop('user_location', None)

    live_map.show()

    said = _texts(st_stub.info) + _texts(st_stub.caption)
    assert 'Locate Me' in said


def test_arrivals_panel_reports_when_no_stops_are_nearby(monkeypatch):
    live_map, st_stub = _live_map_with_one_region(monkeypatch, 'Rapid Bus KL')
    st_stub.session_state['selected_region'] = 'Rapid Bus KL'
    st_stub.session_state['_region_for_search'] = 'Rapid Bus KL'
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [])

    live_map.show()

    said = _texts(st_stub.info) + _texts(st_stub.caption)
    assert 'No stops found' in said


# ── tap-a-bus: main map selection (secondary "arrivals" view) ────────────────
#
# A bare MagicMock() — the generic st_stub used by every other test above —
# never exercises this feature's real code path: `MagicMock().get(...)`
# returns another (truthy) Mock rather than raising, so `picked` becomes a
# Mock and the `if picked:` branch is entered with a nonsense value, not the
# `except` clause. These tests instead stub a realistic `.selection.objects`
# payload (a plain dict, via SimpleNamespace) so the try branch's actual
# parsing and rendering logic is exercised, both when something is picked and
# when nothing is.

def _live_map_with_selection(monkeypatch, selection_payload, df=None):
    """Like _live_map_with_one_region, but wires st.pydeck_chart to return a
    caller-supplied selection object, and hands back the vehicle's fixed
    timestamp so a test can build a matching timetable for it.

    Freezes the clock live_map sees (monkeypatches live_map.time.time) so the
    frozen `now` returned here and show()'s own `int(time.time())` calls read
    identically. Without this, a margin between the two reads is
    load-bearing — the same off-by-a-tick shape already fixed elsewhere in
    this file for test_sync_time_is_never_in_the_future.

    Pass `df` to control exactly which vehicles are in df_live/df_map (e.g.
    to build a vehicle that is live but filtered out of the drawn frame);
    the default is a single fresh vehicle, 'V1'.
    """
    from app_pages import live_map

    st_stub = _stub_streamlit(monkeypatch, live_map)
    st_stub.selectbox.return_value = 'Rapid Bus KL'
    st_stub.text_input.return_value = ''
    st_stub.session_state['map_theme'] = 'dark'
    st_stub.session_state['getting_location'] = False
    st_stub.session_state['selected_region'] = 'Rapid Bus KL'
    st_stub.session_state['_region_for_search'] = 'Rapid Bus KL'
    st_stub.columns.side_effect = lambda spec, *a, **k: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    st_stub.pydeck_chart.return_value = selection_payload

    now = int(time.time())
    monkeypatch.setattr(live_map.time, 'time', lambda: float(now))

    if df is None:
        df = pd.DataFrame({
            'region': ['Rapid Bus KL'], 'vehicle_id': ['V1'],
            'latitude': [3.14], 'longitude': [101.68], 'bearing': [90.0],
            'speed': [10.0], 'timestamp': [now],
            'trip_id': ['T1'], 'route_id': ['T5800'],
            'freshness': ['fresh'], 'age_seconds': [5],
        })
    monkeypatch.setattr(
        live_map.db, 'get_live_data_optimized',
        lambda *a, **k: (df, {'total': len(df), 'stale': 0, 'hidden': 0,
                              'regions': 1, 'busiest': 'Rapid Bus KL'}, 'now'),
    )
    return live_map, st_stub, now


def test_tapped_vehicle_with_realistic_selection_renders_its_arrival(monkeypatch):
    """Success path: a real dict payload (not a MagicMock) resolves 'V1',
    and its timetable puts a stop 5 minutes ahead within walking distance —
    the panel must render that arrival."""
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}

    offset_seconds = int(live_map.UTC_OFFSET_HOURS) * 3600
    local_seconds_of_day = (now + offset_seconds) % 86400
    stops = [
        {'stop_id': 'S0', 'stop_name': 'Origin Stop', 'stop_lat': 3.14, 'stop_lon': 101.68,
         'arrival_seconds': local_seconds_of_day},
        {'stop_id': 'S1', 'stop_name': 'Nearby Stop', 'stop_lat': 3.1401, 'stop_lon': 101.6801,
         'arrival_seconds': local_seconds_of_day + 300},
    ]
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: stops)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: 'Terminal X')

    live_map.show()

    said = _texts(st_stub.info)
    assert 'Nearby Stop' in said, f"expected the tapped vehicle's arrival at Nearby Stop, got: {said!r}"
    # Deliberately not st.success: a green confirmation box reads as certainty,
    # and this is an estimate that can rest on a position minutes old.
    assert 'Nearby Stop' not in _texts(st_stub.success), \
        "an estimate must not be rendered as a green confidence box"


def test_the_tapped_arrival_carries_the_same_caveats_as_the_stop_panel(monkeypatch):
    """
    Both panels show the same estimate for the same bus, so they must qualify
    it identically. The secondary used to render a bare green box: no delay, no
    position age, no one-stop caveat -- so a four-minute-stale position read as
    an unqualified confident "~6 min". df_map admits positions up to 300s old.
    """
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]})
    )
    now = int(time.time())
    # 240s old: still drawn (under LIVE_STALE_SECONDS) but well past fresh.
    df = pd.DataFrame({
        'region': ['Rapid Bus KL'], 'vehicle_id': ['V1'],
        'latitude': [3.14], 'longitude': [101.68], 'bearing': [90.0],
        'speed': [10.0], 'timestamp': [now - 240], 'trip_id': ['T1'],
        'route_id': ['T5800'], 'freshness': ['stale'], 'age_seconds': [240],
    })
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection, df=df)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}

    offset_seconds = int(live_map.UTC_OFFSET_HOURS) * 3600
    local_seconds_of_day = (now + offset_seconds) % 86400
    stops = [
        {'stop_id': 'S0', 'stop_name': 'Origin Stop', 'stop_lat': 3.14, 'stop_lon': 101.68,
         'arrival_seconds': local_seconds_of_day - 240},
        {'stop_id': 'S1', 'stop_name': 'Nearby Stop', 'stop_lat': 3.1401, 'stop_lon': 101.6801,
         'arrival_seconds': local_seconds_of_day + 300},
    ]
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: stops)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: 'Terminal X')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: False)

    live_map.show()

    said = _texts(st_stub.info) + _texts(st_stub.caption)
    assert 'last reported 4 min ago' in said, \
        f"a stale position must be shown with its age: {said!r}"
    assert 'accurate to about one stop' in said, \
        f"the accuracy caveat is missing from the tapped-bus panel: {said!r}"


def test_a_frequency_based_trip_shows_no_lateness_clause(monkeypatch):
    """
    99% of the Rapid Bus KL feed runs to a headway, so no lateness is knowable
    and arrivals_for_stops reports delay_seconds as None. The renderer guarded
    only on `>= 60`, which raises TypeError on None -- the panel must instead
    say nothing at all about lateness.

    This assertion was vacuous for a while: the tapped panel had lost the
    ability to state a lateness at all, so it passed unconditionally and would
    have kept passing with the frequency suppression deleted outright. The
    fixture is built to bite -- the vehicle is a measured +3,600s against the
    template, which the same panel renders as "60 min late" the moment the
    suppression stops working (verified by flipping is_frequency_based to
    False, which fails this test).
    """
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}

    offset_seconds = int(live_map.UTC_OFFSET_HOURS) * 3600
    local_seconds_of_day = (now + offset_seconds) % 86400
    stops = [
        {'stop_id': 'S0', 'stop_name': 'Origin Stop', 'stop_lat': 3.14, 'stop_lon': 101.68,
         # An hour "late" against the template -- pure fiction on a headway trip.
         'arrival_seconds': local_seconds_of_day - 3600},
        {'stop_id': 'S1', 'stop_name': 'Nearby Stop', 'stop_lat': 3.1401, 'stop_lon': 101.6801,
         'arrival_seconds': local_seconds_of_day - 3600 + 300},
    ]
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: stops)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: 'Terminal X')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: True)

    live_map.show()   # must not raise on a None delay

    said = _texts(st_stub.info)
    assert 'Nearby Stop' in said, f"the arrival itself must still render: {said!r}"
    assert 'late' not in said, f"no lateness may be claimed on a headway trip: {said!r}"
    # Not knowable is not zero and not "on time" -- it is silence.
    assert '0 min late' not in said, said
    assert 'on time' not in said, said


def test_tapped_vehicle_with_no_selection_renders_nothing(monkeypatch):
    """A realistic but empty payload (`objects` is an empty dict): nothing was
    tapped, so the panel must render nothing and must not raise."""
    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}

    live_map.show()   # must not raise

    said = _texts(st_stub.success) + _texts(st_stub.info)
    assert 'min walk' not in said, "no vehicle was tapped, so no arrival should render"


def test_a_selection_survives_a_render_that_reports_none(monkeypatch):
    """
    A tap is reported for exactly one render; every auto-refresh afterwards
    hands back an empty selection. Without persistence the panel appeared for a
    single frame and then vanished -- the module's own comment claimed it was
    re-resolved from the current frame each render, and it was not.
    """
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    # As if a previous render had recorded the tap.
    st_stub.session_state['selected_vehicle_id'] = 'V1'

    offset_seconds = int(live_map.UTC_OFFSET_HOURS) * 3600
    local_seconds_of_day = (now + offset_seconds) % 86400
    stops = [
        {'stop_id': 'S0', 'stop_name': 'Origin Stop', 'stop_lat': 3.14, 'stop_lon': 101.68,
         'arrival_seconds': local_seconds_of_day},
        {'stop_id': 'S1', 'stop_name': 'Nearby Stop', 'stop_lat': 3.1401, 'stop_lon': 101.6801,
         'arrival_seconds': local_seconds_of_day + 300},
    ]
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: stops)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: 'Terminal X')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: False)

    live_map.show()

    assert 'Nearby Stop' in _texts(st_stub.info), \
        "the selection was dropped on the first render that reported none"


def test_a_tap_is_recorded_so_the_next_render_can_reuse_it(monkeypatch):
    """The companion half: a fresh tap must be written to session state."""
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)

    live_map.show()

    assert st_stub.session_state.get('selected_vehicle_id') == 'V1'


def test_every_layer_in_the_selectable_deck_declares_a_stable_id(monkeypatch):
    """
    Streamlit requires every layer to declare an `id` once on_select is active,
    and a pdk.Layer built without one is assigned a fresh uuid4 on each
    construction. st.pydeck_chart hashes the whole deck spec into the chart's
    widget id, so an unnamed layer rewrites that id on every render and the
    selection is written under one id and read back under another -- tap-a-bus
    could never fire. Only the vehicles layer used to carry an id.

    Two renders are driven and the ids compared, so this fails for a layer that
    is merely *named by accident* as well as for one that is unnamed.
    """
    def ids_for_one_render():
        selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
        live_map, st_stub, _ = _live_map_with_selection(monkeypatch, selection)
        # A user location adds the marker and accuracy-circle layers too.
        st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
        monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [])
        live_map.show()
        deck = st_stub.pydeck_chart.call_args_list[0].args[0]
        return [layer.id for layer in deck.layers]

    first = ids_for_one_render()
    second = ids_for_one_render()

    assert len(first) == 4, f"expected vehicles, arrows, marker and accuracy: {first}"
    assert first == second, f"layer ids churn between renders: {first} vs {second}"
    assert 'vehicles' in first
    for layer_id in first:
        assert '-' not in layer_id or not len(layer_id) == 36, \
            f"layer {layer_id!r} looks like a generated uuid4, not a declared id"


def test_the_deck_data_excludes_columns_recomputed_every_render(monkeypatch):
    """
    Stable ids alone are not enough: the deck's *data* is hashed into the
    widget id too, so a column recomputed from the wall clock on every render
    (a relative "12s ago" freshness string) churns the id just as effectively
    as an unnamed layer.
    """
    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _ = _live_map_with_selection(monkeypatch, selection)
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [])

    live_map.show()

    deck = st_stub.pydeck_chart.call_args_list[0].args[0]
    vehicles = next(layer for layer in deck.layers if layer.id == 'vehicles')
    # pydeck normalises a DataFrame into a list of per-row records.
    columns = set(vehicles.data[0])

    assert 'freshness_display' not in columns, \
        "a per-second relative age is back in the deck data"
    assert 'age_seconds' not in columns and 'timestamp' not in columns, \
        f"the deck carries more than the layer draws: {sorted(columns)}"
    # It still carries what the tooltip actually shows — now pre-rendered into
    # one field, per row, shared with every other layer's tooltip.
    assert {'vehicle_id', 'tip_text'} <= columns
    tip_text = vehicles.data[0]['tip_text']
    for label in ('Vehicle', 'Route', 'Speed', 'Bearing', 'Last reported'):
        assert label in tip_text
    assert '\n' in tip_text, "the five facts must be separated by real newlines"


def test_tapped_vehicle_filtered_out_is_distinguished_from_genuinely_gone(monkeypatch):
    """
    Pins the fix for a real bug: df_map has already been through the region,
    freshness, and route-search filters, so a selection that survives from
    before one of those changed could point at a vehicle that is still live
    in df_live but simply not in the currently-drawn df_map. That must never
    read as "no longer reporting" — a live bus is not gone.

    Two vehicles are seeded: V-HIDDEN (freshness='hidden', filtered out of
    df_map but present in df_live) and V-FRESH (keeps df_map non-empty past
    the early-return). Tapping V-HIDDEN must produce the "still reporting,
    but is not shown" wording and must NOT say "no longer reporting".
    Tapping V-GONE (absent from both frames) is the companion case, and must
    say "no longer reporting" — asserting only one half of this pair could
    pass for the wrong reason (e.g. a message that never mentions "no longer
    reporting" for anyone).
    """
    now = int(time.time())
    df = pd.DataFrame({
        'region': ['Rapid Bus KL', 'Rapid Bus KL'],
        'vehicle_id': ['V-HIDDEN', 'V-FRESH'],
        'latitude': [3.14, 3.15], 'longitude': [101.68, 101.69],
        'bearing': [90.0, 90.0], 'speed': [10.0, 10.0],
        'timestamp': [now, now], 'trip_id': ['T1', 'T2'],
        'route_id': ['T5800', 'T5801'],
        'freshness': ['hidden', 'fresh'], 'age_seconds': [5000, 5],
    })

    # -- tap the filtered-but-live vehicle --
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V-HIDDEN"}]})
    )
    live_map, st_stub, _ = _live_map_with_selection(monkeypatch, selection, df=df)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}

    live_map.show()

    said = _texts(st_stub.info)
    assert 'still reporting, but is not shown in the current view' in said, (
        f"expected the filtered-but-live wording, got: {said!r}"
    )
    assert 'no longer reporting' not in said, (
        f"a live vehicle must never be reported as no longer reporting: {said!r}"
    )


def test_tapped_vehicle_absent_from_both_frames_says_no_longer_reporting(monkeypatch):
    """Companion to the test above: a vehicle_id in neither df_live nor
    df_map is genuinely gone, and must still say so."""
    now = int(time.time())
    df = pd.DataFrame({
        'region': ['Rapid Bus KL'], 'vehicle_id': ['V-FRESH'],
        'latitude': [3.15], 'longitude': [101.69], 'bearing': [90.0],
        'speed': [10.0], 'timestamp': [now], 'trip_id': ['T2'],
        'route_id': ['T5801'], 'freshness': ['fresh'], 'age_seconds': [5],
    })

    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V-GONE"}]})
    )
    live_map, st_stub, _ = _live_map_with_selection(monkeypatch, selection, df=df)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}

    live_map.show()

    said = _texts(st_stub.info)
    assert 'is no longer reporting' in said, f"expected the gone-vehicle wording, got: {said!r}"


def test_clearing_a_bus_selection_survives_a_repeated_payload(monkeypatch):
    """
    Clearing must stick even when Streamlit hands back the same selection.

    The deck's selection lives in widget state keyed by the element id, and that
    id is derived from the deck spec. When nothing on the map changed between
    renders the id is unchanged, so the *same* payload is returned on the very
    next run — and the code used to re-adopt it immediately, rewriting the
    session key it had just popped. The clear looked ignored.
    """
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)

    # First render: the tap registers.
    live_map.show()
    assert st_stub.session_state.get('selected_vehicle_id') == 'V1'

    # The user clicks "Clear bus selection".
    st_stub.button.return_value = True
    live_map.show()
    st_stub.button.return_value = False

    # Next render still receives the identical payload naming V1, because the
    # deck spec did not change. It must NOT come back.
    live_map.show()
    assert st_stub.session_state.get('selected_vehicle_id') is None, \
        "a cleared selection was re-adopted from the repeated payload"


def test_a_cleared_bus_can_be_selected_again(monkeypatch):
    """
    Clearing must not blacklist a bus. Once the stale payload has been shrugged
    off, tapping the same vehicle again has to work — otherwise the fix for the
    sticky selection quietly costs the user the ability to reselect.
    """
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)

    live_map.show()                       # tap V1
    st_stub.button.return_value = True
    live_map.show()                       # clear it
    st_stub.button.return_value = False
    live_map.show()                       # stale payload shrugged off
    assert st_stub.session_state.get('selected_vehicle_id') is None

    live_map.show()                       # user taps V1 again
    assert st_stub.session_state.get('selected_vehicle_id') == 'V1', \
        "clearing a bus permanently blocked reselecting it"


# ── tap-a-stop: clearing must stick, and must not blacklist the stop ─────────
#
# Cloned from the bus-clearing pair above. The stop path originally bumped
# deck_generation on clear but had no belt-and-braces ignore for a repeated
# payload -- exactly the half-mechanism the bus path's own comment warns
# against ("Bumping the widget key should be enough ... but the payload is
# Streamlit's to deliver").
#
# button.return_value = True presses every button, including "🗑️ Clear
# Location" (rendered because these tests need user_location set), which
# raises on `del st.session_state.user_location`. Press only the button
# under test via side_effect instead.

def test_clearing_a_stop_selection_survives_a_repeated_payload(monkeypatch):
    """
    Companion to test_clearing_a_bus_selection_survives_a_repeated_payload:
    the deck's selection lives in widget state keyed by the element id, and
    that id is derived from the deck spec. When nothing on the map changed
    between renders the id is unchanged, so the *same* payload is returned on
    the very next run -- and the stop path re-adopted it immediately,
    rewriting the session key it had just popped. The clear looked ignored.
    """
    stop = {'stop_id': 'S1', 'stop_name': 'Tapped Stop',
            'stop_lat': 3.1401, 'stop_lon': 101.6801, 'distance_m': 50.0}
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"nearby-stops": [{"stop_id": "S1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [stop])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])

    # First render: the tap registers.
    live_map.show()
    assert st_stub.session_state.get('selected_stop_id') == 'S1'

    # The user clicks "Clear stop selection".
    st_stub.button.side_effect = lambda label, *a, **k: label == "Clear stop selection"
    live_map.show()
    st_stub.button.side_effect = None
    st_stub.button.return_value = False

    # Next render still receives the identical payload naming S1, because the
    # deck spec did not change. It must NOT come back.
    live_map.show()
    assert st_stub.session_state.get('selected_stop_id') is None, \
        "a cleared stop selection was re-adopted from the repeated payload"


def test_a_cleared_stop_can_be_selected_again(monkeypatch):
    """
    Clearing must not blacklist a stop. Once the stale payload has been
    shrugged off, tapping the same stop again has to work -- otherwise the fix
    for the repeated-payload bug would quietly cost the user the ability to
    reselect it.
    """
    stop = {'stop_id': 'S1', 'stop_name': 'Tapped Stop',
            'stop_lat': 3.1401, 'stop_lon': 101.6801, 'distance_m': 50.0}
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"nearby-stops": [{"stop_id": "S1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [stop])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])

    live_map.show()                       # tap S1
    st_stub.button.side_effect = lambda label, *a, **k: label == "Clear stop selection"
    live_map.show()                       # clear it
    st_stub.button.side_effect = None
    st_stub.button.return_value = False
    live_map.show()                       # stale payload shrugged off
    assert st_stub.session_state.get('selected_stop_id') is None

    live_map.show()                       # user taps S1 again
    assert st_stub.session_state.get('selected_stop_id') == 'S1', \
        "clearing a stop permanently blocked reselecting it"


def test_a_served_stop_beyond_the_nearest_few_is_still_shown(monkeypatch):
    """
    Stops are chosen by usefulness, not raw proximity.

    Reported from Bukit Jalil: 15 stops sat within 800 m, the panel evaluated
    only the 5 nearest, and the one stop with a bus inbound ranked 9th. Every
    stop on screen said "nothing inbound" while a bus was on its way to one the
    panel never looked at.
    """
    from app_pages import live_map

    st_stub = _stub_streamlit(monkeypatch, live_map)
    st_stub.selectbox.return_value = 'Rapid Bus KL'
    st_stub.text_input.return_value = ''
    st_stub.session_state.update({
        'map_theme': 'dark', 'getting_location': False,
        'selected_region': 'Rapid Bus KL', '_region_for_search': 'Rapid Bus KL',
        'user_location': {'lat': 3.0586, 'lon': 101.6739, 'accuracy': 10},
    })
    st_stub.columns.side_effect = lambda spec, *a, **k: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    now = int(time.time())
    monkeypatch.setattr(live_map.time, 'time', lambda: float(now))

    # Eight stops in range. Only the FAR one is served.
    stops = [
        {'stop_id': f'S{i}', 'stop_name': f'CLOSE STOP {i}',
         'stop_lat': 3.0586 + i * 0.0002, 'stop_lon': 101.6739,
         'distance_m': 20.0 * (i + 1)}
        for i in range(7)
    ]
    served = {'stop_id': 'FAR', 'stop_name': 'LRT AWAN BESAR',
              'stop_lat': 3.0640, 'stop_lon': 101.6739, 'distance_m': 585.0}
    # The stub MUST honour `limit` the way the real get_stops_near does —
    # it sorts by distance and returns found[:limit]. A stub that ignores
    # `limit` bypasses the very truncation this bug is about, and the test
    # would then pass against the unfixed code.
    all_stops = stops + [served]

    def fake_stops_near(agency, lat, lon, radius_m=800, limit=5):
        return sorted(all_stops, key=lambda s: s['distance_m'])[:limit]

    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', fake_stops_near)

    # A trip that stops at the bus's position, then at the far stop.
    trip = [
        {'stop_id': 'ORIGIN', 'stop_name': 'ORIGIN', 'stop_lat': 3.0500,
         'stop_lon': 101.6739, 'arrival_seconds': 0},
        {'stop_id': 'FAR', 'stop_name': 'LRT AWAN BESAR', 'stop_lat': 3.0640,
         'stop_lon': 101.6739, 'arrival_seconds': 600},
    ]
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: trip)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: True)

    df = pd.DataFrame([{
        'region': 'Rapid Bus KL', 'vehicle_id': 'V1',
        'latitude': 3.0500, 'longitude': 101.6739, 'bearing': 90.0, 'speed': 10.0,
        'timestamp': now, 'trip_id': 'T1', 'route_id': 'S6060',
        'freshness': 'fresh', 'age_seconds': 5,
    }])
    monkeypatch.setattr(
        live_map.db, 'get_live_data_optimized',
        lambda *a, **k: (df, {'total': 1, 'stale': 0, 'hidden': 0,
                              'regions': 1, 'busiest': 'Rapid Bus KL'}, 'now'),
    )

    live_map.show()

    # The stop name is a button label now, not markdown text (Task 1) --
    # include it so this still tests "was the stop shown at all", not
    # incidentally "was it shown as a link".
    said = _texts(st_stub.markdown) + _texts(st_stub.caption) + _texts(st_stub.button)
    assert 'LRT AWAN BESAR' in said, \
        "the only served stop was dropped for being 8th-nearest"


def test_format_route_heading_labels_the_two_parts():
    from app_pages import live_map
    lines = live_map.format_route_heading(
        {'short': 'PAVILION BUKIT JALIL (PAVBJ)',
         'long': 'Stesen LRT Awan Besar ~ Pavilion Bukit Jalil'},
        fallback='ignored',
    )
    assert lines[0] == '**Route:** PAVILION BUKIT JALIL (PAVBJ)'
    assert lines[1] == '**Runs:** Stesen LRT Awan Besar ↔ Pavilion Bukit Jalil'


def test_format_route_heading_omits_a_path_that_repeats_the_name():
    from app_pages import live_map
    lines = live_map.format_route_heading(
        {'short': 'T580', 'long': 'T580'}, fallback='ignored')
    assert lines == ['**Route:** T580']


def test_format_route_heading_falls_back_when_parts_are_missing():
    from app_pages import live_map
    lines = live_map.format_route_heading({'short': '', 'long': ''}, fallback='T580')
    assert lines == ['**Route:** T580']


def test_format_route_heading_does_not_repeat_a_long_name_with_no_short_name():
    from app_pages import live_map
    lines = live_map.format_route_heading(
        {'short': '', 'long': 'Stesen LRT Awan Besar ~ Pavilion Bukit Jalil'},
        fallback='S6060',
    )
    assert lines == ['**Route:** Stesen LRT Awan Besar ↔ Pavilion Bukit Jalil']


def test_nearby_stops_layer_has_a_stable_explicit_id(monkeypatch):
    """An unnamed pdk.Layer takes a fresh uuid each render, which churns the
    deck spec hash and breaks map selection. Every layer must be named."""
    import pydeck as pdk
    from app_pages import live_map

    made = []
    real_layer = pdk.Layer

    def recording_layer(*a, **k):
        made.append(k.get('id'))
        return real_layer(*a, **k)

    monkeypatch.setattr(live_map.pdk, 'Layer', recording_layer)
    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map_mod, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.0586, 'lon': 101.6739,
                                              'accuracy': 10}
    monkeypatch.setattr(live_map_mod.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'A STOP',
                                          'stop_lat': 3.0586, 'stop_lon': 101.6739,
                                          'distance_m': 50.0}])
    live_map_mod.show()

    assert None not in made, "a pydeck layer was created without an explicit id"
    assert 'nearby-stops' in made


def test_stop_names_link_to_google_maps(monkeypatch):
    from app_pages import live_map

    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map_mod, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.0586, 'lon': 101.6739,
                                              'accuracy': 10}
    monkeypatch.setattr(live_map_mod.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'A STOP',
                                          'stop_lat': 3.058659, 'stop_lon': 101.673981,
                                          'distance_m': 50.0}])
    live_map_mod.show()

    said = _texts(st_stub.markdown)
    assert 'maps.google.com' in said or 'google.com/maps' in said
    assert '3.058659,101.673981' in said


def test_nearby_stops_layer_is_a_stroked_ring_not_a_vehicle_dot(monkeypatch):
    """Buses are filled ScatterplotLayer dots. A stop drawn the same way (just
    a different colour/radius) reads as a smaller bus, not a different kind
    of thing. The stops layer must stay stroked so its SHAPE, not only its
    colour, differs from a vehicle.

    Task 1 gave the ring a faint fill too, so a tap inside it is picked
    (deck.gl does not pick the dead centre of an unfilled shape) -- that fill
    is deliberately non-zero-but-faint, covered by
    test_the_stop_ring_fill_is_faint_but_not_invisible and
    test_stop_rings_are_clickable_across_their_whole_face, not here."""
    import pydeck as pdk
    from app_pages import live_map

    made_kwargs = {}
    real_layer = pdk.Layer

    def recording_layer(*a, **k):
        if k.get('id') == 'nearby-stops':
            made_kwargs.update(k)
        return real_layer(*a, **k)

    monkeypatch.setattr(live_map.pdk, 'Layer', recording_layer)
    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map_mod, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.0586, 'lon': 101.6739,
                                              'accuracy': 10}
    monkeypatch.setattr(live_map_mod.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'A STOP',
                                          'stop_lat': 3.0586, 'stop_lon': 101.6739,
                                          'distance_m': 50.0}])
    live_map_mod.show()

    assert made_kwargs, "the nearby-stops layer was never constructed"
    assert made_kwargs.get('stroked') is True, \
        "stop markers must be outlined (stroked=True) to read as rings"


def test_the_stops_layer_is_tappable_and_carries_its_own_tooltip(monkeypatch):
    """
    Every stop-selection test injects a synthetic selection payload, so the two
    settings that make a real tap possible are invisible to all of them:
    reverting `pickable=True` to the `pickable=False` 2.6.0 shipped leaves them
    all green while the feature is dead in the browser, and restoring the old
    vehicle-specific Deck tooltip template would make a stop render literal
    `{vehicle_id}` text. Both are pinned here, against the built deck rather
    than against a mock.
    """
    from app_pages import live_map

    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map_mod, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.0586, 'lon': 101.6739,
                                              'accuracy': 10}
    monkeypatch.setattr(live_map_mod.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'A STOP',
                                          'stop_lat': 3.0586, 'stop_lon': 101.6739,
                                          'distance_m': 50.0}])
    live_map_mod.show()

    deck = st_stub.pydeck_chart.call_args_list[0].args[0]
    stops = next((l for l in deck.layers if l.id == 'nearby-stops'), None)
    assert stops is not None, "the nearby-stops layer is missing from the deck"
    assert stops.pickable is True, \
        "stops must be pickable or a tap on a stop ring does nothing"
    assert 'tip_text' in stops.data[0], \
        "each stop row must carry its own rendered tooltip text"
    assert 'A STOP' in stops.data[0]['tip_text']

    assert deck._tooltip['text'] == '{tip_text}', \
        ("the Deck tooltip must name the shared per-row field; a "
         "vehicle-specific template renders literally on a stop")


# ---------------------------------------------------------------------------
# Final whole-branch review fixes
#
# F1/F2: the tapped-bus panel must state the same facts as the stop-centric
# panel -- including the lateness clause and the destination, both of which the
# hand-built body silently dropped -- and the headway-honesty rule must be
# guarded by tests that can actually fail.
# ---------------------------------------------------------------------------

def test_the_tapped_panel_states_the_lateness_and_the_destination(monkeypatch):
    """
    Only Rapid Bus KL is headway-based. Twelve of the thirteen agency feeds
    ship no frequencies.txt at all, so delay_seconds is a real number for
    roughly 18,500 trips -- KTM, myBAS Johor, MRT Feeder, Penang. Outside KL
    the stop panel said a bus was 6 minutes late while the tapped panel, for
    the very same bus, said nothing. The destination went the same way.
    """
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}

    offset_seconds = int(live_map.UTC_OFFSET_HOURS) * 3600
    local_seconds_of_day = (now + offset_seconds) % 86400
    # Timetabled at the bus's current position six minutes ago, and the bus is
    # only reporting there now: a measured +360s.
    stops = [
        {'stop_id': 'S0', 'stop_name': 'Origin Stop', 'stop_lat': 3.14, 'stop_lon': 101.68,
         'arrival_seconds': local_seconds_of_day - 360},
        {'stop_id': 'S1', 'stop_name': 'Nearby Stop', 'stop_lat': 3.1401, 'stop_lon': 101.6801,
         'arrival_seconds': local_seconds_of_day - 360 + 300},
    ]
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: stops)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: 'Terminal X')
    # No frequencies.txt for this agency, so the delay IS knowable.
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: False)

    live_map.show()

    said = _texts(st_stub.info)
    assert '6 min late' in said, \
        f"the tapped panel dropped a lateness the stop panel would have shown: {said!r}"
    assert 'Terminal X' in said, \
        f"the tapped panel dropped the destination the stop panel would have shown: {said!r}"


def test_format_arrival_states_a_measured_lateness():
    from app_pages import live_map
    line = live_map.format_arrival({
        'route_display': 'T580', 'headsign': 'Terminal X',
        'eta_seconds': 300, 'delay_seconds': 360, 'age_seconds': 5,
    })
    assert '6 min late' in line, line


def test_format_arrival_claims_no_lateness_when_it_is_not_knowable():
    """
    delay_seconds is None on a headway trip: there is no published start time
    to be late against. That is "not knowable", never zero and never "on time".
    """
    from app_pages import live_map
    line = live_map.format_arrival({
        'route_display': 'T580', 'headsign': 'Terminal X',
        'eta_seconds': 300, 'delay_seconds': None, 'age_seconds': 5,
    })
    assert 'late' not in line, line
    assert '0 min' not in line, line


def test_format_arrival_says_nothing_about_a_sub_minute_delay():
    """Under the 60-second floor the delay is noise, not news."""
    from app_pages import live_map
    line = live_map.format_arrival({
        'route_display': 'T580', 'headsign': 'Terminal X',
        'eta_seconds': 300, 'delay_seconds': 30, 'age_seconds': 5,
    })
    assert 'late' not in line, line
    assert '0 min' not in line, line


# ---------------------------------------------------------------------------
# F3: a universal claim may only be made on universal evidence
#
# The arrivals panel is fed from df_map, which a route search narrows to one
# route. "No buses are currently en route to any stop within 800 m of you" is
# then a claim about every route made from evidence about one.
# ---------------------------------------------------------------------------

def _arrivals_panel_with_nothing_inbound(monkeypatch, route_query):
    """Render the arrivals panel with one stop in range and no bus reaching it."""
    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.text_input.return_value = route_query
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    # Keep route_display off the live feed: 'T5800' then contains the searched
    # 'T580', so the search filters rather than falling through to the
    # "route not in this region" branch.
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_name', lambda *a, **k: '')
    monkeypatch.setattr(
        live_map.gtfs_static, 'get_stops_near',
        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'A STOP',
                          'stop_lat': 3.14, 'stop_lon': 101.68, 'distance_m': 50.0}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: True)
    live_map.show()
    return st_stub


def test_nothing_inbound_names_the_route_when_a_search_is_filtering(monkeypatch):
    """
    A user searching T580 who is told "no buses are en route to any stop within
    800 m" reads it as "nothing is coming", which may be flatly untrue -- every
    other route was filtered out of the frame before the panel looked.
    """
    st_stub = _arrivals_panel_with_nothing_inbound(monkeypatch, 'T580')

    said = _texts(st_stub.caption)
    assert "No buses matching 'T580' are currently en route" in said, \
        f"the panel-level claim was not narrowed to the searched route: {said!r}"
    assert "no bus matching 'T580' currently en route to this stop" in said, \
        f"the per-stop claim was not narrowed to the searched route: {said!r}"
    assert 'No buses are currently en route to any stop' not in said, \
        f"the unqualified universal claim survived an active search: {said!r}"


def test_nothing_inbound_stays_unqualified_with_no_search(monkeypatch):
    """Without a filter the frame IS every route, so the plain wording is
    correct and must not grow a qualifier it has not earned."""
    st_stub = _arrivals_panel_with_nothing_inbound(monkeypatch, '')

    said = _texts(st_stub.caption)
    assert 'No buses are currently en route to any stop within 800 m of you.' in said, said
    assert 'no bus currently en route to this stop' in said, said
    assert 'matching' not in said, \
        f"unfiltered copy must not name a route: {said!r}"


# ---------------------------------------------------------------------------
# F4: stops evaluated, found to have a bus coming, and then not shown
# ---------------------------------------------------------------------------

def test_served_stops_beyond_the_display_cap_are_counted_not_dropped(monkeypatch):
    """
    The median 800 m neighbourhood in this feed holds 13 stops, so more than
    five stops with buses coming is ordinary. Cutting them silently is the
    reported bug in miniature: a stop the app evaluated, found a bus inbound
    for, and then never mentioned.
    """
    selection = SimpleNamespace(selection=SimpleNamespace(objects={}))
    df = pd.DataFrame([{
        'region': 'Rapid Bus KL', 'vehicle_id': 'V1',
        'latitude': 3.10, 'longitude': 101.68, 'bearing': 90.0, 'speed': 10.0,
        'timestamp': int(time.time()), 'trip_id': 'T1', 'route_id': 'T5800',
        'freshness': 'fresh', 'age_seconds': 5,
    }])
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection, df=df)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}

    offset_seconds = int(live_map.UTC_OFFSET_HOURS) * 3600
    local_seconds_of_day = (now + offset_seconds) % 86400

    # Seven stops in range, every one of them with this bus still to come.
    near = [{'stop_id': f'S{i}', 'stop_name': f'STOP {i}',
             'stop_lat': 3.14 + i * 0.0002, 'stop_lon': 101.68,
             'distance_m': 20.0 * (i + 1)} for i in range(7)]
    trip = [{'stop_id': 'ORIGIN', 'stop_name': 'ORIGIN',
             'stop_lat': 3.10, 'stop_lon': 101.68,
             'arrival_seconds': local_seconds_of_day}]
    trip += [dict(s, arrival_seconds=local_seconds_of_day + 60 * (i + 1))
             for i, s in enumerate(near)]

    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: near)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: trip)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: True)

    live_map.show()

    said = _texts(st_stub.caption)
    assert '2 more nearby stop(s) also have buses coming' in said, \
        f"seven served stops, five shown, and the other two vanished silently: {said!r}"


# ---------------------------------------------------------------------------
# F5: get_route_parts re-parsed a 1.7 MB ZIP on every render
# ---------------------------------------------------------------------------

def test_get_route_parts_reads_routes_txt_once_per_agency(tmp_path, monkeypatch):
    """
    With a bus selected and auto-refresh on, this runs every 20 seconds. The
    module already memoises its other routes.txt lookup for exactly this
    reason; this one opened the ZIP every time.
    """
    import zipfile
    from utils import gtfs_static

    z = _make_static_zip(tmp_path, 'kl', [
        ('T5800', 'T580', 'Awan Besar ~ TPM'),
        ('S6060', '', 'Stesen LRT Awan Besar ~ Pavilion Bukit Jalil'),
    ])
    opens = []

    def fake_load(slug):
        opens.append(slug)
        return zipfile.ZipFile(z)

    monkeypatch.setattr(gtfs_static, '_load_zip', fake_load)
    gtfs_static._ROUTE_PARTS_INDEX.clear()

    for _ in range(5):
        assert gtfs_static.get_route_parts('kl', 'T5800') == {
            'short': 'T580', 'long': 'Awan Besar ~ TPM'}
        assert gtfs_static.get_route_parts('kl', 'S6060')['short'] == ''
    assert len(opens) == 1, f"routes.txt was re-read {len(opens)} times"


def test_get_route_parts_is_empty_for_an_unknown_route(tmp_path, monkeypatch):
    import zipfile
    from utils import gtfs_static

    z = _make_static_zip(tmp_path, 'kl', [('T5800', 'T580', 'Awan Besar ~ TPM')])
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(z))
    gtfs_static._ROUTE_PARTS_INDEX.clear()

    assert gtfs_static.get_route_parts('kl', 'NOPE') == {'short': '', 'long': ''}
    assert gtfs_static.get_route_parts('kl', '') == {'short': '', 'long': ''}


def test_get_route_parts_survives_a_dead_feed_and_recovers(tmp_path, monkeypatch):
    """Never raises on an unavailable feed -- and must not cache that failure,
    or one outage would blank every route name for the life of the process."""
    import zipfile
    from utils import gtfs_static

    z = _make_static_zip(tmp_path, 'kl', [('T5800', 'T580', 'Awan Besar ~ TPM')])
    gtfs_static._ROUTE_PARTS_INDEX.clear()

    def boom(slug):
        raise OSError("feed withdrawn")

    monkeypatch.setattr(gtfs_static, '_load_zip', boom)
    assert gtfs_static.get_route_parts('kl', 'T5800') == {'short': '', 'long': ''}

    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(z))
    assert gtfs_static.get_route_parts('kl', 'T5800')['short'] == 'T580'


def test_get_route_name_still_joins_the_two_parts(tmp_path, monkeypatch):
    """get_route_name is a strict subset of get_route_parts and now shares its
    cache. Its contract is unchanged: joined when both parts exist, whichever
    one exists otherwise, empty string when the route or the feed is missing."""
    import zipfile
    from utils import gtfs_static

    z = _make_static_zip(tmp_path, 'kl', [
        ('T5800', 'T580', 'Awan Besar ~ TPM'),
        ('S6060', '', 'Awan Besar ~ Pavilion'),
        ('X1', 'X1', ''),
    ])
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(z))
    gtfs_static._ROUTE_PARTS_INDEX.clear()

    assert gtfs_static.get_route_name('kl', 'T5800') == 'T580 — Awan Besar ~ TPM'
    assert gtfs_static.get_route_name('kl', 'S6060') == 'Awan Besar ~ Pavilion'
    assert gtfs_static.get_route_name('kl', 'X1') == 'X1'
    assert gtfs_static.get_route_name('kl', 'NOPE') == ''
    assert gtfs_static.get_route_name('kl', '') == ''


def test_get_route_parts_rereads_when_the_zip_is_refreshed(tmp_path, monkeypatch):
    """
    The ZIP cache rolls every 24 hours and a new static release can rename a
    route. This module already learned that lesson once for the trip index,
    whose docstring records it: an index keyed on presence alone let a process
    serve a superseded timetable indefinitely. The route index is keyed on the
    ZIP's mtime for the same reason.
    """
    import zipfile
    from utils import gtfs_static

    old = _make_static_zip(tmp_path, 'old', [('T5800', 'T580', 'Awan Besar ~ TPM')])
    new = _make_static_zip(tmp_path, 'new', [('T5800', 'T580', 'Awan Besar ~ Bandar Malaysia')])

    current = {'zip': old, 'mtime': 1000.0}
    monkeypatch.setattr(gtfs_static, '_load_zip',
                        lambda slug: zipfile.ZipFile(current['zip']))
    monkeypatch.setattr(gtfs_static, '_zip_mtime', lambda slug: current['mtime'])
    gtfs_static._ROUTE_PARTS_INDEX.clear()
    gtfs_static._ROUTE_PARTS_MTIME.clear()

    assert gtfs_static.get_route_parts('kl', 'T5800')['long'] == 'Awan Besar ~ TPM'

    # A new static release lands: same slug, same route_id, renamed.
    current['zip'] = new
    current['mtime'] = 2000.0
    assert gtfs_static.get_route_parts('kl', 'T5800')['long'] == (
        'Awan Besar ~ Bandar Malaysia'), "served a superseded routes.txt"


def _arrival(**kw):
    base = {'route_display': 'T580', 'headsign': 'Awan Besar',
            'eta_seconds': 360, 'delay_seconds': None, 'age_seconds': 0}
    base.update(kw)
    return base


def test_arrival_row_labels_the_route_so_a_place_name_cannot_be_mistaken():
    from app_pages import live_map
    # PAVILION BUKIT JALIL is both a route name and a shopping mall. Without
    # the word "Route" there is nothing to tell a reader which it is.
    line = live_map.format_arrival(
        _arrival(route_display='PAVBJ', headsign='Pavilion Bukit Jalil'))
    assert line.startswith('Route PAVBJ')
    assert '→ Pavilion Bukit Jalil' in line


def test_arrival_row_states_the_arrival():
    from app_pages import live_map
    line = live_map.format_arrival(_arrival(eta_seconds=360))
    assert 'arrives' in line
    assert '~6 min' in line


def test_arrival_row_is_one_line():
    from app_pages import live_map
    line = live_map.format_arrival(
        _arrival(delay_seconds=120, age_seconds=180))
    assert '\n' not in line


def test_arrival_row_omits_the_headsign_when_there_is_none():
    from app_pages import live_map
    line = live_map.format_arrival(_arrival(headsign=''))
    assert '→' not in line
    assert line.startswith('Route T580')


def test_arrival_row_states_lateness_when_it_is_knowable():
    from app_pages import live_map
    line = live_map.format_arrival(_arrival(delay_seconds=120))
    assert '2 min late' in line


def test_arrival_row_says_nothing_at_all_when_lateness_is_unknowable():
    from app_pages import live_map
    # None means the trip runs to a headway and has no published start time to
    # be late against. It is never zero and never "on time"; it is silence.
    line = live_map.format_arrival(_arrival(delay_seconds=None))
    assert 'late' not in line
    assert 'on time' not in line
    assert '0 min' not in line


def test_arrival_row_does_not_call_a_punctual_bus_late():
    from app_pages import live_map
    line = live_map.format_arrival(_arrival(delay_seconds=0))
    assert 'late' not in line


def test_arrival_row_flags_a_stale_position_without_hedging_prose():
    from app_pages import live_map
    line = live_map.format_arrival(_arrival(age_seconds=180), fresh_seconds=60)
    assert 'position 3 min old' in line
    assert 'less certain' not in line


def test_arrival_row_stays_quiet_about_a_fresh_position():
    from app_pages import live_map
    line = live_map.format_arrival(_arrival(age_seconds=30), fresh_seconds=60)
    assert 'position' not in line


def test_arrival_row_survives_a_missing_route_display():
    from app_pages import live_map
    line = live_map.format_arrival(_arrival(route_display=''))
    assert line.startswith('Route —')


def test_tip_text_does_not_escape_a_name_containing_markup_characters():
    # Streamlit escapes interpolated tooltip values itself (bug fix #15820).
    # If _tip_text also escaped, the result would be double-escaped and a stop
    # named "A & B" would render as the literal text "A &amp; B". So the raw
    # value — '&' and '<' included — must appear verbatim: no HTML entities,
    # because _tip_text itself must never escape anything.
    from app_pages import live_map
    out = live_map._tip_text('Stop', 'A & B <Terminal>')
    assert out == 'Stop: A & B <Terminal>'
    assert '&amp;' not in out
    assert '&lt;' not in out


def test_tip_text_labels_the_value():
    from app_pages import live_map
    assert live_map._tip_text('Stop', 'KL2324') == 'Stop: KL2324'


class _Sel:
    """Minimal stand-in for a Streamlit pydeck selection payload."""
    def __init__(self, objects):
        self.selection = type('S', (), {'objects': objects})()


def test_picked_stop_id_reads_the_stops_layer():
    from app_pages import live_map
    sel = _Sel({'nearby-stops': [{'stop_id': 'KL2324'}]})
    assert live_map._picked_stop_id(sel) == 'KL2324'


def test_picked_stop_id_ignores_a_vehicle_pick():
    from app_pages import live_map
    sel = _Sel({'vehicles': [{'vehicle_id': 'BUS1'}]})
    assert live_map._picked_stop_id(sel) is None


def test_picked_stop_id_is_none_for_an_empty_selection():
    from app_pages import live_map
    assert live_map._picked_stop_id(_Sel({})) is None


def test_picked_stop_id_coerces_a_non_string_id():
    from app_pages import live_map
    # A non-string id reaching a pandas comparison against an Arrow-backed
    # column raises NotImplementedError and takes the whole page down.
    sel = _Sel({'nearby-stops': [{'stop_id': 2324}]})
    assert live_map._picked_stop_id(sel) == '2324'


def test_picked_stop_id_survives_an_unexpected_payload_shape():
    from app_pages import live_map
    assert live_map._picked_stop_id(_Sel({'nearby-stops': [{}]})) is None
    assert live_map._picked_stop_id(_Sel({'nearby-stops': 'not a list'})) is None
    assert live_map._picked_stop_id(object()) is None


# ── last-tap-wins: a fresh tap in either direction must clear the other ──────
#
# `picked` (the vehicle id) is re-read from session state whenever nothing was
# tapped this render, so it stays non-None for as long as a bus is selected —
# not only on the render where it was actually tapped. The last-tap-wins check
# must compare against a *fresh* tap, not that sticky value, or a stop tap can
# never be recorded while any bus stays selected.

def test_a_fresh_stop_tap_overrides_a_sticky_bus_selection(monkeypatch):
    """
    Regression: a bus selected on an earlier render left `picked` truthy on
    every later render (it is re-read from session state), so the
    last-tap-wins check saw a "bus selected" signal even on the render where
    the user actually tapped a stop, and silently discarded the stop tap.
    """
    stop = {'stop_id': 'S1', 'stop_name': 'Tapped Stop',
            'stop_lat': 3.1401, 'stop_lon': 101.6801, 'distance_m': 50.0}
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"nearby-stops": [{"stop_id": "S1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    # As if a bus was already selected from an earlier render.
    st_stub.session_state['selected_vehicle_id'] = 'V1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [stop])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: False)

    live_map.show()

    said = _texts(st_stub.info)
    assert 'Tapped Stop' in said, \
        f"the freshly tapped stop must render even though a bus was sticky: {said!r}"
    buttons = _texts(st_stub.button)
    assert 'Clear bus selection' not in buttons, \
        "the bus panel must not render beside a freshly tapped stop"
    assert st_stub.session_state.get('selected_vehicle_id') is None, \
        "a fresh stop tap must clear the sticky bus selection"


def test_a_fresh_bus_tap_overrides_a_sticky_stop_selection(monkeypatch):
    """Companion direction: tapping a bus while a stop panel is showing must
    swap it out for the bus panel. This direction already worked before the
    fix — pinned here so a future change cannot regress it silently."""
    stop = {'stop_id': 'S1', 'stop_name': 'Sticky Stop',
            'stop_lat': 3.14, 'stop_lon': 101.68, 'distance_m': 50.0}
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    # As if a stop was already selected from an earlier render.
    st_stub.session_state['selected_stop_id'] = 'S1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [stop])

    offset_seconds = int(live_map.UTC_OFFSET_HOURS) * 3600
    local_seconds_of_day = (now + offset_seconds) % 86400
    trip_stops = [
        {'stop_id': 'S0', 'stop_name': 'Origin Stop', 'stop_lat': 3.14, 'stop_lon': 101.68,
         'arrival_seconds': local_seconds_of_day},
        {'stop_id': 'S1', 'stop_name': 'Nearby Stop', 'stop_lat': 3.1401, 'stop_lon': 101.6801,
         'arrival_seconds': local_seconds_of_day + 300},
    ]
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: trip_stops)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: 'Terminal X')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: False)

    live_map.show()

    said = _texts(st_stub.info)
    assert 'Nearby Stop' in said, \
        f"the freshly tapped bus's arrival must render: {said!r}"
    assert '📍' not in said, \
        "the stop panel must not render beside a freshly tapped bus"
    assert st_stub.session_state.get('selected_stop_id') is None, \
        "a fresh bus tap must clear the sticky stop selection"


def test_clicking_a_stop_name_clears_a_sticky_bus_selection(monkeypatch):
    """
    A ring tap clears the sticky vehicle id in the same render (:1043),
    before the bus panel's `if picked:` check runs, so tapping a stop ring
    never renders a bus panel beside it. The name button cannot reach that
    same-render guard -- st.button() is read deep inside the arrivals loop,
    well after the bus panel has already been emitted for this render -- so
    it instead has to leave session state clean for the *next* render, the
    one a real st.rerun() immediately triggers. Reproduce that next render
    directly: a bus was sticky, the button click (render 1, asserted below)
    wrote selected_stop_id and must also have cleared selected_vehicle_id --
    if it had not, render 2 (no fresh tap of either kind, exactly what an
    auto-refresh after the rerun looks like) would render both panels at
    once, precisely what the last-tap-wins comment at :1033-1035 forbids.
    """
    stop = {'stop_id': 'S1', 'stop_name': 'Tapped By Name',
            'stop_lat': 3.14, 'stop_lon': 101.68, 'distance_m': 50.0}
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    st_stub.session_state['selected_vehicle_id'] = 'V1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [stop])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: False)

    # Render 1: the click. A bus is still sticky when the name button fires.
    # Substring: the label carries Streamlit colour markup (`:blue[NAME]`)
    # so the name reads as clickable; a test naming the stop should not
    # have to know that.
    st_stub.button.side_effect = lambda label, *a, **k: 'Tapped By Name' in str(label)
    live_map.show()
    assert st_stub.session_state.get('selected_vehicle_id') is None, \
        "clicking a stop's name must clear the sticky bus selection, like a ring tap does"
    assert st_stub.session_state.get('selected_stop_id') == 'S1'

    # Render 2: what st.rerun() triggers in reality. No fresh tap of either
    # kind -- only the state render 1 left behind. Clear the call logs so
    # this render's content can be checked on its own.
    st_stub.info.call_args_list.clear()
    st_stub.markdown.call_args_list.clear()
    st_stub.button.call_args_list.clear()
    st_stub.button.side_effect = None
    st_stub.button.return_value = False

    live_map.show()

    said = _texts(st_stub.info)
    assert '📍 **Tapped By Name**' in said, \
        "the stop panel must render on the render after the click"
    buttons = _texts(st_stub.button)
    assert 'Clear bus selection' not in buttons, \
        "the bus panel must not still be showing once the click's rerun has settled"


def test_clicking_a_stop_name_suppresses_a_redelivered_vehicle_payload(monkeypatch):
    """
    Clearing selected_vehicle_id is not enough on its own. If the pydeck
    payload naming the still-selected bus survives the click's own st.rerun()
    -- which is exactly what test_clearing_a_stop_selection_survives_a_repeated
    _payload proves Streamlit can do -- the next render reads it as a *fresh*
    vehicle tap, and a fresh vehicle tap sets selected_stop_id to None. The
    click would then do nothing at all.

    That does not happen today only incidentally: the selected ring's own
    line_color/line_width change the deck spec, Streamlit hashes the spec into
    the element id, and a payload stored against the old id is not redelivered
    against the new one. Style the highlight some other way -- or drop it --
    and the click silently breaks. So the click also arms the same one-shot
    'ignore this vehicle for exactly one render' token "Clear bus selection"
    uses, which makes the guarantee the code's own rather than a side effect
    of how a ring happens to be drawn.
    """
    stop = {'stop_id': 'S1', 'stop_name': 'Tapped By Name',
            'stop_lat': 3.14, 'stop_lon': 101.68, 'distance_m': 50.0}
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    st_stub.session_state['selected_vehicle_id'] = 'V1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [stop])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: False)

    # Render 1: the click, with a bus still sticky.
    # Substring: the label carries Streamlit colour markup (`:blue[NAME]`)
    # so the name reads as clickable; a test naming the stop should not
    # have to know that.
    st_stub.button.side_effect = lambda label, *a, **k: 'Tapped By Name' in str(label)
    live_map.show()
    assert st_stub.session_state.get('cleared_vehicle_id') == 'V1', \
        "the click must arm the one-shot suppression, not rely on the deck spec changing"

    # Render 2: the rerun, with the vehicle payload redelivered.
    st_stub.button.side_effect = None
    st_stub.button.return_value = False
    st_stub.pydeck_chart.return_value = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]}))
    st_stub.info.call_args_list.clear()

    live_map.show()

    assert st_stub.session_state.get('selected_stop_id') == 'S1', \
        "a redelivered vehicle payload stole the selection the click had just made"
    assert '📍 **Tapped By Name**' in _texts(st_stub.info)
    # One render only. The token is gone, so the same bus is tappable again on
    # the render after -- dismissing a bus must never cost the ability to
    # re-select it.
    assert 'cleared_vehicle_id' not in st_stub.session_state, \
        "the suppression outlived its one render"


def test_a_sticky_stop_selection_survives_a_render_that_reports_none(monkeypatch):
    """
    Companion to test_a_selection_survives_a_render_that_reports_none: a stop
    tap is reported for exactly one render too, so the stop panel must persist
    across auto-refresh the same way the bus panel already does.
    """
    stop = {'stop_id': 'S1', 'stop_name': 'Persisted Stop',
            'stop_lat': 3.14, 'stop_lon': 101.68, 'distance_m': 50.0}
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    # As if a previous render had recorded the tap.
    st_stub.session_state['selected_stop_id'] = 'S1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [stop])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])

    live_map.show()

    said = _texts(st_stub.info)
    assert 'Persisted Stop' in said, \
        "the stop selection was dropped on a render that reported no tap"


def test_a_selected_stop_out_of_range_clears_itself(monkeypatch):
    """
    The user walked away from a stop they had selected, so it fell out of
    _nearby_stops on this render. Reachable in normal use: the selection is
    sticky across auto-refresh (see the test above) but the nearby-stop scan
    is recomputed every render from the user's current location. The panel
    must clear the stale selection rather than fail to look the stop up, and
    must not render a panel for a stop that is no longer confirmed nearby.
    """
    other_stop = {'stop_id': 'S2', 'stop_name': 'A Different Stop',
                  'stop_lat': 3.20, 'stop_lon': 101.70, 'distance_m': 50.0}
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    # As if a previous render had recorded a tap on a stop no longer in range.
    st_stub.session_state['selected_stop_id'] = 'S1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [other_stop])

    live_map.show()

    assert st_stub.session_state.get('selected_stop_id') is None, \
        "a selection for a stop that fell out of range must clear itself"
    said = _texts(st_stub.info)
    assert 'A Different Stop' not in said and '📍' not in said, \
        "no stop panel should render once the selected stop is out of range"


def test_a_selection_clears_itself_when_no_stops_are_in_range_at_all(monkeypatch):
    """
    Companion to the test above, and a real hole it did not cover: the panel's
    guard required a non-empty _nearby_stops before it would even look the
    selection up, so walking entirely out of range (or clearing the location)
    short-circuited past the self-heal and left the stale id in session state.
    The panel then reappeared *without a tap* as soon as the user walked back
    into range or pressed Locate Me again.
    """
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    st_stub.session_state['selected_stop_id'] = 'S1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [])

    live_map.show()

    assert st_stub.session_state.get('selected_stop_id') is None, \
        "a selection must clear itself when no stop can confirm it"
    assert '📍 **' not in _texts(st_stub.info), \
        "no stop panel should render with nothing in range"


def test_the_tapped_stop_panel_lists_no_more_arrivals_than_the_panel_below(monkeypatch):
    """
    The two panels answer the same question about the same stop. The panel
    below has always capped a stop at ARRIVALS_PER_STOP rows; the tapped-stop
    panel rendered every row, so one stop could show six buses in the box above
    and three in the list below — a contradiction, not two views.
    """
    stop = {'stop_id': 'S1', 'stop_name': 'Busy Stop',
            'stop_lat': 3.14, 'stop_lon': 101.68, 'distance_m': 50.0}
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    st_stub.session_state['selected_stop_id'] = 'S1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [stop])

    six = [{'route_display': f'R{i}', 'headsign': '', 'eta_seconds': 60 * (i + 1),
            'delay_seconds': None, 'age_seconds': 0} for i in range(6)]
    monkeypatch.setattr(live_map.eta, 'arrivals_for_stops',
                        lambda *a, **k: ({'S1': six}, {}))

    live_map.show()

    panel = _texts(st_stub.info)
    listed = [f'R{i}' for i in range(6) if f'Route R{i}' in panel]
    assert listed == ['R0', 'R1', 'R2'], \
        f"the tapped-stop panel must cap at {live_map.ARRIVALS_PER_STOP}: {listed}"


def test_a_blank_stop_id_is_not_a_stop_id():
    """
    get_stops_near takes stop_id straight from stops.txt with only a .strip(),
    so a feed row with a blank id yields stop_id == ''. That is falsy but not
    None, which made the guards downstream disagree about the same value.
    """
    from app_pages import live_map
    assert live_map._picked_stop_id(_Sel({'nearby-stops': [{'stop_id': ''}]})) is None


def test_a_blank_stop_id_does_not_eat_the_bus_selection(monkeypatch):
    """
    The tap was recorded (`'' is not None`), the sticky bus selection was
    cleared to make room for it, and then the panel's own truthiness check
    declined to render anything. A dead tap that destroyed the previous
    selection.
    """
    blank = SimpleNamespace(
        selection=SimpleNamespace(objects={"nearby-stops": [{"stop_id": ""}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, blank)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    st_stub.session_state['selected_vehicle_id'] = 'V1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])

    live_map.show()

    assert st_stub.session_state.get('selected_vehicle_id') == 'V1', \
        "a blank stop id must not destroy the existing bus selection"


def _make_timetable_zip(tmp_path, name):
    """
    A minimal GTFS static ZIP with a loop route and a second route.

    Models the real shape that motivated this feature: T580 leaves LRT Awan
    Besar, reaches KM1 Bukit Jalil one minute later, and returns past Green
    Avenue Condominium 32 minutes later before ending where it began. Two trips
    share that pattern so de-duplication has something to collapse.
    """
    import zipfile
    p = tmp_path / f"{name}.zip"
    stops = (
        "stop_id,stop_name,stop_lat,stop_lon\n"
        "S1,LRT AWAN BESAR,3.0621,101.6706\n"
        "S2,KM1 BUKIT JALIL,3.0584,101.6744\n"
        "S3,GREEN AVENUE CONDOMINIUM,3.0587,101.6740\n"
        "S4,ELSEWHERE,3.0700,101.6800\n"
    )
    routes = (
        "route_id,route_short_name,route_long_name\n"
        "T5800,T580,Awan Besar ~ TPM\n"
        "U6000,650,Elsewhere ~ Awan Besar\n"
    )
    trips = (
        "route_id,trip_id,trip_headsign\n"
        "T5800,t_loop_a,\n"
        "T5800,t_loop_b,\n"
        "T5800,t_short,\n"
        "U6000,t_other,Awan Besar\n"
    )
    # t_loop_a and t_loop_b are the same sequence at different times of day —
    # one pattern. t_short is a genuinely different sequence on the same route,
    # so the fixture exercises both collapsing and keeping.
    stop_times = (
        "trip_id,stop_sequence,arrival_time,stop_id\n"
        "t_loop_a,1,06:00:00,S1\n"
        "t_loop_a,2,06:01:00,S2\n"
        "t_loop_a,3,06:32:00,S3\n"
        "t_loop_a,4,06:40:00,S1\n"
        "t_loop_b,1,07:00:00,S1\n"
        "t_loop_b,2,07:01:00,S2\n"
        "t_loop_b,3,07:32:00,S3\n"
        "t_loop_b,4,07:40:00,S1\n"
        "t_short,1,09:00:00,S1\n"
        "t_short,2,09:05:00,S4\n"
        "t_short,3,09:12:00,S1\n"
        "t_other,1,08:00:00,S4\n"
        "t_other,2,08:10:00,S1\n"
    )
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('stops.txt', stops)
        zf.writestr('routes.txt', routes)
        zf.writestr('trips.txt', trips)
        zf.writestr('stop_times.txt', stop_times)
    return str(p)


def _use_timetable_zip(monkeypatch, path):
    """Point gtfs_static at a fixture ZIP and clear every index it fills."""
    import zipfile
    from utils import gtfs_static
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(path))
    monkeypatch.setattr(gtfs_static, '_zip_mtime', lambda slug: 1000.0)
    gtfs_static._TRIP_STOPS_INDEX.clear()
    gtfs_static._TRIP_HEADSIGN_INDEX.clear()
    gtfs_static._TRIP_FREQUENCY_INDEX.clear()
    gtfs_static._TRIP_INDEX_MTIME.clear()
    gtfs_static._STOP_ROUTES_INDEX.clear()
    gtfs_static._ROUTE_TRIPS_INDEX.clear()
    gtfs_static._ROUTE_PARTS_INDEX.clear()
    gtfs_static._ROUTE_PARTS_MTIME.clear()
    return gtfs_static


def test_routes_at_stop_lists_every_route_serving_it(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    names = [r['short'] for r in g.get_routes_at_stop('kl', 'S1')]
    assert names == ['650', 'T580']


def test_routes_at_stop_returns_the_only_route_that_serves_a_stop(tmp_path, monkeypatch):
    # The reported failure in miniature: three routes reach the interchange,
    # exactly one reaches the destination.
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    served = g.get_routes_at_stop('kl', 'S3')
    assert [r['short'] for r in served] == ['T580']
    assert served[0]['route_id'] == 'T5800'
    assert served[0]['long'] == 'Awan Besar ~ TPM'


def test_routes_at_stop_is_empty_for_an_unknown_stop(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    assert g.get_routes_at_stop('kl', 'NOPE') == []
    assert g.get_routes_at_stop('kl', '') == []


def test_routes_at_stop_is_empty_when_the_feed_is_unavailable(monkeypatch):
    from utils import gtfs_static
    def boom(slug):
        raise OSError('no zip')
    monkeypatch.setattr(gtfs_static, '_load_zip', boom)
    monkeypatch.setattr(gtfs_static, '_zip_mtime', lambda slug: 1.0)
    gtfs_static._STOP_ROUTES_INDEX.clear()
    gtfs_static._TRIP_INDEX_MTIME.clear()
    assert gtfs_static.get_routes_at_stop('kl', 'S1') == []


def _stub_routes_at_stop(monkeypatch, parts_by_route_id):
    """Point get_routes_at_stop at a hand-built index, no ZIP involved."""
    from utils import gtfs_static
    monkeypatch.setattr(gtfs_static, '_trip_index_is_current', lambda slug: True)
    monkeypatch.setattr(gtfs_static, '_STOP_ROUTES_INDEX',
                        {'kl': {'S1': set(parts_by_route_id)}})
    monkeypatch.setattr(gtfs_static, 'get_route_parts',
                        lambda slug, rid: dict(parts_by_route_id[rid]))
    return gtfs_static


def test_routes_at_stop_orders_bus_numbers_as_numbers(monkeypatch):
    # Lexicographically '10' sorts before '2', which is wrong for a rider
    # reading a list of bus numbers -- and '9' would land after '100'.
    g = _stub_routes_at_stop(monkeypatch, {
        'R10': {'short': '10', 'long': ''},
        'R2': {'short': '2', 'long': ''},
        'R100': {'short': '100', 'long': ''},
        'R9': {'short': '9', 'long': ''},
        'T580': {'short': 'T580', 'long': ''},
        'T99': {'short': 'T99', 'long': ''},
    })
    assert [r['short'] for r in g.get_routes_at_stop('kl', 'S1')] == \
        ['2', '9', '10', '100', 'T99', 'T580']


def test_routes_at_stop_orders_two_routes_sharing_a_short_name_the_same_way_every_time(monkeypatch):
    # The route ids come out of a set, whose iteration order varies between
    # processes. Without route_id as a final tiebreak the same stop would list
    # its routes in a different order on a rerun.
    g = _stub_routes_at_stop(monkeypatch, {
        'B_SECOND': {'short': 'T580', 'long': 'Awan Besar ~ TPM'},
        'A_FIRST': {'short': 'T580', 'long': 'Awan Besar ~ Bukit Jalil'},
    })
    first = [r['route_id'] for r in g.get_routes_at_stop('kl', 'S1')]
    assert first == ['A_FIRST', 'B_SECOND'], first
    # Same answer however the set happens to iterate.
    for _ in range(5):
        assert [r['route_id'] for r in g.get_routes_at_stop('kl', 'S1')] == first


def test_routes_at_stop_survives_a_digit_that_int_cannot_parse(monkeypatch):
    # str.isdigit() is True for a superscript or a circled digit, but int()
    # raises on both -- and \d does not match them, so they arrive in a chunk
    # the digit branch should never have claimed. This sort runs inside the
    # tapped-stop panel, where nothing may raise into the render.
    g = _stub_routes_at_stop(monkeypatch, {
        'R_ODD': {'short': 'T580³', 'long': ''},      # superscript three
        'R_CIRCLED': {'short': '④', 'long': ''},      # circled four
        'R2': {'short': '2', 'long': ''},
    })
    shorts = [r['short'] for r in g.get_routes_at_stop('kl', 'S1')]
    assert '2' in shorts and len(shorts) == 3


def test_natural_key_treats_an_unparseable_digit_as_text(monkeypatch):
    from utils import gtfs_static
    # Sorted as text, not silently dropped, and still ordering real numbers.
    assert gtfs_static._natural_key('T580³')
    assert gtfs_static._natural_key('2') < gtfs_static._natural_key('10')


def test_natural_key_still_reads_arabic_indic_digits_as_numbers(monkeypatch):
    from utils import gtfs_static
    # These DO match \d and int() parses them, so the fix must not send every
    # non-ASCII digit down the text branch.
    assert gtfs_static._natural_key('٢') == gtfs_static._natural_key('2')


def test_route_patterns_collapse_trips_that_share_a_sequence(tmp_path, monkeypatch):
    # t_loop_a and t_loop_b visit the same stops at different times of day.
    # That is one pattern, not two.
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    sequences = [[s['stop_id'] for s in p['stops']]
                 for p in g.get_route_patterns('kl', 'T5800')]
    assert sequences.count(['S1', 'S2', 'S3', 'S1']) == 1


def test_route_patterns_returns_each_genuinely_different_sequence(tmp_path, monkeypatch):
    # 37 of Rapid KL's 136 routes run two patterns and one runs three. Showing
    # a single "the" sequence would be wrong for a quarter of the network.
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    sequences = [[s['stop_id'] for s in p['stops']]
                 for p in g.get_route_patterns('kl', 'T5800')]
    assert len(sequences) == 2
    assert ['S1', 'S2', 'S3', 'S1'] in sequences
    assert ['S1', 'S4', 'S1'] in sequences


def test_route_patterns_carry_arrival_seconds_for_journey_times(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    loop = next(p for p in g.get_route_patterns('kl', 'T5800')
                if [s['stop_id'] for s in p['stops']] == ['S1', 'S2', 'S3', 'S1'])
    base = loop['stops'][0]['arrival_seconds']
    assert [(s['arrival_seconds'] - base) // 60 for s in loop['stops']] == [0, 1, 32, 40]


def test_route_patterns_can_be_filtered_to_one_stop(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    # S2 is on the long loop only; S4 on the short one only; S9 on neither.
    assert len(g.get_route_patterns('kl', 'T5800', stop_id='S2')) == 1
    assert len(g.get_route_patterns('kl', 'T5800', stop_id='S4')) == 1
    assert g.get_route_patterns('kl', 'T5800', stop_id='S9') == []


def test_route_patterns_is_empty_for_an_unknown_route(tmp_path, monkeypatch):
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    assert g.get_route_patterns('kl', 'NOPE') == []
    assert g.get_route_patterns('kl', '') == []


def test_the_new_indexes_are_filled_by_the_same_pass_as_the_old_ones(tmp_path, monkeypatch):
    # They must be written together. An index built independently could survive
    # a rebuild of its siblings and serve a superseded timetable.
    g = _use_timetable_zip(monkeypatch, _make_timetable_zip(tmp_path, 'kl'))
    assert g._STOP_ROUTES_INDEX == {}
    g.get_trip_stops('kl', 't_loop_a')          # touches only the old API
    assert g._STOP_ROUTES_INDEX.get('kl'), "the new index was not filled by the shared pass"
    assert g._ROUTE_TRIPS_INDEX.get('kl')


def test_a_failed_build_stores_neither_new_index(monkeypatch):
    from utils import gtfs_static
    def boom(slug):
        raise OSError('no zip')
    monkeypatch.setattr(gtfs_static, '_load_zip', boom)
    monkeypatch.setattr(gtfs_static, '_zip_mtime', lambda slug: 1.0)
    gtfs_static._STOP_ROUTES_INDEX.clear()
    gtfs_static._ROUTE_TRIPS_INDEX.clear()
    gtfs_static._TRIP_INDEX_MTIME.clear()
    gtfs_static._build_trip_index('kl')
    assert 'kl' not in gtfs_static._STOP_ROUTES_INDEX
    assert 'kl' not in gtfs_static._ROUTE_TRIPS_INDEX


# ── tap-a-stop panel: every serving route, not only those with a live bus ───
#
# test_a_selected_stop_out_of_range_clears_itself already shows the shape
# these tests need: _live_map_with_selection with an empty vehicle selection,
# a stop pinned into session_state['selected_stop_id'], and get_stops_near
# stubbed to confirm it. _render_stop_panel below is exactly that scaffolding,
# factored out so each test only has to stub the GTFS lookups it cares about.

def _render_stop_panel(monkeypatch, extra_nearby=None):
    """
    Run live_map.show() with a stop already selected, so the tapped-stop panel
    renders. Mirrors the setup test_a_selected_stop_out_of_range_clears_itself
    uses: an empty vehicle selection via _live_map_with_selection, plus a
    stop pinned in session state and confirmed by get_stops_near.

    extra_nearby lets a test add more stops to the confirmed-nearby scan
    (get_stops_near returns them alongside the tapped stop itself), so a
    pattern's stop rows can exercise the near-you mark for a second, genuinely
    nearby stop -- the tapped stop alone never produces one, since it's never
    a *different* row in its own pattern.
    """
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.06, 'lon': 101.67, 'accuracy': 10}
    st_stub.session_state['selected_stop_id'] = 'S1'
    nearby = [{'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR',
               'stop_lat': 3.0621, 'stop_lon': 101.6706, 'distance_m': 560.0}]
    nearby += extra_nearby or []
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: nearby)
    live_map.show()
    return st_stub


def test_the_stop_panel_names_every_route_that_serves_the_stop(monkeypatch):
    # The reported failure: the panel listed the routes with a live bus and so
    # omitted the only route that reaches the rider's destination.
    from app_pages import live_map
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [
                            {'route_id': 'T5800', 'short': 'T580', 'long': 'Awan Besar ~ TPM'},
                            {'route_id': 'S6060', 'short': 'PAVBJ', 'long': 'Awan Besar ~ Pavilion'},
                        ])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns', lambda *a, **k: [])
    st_stub = _render_stop_panel(monkeypatch)

    said = _texts(st_stub.info) + _texts(st_stub.markdown) + _texts(st_stub.caption)
    assert 'T580' in said, "a route with no live bus must still be listed"
    assert 'PAVBJ' in said


def test_the_stop_panel_opens_a_route_to_its_stop_sequence(monkeypatch):
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'KM1 BUKIT JALIL', 'arrival_seconds': 60},
             {'stop_id': 'S3', 'stop_name': 'GREEN AVENUE', 'arrival_seconds': 1920},
             {'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 2400}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'T5800', 'short': 'T580', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: True)
    st_stub = _render_stop_panel(monkeypatch)

    said = _texts(st_stub.markdown) + _texts(st_stub.caption)
    assert 'KM1 BUKIT JALIL' in said
    assert '+1 min' in said, "the useful stop is one minute out"
    assert '+32 min' in said, "the stop named after the destination is 32 minutes out"


def test_the_returning_row_of_a_loop_renders_its_journey_time(monkeypatch):
    # build_stop_rows computes +40 for the row where a loop comes back to the
    # stop you tapped, but the render used to mark that row "you tapped this"
    # and stop there -- so rows 1 and 4 came out as identical text and the
    # loop's closure, the one thing this panel exists to make visible, never
    # reached the page. The unit test proved the data layer; only a render
    # assertion proves the number survives to the markdown.
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'KM1 BUKIT JALIL', 'arrival_seconds': 60},
             {'stop_id': 'S3', 'stop_name': 'GREEN AVENUE', 'arrival_seconds': 1920},
             {'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 2400}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'T5800', 'short': 'T580', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: True)
    st_stub = _render_stop_panel(monkeypatch)

    said = _texts(st_stub.markdown)
    assert '+40 min' in said, \
        f"the returning row must state the loop's length, not just repeat the mark: {said}"
    # The anchor row itself is 0 minutes out -- "+0 min" would be noise.
    assert '+0 min' not in said, said
    # Both ends stay marked, so the rider can see it is the same stop.
    assert said.count('← you tapped this') == 2, said


def test_the_stop_panel_escapes_a_stop_name_from_the_feed(monkeypatch):
    # The panel joins every row into one markdown block with a hard break,
    # which is an inline <br> rather than a new block -- so an unescaped
    # metacharacter in a stop name (untrusted GTFS feed text) could pair with
    # a matching character several rows away and swallow the rows between
    # into unintended formatting. This confirms the panel actually calls the
    # escape on the way in, not only that route_view.escape_markdown works
    # in isolation.
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'Depot *Alpha', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'closes* there', 'arrival_seconds': 60}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'R', 'short': 'R1', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: False)
    st_stub = _render_stop_panel(monkeypatch)

    said = _texts(st_stub.markdown)
    assert 'Depot \\*Alpha' in said, \
        "the panel must escape asterisks from stop names, not pass them through"
    assert 'closes\\* there' in said
    # The raw, unescaped forms must not appear at all -- that is exactly the
    # shape that could pair up across the joined block and swallow the rows
    # between into unintended formatting.
    assert 'Depot *Alpha' not in said, said
    assert 'closes* there' not in said, said


def test_the_stop_panel_marks_a_pattern_stop_that_is_also_near_the_rider(monkeypatch):
    # This is the mark that makes the real case legible: at LRT Awan Besar,
    # KM1 Bukit Jalil (+1 min) is near the rider's building just as much as
    # Green Avenue Condominium (+32 min) is -- seeing both marked is what
    # makes the right choice obvious, rather than trusting the stop name.
    # get_stops_near must return a *second* stop distinct from the tapped one
    # for this to be exercised at all; the tapped stop itself never produces
    # a near-you mark on its own row.
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'KM1 BUKIT JALIL', 'arrival_seconds': 60}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'T5800', 'short': 'T580', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: True)

    extra_nearby = [{'stop_id': 'S2', 'stop_name': 'KM1 BUKIT JALIL',
                      'stop_lat': 3.063, 'stop_lon': 101.671, 'distance_m': 120.0}]
    st_stub = _render_stop_panel(monkeypatch, extra_nearby=extra_nearby)

    # No ORS key in the test harness, so this resolves through the
    # straight-line fallback -- computed the same way live_map does, rather
    # than hardcoding a minute figure that would drift if the constants move.
    expected_minutes = live_map.walking.estimate_minutes(120.0)
    said = _texts(st_stub.markdown)
    assert '120 m from you' in said, said
    assert f'~{expected_minutes} min walk (estimated)' in said, said
    # The subordination has to be visual, not whitespace: HTML collapses
    # runs of spaces to one, and this line lost st.caption's muted styling
    # when it moved into the joined markdown block. Without the arrow and
    # italics it reads as a peer stop rather than a note about one.
    assert f'*↳ ~120 m from you · ~{expected_minutes} min walk (estimated)*' in said, said


def test_the_serves_line_and_expander_label_escape_feed_text(monkeypatch):
    # Two sites this branch added that interpolate feed text into markdown.
    # The Serves: line joins several route names into ONE st.markdown call, so
    # an unmatched * in one name can pair with one in another and swallow the
    # names between. st.expander renders markdown in its label too, and that
    # label carries both the route name and a headsign-derived title.
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'KM1 BUKIT JALIL', 'arrival_seconds': 60}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [
                            {'route_id': 'R1', 'short': 'T*580', 'long': ''},
                            {'route_id': 'R2', 'short': '65*0', 'long': ''},
                        ])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign',
                        lambda *a: 'to _Pavilion_')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: False)
    st_stub = _render_stop_panel(monkeypatch)

    serves = _texts(st_stub.markdown)
    assert 'T\\*580' in serves and '65\\*0' in serves, serves
    # The raw pair is what could bleed across the joined line.
    assert 'T*580' not in serves, serves

    labels = _texts(st_stub.expander)
    assert 'to \\_Pavilion\\_' in labels, labels
    assert 'to _Pavilion_' not in labels, labels
    assert 'T\\*580' in labels, labels


def test_the_tapped_row_does_not_repeat_the_walk_distance_already_in_the_header(monkeypatch):
    # The tapped stop is in _nearby_stops -- that is how it was confirmed at
    # all -- so its own row would carry a near-you mark repeating verbatim the
    # "~560 m · ~9 min walk" the panel header printed two lines above. The
    # mark exists to point out *other* stops within walking distance.
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'KM1 BUKIT JALIL', 'arrival_seconds': 60}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'T5800', 'short': 'T580', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: False)
    st_stub = _render_stop_panel(monkeypatch)

    # 560 m is the tapped stop's own distance, stated once in the header.
    assert _texts(st_stub.info).count('560 m') == 1, _texts(st_stub.info)
    assert '560 m from you' not in _texts(st_stub.markdown), _texts(st_stub.markdown)


def test_the_near_you_lookup_asks_only_about_stops_a_pattern_calls_at(monkeypatch):
    # This lookup runs before the Arrivals-near-you panel makes its own,
    # smaller one. On a cold grid cell it is therefore the request that
    # *creates* the walk data -- and a failure arms walking._FAIL_UNTIL for
    # 60 s, dropping that panel to "(estimated)" for stops its own request
    # would have routed. Asking only about stops a rendered pattern actually
    # calls at keeps this request no larger than the marks it can draw.
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'LRT AWAN BESAR', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'KM1 BUKIT JALIL', 'arrival_seconds': 60}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'T5800', 'short': 'T580', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: False)

    asked = []
    real_walk_times = live_map.walking.walk_times

    def spy(user_lat, user_lon, stop_list, agency_slug, api_key=None):
        asked.append([s['stop_id'] for s in stop_list])
        return real_walk_times(user_lat, user_lon, stop_list, agency_slug,
                               api_key=api_key)
    monkeypatch.setattr(live_map.walking, 'walk_times', spy)

    # S9 is nearby but on no pattern this panel renders; S2 is on the pattern.
    extra_nearby = [
        {'stop_id': 'S2', 'stop_name': 'KM1 BUKIT JALIL',
         'stop_lat': 3.063, 'stop_lon': 101.671, 'distance_m': 120.0},
        {'stop_id': 'S9', 'stop_name': 'UNRELATED STOP',
         'stop_lat': 3.064, 'stop_lon': 101.672, 'distance_m': 700.0},
    ]
    _render_stop_panel(monkeypatch, extra_nearby=extra_nearby)

    # Three lookups in order: the header's own single tapped stop, the
    # pattern-mark lookup, then the Arrivals-near-you panel's full scan. Only
    # the middle one is under test -- the panel below is entitled to ask about
    # every nearby stop, since it lists them all.
    assert ['S1'] in asked, asked
    assert ['S1', 'S2'] in asked, \
        f"the mark lookup must cover the pattern's nearby stops and no others: {asked}"
    assert asked.index(['S1', 'S2']) < asked.index(['S1', 'S2', 'S9']), \
        f"the narrowed lookup must precede the panel's full scan: {asked}"


def test_stops_before_the_tapped_one_are_not_rendered_as_plus_minus(monkeypatch):
    # A stop the bus passes before reaching yours has a negative offset.
    # Rendering it through the "+N min" branch would print "+-5 min".
    from app_pages import live_map
    stops = [{'stop_id': 'S0', 'stop_name': 'BEFORE', 'arrival_seconds': 0},
             {'stop_id': 'S1', 'stop_name': 'YOURS', 'arrival_seconds': 300},
             {'stop_id': 'S2', 'stop_name': 'AFTER', 'arrival_seconds': 720}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'R', 'short': 'R1', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: False)
    st_stub = _render_stop_panel(monkeypatch)

    said = _texts(st_stub.markdown)
    assert '+-' not in said, said
    assert '5 min earlier' in said
    assert '+7 min' in said


def test_the_stop_panel_says_journey_times_come_from_the_timetable(monkeypatch):
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'A', 'arrival_seconds': 0},
             {'stop_id': 'S2', 'stop_name': 'B', 'arrival_seconds': 60}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'R', 'short': 'R1', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: False)
    st_stub = _render_stop_panel(monkeypatch)

    said = _texts(st_stub.caption)
    assert 'timetable' in said.lower()


def test_the_stop_panel_never_invents_a_departure_time_for_a_headway_route(monkeypatch):
    from app_pages import live_map
    stops = [{'stop_id': 'S1', 'stop_name': 'A', 'arrival_seconds': 21600},
             {'stop_id': 'S2', 'stop_name': 'B', 'arrival_seconds': 21660}]
    monkeypatch.setattr(live_map.gtfs_static, 'get_routes_at_stop',
                        lambda slug, sid: [{'route_id': 'R', 'short': 'R1', 'long': ''}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_route_patterns',
                        lambda *a, **k: [{'trip_id': 't1', 'stops': stops}])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a: True)
    st_stub = _render_stop_panel(monkeypatch)

    said = _texts(st_stub.markdown) + _texts(st_stub.caption)
    assert '06:00' not in said, "a headway trip has no published departure to show"


# ── Network health sparkline ──────────────────────────────────────────────
#
# The hover used to read "(1035, 100)". The first number was the row's
# position in the array, which Plotly substitutes when no x is supplied --
# an internal offset with no meaning to a reader.

def _trend(scores, with_times=True):
    import pandas as pd
    df = pd.DataFrame({'reliability_score': scores})
    if with_times:
        df['datetime'] = pd.to_datetime(
            [1754130000 + i * 83 for i in range(len(scores))], unit='s', utc=True)
    return df


def test_sparkline_hover_states_the_clock_time_not_an_array_index():
    from app_pages import network_health
    fig = network_health._sparkline(_trend([100, 90, 100]), '#00ff00')
    trace = fig.data[0]
    import pandas as pd
    assert trace.x is not None, "no x means Plotly falls back to the array index"
    # Plotly stores these as numpy datetime64, so compare the instants rather
    # than the container types.
    expected = _trend([100, 90, 100])['datetime']
    assert list(pd.to_datetime(trace.x, utc=True)) == list(expected)
    assert '%{x|' in trace.hovertemplate, trace.hovertemplate
    assert 'score' in trace.hovertemplate


def test_sparkline_hover_names_what_the_score_is():
    # "(1035, 100)" gave the reader two bare numbers and no units.
    from app_pages import network_health
    trace = network_health._sparkline(_trend([100, 90]), '#00ff00').data[0]
    assert '<extra></extra>' in trace.hovertemplate, \
        "the trace-name box would otherwise sit beside the value"


def test_sparkline_labels_the_cycle_number_when_there_are_no_timestamps():
    # A bare number is what caused the confusion; if the datetime column is
    # missing, say the number is a cycle rather than printing it naked.
    from app_pages import network_health
    trace = network_health._sparkline(_trend([100, 90], with_times=False), '#00ff00').data[0]
    assert list(trace.x) == [0, 1]
    assert 'cycle' in trace.hovertemplate, trace.hovertemplate


def test_sparkline_keeps_the_score_axis_pinned_to_the_full_range():
    # The trace must stay comparable with the headline score above it: a dip
    # to 60 has to look like a dip, not fill the box because the axis rescaled.
    from app_pages import network_health
    fig = network_health._sparkline(_trend([100, 60]), '#00ff00')
    assert tuple(fig.layout.yaxis.range) == (0, 100)
    assert fig.layout.yaxis.visible is False


# ── Network health: when did this region last have buses? ─────────────────
#
# A region scores 100 "Reliable" while reporting zero vehicles, because an
# EMPTY cycle is a correct answer from a healthy feed. Read off the card that
# is indistinguishable from "buses are running", which is what sent a reader
# to the Live Map expecting vehicles and finding none.

def test_last_vehicles_label_says_plainly_when_there_were_none():
    from app_pages import network_health
    assert network_health._last_vehicles_label(None, 1_000_000) == \
        'no buses reported in this window'


def test_last_vehicles_label_handles_a_missing_value_from_the_mart():
    import pandas as pd
    from app_pages import network_health
    assert network_health._last_vehicles_label(pd.NA, 1_000_000) == \
        'no buses reported in this window'


def test_last_vehicles_label_reads_as_current_when_buses_are_reporting():
    from app_pages import network_health
    assert network_health._last_vehicles_label(999_970, 1_000_000) == 'buses reporting now'


def test_last_vehicles_label_counts_minutes_then_hours():
    from app_pages import network_health
    assert network_health._last_vehicles_label(1_000_000 - 300, 1_000_000) == \
        'buses last seen 5 min ago'
    assert network_health._last_vehicles_label(1_000_000 - 3 * 3600, 1_000_000) == \
        'buses last seen 3h ago'
    assert network_health._last_vehicles_label(1_000_000 - (3 * 3600 + 20 * 60), 1_000_000) == \
        'buses last seen 3h 20m ago'


def test_last_vehicles_label_does_not_report_a_negative_age():
    # Clock skew between the feed's timestamp and ours must not produce
    # "buses last seen -2 min ago".
    from app_pages import network_health
    assert network_health._last_vehicles_label(1_000_060, 1_000_000) == 'buses reporting now'


# ── One stop_id, one stop ─────────────────────────────────────────────────

def _make_stops_zip(tmp_path, name, rows):
    """Build a GTFS static ZIP containing just stops.txt.

    `rows` is a list of (stop_id, stop_name, lat, lon) — passed through
    verbatim, duplicates included, because that is the malformed feed under
    test here.
    """
    import zipfile
    p = tmp_path / f"{name}.zip"
    header = "stop_id,stop_name,stop_lat,stop_lon\n"
    body = "".join(f"{r[0]},{r[1]},{r[2]},{r[3]}\n" for r in rows)
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('stops.txt', header + body)
    return str(p)


def test_a_repeated_stop_id_yields_one_stop(tmp_path, monkeypatch):
    """
    A feed may list the same stop_id twice. live_map keys one st.button per
    stop on that id, and Streamlit raises StreamlitDuplicateElementKey on a
    repeated key — straight into the render, killing the whole Live Map. It
    would also show the stop twice in the panel, ask the walk-time matrix for
    it twice, and draw two rings on one spot.

    Tested here rather than through the page, because the MagicMock st stub
    cannot raise Streamlit's duplicate-key error: a page-level test would pass
    against the very bug it was written for.
    """
    import zipfile
    from utils import gtfs_static
    z = _make_stops_zip(tmp_path, 'kl', [
        ('S1', 'GREEN AVENUE CONDOMINIUM', 3.1401, 101.6801),
        ('S1', 'GREEN AVENUE CONDOMINIUM (DUPLICATE ROW)', 3.1402, 101.6802),
        ('S2', 'KM1 BUKIT JALIL', 3.1403, 101.6803),
    ])
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(z))

    out = gtfs_static.get_stops_near('kl', 3.14, 101.68)

    assert [s['stop_id'] for s in out] == ['S1', 'S2']
    # The nearest occurrence is the one kept — a duplicate must not move a
    # stop further away than the feed says it is.
    assert out[0]['stop_name'] == 'GREEN AVENUE CONDOMINIUM'


def test_a_repeated_stop_id_does_not_spend_one_of_the_limit_places(tmp_path, monkeypatch):
    """De-duplication runs before the limit slice. After it, one duplicated
    row would push a real stop off the end of a five-stop panel."""
    import zipfile
    from utils import gtfs_static
    z = _make_stops_zip(tmp_path, 'kl', [
        ('S1', 'A', 3.1401, 101.6801),
        ('S1', 'A again', 3.14011, 101.6801),
        ('S2', 'B', 3.1403, 101.6803),
    ])
    monkeypatch.setattr(gtfs_static, '_load_zip', lambda slug: zipfile.ZipFile(z))

    out = gtfs_static.get_stops_near('kl', 3.14, 101.68, limit=2)

    assert [s['stop_id'] for s in out] == ['S1', 'S2']


# ── Which other region has stops near me? ─────────────────────────────────

def _stub_stops_near(monkeypatch, by_slug, asked=None):
    """by_slug: {slug: [(stop_id, distance_m), ...]}.

    Pass `asked` (a list) to record every per-agency lookup the scan makes,
    which is how the memoisation tests below tell a served-from-cache call
    from one that walked all fourteen agencies again.

    The region-scan cache is module-level and outlives a test, and every test
    here scans from the same coordinates — without clearing it, the second
    test would be answered from the first one's stubs.
    """
    from utils import gtfs_static

    def fake(slug, lat, lon, radius_m=800, limit=5):
        if asked is not None:
            asked.append((slug, lat, lon))
        rows = by_slug.get(slug)
        if rows is None:
            raise OSError('no timetable for ' + slug)
        return [{'stop_id': sid, 'stop_name': 'S' + sid, 'stop_lat': 3.0,
                 'stop_lon': 101.0, 'distance_m': float(d)}
                for sid, d in rows if d <= radius_m][:limit]

    monkeypatch.setattr(gtfs_static, 'get_stops_near', fake)
    gtfs_static._REGION_STOPS_INDEX.clear()
    return gtfs_static


def test_region_scan_orders_regions_by_their_closest_stop(monkeypatch):
    g = _stub_stops_near(monkeypatch, {
        'prasarana?category=rapid-bus-kl': [('a', 152), ('b', 300)],
        'prasarana?category=rapid-bus-mrtfeeder': [('c', 1200)],
        'ktmb': [],
    })
    out = g.find_regions_with_stops_near(3.0586, 101.6739)
    assert [r['region'] for r in out] == ['Rapid Bus KL', 'Rapid Bus MRT Feeder']
    assert out[0]['nearest_m'] == 152
    assert out[0]['count'] == 2


def test_region_scan_orders_by_distance_not_by_dict_declaration_order(monkeypatch):
    # KTM Berhad is declared 5th in STATIC_API_SOURCES and Rapid Bus KL 1st, so
    # this only passes if the result is actually sorted by nearest_m — dict
    # insertion order alone would put Rapid Bus KL first.
    g = _stub_stops_near(monkeypatch, {
        'prasarana?category=rapid-bus-kl': [('a', 300)],
        'ktmb': [('k', 100)],
    })
    out = g.find_regions_with_stops_near(3.0586, 101.6739)
    assert [r['region'] for r in out] == ['KTM Berhad', 'Rapid Bus KL']


def test_region_scan_excludes_the_region_already_selected(monkeypatch):
    g = _stub_stops_near(monkeypatch, {
        'prasarana?category=rapid-bus-kl': [('a', 152)],
        'ktmb': [('k', 400)],
    })
    out = g.find_regions_with_stops_near(
        3.0586, 101.6739, exclude_slug='prasarana?category=rapid-bus-kl')
    assert [r['region'] for r in out] == ['KTM Berhad']


def test_region_scan_skips_an_agency_whose_timetable_is_unavailable(monkeypatch):
    # One dead feed must not cost the user the other twelve answers.
    g = _stub_stops_near(monkeypatch, {'ktmb': [('k', 400)]})   # every other slug raises
    out = g.find_regions_with_stops_near(3.0586, 101.6739)
    assert [r['region'] for r in out] == ['KTM Berhad']


def test_region_scan_returns_nothing_when_no_region_has_stops(monkeypatch):
    g = _stub_stops_near(monkeypatch, {'ktmb': [], 'mybas-melaka': []})
    assert g.find_regions_with_stops_near(3.0586, 101.6739) == []


def test_region_scan_caps_the_number_of_suggestions(monkeypatch):
    g = _stub_stops_near(monkeypatch, {
        'ktmb': [('a', 100)], 'mybas-melaka': [('b', 200)],
        'mybas-johor': [('c', 300)], 'mybas-kuching': [('d', 400)],
    })
    assert len(g.find_regions_with_stops_near(3.0586, 101.6739, limit=2)) == 2


def test_region_scan_count_reflects_every_stop_in_range_not_a_truncated_page(monkeypatch):
    # get_stops_near filters by radius and sorts, *then* truncates to its own
    # limit param. If find_regions_with_stops_near passed a small limit
    # through, a dense agency with more nearby stops than that limit would
    # report a count lower than what is actually there — understating exactly
    # the dense urban case this feature is for.
    many = [('s' + str(i), 100 + i) for i in range(80)]
    g = _stub_stops_near(monkeypatch, {'prasarana?category=rapid-bus-kl': many})
    out = g.find_regions_with_stops_near(3.0586, 101.6739)
    assert out[0]['count'] == 80


def test_a_second_scan_from_the_same_place_costs_no_agency_lookups(monkeypatch):
    """
    The dead end is sticky — the user has not moved, so every 20-second
    auto-refresh re-entered this scan. Warm that re-read fourteen cached ZIPs;
    cold it downloads up to thirteen of them inside a page render, and one
    agency endpoint that hangs cost REQUEST_TIMEOUT on every single refresh
    because nothing remembered the previous attempt.
    """
    asked = []
    g = _stub_stops_near(monkeypatch, {'ktmb': [('k', 400)]}, asked=asked)

    first = g.find_regions_with_stops_near(3.0586, 101.6739)
    after_first = len(asked)
    second = g.find_regions_with_stops_near(3.0586, 101.6739)

    assert after_first > 1, "the first call should have walked every agency"
    assert len(asked) == after_first, \
        f"the repeat scan hit the feeds again: {asked[after_first:]!r}"
    assert second == first, "a cached answer must be the answer, not a stub of one"


def test_a_scan_from_a_different_place_is_not_answered_from_the_cache(monkeypatch):
    """The cache keys on where you are, not on the fact that a scan happened."""
    asked = []
    g = _stub_stops_near(monkeypatch, {'ktmb': [('k', 400)]}, asked=asked)

    g.find_regions_with_stops_near(3.0586, 101.6739)
    after_first = len(asked)
    # ~1.4 km away: far outside one grid cell.
    g.find_regions_with_stops_near(3.0716, 101.6739)

    assert len(asked) > after_first, \
        "moving to a new place returned the previous place's regions"


def test_gps_jitter_while_standing_still_does_not_re_run_the_scan(monkeypatch):
    """
    A phone reports a slightly different fix every refresh. Keying on raw
    coordinates would make the cache useless for the one situation it exists
    for — a stationary user at a dead end — so it keys on the same location
    grid walking's cache uses.
    """
    from utils import walking
    asked = []
    g = _stub_stops_near(monkeypatch, {'ktmb': [('k', 400)]}, asked=asked)

    g.find_regions_with_stops_near(3.0586, 101.6739)
    after_first = len(asked)
    # A tenth of a grid cell — a few metres, well inside any GPS fix's own error.
    g.find_regions_with_stops_near(3.0586 + walking.GRID_DEGREES / 10, 101.6739)

    assert len(asked) == after_first, \
        "a few metres of jitter re-ran the whole fourteen-agency scan"


def test_a_stale_scan_is_re_run_rather_than_kept_for_the_life_of_the_process(monkeypatch):
    """
    The cached row records which agencies *answered*, so an agency skipped
    because its feed was briefly unreadable would stay missing from the hint
    forever if this never expired — the same failure _ROUTE_PARTS_INDEX
    refuses to cache a failed read to avoid.
    """
    asked = []
    g = _stub_stops_near(monkeypatch, {'ktmb': [('k', 400)]}, asked=asked)

    g.find_regions_with_stops_near(3.0586, 101.6739)
    after_first = len(asked)
    # Age every entry past the TTL rather than sleeping through it.
    for key, (stored_at, rows) in list(g._REGION_STOPS_INDEX.items()):
        g._REGION_STOPS_INDEX[key] = (
            stored_at - g.REGION_SCAN_TTL_SECONDS - 1, rows)

    g.find_regions_with_stops_near(3.0586, 101.6739)
    assert len(asked) > after_first, "an expired scan was served from the cache"


def test_an_unusable_coordinate_still_does_not_raise(monkeypatch):
    """
    The caller runs this inside a Streamlit render with no guard of its own.
    Before the cache existed, a NaN latitude was rejected per-agency inside
    get_stops_near's try; snapping it to a grid key must not turn that into a
    crash.
    """
    g = _stub_stops_near(monkeypatch, {'ktmb': [('k', 400)]})
    # Both of these raise out of snap_to_grid — ValueError for NaN, TypeError
    # for None — and must be absorbed into an uncached scan, not re-raised.
    assert isinstance(g.find_regions_with_stops_near(float('nan'), 101.6739), list)
    assert isinstance(g.find_regions_with_stops_near(None, 101.6739), list)
    assert g._REGION_STOPS_INDEX == {}, \
        "an unusable coordinate must not be written to the cache"


def test_a_caller_mutating_the_result_cannot_poison_the_cache(monkeypatch):
    g = _stub_stops_near(monkeypatch, {'ktmb': [('k', 400)]})
    out = g.find_regions_with_stops_near(3.0586, 101.6739)
    out[0]['region'] = 'MUTATED'
    assert g.find_regions_with_stops_near(3.0586, 101.6739)[0]['region'] == 'KTM Berhad'


# ── Route aliases ─────────────────────────────────────────────────────────

def test_gokl14_resolves_to_the_route_the_feed_publishes():
    # The bus is branded GOKL14; no GOKL route exists anywhere in the feed.
    from utils import gtfs_static
    resolved, source = gtfs_static.resolve_route_alias('GOKL14')
    assert resolved == 'PAVILION BUKIT JALIL (PAVBJ)'
    assert source == 'GOKL14', "the caller needs this to disclose the alias"


def test_alias_lookup_is_case_and_space_insensitive():
    from utils import gtfs_static
    for q in ('gokl14', '  GoKL14 '):
        resolved, source = gtfs_static.resolve_route_alias(q)
        assert resolved == 'PAVILION BUKIT JALIL (PAVBJ)', q
        assert source is not None


def test_a_real_route_name_is_never_rewritten():
    from utils import gtfs_static
    resolved, source = gtfs_static.resolve_route_alias('T580')
    assert resolved == 'T580'
    assert source is None, "no disclosure when the feed's own name matched"


def test_an_unknown_query_passes_through_untouched():
    from utils import gtfs_static
    assert gtfs_static.resolve_route_alias('ZZZ9') == ('ZZZ9', None)
    assert gtfs_static.resolve_route_alias('') == ('', None)
    assert gtfs_static.resolve_route_alias(None) == ('', None)


# ── A region whose feed went quiet ────────────────────────────────────────
#
# Rapid Bus KL's realtime feed returned HTTP 200 with a 15-byte body and zero
# entities while the MRT Feeder returned 102 vehicles the same second. The
# page answered that by returning early, which deleted the map -- and with it
# the user's own location marker, the nearby stop rings and the whole
# tapped-stop panel. Stops come from the published timetable and never needed
# a live vehicle to exist.

def _quiet_region_frame(freshness='fresh'):
    """
    A df_live in which the selected region ('Rapid Bus KL') contributes no
    drawable rows, while another region still reports.

    Non-empty overall, deliberately: an entirely empty frame takes the
    network-wide branch near the top of show(), which is a different message
    about a different cause. Only a frame with rows for some other region
    reaches the regional paths these tests exercise.

    freshness='hidden' additionally puts a Rapid Bus KL row in the frame that
    the hidden-vehicle filter then removes — the *second* regional emptiness,
    with its own warning, reached only when the region did report.
    """
    rows = [{'region': 'Rapid Bus MRT Feeder', 'vehicle_id': 'M1',
             'latitude': 3.14, 'longitude': 101.68, 'bearing': 90.0,
             'speed': 10.0, 'timestamp': int(time.time()),
             'trip_id': 'T9', 'route_id': 'T9000',
             'freshness': 'fresh', 'age_seconds': 5}]
    if freshness == 'hidden':
        rows.append({'region': 'Rapid Bus KL', 'vehicle_id': 'V1',
                     'latitude': 3.06, 'longitude': 101.67, 'bearing': 90.0,
                     'speed': 0.0, 'timestamp': int(time.time()) - 4000,
                     'trip_id': 'T1', 'route_id': 'T5800',
                     'freshness': 'hidden', 'age_seconds': 4000})
    return pd.DataFrame(rows)


def _live_map_no_vehicles(monkeypatch, df=None, located=True):
    """
    Drive show() with the selected region reporting nothing that can be drawn.

    `located` False drops the user's location, which is the one case where
    there is genuinely nothing to put on a map at all.
    """
    from app_pages import live_map

    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(
        monkeypatch, empty, df=_quiet_region_frame() if df is None else df)
    if located:
        st_stub.session_state['user_location'] = {'lat': 3.06, 'lon': 101.67,
                                                  'accuracy': 10}
    monkeypatch.setattr(
        live_map.gtfs_static, 'get_stops_near',
        lambda *a, **k: [{'stop_id': 'S3', 'stop_name': 'GREEN AVENUE CONDOMINIUM',
                          'stop_lat': 3.0621, 'stop_lon': 101.6706,
                          'distance_m': 220.0}])
    live_map.show()
    return st_stub


def test_the_map_still_renders_when_the_region_has_no_vehicles(monkeypatch):
    # Rapid Bus KL's feed went quiet upstream. The map, the user's marker and
    # the stop rings all vanished with it -- but stops come from the timetable
    # and never needed a live vehicle.
    st_stub = _live_map_no_vehicles(monkeypatch)
    assert st_stub.pydeck_chart.called, "the deck was not built"
    said = _texts(st_stub.warning)
    assert 'has reported in the last' in said, "the cause must still be named"


def test_a_cleared_bus_token_expires_even_on_a_render_with_no_vehicles(monkeypatch):
    """
    "One render, not forever" — but the pop sat inside the `no_vehicles`
    guard, so a feed that went quiet on the render right after a clear kept
    the token alive for the whole outage, and the first re-tap of that bus
    once it came back was swallowed. The stop-side pop was always outside its
    guard; these two paths must behave the same way.
    """
    from app_pages import live_map

    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(
        monkeypatch, empty, df=_quiet_region_frame())
    st_stub.session_state['user_location'] = {'lat': 3.06, 'lon': 101.67,
                                              'accuracy': 10}
    # A stop keeps the render off the dead-end region scan, which is not what
    # this test is about.
    monkeypatch.setattr(
        live_map.gtfs_static, 'get_stops_near',
        lambda *a, **k: [{'stop_id': 'S3', 'stop_name': 'GREEN AVENUE CONDOMINIUM',
                          'stop_lat': 3.0621, 'stop_lon': 101.6706,
                          'distance_m': 220.0}])
    st_stub.session_state['cleared_vehicle_id'] = 'V1'
    st_stub.session_state['cleared_stop_id'] = 'S3'

    live_map.show()

    assert 'cleared_vehicle_id' not in st_stub.session_state, \
        "the dismissal token outlived its one render because the feed was quiet"
    assert 'cleared_stop_id' not in st_stub.session_state, \
        "the stop-side token must keep expiring on the same render"


def test_nearby_stops_are_still_offered_when_no_vehicle_is_reporting(monkeypatch):
    st_stub = _live_map_no_vehicles(monkeypatch)
    # The stop name is a button label now, not markdown text (Task 1).
    said = (_texts(st_stub.markdown) + _texts(st_stub.info) + _texts(st_stub.caption)
            + _texts(st_stub.button))
    assert 'GREEN AVENUE' in said, "timetable stops disappeared with the buses"


def test_no_vehicle_layer_is_built_when_there_are_no_vehicles(monkeypatch):
    # Building a vehicles layer from an empty frame would need columns that
    # were never computed.
    st_stub = _live_map_no_vehicles(monkeypatch)
    deck = st_stub.pydeck_chart.call_args[0][0]
    ids = [l.id for l in deck.layers]
    assert 'vehicles' not in ids, ids
    assert 'nearby-stops' in ids, ids


def test_the_user_marker_survives_a_region_with_no_vehicles(monkeypatch):
    # "if my location is not inside the selected region, the bus stop near me
    # also disappear" -- the marker and its accuracy circle are the user's own
    # data and owe nothing to the feed.
    st_stub = _live_map_no_vehicles(monkeypatch)
    ids = [l.id for l in st_stub.pydeck_chart.call_args[0][0].layers]
    assert 'user-location' in ids, ids
    assert 'user-accuracy' in ids, ids


def test_arrivals_panel_says_no_bus_is_coming_rather_than_vanishing(monkeypatch):
    st_stub = _live_map_no_vehicles(monkeypatch)
    said = _texts(st_stub.caption)
    assert 'no bus currently en route to this stop' in said, said
    assert 'Showing 0 active vehicles' not in said, \
        "a vehicle count is not a claim this frame can support"


def test_the_map_survives_a_region_whose_vehicles_are_all_too_old(monkeypatch):
    # The second regional emptiness: the region *did* report, but every vehicle
    # is older than the drawn window, so the hidden filter empties the frame.
    # It returned early too, and cost the same map.
    st_stub = _live_map_no_vehicles(monkeypatch, df=_quiet_region_frame('hidden'))
    said = _texts(st_stub.warning)
    assert 'No recent data for Rapid Bus KL' in said, said
    assert '1 vehicle(s) last reported over' in said, said
    ids = [l.id for l in st_stub.pydeck_chart.call_args[0][0].layers]
    assert 'nearby-stops' in ids, ids
    assert 'vehicles' not in ids, ids


def test_a_quiet_region_with_no_location_renders_without_raising(monkeypatch):
    # No vehicles and no location leaves nothing to draw and nothing to centre
    # a camera on, so no deck is built -- and the selection readers below it
    # must cope with that rather than take the page down.
    st_stub = _live_map_no_vehicles(monkeypatch, located=False)
    assert not st_stub.pydeck_chart.called, "there was nothing to put on a map"
    assert 'has reported in the last' in _texts(st_stub.warning)
    assert 'Locate Me' in _texts(st_stub.info)


def test_a_bus_selected_before_the_feed_went_quiet_does_not_break_the_page(monkeypatch):
    # The sticky selection is re-resolved against df_map on every render. An
    # empty region frame carries no vehicle_id column to resolve it against,
    # so the lookup has to be skipped, not attempted and caught.
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(
        monkeypatch, empty, df=_quiet_region_frame())
    st_stub.session_state['user_location'] = {'lat': 3.06, 'lon': 101.67, 'accuracy': 10}
    st_stub.session_state['selected_vehicle_id'] = 'V1'
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [])

    live_map.show()   # must not raise

    assert 'has reported in the last' in _texts(st_stub.warning)


def test_the_camera_follows_the_buses_when_a_quiet_region_recovers(monkeypatch):
    # Two renders, because one cannot catch this. The quiet render centres on
    # the user -- correctly, it is the only anchor there is -- but it also
    # writes the bookkeeping that decides whether the *next* render re-centres.
    # With that written and neither the region nor the search changed, the
    # render where the feed came back left the camera on the user and drew the
    # buses off-screen, beneath a "Showing N active vehicles" caption: exactly
    # the banner-over-an-empty-map failure the re-centre rule exists to prevent.
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(
        monkeypatch, empty, df=_quiet_region_frame())
    # Penang, far outside the selected KL region -- the reported case, "if my
    # location is not inside the selected region".
    st_stub.session_state['user_location'] = {'lat': 5.41, 'lon': 100.33, 'accuracy': 10}
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [])

    live_map.show()
    assert round(st_stub.session_state['map_view_state']['latitude'], 2) == 5.41, \
        "the quiet render should anchor on the user; nothing else exists to anchor on"

    # 20 seconds later the feed is back. Same region, same (empty) search --
    # the user changed nothing, so only the vehicles returning can move the view.
    recovered = pd.DataFrame({
        'region': ['Rapid Bus KL', 'Rapid Bus KL'], 'vehicle_id': ['V1', 'V2'],
        'latitude': [3.05, 3.07], 'longitude': [101.66, 101.68],
        'bearing': [90.0, 90.0], 'speed': [10.0, 10.0],
        'timestamp': [now, now], 'trip_id': ['T1', 'T2'],
        'route_id': ['T5800', 'T5800'],
        'freshness': ['fresh', 'fresh'], 'age_seconds': [5, 5],
    })
    monkeypatch.setattr(
        live_map.db, 'get_live_data_optimized',
        lambda *a, **k: (recovered, {'total': 2, 'stale': 0, 'hidden': 0,
                                     'regions': 1, 'busiest': 'Rapid Bus KL'}, 'now'))

    # The 20 seconds, spelled out. A finished fetch is the only thing that
    # replaces the held frame, so that is how new vehicles reach the page at
    # all. Driven through the same seam the page uses -- a fetch was in flight
    # and is no longer -- rather than by reaching for the private helper.
    # Without it the second render redraws the frame the first one held, which
    # is the whole point of holding it: see `live_map._live_frame`.
    st_stub.session_state['fetch_in_flight'] = True
    monkeypatch.setattr(live_map.background_fetch, 'is_running', lambda: False)

    live_map.show()

    view = st_stub.session_state['map_view_state']
    assert round(view['latitude'], 2) == 3.06, \
        f"the camera stayed put while the buses were drawn off-screen: {view}"
    assert round(view['longitude'], 2) == 101.67, view


def test_a_quiet_region_does_not_yank_the_camera_back_on_every_refresh(monkeypatch):
    # The other half of the same rule. Re-arming the trigger by simply never
    # writing the bookkeeping would leave region_changed true for every render
    # of the outage, so each 20-second auto-refresh would drag the viewport off
    # wherever the user had panned to.
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(
        monkeypatch, empty, df=_quiet_region_frame())
    st_stub.session_state['user_location'] = {'lat': 5.41, 'lon': 100.33, 'accuracy': 10}
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [])

    live_map.show()
    # The user pans away while the feed is still quiet.
    st_stub.session_state['map_view_state']['latitude'] = 5.99
    live_map.show()

    assert st_stub.session_state['map_view_state']['latitude'] == 5.99, \
        "an auto-refresh during an outage pulled the viewport back"


# ---------------------------------------------------------------------------
# The map has to stay still between renders, or a tap on it is lost
#
# Reported as "when i click the bus stop ring the page flashing but do
# nothing", and separately "the bus also cannot be clicked ... but if i apply
# a route name filter, then clicked the available bus, it works" -- the filter
# left too few buses for any of them to change state in that instant, which is
# the clue that named the cause.
#
# `get_live_data_optimized` anchors its window on wall-clock now, so two calls
# a second apart disagree with no new data at all: the cutoff moved, and a bus
# crossed from fresh to stale. Both are drawn -- a dimmer dot, a "stale" line
# in a tooltip -- so the deck spec differs. Streamlit folds the deck spec into
# the chart's identity, so the rerun a tap triggers built a *different* chart,
# and a different chart has no tap recorded against it. Every tap was
# discarded before any code could read it.
# ---------------------------------------------------------------------------


def _moving_window_frames(now):
    """Two frames one tick apart: same bus, one second older and now stale."""
    def frame(freshness, age):
        return pd.DataFrame({
            'region': ['Rapid Bus KL'], 'vehicle_id': ['V1'],
            'latitude': [3.14], 'longitude': [101.68], 'bearing': [90.0],
            'speed': [10.0], 'timestamp': [now], 'trip_id': ['T1'],
            'route_id': ['T5800'], 'freshness': [freshness],
            'age_seconds': [age],
        })
    return [frame('fresh', 5), frame('stale', 400)]


def test_the_map_is_unchanged_across_a_rerun_so_a_tap_on_it_survives(monkeypatch):
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)

    frames = iter(_moving_window_frames(now))
    monkeypatch.setattr(
        live_map.db, 'get_live_data_optimized',
        lambda *a, **k: (next(frames), {'total': 1, 'stale': 0, 'hidden': 0,
                                        'regions': 1, 'busiest': 'Rapid Bus KL'},
                         'now'))

    live_map.show()
    live_map.show()

    first, second = (c[0][0] for c in st_stub.pydeck_chart.call_args_list[:2])
    assert first.to_json() == second.to_json(), \
        "the map changed under a rerun, so Streamlit would drop the tap"


def test_the_database_is_read_once_per_refresh_not_once_per_rerun(monkeypatch):
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)

    reads = []
    frames = iter(_moving_window_frames(now))
    def counted(*a, **k):
        reads.append(1)
        return (next(frames), {'total': 1, 'stale': 0, 'hidden': 0,
                               'regions': 1, 'busiest': 'Rapid Bus KL'}, 'now')
    monkeypatch.setattr(live_map.db, 'get_live_data_optimized', counted)

    live_map.show()
    live_map.show()

    assert len(reads) == 1, f"the frame was re-read on a plain rerun: {len(reads)} reads"


def test_refreshing_replaces_the_held_frame(monkeypatch):
    # Holding the frame must not mean holding it forever -- pressing Refresh
    # Data has to show the data that press just fetched.
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)

    reads = []
    frames = iter(_moving_window_frames(now))
    def counted(*a, **k):
        reads.append(1)
        return (next(frames), {'total': 1, 'stale': 0, 'hidden': 0,
                               'regions': 1, 'busiest': 'Rapid Bus KL'}, 'now')
    monkeypatch.setattr(live_map.db, 'get_live_data_optimized', counted)

    live_map.show()
    st_stub.button.return_value = True         # the user presses Refresh Data
    live_map.show()

    assert len(reads) == 2, "Refresh Data redrew the frame it was meant to replace"


def test_auto_refresh_fetches_on_a_tick_and_not_on_every_rerun(monkeypatch):
    # Fetching on every rerun refetched every agency feed whenever the user
    # touched anything -- the several-second pause reported as "if i change the
    # map appearance, my location got reset but would restored after waiting
    # for like 5 seconds". It also moved the map out from under the tap being
    # handled, which is the bug above. The fetch runs on a thread now, so the
    # rule is asserted where the page and the thread meet.
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['auto_refresh'] = True

    started = []
    monkeypatch.setattr(live_map.background_fetch, 'start',
                        lambda: started.append(1) or True)

    st_stub.session_state['auto_refresh_counter'] = 1
    live_map.show()
    live_map.show()                            # same tick: a tap, not a timer
    assert len(started) == 1, f"a rerun refetched the network: {len(started)} fetches"

    st_stub.session_state['auto_refresh_counter'] = 2
    live_map.show()
    assert len(started) == 2, "the 20-second timer stopped fetching"


def test_the_auto_refresh_never_blocks_the_render(monkeypatch):
    # The whole point: 3-10 seconds of waiting on api.data.gov.my used to sit
    # in front of the page on every tick, for data already 26-124 seconds old.
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['auto_refresh'] = True
    st_stub.session_state['auto_refresh_counter'] = 1

    blocking = []
    monkeypatch.setattr(live_map, 'fetch_and_store_transit_data',
                        lambda *a, **k: blocking.append(1))

    live_map.show()

    assert not blocking, "the render waited on the network again"


def test_a_running_fetch_is_visible_to_the_user(monkeypatch):
    # A refresh the user cannot see is a page they cannot tell from a stuck
    # one. This note is what buys the right to fetch out of sight.
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)
    monkeypatch.setattr(live_map.background_fetch, 'is_running', lambda: True)

    live_map.show()

    assert 'updating' in _texts(st_stub.success), _texts(st_stub.success)


def test_nothing_says_updating_when_no_fetch_is_running(monkeypatch):
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)

    live_map.show()

    assert 'updating' not in _texts(st_stub.success)


def test_a_finished_fetch_replaces_the_held_frame(monkeypatch):
    # The thread owns no session state and must not reach into any, so the
    # page notices the fetch has ended and drops the frame itself.
    from app_pages import live_map
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)

    reads = []
    frames = iter(_moving_window_frames(now))
    def counted(*a, **k):
        reads.append(1)
        return (next(frames), {'total': 1, 'stale': 0, 'hidden': 0,
                               'regions': 1, 'busiest': 'Rapid Bus KL'}, 'now')
    monkeypatch.setattr(live_map.db, 'get_live_data_optimized', counted)

    live_map.show()
    st_stub.session_state['fetch_in_flight'] = True    # one ran while away
    live_map.show()

    assert len(reads) == 2, "the page kept the frame a finished fetch replaced"


# ---------------------------------------------------------------------------
# Every second tap on the map did nothing
#
# Reported from five screenshots: tap a stop, the panel is right; tap the next
# stop, the panel still names the previous one; tap a third, it catches up.
# Odd taps worked, even taps were swallowed.
#
# The stops layer is built from `selected_stop_id` *before* `st.pydeck_chart`
# hands back this render's tap, so the drawn map is always one selection
# behind. That would be a cosmetic lag on the magenta ring if the highlight
# were not part of the deck spec -- but it is, and Streamlit folds the deck
# spec into the chart's identity. So a tap that moved the selection left the
# chart on screen with an id the *next* render would not rebuild, and the next
# tap was looked up under an id with no widget state behind it.
#
# Rerunning as soon as the selection moves keeps the drawn chart and the next
# render's chart the same chart.
# ---------------------------------------------------------------------------


_STOP_S1 = {'stop_id': 'S1', 'stop_name': 'Tapped Stop',
            'stop_lat': 3.1401, 'stop_lon': 101.6801, 'distance_m': 50.0}
_STOP_S2 = {'stop_id': 'S2', 'stop_name': 'Second Stop',
            'stop_lat': 3.1402, 'stop_lon': 101.6802, 'distance_m': 70.0}


def _live_map_for_tap(monkeypatch, payload_objects):
    from app_pages import live_map as _lm            # noqa: F401  (import shape)
    selection = SimpleNamespace(selection=SimpleNamespace(objects=payload_objects))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [_STOP_S1, _STOP_S2])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])
    return live_map, st_stub


def test_a_tap_that_moves_the_stop_selection_redraws_the_map(monkeypatch):
    live_map, st_stub = _live_map_for_tap(
        monkeypatch, {"nearby-stops": [{"stop_id": "S2"}]})
    st_stub.session_state['selected_stop_id'] = 'S1'   # S1 is what is drawn

    st_stub.rerun.reset_mock()
    live_map.show()

    assert st_stub.session_state['selected_stop_id'] == 'S2'
    assert st_stub.rerun.called, \
        "the map was left drawn with the old highlight, so the next tap is lost"


def test_re_tapping_the_selected_stop_does_not_redraw(monkeypatch):
    # The guard. Re-tapping the stop already selected leaves the deck spec --
    # and so the chart's id -- untouched, so Streamlit can hand the same
    # payload back on the next render. An unguarded rerun would spin on it.
    live_map, st_stub = _live_map_for_tap(
        monkeypatch, {"nearby-stops": [{"stop_id": "S1"}]})
    st_stub.session_state['selected_stop_id'] = 'S1'

    st_stub.rerun.reset_mock()
    live_map.show()

    assert st_stub.session_state['selected_stop_id'] == 'S1'
    assert not st_stub.rerun.called, "a repeated payload would rerun forever"


def test_a_bus_tap_that_drops_a_stop_selection_redraws_the_map(monkeypatch):
    # Same cause from the other direction: tapping a bus clears the stop
    # selection, which un-highlights a ring, which changes the deck spec.
    live_map, st_stub = _live_map_for_tap(
        monkeypatch, {"vehicles": [{"vehicle_id": "V1"}]})
    st_stub.session_state['selected_stop_id'] = 'S1'

    st_stub.rerun.reset_mock()
    live_map.show()

    assert st_stub.session_state['selected_stop_id'] is None
    assert st_stub.rerun.called, \
        "the ring stayed highlighted after a bus tap, so the next tap is lost"


def test_a_render_with_no_tap_does_not_redraw(monkeypatch):
    # An auto-refresh must not turn into a rerun loop.
    live_map, st_stub = _live_map_for_tap(monkeypatch, {})
    st_stub.session_state['selected_stop_id'] = 'S1'

    st_stub.rerun.reset_mock()
    live_map.show()

    assert not st_stub.rerun.called


# ---------------------------------------------------------------------------
# The map and "Arrivals near you" describe the same stop
#
# The map draws a ring for every stop the scan found; the list is a ranked top
# few. So tapping a ring usually selected a stop the list did not contain, and
# the list's only feedback -- the highlighted name -- was missing from the one
# place the user was looking. Asked for directly: "i want the map and the
# arrivals near you list to be sync."
# ---------------------------------------------------------------------------


def _many_stops(n=9):
    return [{'stop_id': f'S{i}', 'stop_name': f'STOP {i}',
             'stop_lat': 3.14 + i / 1000, 'stop_lon': 101.68,
             'distance_m': 50.0 + i * 10}
            for i in range(n)]


def _stop_buttons(st_stub):
    """(name, is_selected) for each stop button drawn in the list."""
    out = []
    for c in st_stub.button.call_args_list:
        key = c.kwargs.get('key') or ''
        if str(key).startswith('pick_stop_'):
            out.append((str(c.args[0]), c.kwargs.get('type') == 'primary'))
    return out


def _live_map_for_list(monkeypatch, selected=None):
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    if selected:
        st_stub.session_state['selected_stop_id'] = selected
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near',
                        lambda *a, **k: _many_stops())
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])
    return live_map, st_stub


def test_a_stop_tapped_on_the_map_is_listed_even_when_it_did_not_rank(monkeypatch):
    # S8 is the furthest of nine and would never make the ranked list.
    live_map, st_stub = _live_map_for_list(monkeypatch, selected='S8')

    live_map.show()

    listed = _stop_buttons(st_stub)
    assert any('STOP 8' in name for name, _ in listed), \
        f"the tapped stop was left out of the list: {[n for n, _ in listed]}"


def test_the_tapped_stop_is_listed_first_and_highlighted(monkeypatch):
    live_map, st_stub = _live_map_for_list(monkeypatch, selected='S8')

    live_map.show()

    listed = _stop_buttons(st_stub)
    assert listed, "no stop buttons were drawn"
    name, is_selected = listed[0]
    assert 'STOP 8' in name, f"the tapped stop was not first: {listed[0]}"
    assert is_selected, "the tapped stop was listed but not highlighted"
    assert sum(1 for _, sel in listed if sel) == 1, \
        f"more than one stop was highlighted: {listed}"


def test_pinning_the_tapped_stop_does_not_lengthen_the_list(monkeypatch):
    live_map, st_stub_plain = _live_map_for_list(monkeypatch)
    live_map.show()
    without = len(_stop_buttons(st_stub_plain))

    live_map, st_stub_picked = _live_map_for_list(monkeypatch, selected='S8')
    live_map.show()
    with_pin = len(_stop_buttons(st_stub_picked))

    assert with_pin == without, \
        f"the list grew under the user: {without} -> {with_pin}"


def test_nothing_is_highlighted_when_no_stop_is_selected(monkeypatch):
    live_map, st_stub = _live_map_for_list(monkeypatch)

    live_map.show()

    assert not any(sel for _, sel in _stop_buttons(st_stub))


# ---------------------------------------------------------------------------
# The Route Viewer follows the tapped bus, and never swaps it for another
#
# Reported as "when i choose PAVBJ VGJ8310 bus in route viewer, it persist to
# CDH2336". The options were every drawn vehicle, rebuilt from `df_map` every
# render -- and `df_map` drops a bus five minutes after it stops reporting and
# narrows again under a route search. Streamlit answers a stored selection
# that has left the options by falling back to the first option and saying
# nothing, so the viewer showed one bus's id above another bus's route.
# ---------------------------------------------------------------------------


def _vehicle_picker_calls(st_stub):
    return [c for c in st_stub.selectbox.call_args_list
            if c.args and c.args[0] == "Select Vehicle"]


def _route_viewer_frame(now):
    """One drawn bus and one that has gone quiet -- quiet is dropped by df_map."""
    return pd.DataFrame({
        'region': ['Rapid Bus KL', 'Rapid Bus KL'],
        'vehicle_id': ['V1', 'VQUIET'],
        'latitude': [3.14, 3.15], 'longitude': [101.68, 101.69],
        'bearing': [90.0, 90.0], 'speed': [10.0, 0.0],
        'timestamp': [now, now], 'trip_id': ['T1', 'TQUIET'],
        'route_id': ['R1', 'RQUIET'],
        'freshness': ['fresh', 'hidden'], 'age_seconds': [5, 900],
    })


def test_the_route_viewer_asks_for_a_tap_when_no_bus_is_selected(monkeypatch):
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)

    live_map.show()

    assert not _vehicle_picker_calls(st_stub), \
        "a vehicle picker was drawn with no bus selected"
    assert 'Tap a bus on the map' in _texts(st_stub.info)


def test_the_route_viewer_offers_only_the_tapped_bus(monkeypatch):
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['selected_vehicle_id'] = 'V1'

    live_map.show()

    calls = _vehicle_picker_calls(st_stub)
    assert calls, "the vehicle picker was not drawn for the selected bus"
    assert calls[0].kwargs.get('options') == ['V1'], calls[0].kwargs
    # No widget key: a stored value is the only thing that can go stale, and
    # with one option there is nothing worth storing.
    assert 'key' not in calls[0].kwargs, calls[0].kwargs


def test_a_quiet_bus_keeps_its_own_route_instead_of_borrowing_another(monkeypatch):
    # VQUIET is in the live frame but not in df_map, because df_map drops
    # anything older than the draw cutoff. Resolving against df_map returned an
    # empty row, and the picker had already fallen back to a different bus.
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(
        monkeypatch, empty, df=None)
    monkeypatch.setattr(
        live_map.db, 'get_live_data_optimized',
        lambda *a, **k: (_route_viewer_frame(now),
                         {'total': 1, 'stale': 0, 'hidden': 1, 'regions': 1,
                          'busiest': 'Rapid Bus KL'}, 'now'))
    st_stub.session_state['selected_vehicle_id'] = 'VQUIET'

    asked = []
    monkeypatch.setattr(live_map.gtfs_static, 'get_shapes_for_trip',
                        lambda slug, trip: asked.append(trip) or [])

    live_map.show()

    assert 'TQUIET' in asked, \
        f"the viewer resolved a different bus's trip: {asked}"
    assert 'has not reported' not in _texts(st_stub.warning)


def test_the_live_map_prints_no_raw_position_table(monkeypatch):
    # 📊 Data Table is the page for reading rows. The Route Viewer used to
    # print one vehicle's raw positions underneath its map -- the same columns,
    # for one vehicle, with no filter, sort or download. The trail itself is
    # still drawn as a line; only the table is gone.
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['selected_vehicle_id'] = 'V1'
    trail = pd.DataFrame({
        'vehicle_id': ['V1', 'V1'], 'latitude': [3.14, 3.15],
        'longitude': [101.68, 101.69], 'bearing': [90.0, 91.0],
        'speed': [10.0, 11.0], 'timestamp': [now - 60, now],
    })
    monkeypatch.setattr(live_map.db, 'get_vehicle_trail', lambda *a, **k: trail)

    live_map.show()

    assert not st_stub.dataframe.called, \
        "the Live Map printed a raw position table again"


def test_a_bus_gone_from_the_window_is_named_not_replaced(monkeypatch):
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['selected_vehicle_id'] = 'GHOST'

    live_map.show()

    said = _texts(st_stub.warning)
    assert 'GHOST' in said and 'has not reported' in said, said
    assert _vehicle_picker_calls(st_stub)[0].kwargs.get('options') == ['GHOST'], \
        "the viewer switched to a bus the user did not choose"


# ---------------------------------------------------------------------------
# Task 3: progressive radius and the region hint
#
# Standing in Bukit Jalil with KTM Berhad selected, the arrivals panel said
# only "No stops found within 800 m of you in KTM Berhad" -- true, and
# useless, since Rapid Bus KL had 15 stops within that same 800 m and the app
# had the data to know it. These tests cover the widened second search and
# the region hint at the true dead end.
# ---------------------------------------------------------------------------

# Captured at import time, before any test has had a chance to monkeypatch
# it, so `_render_live_map` can tell "a caller already stubbed get_stops_near"
# apart from "nobody has" -- see the comment inside the fixture.
from utils import gtfs_static as _gtfs_static_module
_REAL_GET_STOPS_NEAR = _gtfs_static_module.get_stops_near

# The default nearby-stops fixture Task 1's tests drive when they don't
# supply their own. 'S1' is the name a real click test names by label, so it
# must be first (the panel's unserved-fill keeps scan order) and it must be
# distinguishable from the rest for the highlight tests.
_DEFAULT_NEARBY_STOPS = [
    {'stop_id': 'S1', 'stop_name': 'KL1743 GREEN AVENUE CONDOMINIUM',
     'stop_lat': 3.1405, 'stop_lon': 101.6805, 'distance_m': 120.0},
    {'stop_id': 'S2', 'stop_name': 'KL1291 KM1 BUKIT JALIL',
     'stop_lat': 3.1408, 'stop_lon': 101.6809, 'distance_m': 260.0},
]


def _render_live_map(monkeypatch, route_query='', selected_stop_id=None,
                      pressed_button=None):
    """Drive live_map.show() through the stop-resolution and arrivals code:
    a selected region with vehicles, plus a known user_location, so the page
    reaches the stop-resolution and arrivals branches rather than exiting
    early on "no location" or "no vehicles".

    Built from the same parts as `_live_map_with_selection` -- a realistic
    (non-MagicMock) pydeck_chart selection payload, so tap-a-bus takes its
    real parsing path -- plus a `route_query` forwarded to the search box
    stub, defaulting to no active search. Task 4 reuses this with
    route_query='GOKL14'.

    `selected_stop_id`, when given, seeds session_state['selected_stop_id']
    before show() runs, as a ring tap would have left it. `pressed_button`,
    when given, makes st.button(...) return True only for that label --
    every other button (refresh, clear selection, the other stops) stays
    False -- so a test can simulate clicking one particular stop's name.

    Callers monkeypatch `live_map.gtfs_static` (get_stops_near, and where
    relevant find_regions_with_stops_near) *before* calling this, since
    show() runs inside it; the stubbed `st` is returned so a test can read
    the text mocks afterward. A caller that does not supply its own
    get_stops_near gets _DEFAULT_NEARBY_STOPS instead of the real
    (network-touching) lookup -- the identity check against
    _REAL_GET_STOPS_NEAR is what lets a caller's own monkeypatch, applied
    before this function runs, win over that default rather than being
    clobbered by it.
    """
    from app_pages import live_map

    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)
    st_stub.text_input.return_value = route_query
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    if selected_stop_id is not None:
        st_stub.session_state['selected_stop_id'] = selected_stop_id
    # A stop is only ever reached via nearby/_arrivals in these tests, never
    # via the tapped-vehicle trip lookup (the selection is always empty) --
    # but arrivals_for_stops still calls these for the one live vehicle, so
    # they must be safe to call rather than touching a real GTFS zip.
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: '')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: True)
    if live_map.gtfs_static.get_stops_near is _REAL_GET_STOPS_NEAR:
        monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near',
                            lambda *a, **k: [dict(s) for s in _DEFAULT_NEARBY_STOPS])
    if pressed_button is not None:
        # Substring, not equality: a stop's label carries Streamlit colour
        # markup (`:blue[NAME]`) so the name reads as clickable, and a test
        # naming the stop should not have to know that.
        st_stub.button.side_effect = (
            lambda label, *a, **k: pressed_button in str(label))

    live_map.show()
    return st_stub


def test_the_stop_search_widens_when_the_first_radius_finds_nothing(monkeypatch):
    from app_pages import live_map
    asked = []

    def fake(slug, lat, lon, radius_m=800, limit=5):
        asked.append(radius_m)
        return [] if radius_m <= live_map.NEARBY_STOP_RADIUS_M else [
            {'stop_id': 'S1', 'stop_name': 'FAR STOP', 'stop_lat': 3.0,
             'stop_lon': 101.0, 'distance_m': 900.0}]

    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', fake)
    st_stub = _render_live_map(monkeypatch)

    assert live_map.NEARBY_STOP_RADIUS_M in asked
    assert live_map.NEARBY_STOP_WIDE_RADIUS_M in asked
    # The stop name is a button label now, not markdown text (Task 1).
    said = (_texts(st_stub.markdown) + _texts(st_stub.info) + _texts(st_stub.caption)
            + _texts(st_stub.button))
    assert 'FAR STOP' in said
    assert str(live_map.NEARBY_STOP_WIDE_RADIUS_M) in said, \
        "the panel must name the radius it actually used"


def test_the_stop_search_does_not_widen_when_the_first_radius_finds_stops(monkeypatch):
    from app_pages import live_map
    asked = []

    def fake(slug, lat, lon, radius_m=800, limit=5):
        asked.append(radius_m)
        return [{'stop_id': 'S1', 'stop_name': 'NEAR STOP', 'stop_lat': 3.0,
                 'stop_lon': 101.0, 'distance_m': 200.0}]

    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', fake)
    _render_live_map(monkeypatch)
    assert live_map.NEARBY_STOP_WIDE_RADIUS_M not in asked, \
        "widening when the near search succeeded costs a second scan for nothing"


def test_the_tapped_bus_panel_follows_the_widened_radius(monkeypatch):
    """
    The tapped-bus panel used to hard-code NEARBY_STOP_RADIUS_M, so with the
    widened radius in force it denied a bus the panel directly below it was
    listing: the map drew rings at 1200 m, "Arrivals near you" showed a bus en
    route to one of them, and tapping that bus answered "V1 does not come
    within 800 m of you on its current trip". Two panels, one bus, opposite
    answers.

    The trip here has no stop inside 800 m and two between 800 m and 1500 m,
    which is exactly the state that produced the contradiction.
    """
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"vehicles": [{"vehicle_id": "V1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}

    # ~1200 m north of the user: outside the primary radius, inside the wide
    # one, so the widened search is what puts it on the map at all.
    far_stop = {'stop_id': 'S1', 'stop_name': 'FAR STOP',
                'stop_lat': 3.1508, 'stop_lon': 101.68, 'distance_m': 1202.0}

    def stops_near(slug, lat, lon, radius_m=800, limit=5):
        return [] if radius_m <= live_map.NEARBY_STOP_RADIUS_M else [dict(far_stop)]

    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', stops_near)

    offset_seconds = int(live_map.UTC_OFFSET_HOURS) * 3600
    local_seconds_of_day = (now + offset_seconds) % 86400
    trip_stops = [
        # ~1000 m north: the stop the bus is at now, also beyond 800 m.
        {'stop_id': 'S0', 'stop_name': 'FAR ORIGIN', 'stop_lat': 3.1490,
         'stop_lon': 101.68, 'arrival_seconds': local_seconds_of_day},
        dict(far_stop, arrival_seconds=local_seconds_of_day + 300),
    ]
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: trip_stops)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_headsign', lambda *a, **k: 'Terminal X')
    monkeypatch.setattr(live_map.gtfs_static, 'is_frequency_based', lambda *a, **k: True)

    live_map.show()

    said = _texts(st_stub.info) + _texts(st_stub.caption) + _texts(st_stub.markdown)
    # Not a blanket ban on the string "800 m": the Arrivals panel legitimately
    # says "Nothing within 800 m — showing stops up to 1500 m" here. It is this
    # panel's own denial that must not quote it.
    assert f"does not come within {live_map.NEARBY_STOP_RADIUS_M} m" not in said, \
        f"the tapped-bus panel still quotes the primary radius: {said!r}"
    assert 'does not come within' not in said, \
        f"a bus reaching a stop the panel below lists was denied: {said!r}"
    assert '**Arrives** FAR STOP' in said, \
        f"the arrival at the widened-radius stop was not rendered: {said!r}"


def test_the_dead_end_names_regions_that_do_have_stops_near_you(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [])
    monkeypatch.setattr(live_map.gtfs_static, 'find_regions_with_stops_near',
                        lambda *a, **k: [
                            {'region': 'Rapid Bus KL', 'slug': 's1',
                             'count': 15, 'nearest_m': 152.0}])
    st_stub = _render_live_map(monkeypatch)

    said = _texts(st_stub.markdown) + _texts(st_stub.info) + _texts(st_stub.caption)
    assert 'Rapid Bus KL' in said
    assert '15' in said
    assert 'Switch region' in said


def test_the_region_scan_runs_only_at_the_dead_end(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near',
                        lambda *a, **k: [{'stop_id': 'S1', 'stop_name': 'NEAR',
                                          'stop_lat': 3.0, 'stop_lon': 101.0,
                                          'distance_m': 100.0}])

    def explode(*a, **k):
        raise AssertionError('scanned every region when stops were already found')

    monkeypatch.setattr(live_map.gtfs_static, 'find_regions_with_stops_near', explode)
    _render_live_map(monkeypatch)


# ---------------------------------------------------------------------------
# Task 4: accept a flat api_key, and resolve the alias once before it reaches
# either matcher.
#
# Two real failures. The owner's OpenRouteService key was pasted into
# Streamlit Secrets as a single line, `api_key = 'eyJ...'`, with no section
# header -- _ors_api_key read st.secrets['routing']['api_key'], and the
# deliberately broad except swallowed the miss, so walk times stayed
# "(estimated)" with nothing saying why. And a rider reads "GOKL14" on the
# front of the bus; the feed publishes that route as PAVILION BUKIT JALIL
# (PAVBJ) -- no "GOKL" route exists anywhere in the feed.
# ---------------------------------------------------------------------------

def test_secrets_are_read_from_the_sectioned_form(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map, '_config', None)
    live_map.st.secrets = {'routing': {'api_key': 'SECTIONED'}}
    assert live_map._ors_api_key() == 'SECTIONED'


def test_secrets_are_also_read_from_a_flat_api_key(monkeypatch):
    # A single pasted line is what a reader reaches for, and the miss was
    # silent: walk times stayed "(estimated)" with nothing saying why.
    from app_pages import live_map
    monkeypatch.setattr(live_map, '_config', None)
    live_map.st.secrets = {'api_key': 'FLAT'}
    assert live_map._ors_api_key() == 'FLAT'


def test_the_sectioned_form_wins_when_both_are_present(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map, '_config', None)
    live_map.st.secrets = {'routing': {'api_key': 'SECTIONED'}, 'api_key': 'FLAT'}
    assert live_map._ors_api_key() == 'SECTIONED'


def test_no_secret_in_either_form_returns_none(monkeypatch):
    from app_pages import live_map
    monkeypatch.setattr(live_map, '_config', None)
    live_map.st.secrets = {}
    assert live_map._ors_api_key() is None


def test_searching_the_branding_finds_the_published_route(monkeypatch):
    # GOKL14 is painted on the bus; the feed calls it PAVILION BUKIT JALIL.
    #
    # Note: the brief's `lambda df, q: seen.setdefault('query', q) or df`
    # cannot pass -- dict.setdefault returns the value it just set (truthy),
    # so the `or df` never triggers and the stub returns the query string in
    # place of the dataframe, breaking `df_filtered.empty` regardless of
    # aliasing. Rewritten below to record the query and return `df`
    # unchanged, which is the behaviour the brief was reaching for.
    from app_pages import live_map
    seen = {}

    def fake_filter(df, q):
        seen['query'] = q
        return df

    monkeypatch.setattr(live_map.data_processor, 'filter_by_route', fake_filter)
    st_stub = _render_live_map(monkeypatch, route_query='GOKL14')
    assert seen['query'] == 'PAVILION BUKIT JALIL (PAVBJ)'


def test_an_alias_match_discloses_itself(monkeypatch):
    from app_pages import live_map
    st_stub = _render_live_map(monkeypatch, route_query='GOKL14')
    said = _texts(st_stub.caption) + _texts(st_stub.info) + _texts(st_stub.markdown)
    assert 'GOKL14' in said and 'PAVILION BUKIT JALIL' in said, \
        "a hand-written alias must not pass itself off as feed data"
    # Naming both routes is not the disclosure. Two names stated side by side
    # read as two facts the app looked up, and the part that is actually a
    # guess -- that these are the same route -- is precisely the part the feed
    # cannot support. The caption has to attribute that link to this app.
    assert 'by hand' in said, \
        f"the caption does not say the mapping is hand-maintained: {said!r}"
    assert 'not the operator' in said, \
        f"the caption does not say whose mapping it is: {said!r}"
    assert 'nothing in the feed confirms it' in said, \
        f"the caption still implies the feed backs the link: {said!r}"


def test_a_real_route_name_is_not_rewritten_or_disclosed(monkeypatch):
    # T580 is a real short name in the feed -- the alias table must not touch
    # it, and nothing should claim it came from a hand-written mapping.
    from app_pages import live_map
    seen = {}

    def fake_filter(df, q):
        seen['query'] = q
        return df

    monkeypatch.setattr(live_map.data_processor, 'filter_by_route', fake_filter)
    st_stub = _render_live_map(monkeypatch, route_query='T580')
    assert seen['query'] == 'T580'
    said = _texts(st_stub.caption) + _texts(st_stub.info) + _texts(st_stub.markdown)
    assert 'is the name on the bus' not in said


def test_a_slow_region_scan_is_not_cached_already_expired(monkeypatch):
    """The entry used to be stamped when the scan STARTED.

    A scan slower than the TTL was therefore born expired: the cache could
    never serve it, and every render re-scanned — the refresh storm the cache
    exists to prevent, defeated in exactly the case where it matters most
    (several agency endpoints hanging at once).
    """
    from utils import gtfs_static

    class _Clock:
        def __init__(self, t):
            self.t = t

        def time(self):
            return self.t

    clock = _Clock(1_000_000.0)
    # Replace the module's own reference, not the stdlib clock, so nothing
    # outside gtfs_static sees a frozen time.
    monkeypatch.setattr(gtfs_static, 'time', clock)

    calls = {'n': 0}

    def slow(slug, lat, lon, radius_m=800, limit=5):
        calls['n'] += 1
        # Each agency hangs long enough that the whole scan outlasts the TTL.
        clock.t += gtfs_static.REGION_SCAN_TTL_SECONDS
        return ([{'stop_id': 'a', 'stop_name': 'A', 'stop_lat': 3.0,
                  'stop_lon': 101.0, 'distance_m': 100.0}]
                if slug == 'ktmb' else [])

    monkeypatch.setattr(gtfs_static, 'get_stops_near', slow)
    gtfs_static._REGION_STOPS_INDEX.clear()

    first = gtfs_static.find_regions_with_stops_near(3.0586, 101.6739)
    after_first = calls['n']
    assert after_first > 1, "the scan should have walked several agencies"
    assert [r['region'] for r in first] == ['KTM Berhad']

    second = gtfs_static.find_regions_with_stops_near(3.0586, 101.6739)
    assert calls['n'] == after_first, \
        "a slow scan was stored already expired, so the cache served nothing"
    assert [r['region'] for r in second] == ['KTM Berhad']


# ---------------------------------------------------------------------------
# Task 1: a hittable, highlightable ring and a clickable name
# ---------------------------------------------------------------------------

def test_stop_rings_are_clickable_across_their_whole_face(monkeypatch):
    # deck.gl only picks drawn pixels. With filled=False the hollow centre was
    # dead space, so a cursor inside the ring missed the stop entirely.
    st_stub = _render_live_map(monkeypatch)
    deck = st_stub.pydeck_chart.call_args_list[0][0][0]
    stops = next(l for l in deck.layers if l.id == 'nearby-stops')
    assert stops.filled is True, "the centre of the ring is not pickable"
    assert stops.stroked is True, "the ring outline must survive the fill"


def test_the_stop_ring_fill_is_faint_but_not_invisible(monkeypatch):
    # A fully transparent fill invites a later reader to delete a fill that
    # appears to do nothing -- and deleting it silently restores the dead centre.
    st_stub = _render_live_map(monkeypatch)
    deck = st_stub.pydeck_chart.call_args_list[0][0][0]
    stops = next(l for l in deck.layers if l.id == 'nearby-stops')
    alpha = stops.get_fill_color[3]
    assert 0 < alpha < 120, alpha


def test_the_selected_stop_ring_is_drawn_differently(monkeypatch):
    st_stub = _render_live_map(monkeypatch, selected_stop_id='S1')
    deck = st_stub.pydeck_chart.call_args_list[0][0][0]
    rows = next(l for l in deck.layers if l.id == 'nearby-stops').data
    picked = [r for r in rows if r['stop_id'] == 'S1']
    others = [r for r in rows if r['stop_id'] != 'S1']
    assert picked, "fixture must include the selected stop"
    assert others, "fixture must include at least one other stop"
    assert picked[0]['line_color'] != others[0]['line_color'] \
        or picked[0]['line_width'] != others[0]['line_width']


def test_the_selected_stop_ring_is_actually_thicker_not_just_brighter(monkeypatch):
    # ScatterplotLayer's line width defaults to the same coordinate units as
    # get_radius (metres). At get_radius=40 clamped to 5-10 screen pixels, a
    # 2 vs. 4 metre stroke rounds to well under a pixel either way, and
    # line_width_min_pixels=2 floors both to an identical 2px on screen --
    # so the 2-vs-4 the rows carry would be invisible without this. The
    # highlight test above can't catch that (it's an `or`, satisfied by
    # colour alone); this pins the units that make the width term real.
    st_stub = _render_live_map(monkeypatch, selected_stop_id='S1')
    deck = st_stub.pydeck_chart.call_args_list[0][0][0]
    stops = next(l for l in deck.layers if l.id == 'nearby-stops')
    assert stops.line_width_units == 'pixels', \
        "get_line_width's 2/4 are metres, not pixels, without this"


def test_stop_rings_match_when_nothing_is_selected(monkeypatch):
    st_stub = _render_live_map(monkeypatch)
    rows = next(l for l in st_stub.pydeck_chart.call_args_list[0][0][0].layers
                if l.id == 'nearby-stops').data
    assert len({tuple(r['line_color']) for r in rows}) == 1


def test_a_ring_tap_selects_instantly_but_its_own_highlight_lags_one_render(monkeypatch):
    """
    Documents a known, deliberate limitation (fix-round-1 finding 6): unlike
    the name button, which calls st.rerun() itself and so sees its own
    highlight on the very next execution, a ring tap's highlight is not force-
    refreshed the same way. `_selected_stop_id` is read to build the stops
    layer before st.pydeck_chart() has delivered *this* render's click, so the
    just-tapped stop is drawn with the previous (unselected) styling for one
    render. The selection itself and the tapped-stop panel are correct and
    instant -- only the ring's own colour/width lag.

    Deliberately not fixed with a bare st.rerun() after adopting the tap.
    test_clearing_a_stop_selection_survives_a_repeated_payload above proves
    payload redelivery, but only when the deck spec is unchanged between
    renders -- Streamlit hashes the spec into the element id even with an
    explicit key, so adopting a genuinely *new* stop (which changes that
    stop's line_color/line_width row) lands the rerun on a new element id
    with no stored payload, and it would simply terminate: no loop in the
    common case of tapping a different stop than the one already selected.
    The real risk is the narrower case of a rerun landing on an *unchanged*
    spec -- re-tapping the ring of the stop already selected -- where the
    same stale payload could come back and re-enter the same branch. A
    one-line guard (skip the rerun when the adopted stop already matches the
    current selection) would remove that case, and without adding state --
    it reads the selection that is already there. It just was not judged
    worth adding for a highlight that already self-heals within one
    auto-refresh (<=20s) and never affects which panel opens.

    An earlier version of this docstring said the guard's merit was avoiding
    a deck_generation bump "and so the pan/zoom risk". That was overstated:
    in the bundled deck.gl, setProps re-seeds the camera only when
    initialViewState itself changes, which a key bump does not do -- the
    element id already changes on every data refresh while the viewport
    stays put. The conclusion is unchanged; the reason is narrower than it
    was written.
    """
    stop = {'stop_id': 'S1', 'stop_name': 'Tapped Stop',
            'stop_lat': 3.1401, 'stop_lon': 101.6801, 'distance_m': 50.0}
    other = {'stop_id': 'S2', 'stop_name': 'Other Stop',
             'stop_lat': 3.1402, 'stop_lon': 101.6802, 'distance_m': 60.0}
    selection = SimpleNamespace(
        selection=SimpleNamespace(objects={"nearby-stops": [{"stop_id": "S1"}]})
    )
    live_map, st_stub, now = _live_map_with_selection(monkeypatch, selection)
    st_stub.session_state['user_location'] = {'lat': 3.14, 'lon': 101.68, 'accuracy': 10}
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', lambda *a, **k: [stop, other])
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])

    live_map.show()

    # The selection and the panel it opens are correct and instant.
    assert st_stub.session_state.get('selected_stop_id') == 'S1'
    assert 'Tapped Stop' in _texts(st_stub.info)

    # But the ring drawn THIS render shows no highlight yet -- every row is
    # still the same colour, because the layer was built before this tap was
    # known. If this assertion starts failing, the lag has been fixed (or the
    # layer-build order changed) and this test's docstring, the CHANGELOG and
    # the README all need to say so instead.
    rows = next(l for l in st_stub.pydeck_chart.call_args_list[0][0][0].layers
                if l.id == 'nearby-stops').data
    assert len({tuple(r['line_color']) for r in rows}) == 1, \
        "the ring highlight now applies on the same render as the tap -- update the docs"


def test_clicking_a_stop_name_selects_that_stop(monkeypatch):
    # The name used to be a Google Maps link and nothing else.
    st_stub = _render_live_map(monkeypatch, pressed_button='KL1743 GREEN AVENUE CONDOMINIUM')
    assert st_stub.session_state['selected_stop_id'] == 'S1'
    # The rerun is load-bearing, not tidiness: st.button is read deep inside
    # the arrivals loop, long after the panel slot and the deck have already
    # been emitted for this render. Without it the click still selects, but
    # nothing on screen changes until the next auto-refresh -- the feature
    # degrades from instant to "up to 20s", which is the opposite of what the
    # README and CHANGELOG both claim for it.
    assert st_stub.rerun.called, \
        "the click must rerun; without it the selection is invisible for up to 20s"


def test_the_selected_stop_reads_as_selected_in_the_list(monkeypatch):
    """
    The only other feedback for a name click is the ring and the panel, both
    above the list on a phone -- so with every button styled identically,
    clicking a name and not scrolling up reads as "nothing happened". The
    selected stop's button is drawn differently from the rest.
    """
    st_stub = _render_live_map(monkeypatch, selected_stop_id='S1')
    types = {c.kwargs['key']: c.kwargs.get('type')
             for c in st_stub.button.call_args_list
             if str(c.kwargs.get('key', '')).startswith('pick_stop_')}

    assert types['pick_stop_S1'] != types['pick_stop_S2'], \
        f"the selected stop looks the same as every other one: {types}"


def test_no_stop_reads_as_selected_when_none_is(monkeypatch):
    # The companion: nothing selected, nothing singled out.
    st_stub = _render_live_map(monkeypatch)
    types = {c.kwargs.get('type') for c in st_stub.button.call_args_list
             if str(c.kwargs.get('key', '')).startswith('pick_stop_')}
    assert types == {'tertiary'}, types


def test_the_google_maps_link_survives_and_is_no_longer_the_name(monkeypatch):
    st_stub = _render_live_map(monkeypatch)
    said = _texts(st_stub.markdown)
    assert 'google.com/maps' in said
    assert '[KL1743 GREEN AVENUE CONDOMINIUM](' not in said, \
        "the name should be a control now, not the link"


def test_each_stop_button_has_its_own_key(monkeypatch):
    # Streamlit collides same-keyed widgets; five stops need five keys.
    st_stub = _render_live_map(monkeypatch)
    keys = [c.kwargs.get('key') for c in st_stub.button.call_args_list
            if c.kwargs.get('key')]
    assert len(keys) == len(set(keys)), keys


# ---------------------------------------------------------------------------
# Summaries belong in the database, not in pandas
#
# 📊 Data Table and 📈 Analytics each called `get_historical_data()` — SELECT *
# over the retention window — on every visit: 602,950 rows and 6.5 seconds
# measured, growing with the table, to show one screenful and a few averages.
# These tests pin the answers to what the pandas code produced, because a
# speed-up that changes the numbers is not a speed-up.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _temp_live_buses(rows):
    """A throwaway database holding `rows`, wired into utils.db."""
    from utils import db as _db
    path = os.path.join(tempfile.mkdtemp(), 'summaries.duckdb')
    con = _duckdb.connect(path)
    con.execute("""
        CREATE TABLE live_buses (
            region VARCHAR, latitude DOUBLE, longitude DOUBLE, bearing DOUBLE,
            speed DOUBLE, vehicle_id VARCHAR, timestamp VARCHAR,
            trip_id VARCHAR, route_id VARCHAR, insert_timestamp BIGINT,
            created_at TIMESTAMP WITH TIME ZONE
        )
    """)
    for r in rows:
        con.execute(
            "INSERT INTO live_buses VALUES (?,?,?,?,?,?,?,?,?,?,now())",
            [r['region'], 3.1, 101.6, 0.0, r['speed'], r['vehicle_id'],
             str(r['timestamp']), '', '', int(r['timestamp'])])
    con.close()
    with patch.object(_db, 'DATABASE_NAME', path):
        yield _db
    os.unlink(path)


_SUMMARY_ROWS = [
    {'region': 'A', 'vehicle_id': 'V1', 'speed': 10.0, 'timestamp': 100},
    {'region': 'A', 'vehicle_id': 'V1', 'speed': 20.0, 'timestamp': 200},
    {'region': 'B', 'vehicle_id': 'V1', 'speed': 30.0, 'timestamp': 300},
    {'region': 'A', 'vehicle_id': 'V2', 'speed': 0.0,  'timestamp': 400},
    {'region': 'A', 'vehicle_id': 'V2', 'speed': 0.05, 'timestamp': 500},
]


def _pandas_reference():
    """What the old code produced, computed the old way."""
    df = pd.DataFrame(_SUMMARY_ROWS)
    return data_processor.convert_speed_to_kmh(df.copy())


def test_the_speed_summary_matches_what_pandas_produced(monkeypatch):
    ref = _pandas_reference()
    with _temp_live_buses(_SUMMARY_ROWS) as db_mod:
        summary = db_mod.get_vehicle_speed_summary()

    got = (summary.set_index(['vehicle_id', 'region'])
           .eval('speed_sum / n_rows').sort_index())
    want = ref.groupby(['vehicle_id', 'region'])['speed'].mean().sort_index()
    assert list(got.index) == list(want.index)
    assert got.round(9).tolist() == want.round(9).tolist(), f"{got}\n{want}"


def test_regrouping_the_summary_by_vehicle_stays_exact(monkeypatch):
    # V1 has two rows in region A and one in B. Averaging the two regional
    # averages would weight B three times too heavily; summing and dividing
    # does not. This is why the query returns sums and counts.
    ref = _pandas_reference()
    with _temp_live_buses(_SUMMARY_ROWS) as db_mod:
        summary = db_mod.get_vehicle_speed_summary()

    per = summary.groupby('vehicle_id')[['speed_sum', 'n_rows']].sum()
    got = (per['speed_sum'] / per['n_rows']).sort_index()
    want = ref.groupby('vehicle_id')['speed'].mean().sort_index()
    assert got.round(9).tolist() == want.round(9).tolist(), f"{got}\n{want}"


def test_moving_speed_stats_match_and_treat_a_crawl_as_stopped(monkeypatch):
    # 0.05 m/s is 0.18 km/h, which rounds to 0 and is not "moving" — the same
    # judgement the pandas filter made, because it filtered after converting.
    ref = _pandas_reference()
    moving = ref[ref['speed'] > 0]['speed']
    with _temp_live_buses(_SUMMARY_ROWS) as db_mod:
        stats = db_mod.get_moving_speed_stats()

    assert stats['rows'] == len(moving)
    assert stats['max'] == moving.max()
    assert stats['min'] == moving.min()
    assert round(stats['avg'], 9) == round(moving.mean(), 9)
    assert round(stats['median'], 9) == round(moving.median(), 9)


def test_the_table_page_returns_the_newest_rows_and_the_true_total(monkeypatch):
    with _temp_live_buses(_SUMMARY_ROWS) as db_mod:
        page, total = db_mod.get_table_page(['A'], limit=2)

    assert total == 4, "the total must count every matching row, not the page"
    assert len(page) == 2
    assert list(page['timestamp']) == [500, 400], "newest first"


def test_the_page_limit_does_not_redefine_the_average(monkeypatch):
    # avg_speed has always meant "this vehicle's average across the selection".
    # Computing it from the page would quietly turn it into "across the rows
    # that happened to fit on screen".
    with _temp_live_buses(_SUMMARY_ROWS) as db_mod:
        one_row, _ = db_mod.get_table_page(['A'], limit=1)
        everything, _ = db_mod.get_table_page(['A'], limit=None)

    v2_full = everything[everything['vehicle_id'] == 'V2']['avg_speed'].iloc[0]
    assert one_row['avg_speed'].iloc[0] == v2_full, \
        "the average changed when the page shrank"


def test_all_rows_means_all_rows(monkeypatch):
    with _temp_live_buses(_SUMMARY_ROWS) as db_mod:
        page, total = db_mod.get_table_page(['A', 'B'], limit=None)
    assert len(page) == total == len(_SUMMARY_ROWS)


def test_the_region_list_does_not_read_every_row(monkeypatch):
    with _temp_live_buses(_SUMMARY_ROWS) as db_mod:
        regions, sync = db_mod.get_table_regions()
    assert regions == ['A', 'B']
    assert sync, "the sync time must survive the switch away from get_historical_data"


def test_format_table_page_names_the_columns_it_always_did(monkeypatch):
    with _temp_live_buses(_SUMMARY_ROWS) as db_mod:
        page, _ = db_mod.get_table_page(['A'], limit=None)
    out = data_processor.format_table_page(page)
    for col in ('Region', 'Vehicle ID', 'Latitude', 'Longitude', 'Heading (°)',
                'Speed (km/h)', 'Avg Speed (km/h)', 'Timestamp'):
        assert col in out.columns, f"{col} missing from {list(out.columns)}"


# ---------------------------------------------------------------------------
# Locate Me moves the region when the selected one cannot answer
#
# Reported: the app opens on KTM Berhad; pressing Locate Me in Bukit Jalil
# showed nothing, because KTM has no stops there while Rapid Bus KL has fifteen
# within 800 m. The region now follows the user — but only at that dead end,
# and only as part of pressing the button.
# ---------------------------------------------------------------------------


def _live_map_after_locate(monkeypatch, stops_here, elsewhere):
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.05, 'lon': 101.67, 'accuracy': 10}
    st_stub.session_state['region_follow_location'] = True
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near',
                        lambda *a, **k: list(stops_here))
    monkeypatch.setattr(live_map.gtfs_static, 'find_regions_with_stops_near',
                        lambda *a, **k: list(elsewhere))
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])
    return live_map, st_stub


_A_STOP = [{'stop_id': 'S1', 'stop_name': 'A STOP', 'stop_lat': 3.0501,
            'stop_lon': 101.6701, 'distance_m': 40.0}]
_NEARBY_REGION = [{'region': 'Rapid Bus KL', 'count': 15, 'nearest_m': 120.0}]


def test_locate_me_switches_region_when_this_one_has_no_stops(monkeypatch):
    live_map, st_stub = _live_map_after_locate(monkeypatch, [], _NEARBY_REGION)

    live_map.show()

    assert st_stub.session_state.get('pending_region') == 'Rapid Bus KL'
    assert st_stub.rerun.called, "the page must redraw for the new region to take effect"


def test_the_switch_is_explained_not_silent(monkeypatch):
    live_map, st_stub = _live_map_after_locate(monkeypatch, [], _NEARBY_REGION)

    live_map.show()
    note = st_stub.session_state.get('region_switched_note')

    assert note, "a region that changes under the user must say why"
    assert note['to'] == 'Rapid Bus KL'
    assert note['count'] == 15 and note['nearest_m'] == 120


def test_a_region_that_does_have_stops_is_left_alone(monkeypatch):
    # The user may have chosen it deliberately, and it works. Overruling a
    # working choice would be worse than the dead end this fixes.
    live_map, st_stub = _live_map_after_locate(monkeypatch, _A_STOP, _NEARBY_REGION)

    live_map.show()

    assert 'pending_region' not in st_stub.session_state
    assert 'region_switched_note' not in st_stub.session_state


def test_one_far_stop_does_not_block_the_switch(monkeypatch):
    # Reported after the first version shipped. In Bukit Jalil, Rapid Bus MRT
    # Feeder has nothing within 800 m and one stop at 1493 m -- a 23-minute
    # walk at the very edge of the fallback search -- while Rapid Bus KL has
    # fifteen within 800 m and one at the user's feet. Requiring *zero* stops
    # let that single distant stop hold the user in the wrong region.
    from app_pages import live_map as _lm
    empty = SimpleNamespace(selection=SimpleNamespace(objects={}))
    live_map, st_stub, _now = _live_map_with_selection(monkeypatch, empty)
    st_stub.session_state['user_location'] = {'lat': 3.05, 'lon': 101.67, 'accuracy': 10}
    st_stub.session_state['region_follow_location'] = True
    far = [{'stop_id': 'F1', 'stop_name': 'A LONG WALK AWAY',
            'stop_lat': 3.07, 'stop_lon': 101.69, 'distance_m': 1493.0}]

    # Nothing at 800 m; the widened 1500 m search finds the one far stop.
    def staged(slug, lat, lon, radius_m=800, **k):
        return list(far) if radius_m == live_map.NEARBY_STOP_WIDE_RADIUS_M else []
    monkeypatch.setattr(live_map.gtfs_static, 'get_stops_near', staged)
    asked = {}
    def finder(lat, lon, radius_m=1500, **k):
        asked['radius_m'] = radius_m
        return list(_NEARBY_REGION)
    monkeypatch.setattr(live_map.gtfs_static, 'find_regions_with_stops_near', finder)
    monkeypatch.setattr(live_map.gtfs_static, 'get_trip_stops', lambda *a, **k: [])

    live_map.show()

    assert st_stub.session_state.get('pending_region') == 'Rapid Bus KL', \
        "one stop 1.5 km away held the user in a region that cannot help them"
    assert asked.get('radius_m') == live_map.NEARBY_STOP_RADIUS_M, \
        "candidates must clear the same 800 m bar, or the switch trades one far stop for another"


def test_the_region_does_not_follow_a_location_the_user_already_had(monkeypatch):
    # Following the location is part of pressing Locate Me. Without the
    # one-shot flag, a region picked *after* locating would be overridden on
    # the very next render — the user could never choose a quiet region.
    live_map, st_stub = _live_map_after_locate(monkeypatch, [], _NEARBY_REGION)
    st_stub.session_state.pop('region_follow_location')

    live_map.show()

    assert 'pending_region' not in st_stub.session_state


def test_the_permission_is_spent_even_when_no_other_region_helps(monkeypatch):
    # Nowhere has stops nearby. The flag must still be consumed, or every later
    # render would re-run a scan that reads every agency's timetable.
    live_map, st_stub = _live_map_after_locate(monkeypatch, [], [])

    live_map.show()

    assert 'region_follow_location' not in st_stub.session_state
    assert 'pending_region' not in st_stub.session_state


def test_resolve_db_path_relative_and_absolute():
    from utils.db import resolve_db_path, _REPO_ROOT
    assert resolve_db_path(':memory:') == ':memory:'
    assert resolve_db_path('/custom/path/db.duckdb') == '/custom/path/db.duckdb'
    expected = os.path.join(_REPO_ROOT, 'agustiar_analytics.duckdb')
    assert resolve_db_path('agustiar_analytics.duckdb') == expected
    assert resolve_db_path(None) == expected


def test_get_connection_read_only_and_retry(tmp_path, monkeypatch):
    from utils import db as _db
    import duckdb as _duckdb
    test_db = str(tmp_path / 'concurrency_test.duckdb')

    # Create DB and populate table
    con = _duckdb.connect(test_db)
    con.execute("CREATE TABLE test_tbl (id INT)")
    con.execute("INSERT INTO test_tbl VALUES (1), (2)")
    con.close()

    monkeypatch.setattr(_db, 'DATABASE_NAME', test_db)

    # Verify read_only connection can query
    ro_con = _db.get_connection(read_only=True)
    res = ro_con.execute("SELECT count(*) FROM test_tbl").fetchone()[0]
    ro_con.close()
    assert res == 2

    # Verify retry logic on simulated transient exception
    attempts = {'count': 0}
    real_connect = _duckdb.connect

    def flaky_connect(path, **kwargs):
        attempts['count'] += 1
        if attempts['count'] < 3:
            raise _duckdb.IOException("Simulated lock collision")
        return real_connect(path, **kwargs)

    monkeypatch.setattr(_duckdb, 'connect', flaky_connect)
    retry_con = _db.get_connection(read_only=True, max_retries=4, backoff_base=0.01)
    assert attempts['count'] == 3
    retry_con.close()


def test_fetch_endpoint_extracts_start_date_and_start_time():
    from google.transit import gtfs_realtime_pb2
    feed = gtfs_realtime_pb2.FeedMessage()
    header = feed.header
    header.gtfs_realtime_version = "2.0"
    header.timestamp = 1785427200

    entity = feed.entity.add()
    entity.id = "1"
    v = entity.vehicle
    v.position.latitude = 3.14
    v.position.longitude = 101.69
    v.vehicle.id = "BUS101"
    v.timestamp = 1785427200
    v.trip.trip_id = "TRIP_ABC"
    v.trip.route_id = "ROUTE_XYZ"
    v.trip.start_date = "20260827"
    v.trip.start_time = "06:30:00"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = feed.SerializeToString()

    with patch('utils.ingestion._SESSION.get', return_value=mock_resp):
        vehicles, duration, status = _fetch_endpoint("Rapid Bus KL", "test_ep")

    assert status == 'OK'
    assert len(vehicles) == 1
    assert vehicles[0]['start_date'] == "20260827"
    assert vehicles[0]['start_time'] == "06:30:00"


def test_gtfs_static_cached_path_uses_tempfile_dir():
    from utils import gtfs_static
    import tempfile
    path = gtfs_static.get_cached_path("test-agency")
    assert path.startswith(tempfile.gettempdir())
    assert path.endswith("gtfs_static_test_agency.zip")


def test_live_map_ors_api_key_checks_os_environ(monkeypatch):
    from app_pages import live_map
    monkeypatch.setenv('ORS_API_KEY', 'test_env_key_123')
    assert live_map._ors_api_key() == 'test_env_key_123'

