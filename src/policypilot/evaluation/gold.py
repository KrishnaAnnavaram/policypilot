"""Gold questions whose ground truth is COMPUTED from the data.

Each data item stores a gold SQL query or gold pipeline, never a hand-written answer.
The expected result is obtained by running that query (through the same validators
and read-only executors as the agents) on the database being evaluated, so the ground
truth can never drift away from the data. Loading fails if a gold query is invalid -
for example if it compares a column with a value that does not exist in the data model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from ..agents.router import ROUTES
from ..backends.base import DocumentStore, QueryResult, SQLExecutor
from ..safety.mongo_guard import PipelinePolicy, validate_pipeline
from ..safety.sql_guard import SQLPolicy, validate_sql


class GoldSetError(ValueError):
    pass


@dataclass(frozen=True)
class GoldItem:
    id: str
    question: str
    route: str
    gold_sql: str = ""
    gold_pipeline: list | None = None
    relevant_sources: tuple[str, ...] = ()
    ordered: bool = False
    query_lang: str = "en"
    doc_lang: str = "en"
    meta: dict = field(default_factory=dict)

    @property
    def is_data(self) -> bool:
        return self.route in {"sql", "nosql", "both"}


def parse_items(raw: list[dict]) -> list[GoldItem]:
    items: list[GoldItem] = []
    seen: set[str] = set()
    for entry in raw:
        item = GoldItem(
            id=str(entry["id"]), question=str(entry["question"]), route=str(entry["route"]),
            gold_sql=entry.get("gold_sql", ""), gold_pipeline=entry.get("gold_pipeline"),
            relevant_sources=tuple(entry.get("relevant_sources", ())), ordered=bool(entry.get("ordered", False)),
            query_lang=entry.get("query_lang", "en"), doc_lang=entry.get("doc_lang", "en"),
        )
        if item.id in seen:
            raise GoldSetError(f"duplicate gold id {item.id!r}")
        seen.add(item.id)
        if item.route not in ROUTES:
            raise GoldSetError(f"{item.id}: unknown route {item.route!r}")
        if item.is_data and not (item.gold_sql or item.gold_pipeline):
            raise GoldSetError(f"{item.id}: data questions need gold_sql or gold_pipeline")
        if item.route == "nosql" and not item.gold_pipeline and not item.gold_sql:
            raise GoldSetError(f"{item.id}: nosql questions need a gold query")
        if item.route == "pdf" and not item.relevant_sources:
            raise GoldSetError(f"{item.id}: pdf questions need relevant_sources")
        items.append(item)
    return items


def load_gold(path: str | Path | None = None) -> list[GoldItem]:
    if path is None:
        text = (resources.files("policypilot") / "evaluation" / "gold_questions.json").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return parse_items(json.loads(text))


def check_gold_queries(items: list[GoldItem], sql_policy: SQLPolicy, pipeline_policy: PipelinePolicy) -> None:
    """Run every gold query through the validators; raises GoldSetError on the first problem."""
    for item in items:
        try:
            if item.gold_sql:
                validate_sql(item.gold_sql, sql_policy)
            if item.gold_pipeline:
                validate_pipeline(item.gold_pipeline, pipeline_policy)
        except ValueError as exc:
            raise GoldSetError(f"{item.id}: invalid gold query: {exc}") from exc


def ground_truth(item: GoldItem, executor: SQLExecutor, store: DocumentStore,
                 sql_policy: SQLPolicy, pipeline_policy: PipelinePolicy) -> QueryResult:
    if item.gold_sql:
        validated = validate_sql(item.gold_sql, sql_policy)
        return executor.execute(validated.sql, sql_policy.max_rows)
    if item.gold_pipeline:
        validated_p = validate_pipeline(item.gold_pipeline, pipeline_policy)
        return store.aggregate(validated_p.pipeline, pipeline_policy.max_rows)
    raise GoldSetError(f"{item.id}: no gold query")
