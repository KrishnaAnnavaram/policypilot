"""Evaluation harness: computed ground truth, execution accuracy, routing and retrieval metrics."""
from .gold import GoldItem, GoldSetError, load_gold
from .metrics import results_match, routing_report
from .runner import evaluate, format_report

__all__ = ["GoldItem", "GoldSetError", "load_gold", "results_match", "routing_report", "evaluate", "format_report"]
