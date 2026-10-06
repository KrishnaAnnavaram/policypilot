"""Evaluation metrics: execution accuracy, routing precision/recall/F1, retrieval recall@k and MRR.

Data answers are scored by comparing the *result set* the agent's query produced with
the result set of a gold query run on the same data - not by comparing wording.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Sequence

REL_TOL = 1e-4
ABS_TOL = 0.0051          # answers rounded to two decimals still count as correct


def normalize_value(value: Any) -> Any:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return float(stripped.replace(",", ""))
        except ValueError:
            return stripped.lower()
    return value


def values_close(a: Any, b: Any) -> bool:
    a, b = normalize_value(a), normalize_value(b)
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return a == b


def _sort_key(value: Any) -> tuple:
    v = normalize_value(value)
    if v is None:
        return (0, 0.0, "")
    if isinstance(v, float):
        return (1, round(v, 4), "")
    return (2, 0.0, str(v))


def _canonical_row(row: Sequence) -> list:
    return sorted(row, key=_sort_key)


def results_match(pred_rows: Iterable[Sequence], gold_rows: Iterable[Sequence], ordered: bool = False) -> bool:
    """Compare two result sets ignoring column names and column order, with numeric tolerance.

    Rows are compared as a multiset unless ``ordered``. If the gold result is a single
    value, a single predicted row that contains that value also counts as a match
    (agents often return the value with an extra label column).
    """
    pred = [list(r) for r in pred_rows]
    gold = [list(r) for r in gold_rows]
    if len(gold) == 1 and len(gold[0]) == 1 and len(pred) == 1:
        return any(values_close(v, gold[0][0]) for v in pred[0])
    if len(pred) != len(gold):
        return False
    pred_c = [_canonical_row(r) for r in pred]
    gold_c = [_canonical_row(r) for r in gold]
    if not ordered:
        pred_c.sort(key=lambda r: [_sort_key(v) for v in r])
        gold_c.sort(key=lambda r: [_sort_key(v) for v in r])
    for p, g in zip(pred_c, gold_c):
        if len(p) != len(g) or not all(values_close(a, b) for a, b in zip(p, g)):
            return False
    return True


def routing_report(gold: Sequence[str], predicted: Sequence[str], labels: Sequence[str]) -> dict:
    if len(gold) != len(predicted):
        raise ValueError("gold and predicted routes must be aligned")
    per_label = {}
    for label in labels:
        tp = sum(1 for g, p in zip(gold, predicted) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold, predicted) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold, predicted) if g == label and p != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    present = [lab for lab in labels if per_label[lab]["support"]]
    accuracy = sum(g == p for g, p in zip(gold, predicted)) / len(gold) if gold else 0.0
    return {
        "accuracy": accuracy,
        "macro_f1": sum(per_label[lab]["f1"] for lab in present) / len(present) if present else 0.0,
        "per_route": per_label,
        "confusion": {f"{g}->{p}": n for (g, p), n in sorted(Counter(zip(gold, predicted)).items())},
    }


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    rel = set(relevant)
    if not rel:
        return 0.0
    return len(rel & set(retrieved[:k])) / len(rel)


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str]) -> float:
    rel = set(relevant)
    for i, item in enumerate(retrieved, start=1):
        if item in rel:
            return 1.0 / i
    return 0.0
