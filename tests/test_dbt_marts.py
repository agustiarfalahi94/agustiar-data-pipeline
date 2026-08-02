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


def test_dead_feed_is_flagged_unavailable_with_no_score(built_db):
    row = built_db.execute(
        "SELECT feed_unavailable, scoreable_fetches, reliability_score "
        "FROM main.mart_network_health WHERE region = 'DeadFeed'"
    ).fetchone()
    unavailable, scoreable, score = row
    assert unavailable is True
    assert scoreable == 0
    assert score is None


def test_empty_but_healthy_feed_is_not_penalised(built_db):
    row = built_db.execute(
        "SELECT feed_unavailable, scoreable_fetches, availability "
        "FROM main.mart_network_health WHERE region = 'QuietFeed'"
    ).fetchone()
    unavailable, scoreable, availability = row
    assert unavailable is False
    assert scoreable == 2
    assert availability == 1.0     # EMPTY is not an error


def test_ok_region_still_scores_as_before(built_db):
    score = built_db.execute(
        "SELECT reliability_score FROM main.mart_network_health "
        "WHERE region = 'TestRegion'"
    ).fetchone()[0]
    assert score == 95


def test_trend_dead_feed_rows_are_null_not_zero(built_db):
    """NO_FEED rows aren't scoreable, so the trend line should have no point
    for them at all - not a zero dragging the chart down."""
    scores = [
        r[0] for r in built_db.execute(
            "SELECT reliability_score FROM main.mart_region_health_trend "
            "WHERE region = 'DeadFeed'"
        ).fetchall()
    ]
    assert scores == [None, None]


def test_trend_empty_rows_score_on_the_terms_that_apply(built_db):
    """An EMPTY cycle received nothing, so its reporting rate is undefined, not
    zero. Substituting a zero scored it 60 - "Degraded" on the page's own scale -
    for a feed that answered correctly with no service running. The reporting
    term is dropped and the remaining weights renormalised instead."""
    scores = [
        r[0] for r in built_db.execute(
            "SELECT reliability_score FROM main.mart_region_health_trend "
            "WHERE region = 'QuietFeed'"
        ).fetchall()
    ]
    # availability 1.0 (EMPTY != ERROR), lag 0 -> round((0.4+0.2)/0.6*100) = 100
    assert scores == [100, 100]


def test_mixed_ok_and_empty_region_agrees_between_the_two_marts(built_db):
    """The invariant that was silently broken: the scorecard's aggregate score
    and the sparkline/drill-down trend beneath it describe the same window with
    the same macro, so they must not tell different stories. Before the fix a
    region with one OK and one EMPTY cycle read aggregate 100 with a trend of
    [100, 60] - a green "Reliable" card above a sparkline dipping to Degraded."""
    aggregate = built_db.execute(
        "SELECT reliability_score FROM main.mart_network_health "
        "WHERE region = 'MixedFeed'"
    ).fetchone()[0]
    trend = [
        r[0] for r in built_db.execute(
            "SELECT reliability_score FROM main.mart_region_health_trend "
            "WHERE region = 'MixedFeed' ORDER BY fetch_timestamp"
        ).fetchall()
    ]
    scoreable = [s for s in trend if s is not None]
    assert scoreable, "fixture must contain at least one scoreable row"
    assert aggregate == sum(scoreable) / len(scoreable)
    assert aggregate == 100 and trend == [100, 100]


def test_dropout_count_excludes_unscoreable_cycles(built_db):
    """dropout_count sits on the scorecard next to the score, so it must be
    counted over the same rows the score is. Counting NO_FEED/THROTTLED cycles
    produced cards reading "100 / Reliable" beside a four-figure dropout count."""
    rows = dict(built_db.execute(
        "SELECT region, dropout_count FROM main.mart_network_health"
    ).fetchall())
    assert rows['DeadFeed'] == 0       # 2 NO_FEED cycles, none scoreable
    assert rows['ThrottledFeed'] == 0  # 2 THROTTLED cycles, none scoreable
    assert rows['QuietFeed'] == 2      # EMPTY is scoreable and did drop out
    assert rows['MixedFeed'] == 1


def test_unavailable_feeds_carry_their_cause(built_db):
    """`feed_unavailable` alone cannot tell a withdrawn feed from a throttled
    one, so the card used to assert "Withdrawn upstream" for both."""
    rows = {
        r[0]: r[1:] for r in built_db.execute(
            "SELECT region, feed_unavailable, no_feed_count, throttled_count "
            "FROM main.mart_network_health"
        ).fetchall()
    }
    assert rows['DeadFeed'] == (True, 2, 0)
    assert rows['ThrottledFeed'] == (True, 0, 2)
    assert rows['TestRegion'] == (False, 0, 0)


def test_last_vehicle_timestamp_marks_the_newest_cycle_carrying_vehicles(built_db):
    """The score answers "is the feed answering", not "are buses running".

    An EMPTY cycle is a healthy feed reporting no service and is deliberately
    not counted against availability, so a region can hold a perfect score
    while no bus has been seen. The scorecard needs a separate fact to say so,
    and it must be the newest cycle that actually carried vehicles -- not the
    newest cycle overall, which is what last_fetch_timestamp already gives.
    """
    seen, fetched = built_db.execute(
        "SELECT last_vehicle_timestamp, last_fetch_timestamp "
        "FROM main.mart_network_health WHERE region = 'TestRegion'"
    ).fetchone()
    # Both TestRegion cycles carried vehicles, so the two agree here.
    assert seen == 1750000060
    assert seen == fetched


def test_last_vehicle_timestamp_is_null_for_a_feed_that_reported_no_vehicles(built_db):
    """QuietFeed answered every cycle and carried no vehicles in any of them.

    NULL, not 0 and not the fetch time: the window holds no moment at which a
    bus was seen, and inventing one would be exactly the false reassurance the
    column exists to prevent.
    """
    seen, fetched, score = built_db.execute(
        "SELECT last_vehicle_timestamp, last_fetch_timestamp, reliability_score "
        "FROM main.mart_network_health WHERE region = 'QuietFeed'"
    ).fetchone()
    assert seen is None, seen
    assert fetched == 1750000060, "the feed did answer -- only the buses were absent"
    assert score is not None and score > 0, \
        "a quiet feed is still a healthy feed; this is the gap the column explains"
