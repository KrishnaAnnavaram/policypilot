"""Question routing: an LLM router with a validated JSON contract and a keyword fallback."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..llm.base import LLM, LLMError
from ..safety.extract import ExtractionError, extract_json_object
from . import prompts

ROUTES = ("sql", "nosql", "both", "pdf")


@dataclass(frozen=True)
class RouteDecision:
    route: str
    confidence: float
    reason: str
    source: str            # "llm", "keywords" or "keywords-fallback"


def _terms(*words: str) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(words) + r")", re.IGNORECASE)


_CUSTOMER = _terms(
    "customer", "policy ?holder", "income", "earn", "age[ds]?\\b", "older", "younger", "gender", "female", "male",
    "wom[ae]n", "\\bm[ae]n\\b", "married", "single", "parent", "kids?\\b", "child", "education", "phd", "master",
    "bachelor", "high school", "occupation", "job", "doctor", "lawyer", "manager", "student", "clerical",
    "blue collar", "home ?maker", "professional", "home value", "homeowner", "own a home", "commut", "travel time",
    "born", "birth", "demographic",
)
_CLAIMS = _terms(
    "vehicle", "cars?\\b", "suvs?\\b", "\\bvans?\\b", "minivan", "pickup", "sports car", "panel truck",
    "commercial", "private use", "blue ?book", "claim", "crash", "accident", "mvr", "points", "revok",
    "licen[cs]e", "urban", "rural", "\\bred\\b", "years insured", "time in force",
)
_POLICY = _terms(
    "policy", "policies", "cover", "deductible", "premium", "exclusion", "excluded", "grace period", "cancel",
    "reinstat", "renew", "waiting period", "terms", "contract", "faq", "what happens", "can i", "how do i",
    "am i", "should i", "duplicate insurance", "notify", "report a", "file a claim", "documents? (do|are)",
)
_DATA_INTENT = _terms(
    "how many", "number of", "count", "average", "mean", "median", "total", "sum", "percent", "share",
    "proportion", "max", "min", "highest", "lowest", "most", "least", "top", "list", "which customers",
    "distribution", "breakdown", "by gender", "per ",
)


class KeywordRouter:
    """Transparent rule-based router; used offline and whenever the LLM answer is unusable."""

    def route(self, question: str) -> RouteDecision:
        cust = len(_CUSTOMER.findall(question))
        claims = len(_CLAIMS.findall(question))
        policy = len(_POLICY.findall(question))
        data_intent = bool(_DATA_INTENT.search(question))
        if policy and not data_intent:
            return RouteDecision("pdf", 0.7, "policy wording without a data request", "keywords")
        if cust and claims:
            # "customer" alone is a generic word; demand a real demographic term for "both"
            demographic = len(_CUSTOMER.findall(re.sub(r"(?i)\bcustomers?\b", "", question)))
            if demographic:
                return RouteDecision("both", 0.6, "mentions demographics and vehicles/claims", "keywords")
            return RouteDecision("nosql", 0.6, "vehicle/claim question about customers", "keywords")
        if claims:
            return RouteDecision("nosql", 0.6, "vehicle/claim terms", "keywords")
        if cust:
            return RouteDecision("sql", 0.6, "demographic terms", "keywords")
        return RouteDecision("pdf", 0.3, "no data terms found", "keywords")


class LLMRouter:
    def __init__(self, llm: LLM, fallback: KeywordRouter | None = None, min_confidence: float = 0.5):
        self.llm = llm
        self.fallback = fallback or KeywordRouter()
        self.min_confidence = min_confidence

    def route(self, question: str) -> RouteDecision:
        try:
            raw = self.llm.complete(prompts.ROUTER, prompts.wrap_question(question), json_mode=True)
            obj = extract_json_object(raw)
            route = str(obj.get("route", "")).strip().lower()
            confidence = float(obj.get("confidence", 0.0))
        except (LLMError, ExtractionError, TypeError, ValueError) as exc:
            decision = self.fallback.route(question)
            return RouteDecision(decision.route, decision.confidence, f"router error: {exc}", "keywords-fallback")
        if route not in ROUTES:
            decision = self.fallback.route(question)
            return RouteDecision(decision.route, decision.confidence, f"invalid route {route!r}", "keywords-fallback")
        confidence = min(max(confidence, 0.0), 1.0)
        if confidence < self.min_confidence:
            decision = self.fallback.route(question)
            if decision.route != route:
                return RouteDecision(decision.route, decision.confidence,
                                     f"low LLM confidence ({confidence:.2f}) for {route!r}", "keywords-fallback")
        return RouteDecision(route, confidence, str(obj.get("reason", ""))[:200], "llm")
