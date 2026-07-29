import duckdb
import pytest
from utils import dbt_runner


@pytest.fixture(autouse=True)
def _clear_attempt_guard():
    """The failed-attempt guard is module-level state; isolate every test."""
    dbt_runner.reset_attempts()
    yield
    dbt_runner.reset_attempts()


def _make_marts(con, names):
    for i, name in enumerate(names):
        con.execute(f"CREATE VIEW {name} AS SELECT {i} AS region")


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


def test_ensure_dbt_models_never_raises_when_dbt_missing(tmp_path, monkeypatch):
    db = tmp_path / "no_marts.duckdb"

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("dbt not found")

    monkeypatch.setattr(dbt_runner.subprocess, "run", fake_run)

    assert dbt_runner.ensure_dbt_models(str(db)) is False


def test_ensure_dbt_models_stops_retrying_after_max_failures(tmp_path, monkeypatch):
    """A doomed bootstrap must not spawn a subprocess on every auto-refresh."""
    db = tmp_path / "no_marts.duckdb"
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise FileNotFoundError("dbt not found")

    monkeypatch.setattr(dbt_runner.subprocess, "run", fake_run)

    for _ in range(10):
        assert dbt_runner.ensure_dbt_models(str(db)) is False

    assert len(calls) == dbt_runner._MAX_FAILED_ATTEMPTS


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


def test_ensure_dbt_models_skips_when_all_marts_present(tmp_path, monkeypatch):
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
