import os
import subprocess
import sys
import time

import duckdb

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TRANSFORM_DIR = os.path.join(_REPO_ROOT, "transform")

# Every mart the app reads. A partial `dbt run` can leave some of these missing,
# so all of them must be present before the bootstrap is considered done.
_REQUIRED_MARTS = (
    "mart_network_health",
    "mart_region_health_trend",
    "mart_region_vehicle_counts",
)

# The marts are views, so a database that ran an earlier release already has all
# three present - holding their OLD SQL. Presence alone therefore proves nothing:
# without a schema probe the bootstrap short-circuits forever and the new scoring
# never takes effect, silently, because the page degrades rather than crashing.
# These columns exist only in the current mart definitions, so a
# `mart_network_health` missing any of them is a stale view that must be rebuilt.
_SCHEMA_SENTINEL_MART = "mart_network_health"
_SCHEMA_SENTINEL_COLUMNS = ("feed_unavailable", "no_feed_count", "throttled_count",
                            "last_vehicle_timestamp")

# Ingestion calls ensure_dbt_models() after every fetch, and the page auto-
# refreshes every ~20s. Without a guard, a permanently failing bootstrap would
# spawn a doomed 2-5s dbt subprocess inside every Streamlit render. After a
# small number of consecutive failures we back off for a cooldown period
# rather than disabling the bootstrap for the life of the process - on
# Streamlit Cloud that process can live for days, and a transient failure
# (e.g. a DuckDB write-lock collision, or the subprocess timeout) must not
# leave analytics pages empty forever. A success needs no bookkeeping here -
# the marts-exist check short-circuits thereafter.
_MAX_FAILED_ATTEMPTS = 2
_FAILURE_COOLDOWN_SECONDS = 600
_failed_attempts = 0
_last_failure_monotonic = None


def marts_exist(con):
    """Return True only if ALL dbt mart views are present in this DB."""
    placeholders = ", ".join("?" for _ in _REQUIRED_MARTS)
    row = con.execute(
        "SELECT count(DISTINCT table_name) FROM information_schema.tables "
        f"WHERE table_name IN ({placeholders})",
        list(_REQUIRED_MARTS),
    ).fetchone()
    return row[0] == len(_REQUIRED_MARTS)


def marts_are_current(con):
    """Return True only if all mart views exist AND carry the current schema.

    Marts are views: an upgraded app pointed at an existing database finds every
    view present but still defined by the previous release's SQL. Rebuilding on a
    missing sentinel column is what makes a schema change actually reach users.
    """
    if not marts_exist(con):
        return False
    placeholders = ", ".join("?" for _ in _SCHEMA_SENTINEL_COLUMNS)
    row = con.execute(
        "SELECT count(DISTINCT column_name) FROM information_schema.columns "
        f"WHERE table_name = ? AND column_name IN ({placeholders})",
        [_SCHEMA_SENTINEL_MART, *_SCHEMA_SENTINEL_COLUMNS],
    ).fetchone()
    return row[0] == len(_SCHEMA_SENTINEL_COLUMNS)


def reset_attempts():
    """Clear the failed-attempt guard. Intended for tests."""
    global _failed_attempts, _last_failure_monotonic
    _failed_attempts = 0
    _last_failure_monotonic = None


def ensure_dbt_models(database_name):
    """
    Create or refresh the dbt mart views after the source tables exist.
    No-op if all mart views are already present *and* current; a stale view left
    behind by an earlier release is rebuilt. Never raises - a failed or
    unavailable dbt run must not break ingestion.
    Returns True if `dbt run` succeeded, False if skipped/failed.
    """
    global _failed_attempts, _last_failure_monotonic

    db_path = os.path.abspath(database_name)
    try:
        con = duckdb.connect(db_path)
        try:
            if marts_are_current(con):
                return False
        finally:
            con.close()
    except Exception as e:
        print(f"dbt bootstrap check skipped: {e}")
        return False

    if _failed_attempts >= _MAX_FAILED_ATTEMPTS:
        elapsed = (
            time.monotonic() - _last_failure_monotonic
            if _last_failure_monotonic is not None
            else float("inf")
        )
        if elapsed < _FAILURE_COOLDOWN_SECONDS:
            # Failed enough times recently; stop burning render time until
            # the cooldown lapses.
            return False
        print(
            f"dbt bootstrap cooldown ({_FAILURE_COOLDOWN_SECONDS}s) elapsed "
            "after prior failures; retrying."
        )

    env = {**os.environ, "DBT_DUCKDB_PATH": db_path}
    try:
        # Invoke dbt through the interpreter running Streamlit rather than a
        # bare "dbt" from PATH, which may be absent or point at another venv.
        subprocess.run(
            [sys.executable, "-m", "dbt.cli.main", "run",
             "--project-dir", _TRANSFORM_DIR,
             "--profiles-dir", _TRANSFORM_DIR],
            check=True, env=env, cwd=_REPO_ROOT,
            capture_output=True, text=True, timeout=120,
        )
        print("dbt marts created/refreshed")
        _failed_attempts = 0
        _last_failure_monotonic = None
        return True
    except Exception as e:
        _failed_attempts += 1
        _last_failure_monotonic = time.monotonic()
        print(
            "dbt run skipped/failed (non-fatal, attempt "
            f"{_failed_attempts}/{_MAX_FAILED_ATTEMPTS}): {e}"
        )
        stderr = getattr(e, "stderr", None)
        stdout = getattr(e, "stdout", None)
        if stderr:
            print(f"dbt stderr:\n{stderr}")
        if stdout:
            print(f"dbt stdout:\n{stdout}")
        if _failed_attempts >= _MAX_FAILED_ATTEMPTS:
            print(
                "dbt bootstrap paused for this process after "
                f"{_failed_attempts} failed attempts; will retry after "
                f"{_FAILURE_COOLDOWN_SECONDS}s cooldown."
            )
        return False
