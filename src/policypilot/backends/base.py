"""Back-end interfaces and the shared result type."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol


def to_jsonable(value: Any) -> Any:
    """Make query results JSON-safe (ObjectId, Decimal, dates, NaN ...) without raising."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    return str(value)          # bson.ObjectId and anything else exotic


def _flatten(record: dict, prefix: str = "") -> dict:
    out: dict = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(_flatten(value, name + "."))
        else:
            out[name] = value
    return out


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False
    query: str = ""
    meta: dict = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: list[dict], truncated: bool = False, query: str = "") -> "QueryResult":
        flat = [_flatten(to_jsonable(r)) for r in records]
        columns: list[str] = []
        for rec in flat:
            for key in rec:
                if key not in columns:
                    columns.append(key)
        rows = [tuple(rec.get(c) for c in columns) for rec in flat]
        return cls(columns=columns, rows=rows, truncated=truncated, query=query)

    def records(self) -> list[dict]:
        return [dict(zip(self.columns, (to_jsonable(v) for v in row))) for row in self.rows]

    def scalar(self) -> Any:
        if len(self.rows) == 1 and len(self.rows[0]) == 1:
            return self.rows[0][0]
        return None

    def to_json(self) -> str:
        return json.dumps(self.records(), ensure_ascii=False)

    def to_markdown(self, max_rows: int = 20) -> str:
        if not self.rows:
            return "_(no rows)_"
        head = "| " + " | ".join(self.columns) + " |"
        sep = "| " + " | ".join("---" for _ in self.columns) + " |"
        body = ["| " + " | ".join(_fmt(v) for v in row) + " |" for row in self.rows[:max_rows]]
        more = [f"\n_... {len(self.rows) - max_rows} more rows_"] if len(self.rows) > max_rows else []
        return "\n".join([head, sep, *body]) + "".join(more)


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(to_jsonable(value))


class QueryError(RuntimeError):
    """Raised by executors when the database rejects or aborts a query."""


class SQLExecutor(Protocol):
    dialect: str

    def execute(self, sql: str, max_rows: int) -> QueryResult: ...


class DocumentStore(Protocol):
    def aggregate(self, pipeline: list, max_rows: int) -> QueryResult: ...
