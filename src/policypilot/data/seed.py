"""Seed scripts: SQLite/PostgreSQL tables and the MongoDB nested view from ONE dataset.

The document collection is always generated from the same canonical rows as the
SQL tables, so the two back ends can never disagree about a customer.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..schema import TABLES, TABLES_BY_NAME, ddl, to_document
from .synthetic import Dataset


def write_sqlite(data: Dataset, path: str | Path, overwrite: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if overwrite and path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(ddl())
        for table in TABLES:
            rows = getattr(data, table.name)
            cols = table.column_names
            placeholders = ", ".join("?" for _ in cols)
            conn.executemany(
                f"INSERT INTO {table.name} ({', '.join(cols)}) VALUES ({placeholders})",
                [tuple(r.get(c) for c in cols) for r in rows],
            )
        conn.commit()
    finally:
        conn.close()
    return path


def read_sqlite(path: str | Path) -> Dataset:
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        data = Dataset()
        for name in TABLES_BY_NAME:
            setattr(data, name, [dict(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY 1")])
        return data
    finally:
        conn.close()


def build_documents(data: Dataset) -> list[dict]:
    claims_by_vehicle = {c["vehicle_id"]: c for c in data.claims}
    docs = []
    for vehicle in data.vehicles:
        claim = claims_by_vehicle.get(vehicle["vehicle_id"])
        if claim is not None:
            docs.append(to_document(vehicle, claim))
    return docs


def write_jsonl(docs: list[dict], path: str | Path) -> Path:
    """One JSON document per line, for ``mongoimport --file``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for doc in docs:
            fh.write(json.dumps(doc) + "\n")
    return path


MONGO_JSON_SCHEMA = {
    "bsonType": "object",
    "required": ["vehicle_id", "customer_id", "car", "claims"],
    "properties": {
        "vehicle_id": {"bsonType": "int"},
        "customer_id": {"bsonType": "int"},
        "bluebook_value": {"bsonType": ["int", "null"]},
        "years_insured": {"bsonType": ["int", "null"]},
        "car": {"bsonType": "object"},
        "claims": {"bsonType": "object"},
    },
}


def seed_mongo(docs: list[dict], uri: str, db: str, collection: str) -> int:  # pragma: no cover - needs Mongo
    """Recreate the collection with a JSON-schema validator and a unique key. Run as an admin user;
    the application itself must connect with a separate read-only user."""
    import pymongo

    client = pymongo.MongoClient(uri)
    try:
        database = client[db]
        if collection in database.list_collection_names():
            database.drop_collection(collection)
        database.create_collection(collection, validator={"$jsonSchema": MONGO_JSON_SCHEMA})
        coll = database[collection]
        coll.create_index("vehicle_id", unique=True)
        coll.create_index("customer_id")
        if docs:
            coll.insert_many([dict(d) for d in docs])
        return coll.count_documents({})
    finally:
        client.close()


def write_postgres(data: Dataset, dsn: str) -> None:  # pragma: no cover - needs PostgreSQL
    """Create and fill the tables. Run as an owner role, never as the app's read-only role."""
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(ddl())
        for table in TABLES:
            cols = table.column_names
            placeholders = ", ".join("%s" for _ in cols)
            cur.executemany(
                f"INSERT INTO {table.name} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                [tuple(r.get(c) for c in cols) for r in getattr(data, table.name)],
            )
        conn.commit()
