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
    assert 'position 4 min old' in said, \
        f"a stale position must be shown with its age, flagged as less certain: {said!r}"
    assert 'accurate to about one stop' in said, \
        f"the accuracy caveat is missing from the tapped-bus panel: {said!r}"


def test_a_frequency_based_trip_shows_no_lateness_clause(monkeypatch):
    """
    99% of the Rapid Bus KL feed runs to a headway, so no lateness is knowable
    and arrivals_for_stops reports delay_seconds as None. The renderer guarded
    only on `>= 60`, which raises TypeError on None -- the panel must instead
    say nothing at all about lateness.
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
    # It still carries what the tooltip actually shows.
    assert {'vehicle_id', 'route_display', 'last_report_display'} <= columns


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
