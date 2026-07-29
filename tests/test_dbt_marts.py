import os
import subprocess
import duckdb
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSFORM = os.path.join(REPO, "transform")


@pytest.fixture(scope="module")
def built_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("dbt") / "test.duckdb"
    env = {**os.environ, "DBT_DUCKDB_PATH": str(db_path)}
    common = ["--project-dir", TRANSFORM, "--profiles-dir", TRANSFORM,
              "--vars", "{health_window_hours: 876000, retention_days: 40000}"]
    # Seed first: dbt has no DAG edge between a seed and a same-named source(),
    # so `dbt build` alone can run models before seeds load on a fresh DB.
    subprocess.run(["dbt", "seed", *common], check=True, env=env, cwd=REPO)
    subprocess.run(["dbt", "build", *common], check=True, env=env, cwd=REPO)
    con = duckdb.connect(str(db_path))
    yield con
    con.close()


def test_network_health_score_matches_hand_computation(built_db):
    row = built_db.execute(
        "SELECT reporting_rate, availability, avg_data_lag_seconds, "
        "dropout_count, total_fetches, reliability_score "
        "FROM main.mart_network_health WHERE region = 'TestRegion'"
    ).fetchone()
    reporting_rate, availability, avg_lag, dropouts, fetches, score = row
    assert round(reporting_rate, 4) == 0.9      # avg(0.8, 1.0)
    assert round(availability, 4) == 1.0        # no dropouts
    assert round(avg_lag, 4) == 15.0            # avg(30, 0)
    assert dropouts == 0
    assert fetches == 2
    assert score == 95                          # round((0.36+0.40+0.19)*100)


def test_region_health_trend_per_row_score(built_db):
    rows = built_db.execute(
        "SELECT fetch_timestamp, reliability_score "
        "FROM main.mart_region_health_trend "
        "WHERE region = 'TestRegion' ORDER BY fetch_timestamp"
    ).fetchall()
    # Row 1: reporting 0.8, avail 1.0, lag 30 -> round((0.32+0.4+0.18)*100)=90
    # Row 2: reporting 1.0, avail 1.0, lag 0  -> round((0.4+0.4+0.2)*100)=100
    assert [r[1] for r in rows] == [90, 100]
