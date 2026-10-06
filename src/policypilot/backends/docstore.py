"""Document stores: an in-memory store for the offline demo/tests and a MongoDB adapter."""
from __future__ import annotations

from .aggregation import AggregationError, run_pipeline
from .base import QueryError, QueryResult


class InMemoryDocStore:
    def __init__(self, documents: list[dict]):
        self.documents = list(documents)

    def aggregate(self, pipeline: list, max_rows: int) -> QueryResult:
        try:
            records = run_pipeline(self.documents, pipeline)
        except (AggregationError, KeyError, TypeError, ValueError) as exc:
            raise QueryError(f"aggregation failed: {exc}") from exc
        return QueryResult.from_records(records[:max_rows], truncated=len(records) > max_rows)


class MongoDocStore:  # pragma: no cover - needs a MongoDB server
    """MongoDB access. Connect with a user that only has the ``read`` role on the database;
    every aggregation runs with ``maxTimeMS`` and without disk use."""

    def __init__(self, uri: str, database: str, collection: str, timeout_s: float = 5.0):
        import pymongo

        self._client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=int(timeout_s * 1000),
                                           appname="policypilot")
        self._collection = self._client[database][collection]
        self.timeout_ms = int(timeout_s * 1000)

    def aggregate(self, pipeline: list, max_rows: int) -> QueryResult:
        from pymongo.errors import PyMongoError

        try:
            cursor = self._collection.aggregate(pipeline, maxTimeMS=self.timeout_ms, allowDiskUse=False)
            records = []
            for doc in cursor:
                records.append(doc)
                if len(records) > max_rows:
                    break
        except PyMongoError as exc:
            raise QueryError(f"MongoDB error: {exc}") from exc
        return QueryResult.from_records(records[:max_rows], truncated=len(records) > max_rows)

    def close(self) -> None:
        self._client.close()
