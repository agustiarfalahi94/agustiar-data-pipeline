import os
import subprocess
import sys

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

# Ingestion calls ensure_dbt_models() after every fetch, and the page auto-
# refreshes every ~20s. Without a guard, a permanently failing bootstrap would
# spawn a doomed 2-5s dbt subprocess inside every Streamlit render. Give up
# after a small number of failures for the lifetime of the process. A success
# needs no bookkeeping here - the marts-exist check short-circuits thereafter.
_MAX_FAILED_ATTEMPTS = 2
_failed_attempts = 0


def marts_exist(con):
    """Return True only if ALL dbt mart views are present in this DB."""
    placeholders = ", ".join("?" for _ in _REQUIRED_MARTS)
    row = con.execute(
        "SELECT count(DISTINCT table_name) FROM information_schema.tables "
        f"WHERE table_name IN ({placeholders})",
        list(_REQUIRED_MARTS),
    ).fetchone()
    return row[0] == len(_REQUIRED_MARTS)


def reset_attempts():
    """Clear the failed-attempt guard. Intended for tests."""
    global _failed_attempts
    _failed_attempts = 0


def ensure_dbt_models(database_name):
    """
    Create the dbt mart views once, after the source tables exist.
    No-op if all mart views are already present. Never raises - a failed or
    unavailable dbt run must not break ingestion.
    Returns True if `dbt run` succeeded, False if skipped/failed.
    """
    global _failed_attempts

    db_path = os.path.abspath(database_name)
    try:
        con = duckdb.connect(db_path)
        try:
            if marts_exist(con):
                return False
        finally:
            con.close()
    except Exception as e:
        print(f"dbt bootstrap check skipped: {e}")
        return False

    if _failed_attempts >= _MAX_FAILED_ATTEMPTS:
        # Already failed enough times in this process; stop burning render time.
        return False

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
        print("dbt marts created")
        return True
    except Exception as e:
        _failed_attempts += 1
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
                "dbt bootstrap disabled for this process after "
                f"{_MAX_FAILED_ATTEMPTS} failed attempts."
            )
        return False
