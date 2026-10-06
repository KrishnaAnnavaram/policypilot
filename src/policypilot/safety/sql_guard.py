"""Parser-based validation of LLM-written SQL (sqlglot).

A query is accepted only if ALL of the following hold:

* it parses, and is exactly one statement;
* the statement is a ``SELECT`` (or a UNION/INTERSECT/EXCEPT of SELECTs) and contains
  no DML/DDL/``SELECT INTO``/locking/``COPY``/``PRAGMA``/``SET`` node anywhere;
* every table is an allow-listed table (or a CTE defined in the same query) and no
  table-valued function is used;
* every column exists in an allow-listed table (or is an alias defined in the query),
  and ``*`` only appears inside ``COUNT(*)``;
* every function is on an allow-list (so ``pg_sleep``, ``pg_read_file``,
  ``load_extension``, ``dblink`` ... are rejected);
* string literals compared with enum columns are valid enum values;
* a ``LIMIT`` is present and no larger than the configured maximum (added or capped).

The accepted query is re-emitted by sqlglot in the target dialect, so what runs is
exactly the AST that was checked. The database role must still be read-only:
validation is one layer, not the only one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from ..schema import TABLES_BY_NAME


class SQLValidationError(ValueError):
    pass


ALLOWED_FUNCTIONS = frozenset({
    "COUNT", "SUM", "AVG", "MIN", "MAX", "STDDEV", "STDDEV_POP", "STDDEV_SAMP", "VARIANCE",
    "MEDIAN", "PERCENTILE_CONT", "PERCENTILE_DISC", "COUNT_IF",
    "ROUND", "ABS", "FLOOR", "CEIL", "POWER", "POW", "SQRT", "GREATEST", "LEAST", "MOD",
    "COALESCE", "NULLIF", "IFNULL", "CAST", "TRY_CAST", "CASE", "IF", "IIF",
    "AND", "OR", "XOR",
    "LOWER", "UPPER", "LENGTH", "TRIM", "SUBSTRING", "SUBSTR", "CONCAT",
    "EXTRACT", "DATE_PART", "DATE_TRUNC", "TIMESTAMP_TRUNC", "STRFTIME", "DATE", "CURRENT_DATE",
    "STR_TO_TIME", "TIME_TO_STR", "TS_OR_DS_TO_DATE", "YEAR", "MONTH", "DAY",
    "ROW_NUMBER", "RANK", "DENSE_RANK",
})

_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable", "Command", "Merge",
    "Into", "Pragma", "Copy", "Transaction", "Commit", "Rollback", "Set", "Grant", "Revoke",
    "Use", "LoadData", "Lock", "Attach", "Detach", "Analyze", "Truncate", "TruncateTable",
)
_FORBIDDEN_NODES = tuple(t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NODE_NAMES) if isinstance(t, type))


@dataclass
class SQLPolicy:
    tables: dict[str, frozenset[str]]                       # table -> allowed columns
    enums: dict[str, frozenset[str]] = field(default_factory=dict)   # column -> allowed literal values
    max_rows: int = 200
    read_dialect: str = "postgres"
    write_dialect: str = "sqlite"
    max_chars: int = 4000
    schema_name: str = ""                                   # optional allowed schema qualifier

    @classmethod
    def from_schema(cls, allowed_tables: tuple[str, ...] | list[str], **kwargs) -> "SQLPolicy":
        tables: dict[str, frozenset[str]] = {}
        enums: dict[str, set[str]] = {}
        for name in allowed_tables:
            table = TABLES_BY_NAME.get(name.lower())
            if table is None:
                raise ValueError(f"unknown table in allow-list: {name!r}")
            tables[table.name] = frozenset(table.column_names)
            for col in table.columns:
                if col.enum:
                    enums.setdefault(col.name, set()).update(col.enum)
        return cls(tables=tables, enums={k: frozenset(v) for k, v in enums.items()}, **kwargs)

    @property
    def all_columns(self) -> frozenset[str]:
        out: set[str] = set()
        for cols in self.tables.values():
            out |= cols
        return frozenset(out)


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    tables: tuple[str, ...]
    limit: int


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).upper()
    return node.sql_name().upper()


def _check_enum(node: exp.Expression, policy: SQLPolicy) -> None:
    pairs: list[tuple[exp.Column, list[exp.Expression]]] = []
    if isinstance(node, (exp.EQ, exp.NEQ)):
        left, right = node.this, node.expression
        if isinstance(left, exp.Column):
            pairs.append((left, [right]))
        if isinstance(right, exp.Column):
            pairs.append((right, [left]))
    elif isinstance(node, exp.In) and isinstance(node.this, exp.Column):
        pairs.append((node.this, list(node.expressions)))
    for column, values in pairs:
        allowed = policy.enums.get(column.name.lower())
        if not allowed:
            continue
        for value in values:
            if isinstance(value, exp.Literal) and value.is_string and value.this not in allowed:
                raise SQLValidationError(
                    f"invalid value {value.this!r} for column {column.name}; allowed values: {sorted(allowed)}"
                )


def _apply_limit(root: exp.Expression, max_rows: int) -> int:
    current = root.args.get("limit")
    if current is None:
        root.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return max_rows
    count_expr = current.args.get("count") if isinstance(current, exp.Fetch) else current.expression
    if not (isinstance(count_expr, exp.Literal) and not count_expr.is_string and str(count_expr.this).isdigit()):
        raise SQLValidationError("LIMIT must be a plain integer literal")
    limit = min(int(count_expr.this), max_rows)
    root.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
    return limit


def validate_sql(sql: str, policy: SQLPolicy) -> ValidatedSQL:
    if not sql or not sql.strip():
        raise SQLValidationError("empty query")
    if len(sql) > policy.max_chars:
        raise SQLValidationError(f"query is longer than {policy.max_chars} characters")
    try:
        statements = [s for s in sqlglot.parse(sql, read=policy.read_dialect) if s is not None]
    except (ParseError, TokenError) as exc:
        raise SQLValidationError(f"could not parse SQL: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise SQLValidationError(f"expected exactly one statement, found {len(statements)}")
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SQLValidationError(f"only SELECT queries are allowed, got {root.key.upper()}")

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLValidationError(f"forbidden SQL construct: {node.key.upper()}")

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    alias_to_table: dict[str, str] = {}
    used_tables: set[str] = set()
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SQLValidationError("table-valued functions are not allowed")
        name = table.name.lower()
        if table.args.get("catalog") is not None or (table.db and table.db.lower() != policy.schema_name.lower()):
            raise SQLValidationError(f"schema-qualified table {table.sql()} is not allowed")
        if name in cte_names and not table.db:
            continue
        if name not in policy.tables:
            raise SQLValidationError(f"table {name!r} is not allowed; allowed tables: {sorted(policy.tables)}")
        used_tables.add(name)
        alias_to_table[(table.alias or name).lower()] = name

    defined_aliases = {a.alias.lower() for a in root.find_all(exp.Alias) if a.alias}
    for cte in root.find_all(exp.CTE):
        alias = cte.args.get("alias")
        if alias is not None:
            defined_aliases |= {c.name.lower() for c in alias.args.get("columns") or []}
    all_columns = policy.all_columns

    for column in root.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            raise SQLValidationError("'table.*' is not allowed; list the columns you need")
        name = column.name.lower()
        qualifier = column.table.lower() if column.table else ""
        if qualifier and qualifier in alias_to_table:
            if name not in policy.tables[alias_to_table[qualifier]]:
                raise SQLValidationError(f"column {qualifier}.{name} does not exist or is not allowed")
        elif name not in all_columns and name not in defined_aliases:
            raise SQLValidationError(f"column {name!r} does not exist or is not allowed")

    for star in root.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise SQLValidationError("SELECT * is not allowed; list the columns you need")

    for func in root.find_all(exp.Func):
        fname = _function_name(func)
        if fname not in ALLOWED_FUNCTIONS:
            raise SQLValidationError(f"function {fname} is not allowed")

    for node in root.walk():
        _check_enum(node, policy)

    limit = _apply_limit(root, policy.max_rows)
    out = root.sql(dialect=policy.write_dialect, comments=False)
    return ValidatedSQL(sql=out, tables=tuple(sorted(used_tables)), limit=limit)
