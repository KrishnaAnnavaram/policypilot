"""A deterministic, rule-based stand-in for an LLM so the whole pipeline runs offline.

It understands a small, documented vocabulary (counts and averages with simple
demographic, vehicle and claim filters). It exists for the demo, smoke tests and CI;
its output still goes through the same extraction, validation and read-only
execution as a real model's output. It is not meant to be clever.
"""
from __future__ import annotations

import json
import re

from ..agents.router import KeywordRouter

_TASK = re.compile(r"^TASK:\s*(\w+)", re.MULTILINE)
_QUESTION = re.compile(r"<question>(.*?)</question>", re.DOTALL)
_RESULT = re.compile(r"<result>(.*?)</result>", re.DOTALL)
_CONTEXT = re.compile(r'<context id="(\d+)"[^>]*>(.*?)</context>', re.DOTALL)
_EXTRA = re.compile(r"<instructions>(.*?)</instructions>", re.DOTALL)


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


# ---------------------------------------------------------------- SQL vocabulary
_CUSTOMER_FILTERS: list[tuple[str, str]] = [
    (r"\bsingle[- ]parents?\b", "single_parent = 1"),
    (r"\b(unmarried|not married|single)\b", "married = 0"),
    (r"\bmarried\b", "married = 1"),
    (r"\b(female|women|woman)\b", "gender = 'F'"),
    (r"\b(male|men|man)\b", "gender = 'M'"),
    (r"\bphds?\b", "education = 'PhD'"),
    (r"\bmasters?\b", "education = 'Masters'"),
    (r"\bbachelors?\b", "education = 'Bachelors'"),
    (r"\bhigh school\b", "education = 'High School'"),
    (r"\bdoctors?\b", "occupation = 'Doctor'"),
    (r"\blawyers?\b", "occupation = 'Lawyer'"),
    (r"\bmanagers?\b", "occupation = 'Manager'"),
    (r"\bstudents?\b", "occupation = 'Student'"),
    (r"\bclerical\b", "occupation = 'Clerical'"),
    (r"\bblue[- ]collar\b", "occupation = 'Blue Collar'"),
    (r"\bprofessionals?\b", "occupation = 'Professional'"),
    (r"\bhome ?makers?\b", "occupation = 'Home Maker'"),
    (r"\b(homeowners?|own a home|own their home)\b", "home_value > 0"),
    (r"\b(with|have) (kids|children)\b", "kids_at_home > 0"),
]
_CUSTOMER_METRICS = [
    (r"home value", "home_value"), (r"income|earn", "income"), (r"commut|travel time", "commute_minutes"),
    (r"years on (the )?job|job tenure", "years_on_job"), (r"\bage\b|how old", "age"),
]
_CUSTOMER_DIMENSIONS = [("gender", "gender"), ("education", "education"), ("occupation", "occupation")]

# ---------------------------------------------------------------- document vocabulary
_DOC_FILTERS: list[tuple[str, tuple[str, object]]] = [
    (r"\bcommercial\b", ("car_use", "Commercial")),
    (r"\bprivate\b", ("car_use", "Private")),
    (r"\bminivans?\b", ("car.type", "Minivan")),
    (r"\bpanel trucks?\b", ("car.type", "Panel Truck")),
    (r"\bpickups?\b", ("car.type", "Pickup")),
    (r"\bsports cars?\b", ("car.type", "Sports Car")),
    (r"\bsuvs?\b", ("car.type", "SUV")),
    (r"(?<!mini)\bvans?\b", ("car.type", "Van")),
    (r"\bred\b", ("car.red", True)),
    (r"\burban\b", ("urbanicity", "Urban")),
    (r"\brural\b", ("urbanicity", "Rural")),
    (r"\brevoked\b", ("claims.license_revoked", True)),
    (r"\b(crash(ed)?|had a claim|with a claim|filed a claim|claim flag)\b", ("claims.flag", True)),
]
_DOC_METRICS = [
    (r"past claims? (total|payout)|old claim", "claims.past_total"),
    (r"claim amount|claim payout|payout", "claims.amount"),
    (r"blue ?book|car value|resale", "bluebook_value"), (r"car age|age of (the )?car|old(er)? cars?", "car.age"),
    (r"mvr|points", "claims.mvr_points"), (r"years insured|time in force|tenure", "years_insured"),
    (r"claims in the (past|last) (five|5) years|claim frequency", "claims.last_5y"),
]
_DOC_DIMENSIONS = [(r"car type|type of car|body type", "car.type"), (r"car use|usage", "car_use"),
                   (r"urbanicity|urban or rural|area", "urbanicity")]


def _aggregate(question: str) -> str:
    if _has(question, r"\b(average|mean)\b"):
        return "avg"
    if _has(question, r"\b(total|sum)\b"):
        return "sum"
    if _has(question, r"\b(maximum|highest|largest|max)\b"):
        return "max"
    if _has(question, r"\b(minimum|lowest|smallest|min)\b"):
        return "min"
    return "count"


