import pytest

from policypilot.backends.base import QueryError
from policypilot.backends.sql import SQLiteExecutor


def test_reads_work(executor):
    res = executor.execute("SELECT COUNT(*) FROM customers", 10)
    assert res.scalar() == 120


@pytest.mark.parametrize("sql", [
    "DELETE FROM customers",
    "UPDATE customers SET income = 0",
    "DROP TABLE claims",
    "CREATE TABLE x (a INTEGER)",
    "ATTACH DATABASE 'other.db' AS other",
    "SELECT name FROM sqlite_master",
    "PRAGMA writable_schema = 1",
])
def test_executor_is_read_only_even_without_the_validator(executor, sql):
    with pytest.raises(QueryError):
        executor.execute(sql, 10)
    assert executor.execute("SELECT COUNT(*) FROM customers", 10).scalar() == 120


def test_executor_enforces_table_allow_list(db_path):
    only_customers = SQLiteExecutor(db_path, ("customers",))
    with pytest.raises(QueryError):
        only_customers.execute("SELECT COUNT(*) FROM claims", 10)


def test_timeout_aborts_long_queries(db_path):
    slow = SQLiteExecutor(db_path, ("customers",), timeout_s=0.2)
    with pytest.raises(QueryError, match="time limit"):
        slow.execute("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT MAX(i) FROM n", 10)


def test_row_cap_and_truncation_flag(executor):
    res = executor.execute("SELECT customer_id FROM customers", 5)
    assert len(res.rows) == 5 and res.truncated


def test_missing_database_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="seed"):
        SQLiteExecutor(tmp_path / "nope.db", ("customers",))
