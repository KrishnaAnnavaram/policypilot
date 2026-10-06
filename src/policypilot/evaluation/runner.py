"""Evaluate a :class:`~policypilot.service.QAService` end to end on the gold set.

The harness takes the service built by :func:`policypilot.service.build_service` and
reuses its own executors, document store, policies and index, so the scores describe
the system that ships.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..agents.router import ROUTES
from ..backends.base import QueryError
from .gold import GoldItem, check_gold_queries, ground_truth
from .metrics import reciprocal_rank, recall_at_k, results_match, routing_report


@dataclass
class ItemResult:
    id: str
    route: str
    predicted_route: str
    question: str
    ok: bool = False
    correct: bool | None = None
    answer: str = ""
    query: str = ""
    gold_rows: list = field(default_factory=list)
    pred_rows: list = field(default_factory=list)
    retrieved: list = field(default_factory=list)
    recall_at_k: float | None = None
    mrr: float | None = None
    lang_pair: str = ""
    error: str = ""
    seconds: float = 0.0


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def evaluate(service, items: list[GoldItem], k: int | None = None) -> dict:
    k = k or service.rag_agent.top_k
    sql_agent, mongo_agent = service.sql_agent, service.mongo_agent
    check_gold_queries([i for i in items if i.is_data], sql_agent.policy, mongo_agent.policy)
    results: list[ItemResult] = []
    for item in items:
        started = time.perf_counter()
        decision = service.router.route(item.question)
        res = ItemResult(item.id, item.route, decision.route, item.question)
        if item.is_data:
            try:
                gold = ground_truth(item, sql_agent.executor, mongo_agent.store, sql_agent.policy, mongo_agent.policy)
                res.gold_rows = [list(r) for r in gold.rows]
            except (QueryError, ValueError) as exc:
                res.error = f"gold query failed: {exc}"
                results.append(res)
                continue
            answer = service.answer(item.question, decision.route)
            res.ok, res.answer, res.query, res.error = answer.ok, answer.text, answer.query, answer.error
            if answer.ok and answer.result is not None:
                res.pred_rows = [list(r) for r in answer.result.rows]
                res.correct = results_match(res.pred_rows, res.gold_rows, ordered=item.ordered)
            else:
                res.correct = False
        else:
            hits = service.rag_agent.retrieve(item.question)
            res.retrieved = [h.chunk.source for h in hits]
            res.recall_at_k = recall_at_k(res.retrieved, item.relevant_sources, k)
            res.mrr = reciprocal_rank(res.retrieved, item.relevant_sources)
            res.lang_pair = f"{item.query_lang}->{item.doc_lang}"
            if decision.route == "pdf":
                answer = service.answer(item.question, "pdf")
                res.ok, res.answer = answer.ok, answer.text
        res.seconds = round(time.perf_counter() - started, 3)
        results.append(res)
    return summarize(results, k)


def summarize(results: list[ItemResult], k: int) -> dict:
    routing = routing_report([r.route for r in results], [r.predicted_route for r in results], ROUTES)
    data = [r for r in results if r.correct is not None]
    by_route: dict[str, list[bool]] = defaultdict(list)
    for r in data:
        by_route[r.route].append(bool(r.correct))
    routed_ok = [bool(r.correct) for r in data if r.predicted_route == r.route]
    retrieval = [r for r in results if r.recall_at_k is not None]
    by_pair: dict[str, list[ItemResult]] = defaultdict(list)
    for r in retrieval:
        by_pair[r.lang_pair].append(r)
    return {
        "n_items": len(results),
        "routing": routing,
        "execution_accuracy": _mean([float(c) for c in (r.correct for r in data)]),
        "execution_accuracy_by_route": {route: _mean([float(c) for c in v]) for route, v in sorted(by_route.items())},
        "execution_accuracy_when_routed_correctly": _mean([float(c) for c in routed_ok]),
        "gold_errors": [r.id for r in results if r.error.startswith("gold query failed")],
        "retrieval": {
            f"recall@{k}": _mean([r.recall_at_k for r in retrieval]),
            "mrr": _mean([r.mrr for r in retrieval]),
            "by_language_pair": {
                pair: {f"recall@{k}": _mean([r.recall_at_k for r in rs]), "mrr": _mean([r.mrr for r in rs]),
                       "n": len(rs)}
                for pair, rs in sorted(by_pair.items())
            },
        },
        "items": [asdict(r) for r in results],
    }


def format_report(report: dict) -> str:
    def pct(v):
        return "n/a" if v is None else f"{100 * v:.1f}%"

    lines = [
        f"items: {report['n_items']}",
        f"routing accuracy: {pct(report['routing']['accuracy'])}  macro-F1: {report['routing']['macro_f1']:.3f}",
        f"execution accuracy (end to end): {pct(report['execution_accuracy'])}",
        f"execution accuracy when routed correctly: {pct(report['execution_accuracy_when_routed_correctly'])}",
    ]
    for route, acc in report["execution_accuracy_by_route"].items():
        lines.append(f"  {route:<6} {pct(acc)}")
    ret = report["retrieval"]
    recall_key = next(key for key in ret if key.startswith("recall@"))
    mrr = "n/a" if ret["mrr"] is None else f"{ret['mrr']:.3f}"
    lines.append(f"retrieval {recall_key}: {pct(ret[recall_key])}  MRR: {mrr}")
    for pair, m in ret["by_language_pair"].items():
        lines.append(f"  {pair:<7} {recall_key} {pct(m[recall_key])}  MRR {m['mrr']:.3f}  (n={m['n']})")
    if report["gold_errors"]:
        lines.append(f"gold query errors: {', '.join(report['gold_errors'])}")
    return "\n".join(lines)


def write_report(report: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path
