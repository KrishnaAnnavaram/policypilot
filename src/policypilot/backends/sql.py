"""SQL executors. Both open the database READ-ONLY and enforce a statement timeout.

The validator in :mod:`policypilot.safety.sql_guard` is the first layer; these
executors are the second: even a query that slipped past validation cannot write.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .base import QueryError, QueryResult

_SQLITE_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                           getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
_SQLITE_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


class SQLiteExecutor:
    """Read-only SQLite access: ``mode=ro`` URI + ``PRAGMA query_only`` + an authorizer that
    only permits reads of allow-listed tables + a progress-handler timeout."""

    dialect = "sqlite"

    def __init__(self, path: str | Path, allowed_tables: tuple[str, ...], timeout_s: float = 5.0):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"SQLite database not found: {self.path} (run `policypilot seed` first)")
        self.allowed_tables = {t.lower() for t in allowed_tables}
        self.timeout_s = timeout_s

    def _authorizer(self, action: int, arg1, arg2, _db, _trigger) -> int:
        if action not in _SQLITE_ALLOWED_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and arg1 and arg1.lower() not in self.allowed_tables:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _SQLITE_DENIED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.set_authorizer(self._authorizer)
            deadline = time.monotonic() + self.timeout_s
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
            try:
                cur = conn.execute(sql)
                rows = cur.fetchmany(max_rows + 1)
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc).lower():
                    raise QueryError(f"query exceeded the {self.timeout_s:g}s time limit") from exc
                raise QueryError(str(exc)) from exc
            except sqlite3.DatabaseError as exc:
                raise QueryError(str(exc)) from exc
            columns = [d[0] for d in cur.description or []]
            return QueryResult(columns=columns, rows=[tuple(r) for r in rows[:max_rows]],
                               truncated=len(rows) > max_rows, query=sql)
        finally:
            conn.close()


class PostgresExecutor:  # pragma: no cover - needs a PostgreSQL server
    """PostgreSQL access through a read-only role. Each query runs in a READ ONLY
    transaction with ``statement_timeout`` and is rolled back afterwards."""

    dialect = "postgres"

    def __init__(self, dsn: str, timeout_s: float = 5.0):
        if not dsn:
            raise ValueError("POSTGRES_DSN is empty")
        self.dsn = dsn
        self.timeout_s = timeout_s

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        import psycopg

        timeout_ms = int(self.timeout_s * 1000)
        try:
            with psycopg.connect(self.dsn, autocommit=False) as conn:
                conn.read_only = True
                with conn.cursor() as cur:
                    cur.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                    cur.execute(sql)
                    rows = cur.fetchmany(max_rows + 1)
                    columns = [d.name for d in cur.description or []]
                conn.rollback()
        except psycopg.Error as exc:
            raise QueryError(str(exc).strip().splitlines()[0]) from exc
        return QueryResult(columns=columns, rows=[tuple(r) for r in rows[:max_rows]],
                           truncated=len(rows) > max_rows, query=sql)
