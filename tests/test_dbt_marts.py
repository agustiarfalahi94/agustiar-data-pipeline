import os
import subprocess
import sys
import duckdb
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSFORM = os.path.join(REPO, "transform")
# Resolve dbt through the running interpreter rather than PATH.
DBT = [sys.executable, "-m", "dbt.cli.main"]


@pytest.fixture(scope="module")
def built_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("dbt") / "test.duckdb"
    # The `ci` target reads its path from DBT_CI_DUCKDB_PATH only (see
    # transform/profiles.yml) - it never falls back to DBT_DUCKDB_PATH, so
    # only the CI var needs to be set here to point at the tmp file.
    env = {**os.environ, "DBT_CI_DUCKDB_PATH": str(db_path)}
    # --target ci is mandatory: the seeds shadow the real app tables, so they
    # are disabled on every other target. This DB is a throwaway tmp file.
    common = ["--target", "ci",
              "--project-dir", TRANSFORM, "--profiles-dir", TRANSFORM,
              "--vars", "{health_window_hours: 876000, retention_days: 40000}"]
    # Seed first: dbt has no DAG edge between a seed and a same-named source(),
    # so `dbt build` alone can run models before seeds load on a fresh DB.
    subprocess.run([*DBT, "seed", *common], check=True, env=env, cwd=REPO)
    subprocess.run([*DBT, "build", *common], check=True, env=env, cwd=REPO)
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


def test_region_vehicle_counts(built_db):
    count = built_db.execute(
        "SELECT unique_vehicles FROM main.mart_region_vehicle_counts "
        "WHERE region = 'TestRegion'"
    ).fetchone()[0]
    assert count == 2  # V1 and V2 (BadCoords row filtered out in staging)


def test_seeds_disabled_on_dev_target():
    """Regression guard for the data-loss bug: seeds must never be loadable on
    `dev`. The seed fixtures are named identically to the real app tables
    (`live_buses`, `fetch_quality_log`) - if they were ever enabled there,
    `dbt seed`/`dbt build` against a real database would truncate ingested
    history. This does not build anything; it only asks dbt which seed nodes
    would be selected on each target.
    """
    common = ["--resource-type", "seed",
              "--project-dir", TRANSFORM, "--profiles-dir", TRANSFORM]

    dev_result = subprocess.run(
        [*DBT, "--quiet", "ls", "--target", "dev", *common],
        check=True, capture_output=True, text=True, cwd=REPO,
    )
    dev_seeds = [line for line in dev_result.stdout.splitlines() if line.strip()]
    assert dev_seeds == [], f"seeds must be disabled on dev, got: {dev_seeds}"

    ci_result = subprocess.run(
        [*DBT, "--quiet", "ls", "--target", "ci", *common],
        check=True, capture_output=True, text=True, cwd=REPO,
    )
    ci_seeds = [line for line in ci_result.stdout.splitlines() if line.strip()]
    assert ci_seeds, "expected seed nodes to be selected on --target ci"


def test_staging_drops_null_and_null_island_coordinates(built_db):
    """Both invalid-coordinate branches are filtered: 0/0 and NULL."""
    regions = [
        r[0] for r in built_db.execute(
            "SELECT DISTINCT region FROM main.stg_vehicle_positions"
        ).fetchall()
    ]
    assert "BadCoords" not in regions   # 0/0 null-island row
    assert "NullCoords" not in regions  # empty lat/lon row
    assert "TestRegion" in regions
