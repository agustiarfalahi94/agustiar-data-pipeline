import duckdb
from utils import dbt_runner


def test_marts_exist_false_on_empty_db(tmp_path):
    db = tmp_path / "empty.duckdb"
    con = duckdb.connect(str(db))
    try:
        assert dbt_runner.marts_exist(con) is False
    finally:
        con.close()


def test_marts_exist_true_when_view_present(tmp_path):
    db = tmp_path / "with_view.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE VIEW mart_network_health AS SELECT 1 AS region")
        assert dbt_runner.marts_exist(con) is True
    finally:
        con.close()
