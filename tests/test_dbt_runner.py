import duckdb
import pytest
from utils import dbt_runner


@pytest.fixture(autouse=True)
def _clear_attempt_guard():
    """The failed-attempt guard is module-level state; isolate every test."""
    dbt_runner.reset_attempts()
    yield
    dbt_runner.reset_attempts()


def _make_marts(con, names, current_schema=True):
    """Create stand-in mart views. `current_schema=False` reproduces the views a
    previous release left behind: present, but without this release's columns."""
    for i, name in enumerate(names):
        cols = f"{i} AS region"
        if name == dbt_runner._SCHEMA_SENTINEL_MART and current_schema:
            cols += "".join(
                f", false AS {c}" for c in dbt_runner._SCHEMA_SENTINEL_COLUMNS
            )
        con.execute(f"CREATE VIEW {name} AS SELECT {cols}")


def test_marts_exist_false_on_empty_db(tmp_path):
    db = tmp_path / "empty.duckdb"
    con = duckdb.connect(str(db))
    try:
        assert dbt_runner.marts_exist(con) is False
    finally:
        con.close()


def test_marts_exist_false_when_only_sentinel_present(tmp_path):
    """A partial `dbt run` must not be mistaken for a complete one."""
    db = tmp_path / "partial.duckdb"
    con = duckdb.connect(str(db))
    try:
        _make_marts(con, ["mart_network_health"])
        assert dbt_runner.marts_exist(con) is False
    finally:
        con.close()


def test_marts_exist_true_when_all_marts_present(tmp_path):
    db = tmp_path / "with_views.duckdb"
    con = duckdb.connect(str(db))
    try:
        _make_marts(con, dbt_runner._REQUIRED_MARTS)
        assert dbt_runner.marts_exist(con) is True
    finally:
        con.close()


def test_marts_are_current_false_when_schema_is_stale(tmp_path):
    """Marts are views, so an existing database upgraded from a previous release
    has all three present while they still hold the OLD SQL. Presence alone must
    not be mistaken for currency, or the new scoring never reaches the page."""
    db = tmp_path / "stale.duckdb"
    con = duckdb.connect(str(db))
    try:
        _make_marts(con, dbt_runner._REQUIRED_MARTS, current_schema=False)
        assert dbt_runner.marts_exist(con) is True
        assert dbt_runner.marts_are_current(con) is False
    finally:
        con.close()


def test_marts_are_current_true_on_current_schema(tmp_path):
    db = tmp_path / "current.duckdb"
    con = duckdb.connect(str(db))
    try:
        _make_marts(con, dbt_runner._REQUIRED_MARTS)
        assert dbt_runner.marts_are_current(con) is True
    finally:
        con.close()


def test_ensure_dbt_models_rebuilds_stale_marts(tmp_path, monkeypatch):
    """A database whose `mart_network_health` lacks `feed_unavailable` needs a
    rebuild, not a skip."""
    db = tmp_path / "stale_marts.duckdb"
    con = duckdb.connect(str(db))
    try:
        _make_marts(con, dbt_runner._REQUIRED_MARTS, current_schema=False)
    finally:
        con.close()

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return None

    monkeypatch.setattr(dbt_runner.subprocess, "run", fake_run)
    assert dbt_runner.ensure_dbt_models(str(db)) is True
    assert len(calls) == 1


def test_ensure_dbt_models_never_raises_when_dbt_missing(tmp_path, monkeypatch):
    db = tmp_path / "no_marts.duckdb"

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("dbt not found")

    monkeypatch.setattr(dbt_runner.subprocess, "run", fake_run)

    assert dbt_runner.ensure_dbt_models(str(db)) is False


def test_ensure_dbt_models_stops_retrying_after_max_failures(tmp_path, monkeypatch):
    """A doomed bootstrap must not spawn a subprocess on every auto-refresh,
    at least until the cooldown window elapses (see the cooldown test below)."""
    db = tmp_path / "no_marts.duckdb"
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise FileNotFoundError("dbt not found")

    monkeypatch.setattr(dbt_runner.subprocess, "run", fake_run)

    for _ in range(10):
        assert dbt_runner.ensure_dbt_models(str(db)) is False

    assert len(calls) == dbt_runner._MAX_FAILED_ATTEMPTS


def test_ensure_dbt_models_retries_after_cooldown(tmp_path, monkeypatch):
    """Transient failures must not lock the bootstrap out permanently: once
    the cooldown window has elapsed since the last failure, a retry becomes
    possible again."""
    db = tmp_path / "no_marts.duckdb"
    calls = []
    fake_now = {"t": 1_000.0}

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise FileNotFoundError("dbt not found")

    def fake_monotonic():
        return fake_now["t"]

    monkeypatch.setattr(dbt_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(dbt_runner.time, "monotonic", fake_monotonic)

    for _ in range(dbt_runner._MAX_FAILED_ATTEMPTS):
        assert dbt_runner.ensure_dbt_models(str(db)) is False
    assert len(calls) == dbt_runner._MAX_FAILED_ATTEMPTS

    # Still inside the cooldown window - no new subprocess call.
    fake_now["t"] += dbt_runner._FAILURE_COOLDOWN_SECONDS - 1
    assert dbt_runner.ensure_dbt_models(str(db)) is False
    assert len(calls) == dbt_runner._MAX_FAILED_ATTEMPTS

    # Cooldown has now elapsed - a retry must be attempted again.
    fake_now["t"] += 2
    assert dbt_runner.ensure_dbt_models(str(db)) is False
    assert len(calls) == dbt_runner._MAX_FAILED_ATTEMPTS + 1


def test_ensure_dbt_models_invokes_dbt_via_current_interpreter(tmp_path, monkeypatch):
    """dbt must resolve through sys.executable, not a bare `dbt` on PATH."""
    import sys

    db = tmp_path / "no_marts.duckdb"
    captured = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return None

    monkeypatch.setattr(dbt_runner.subprocess, "run", fake_run)
    assert dbt_runner.ensure_dbt_models(str(db)) is True
    assert captured["cmd"][:4] == [sys.executable, "-m", "dbt.cli.main", "run"]


def test_ensure_dbt_models_skips_when_all_marts_present_and_current(tmp_path, monkeypatch):
    db = tmp_path / "all_marts.duckdb"
    con = duckdb.connect(str(db))
    try:
        _make_marts(con, dbt_runner._REQUIRED_MARTS)
    finally:
        con.close()

    def fail(*args, **kwargs):
        raise AssertionError("dbt run must not be invoked when marts exist")

    monkeypatch.setattr(dbt_runner.subprocess, "run", fail)
    assert dbt_runner.ensure_dbt_models(str(db)) is False