def _first(question: str, table: list[tuple[str, str]]) -> str | None:
    for pattern, value in table:
        if _has(question, pattern):
            return value
    return None


def write_sql(question: str, select_ids: bool = False) -> str:
    q = question.lower()
    conditions: list[str] = []
    consumed = q
    for pattern, cond in _CUSTOMER_FILTERS:
        if re.search(pattern, consumed):
            column = cond.split()[0]
            if not any(c.split()[0] == column for c in conditions):
                conditions.append(cond)
            consumed = re.sub(pattern, " ", consumed)
    m = re.search(r"\b(older than|over|above)\s+(\d{1,3})\b", q)
    if m:
        conditions.append(f"age > {int(m.group(2))}")
    m = re.search(r"\b(younger than|under|below)\s+(\d{1,3})\b", q)
    if m:
        conditions.append(f"age < {int(m.group(2))}")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    if select_ids:
        return f"SELECT customer_id FROM customers{where}"
    agg = _aggregate(q)
    metric = _first(q, _CUSTOMER_METRICS)
    dimension = next((col for word, col in _CUSTOMER_DIMENSIONS if re.search(rf"\b(by|per) {word}\b", q)), None)
    value = "COUNT(*)" if agg == "count" or metric is None else f"{agg.upper()}({metric})"
    if agg == "avg" and metric:
        value = f"ROUND(AVG({metric}), 2)"
    if dimension:
        return f"SELECT {dimension}, {value} AS value FROM customers{where} GROUP BY {dimension} ORDER BY {dimension}"
    return f"SELECT {value} AS value FROM customers{where}"


def write_pipeline(question: str) -> list:
    q = question.lower()
    match: dict = {}
    for pattern, (path, value) in _DOC_FILTERS:
        if re.search(pattern, q) and path not in match:
            match[path] = value
    pipeline: list = [{"$match": match}] if match else []
    agg = _aggregate(q)
    metric = _first(q, _DOC_METRICS)
    dimension = next((path for pattern, path in _DOC_DIMENSIONS if re.search(rf"\b(by|per) ({pattern})", q)), None)
    if agg == "count" or metric is None:
        if dimension:
            pipeline += [{"$group": {"_id": f"${dimension}", "value": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
        else:
            pipeline.append({"$count": "value"})
        return pipeline
    group_id = f"${dimension}" if dimension else None
    pipeline.append({"$group": {"_id": group_id, "value": {f"${agg}": f"${metric}"}}})
    if agg == "avg":
        pipeline.append({"$project": {"_id": 1, "value": {"$round": ["$value", 2]}}})
    if dimension:
        pipeline.append({"$sort": {"_id": 1}})
    else:
        pipeline.append({"$project": {"_id": 0, "value": 1}})
    return pipeline


def _format_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def summarize(question: str, records: list[dict]) -> str:
    if not records:
        return "No matching records were found."
    if len(records) == 1 and len(records[0]) == 1:
        return f"The answer is {_format_value(next(iter(records[0].values())))}."
    if len(records) == 1:
        parts = ", ".join(f"{k} = {_format_value(v)}" for k, v in records[0].items())
        return f"Result: {parts}."
    return f"Found {len(records)} rows; see the table below."


class OfflineLLM:
    """Implements the :class:`~policypilot.llm.base.LLM` protocol without any network access."""

    name = "offline-rules"

    def __init__(self) -> None:
        self._router = KeywordRouter()

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        task_match = _TASK.search(system)
        task = task_match.group(1) if task_match else ""
        q_match = _QUESTION.search(user)
        question = q_match.group(1).strip() if q_match else user.strip()
        if task == "route":
            d = self._router.route(question)
            return json.dumps({"route": d.route, "confidence": d.confidence, "reason": d.reason})
        if task == "sql":
            extra = _EXTRA.search(user)
            select_ids = bool(extra and "customer_id" in extra.group(1))
            return "```sql\n" + write_sql(question, select_ids=select_ids) + "\n```"
        if task == "pipeline":
            return "```json\n" + json.dumps(write_pipeline(question)) + "\n```"
        if task == "plan":
            return json.dumps({"customer_question": question, "claims_question": question})
        if task == "answer":
            r = _RESULT.search(user)
            records = json.loads(r.group(1)) if r else []
            return summarize(question, records)
        if task == "rag":
            contexts = _CONTEXT.findall(user)
            if not contexts:
                return "The policy documents do not cover this question."
            cid, text = contexts[0]
            sentences = re.split(r"(?<=[.!?。！？])\s*", text.strip())
            snippet = " ".join(s for s in sentences[:2] if s).strip()
            return f"According to the policy documents: {snippet} [{cid}]"
        if task == "condense":
            return question
        return ""
