import os
import subprocess

import duckdb

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TRANSFORM_DIR = os.path.join(_REPO_ROOT, "transform")
_SENTINEL_VIEW = "mart_network_health"


def marts_exist(con):
    """Return True if the dbt mart views have already been created in this DB."""
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [_SENTINEL_VIEW],
    ).fetchone()
    return row[0] > 0


def ensure_dbt_models(database_name):
    """
    Create the dbt mart views once, after the source tables exist.
    No-op if the views are already present. Never raises — a failed or
    unavailable dbt run must not break ingestion.
    Returns True if `dbt run` was invoked, False if skipped/failed.
    """
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

    env = {**os.environ, "DBT_DUCKDB_PATH": db_path}
    try:
        subprocess.run(
            ["dbt", "run", "--project-dir", _TRANSFORM_DIR,
             "--profiles-dir", _TRANSFORM_DIR],
            check=True, env=env, cwd=_REPO_ROOT,
            capture_output=True, text=True, timeout=120,
        )
        print("✓ dbt marts created")
        return True
    except Exception as e:
        print(f"dbt run skipped/failed (non-fatal): {e}")
        stderr = getattr(e, "stderr", None)
        stdout = getattr(e, "stdout", None)
        if stderr:
            print(f"dbt stderr:\n{stderr}")
        if stdout:
            print(f"dbt stdout:\n{stdout}")
        return False
