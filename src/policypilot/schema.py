"""The single canonical data model.

One typed description of every table and document field drives the DDL, the
synthetic data generator, the LLM prompts, the SQL column allow-list, the
enum-value checks and the MongoDB field allow-list. Customers, vehicles and
claims share ``customer_id`` so cross-source ("both") questions can be joined.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Column:
    name: str
    type: str                      # INTEGER, REAL, TEXT, DATE, BOOLEAN
    description: str
    enum: tuple[str, ...] = ()     # allowed literal values for TEXT columns
    primary_key: bool = False
    references: str = ""           # "table.column" for foreign keys


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    columns: tuple[Column, ...]

    def column(self, name: str) -> Column | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


EDUCATION = ("High School", "Bachelors", "Masters", "PhD", "Less Than High School")
OCCUPATION = ("Blue Collar", "Clerical", "Doctor", "Home Maker", "Lawyer", "Manager",
              "Professional", "Student", "Unknown")
CAR_TYPE = ("Minivan", "Panel Truck", "Pickup", "Sports Car", "SUV", "Van")
CAR_USE = ("Private", "Commercial")
URBANICITY = ("Urban", "Rural")
GENDER = ("F", "M")

TABLES: tuple[Table, ...] = (
    Table(
        "customers",
        "One row per policy holder (demographics).",
        (
            Column("customer_id", "INTEGER", "Customer key, shared with vehicles, claims and documents",
                   primary_key=True),
            Column("birth_date", "DATE", "Date of birth (YYYY-MM-DD)"),
            Column("age", "INTEGER", "Age in years"),
            Column("gender", "TEXT", "Gender: 'F' or 'M'", enum=GENDER),
            Column("married", "BOOLEAN", "1 if married, else 0"),
            Column("single_parent", "BOOLEAN", "1 if the customer is a single parent, else 0"),
            Column("kids_driving", "INTEGER", "Number of children who drive"),
            Column("kids_at_home", "INTEGER", "Number of children living at home"),
            Column("years_on_job", "INTEGER", "Years in the current job"),
            Column("income", "INTEGER", "Annual income in USD"),
            Column("home_value", "INTEGER", "Home value in USD (0 = does not own a home)"),
            Column("education", "TEXT", "Highest education level", enum=EDUCATION),
            Column("occupation", "TEXT", "Occupation", enum=OCCUPATION),
            Column("commute_minutes", "INTEGER", "Commute time in minutes"),
        ),
    ),
    Table(
        "vehicles",
        "One row per insured vehicle.",
        (
            Column("vehicle_id", "INTEGER", "Vehicle key", primary_key=True),
            Column("customer_id", "INTEGER", "Owner", references="customers.customer_id"),
            Column("car_use", "TEXT", "Usage", enum=CAR_USE),
            Column("car_type", "TEXT", "Body type", enum=CAR_TYPE),
            Column("red_car", "BOOLEAN", "1 if the car is red"),
            Column("car_age", "INTEGER", "Age of the car in years"),
            Column("bluebook_value", "INTEGER", "Blue Book resale value in USD"),
            Column("years_insured", "INTEGER", "Time in force: years the customer has been insured"),
            Column("urbanicity", "TEXT", "Area where the car is mostly driven", enum=URBANICITY),
        ),
    ),
    Table(
        "claims",
        "Claim history per vehicle (one row per vehicle).",
        (
            Column("claim_id", "INTEGER", "Claim record key", primary_key=True),
            Column("vehicle_id", "INTEGER", "Vehicle", references="vehicles.vehicle_id"),
            Column("customer_id", "INTEGER", "Customer", references="customers.customer_id"),
            Column("claims_last_5y", "INTEGER", "Number of claims in the past five years"),
            Column("past_claims_total", "INTEGER", "Total payout of past claims in USD"),
            Column("license_revoked", "BOOLEAN", "1 if the driver's licence was revoked in the past 7 years"),
            Column("mvr_points", "INTEGER", "Motor vehicle record (traffic violation) points"),
            Column("claim_amount", "INTEGER", "Amount of the current claim in USD (0 = no claim)"),
            Column("claim_flag", "BOOLEAN", "1 if the vehicle had a crash claim in the current period"),
        ),
    ),
)

TABLES_BY_NAME = {t.name: t for t in TABLES}


def ddl() -> str:
    """Portable DDL that runs on both SQLite and PostgreSQL."""
    stmts = []
    for table in TABLES:
        cols = []
        for col in table.columns:
            sql_type = {"BOOLEAN": "SMALLINT", "DATE": "DATE"}.get(col.type, col.type)
            line = f"  {col.name} {sql_type}"
            if col.primary_key:
                line += " PRIMARY KEY"
            if col.references:
                ref_table, ref_col = col.references.split(".")
                line += f" REFERENCES {ref_table}({ref_col})"
            if col.enum:
                values = ", ".join("'" + v.replace("'", "''") + "'" for v in col.enum)
                line += f" CHECK ({col.name} IN ({values}))"
            cols.append(line)
        stmts.append(f"CREATE TABLE IF NOT EXISTS {table.name} (\n" + ",\n".join(cols) + "\n);")
    return "\n\n".join(stmts)


def describe_tables(allowed: tuple[str, ...] | None = None) -> str:
    """Compact schema description for prompts."""
    lines = []
    for table in TABLES:
        if allowed is not None and table.name not in allowed:
            continue
        lines.append(f"TABLE {table.name} -- {table.description}")
        for col in table.columns:
            extra = f" one of {list(col.enum)}" if col.enum else ""
            fk = f" -> {col.references}" if col.references else ""
            lines.append(f"  {col.name} {col.type}{fk}: {col.description}{extra}")
    return "\n".join(lines)


# ---------------------------------------------------------------- document model
@dataclass(frozen=True)
class DocField:
    path: str
    type: str
    description: str
    enum: tuple[str, ...] = field(default=())


DOC_FIELDS: tuple[DocField, ...] = (
    DocField("vehicle_id", "int", "Vehicle key"),
    DocField("customer_id", "int", "Customer key (same as customers.customer_id in SQL)"),
    DocField("car_use", "string", "Usage", CAR_USE),
    DocField("urbanicity", "string", "Area where the car is mostly driven", URBANICITY),
    DocField("years_insured", "int", "Time in force in years"),
    DocField("bluebook_value", "int", "Blue Book resale value in USD"),
    DocField("car.type", "string", "Body type", CAR_TYPE),
    DocField("car.red", "bool", "True if the car is red"),
    DocField("car.age", "int", "Age of the car in years"),
    DocField("claims.last_5y", "int", "Number of claims in the past five years"),
    DocField("claims.past_total", "int", "Total payout of past claims in USD"),
    DocField("claims.license_revoked", "bool", "True if the licence was revoked"),
    DocField("claims.mvr_points", "int", "Traffic violation points"),
    DocField("claims.amount", "int", "Amount of the current claim in USD (0 = no claim)"),
    DocField("claims.flag", "bool", "True if there was a crash claim in the current period"),
)

DOC_FIELD_PATHS = tuple(f.path for f in DOC_FIELDS)


def describe_documents() -> str:
    lines = ["COLLECTION policies -- one document per insured vehicle, numbers are real numbers"]
    for f in DOC_FIELDS:
        extra = f" one of {list(f.enum)}" if f.enum else ""
        lines.append(f"  {f.path}: {f.type}: {f.description}{extra}")
    return "\n".join(lines)


def _opt_int(value) -> int | None:
    return None if value is None else int(value)


def _opt_bool(value) -> bool | None:
    return None if value is None else bool(value)


def to_document(vehicle: dict, claim: dict) -> dict:
    """Build the nested Mongo view of one vehicle from canonical rows (typed, no strings for numbers)."""
    return {
        "vehicle_id": int(vehicle["vehicle_id"]),
        "customer_id": int(vehicle["customer_id"]),
        "car_use": vehicle["car_use"],
        "urbanicity": vehicle["urbanicity"],
        "years_insured": _opt_int(vehicle["years_insured"]),
        "bluebook_value": _opt_int(vehicle["bluebook_value"]),
        "car": {"type": vehicle["car_type"], "red": _opt_bool(vehicle["red_car"]),
                "age": _opt_int(vehicle["car_age"])},
        "claims": {
            "last_5y": _opt_int(claim["claims_last_5y"]),
            "past_total": _opt_int(claim["past_claims_total"]),
            "license_revoked": _opt_bool(claim["license_revoked"]),
            "mvr_points": _opt_int(claim["mvr_points"]),
            "amount": _opt_int(claim["claim_amount"]),
            "flag": _opt_bool(claim["claim_flag"]),
        },
    }
