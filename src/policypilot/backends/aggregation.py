"""A small in-memory evaluator for the allow-listed subset of MongoDB aggregation.

It powers the offline demo and the tests (no MongoDB server needed). It implements
every stage and operator accepted by :mod:`policypilot.safety.mongo_guard` and follows
MongoDB semantics where they matter for answers: strict bool/number equality,
type-bracketed query comparisons, missing-vs-null, ``$avg`` ignoring non-numbers, and
no ``$count`` output for an empty input.
"""
from __future__ import annotations

import copy
import json
import math
from typing import Any, Callable

_MISSING = object()


class AggregationError(ValueError):
    pass


# ------------------------------------------------------------------ helpers
def get_path(doc: Any, path: str) -> Any:
    current = doc
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list):
            values = [get_path(item, part) for item in current if isinstance(item, dict)]
            current = [v for v in values if v is not _MISSING]
        else:
            return _MISSING
    return current


def set_path(doc: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type_rank(v: Any) -> int:
    if v is None or v is _MISSING:
        return 1
    if _is_number(v):
        return 2
    if isinstance(v, str):
        return 3
    if isinstance(v, dict):
        return 4
    if isinstance(v, list):
        return 5
    if isinstance(v, bool):
        return 8
    return 9


def sort_key(v: Any) -> tuple:
    rank = _type_rank(v)
    if rank == 2:
        return (rank, float(v))
    if rank in (3, 8):
        return (rank, v)
    if rank in (4, 5):
        return (rank, json.dumps(v, sort_keys=True, default=str))
    return (rank, 0)


def values_equal(a: Any, b: Any) -> bool:
    if a is _MISSING:
        a = None
    if b is _MISSING:
        b = None
    if _is_number(a) and _is_number(b):
        return float(a) == float(b)
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return type(a) is type(b) and a == b


def _compare(a: Any, b: Any) -> int:
    ka, kb = sort_key(a), sort_key(b)
    return (ka > kb) - (ka < kb)


def _truthy(v: Any) -> bool:
    if v is None or v is _MISSING or v is False:
        return False
    if _is_number(v):
        return v != 0
    return True


# ------------------------------------------------------------------ expressions
def _num_args(args: list, op: str) -> list | None:
    if any(a is None or a is _MISSING for a in args):
        return None
    if not all(_is_number(a) for a in args):
        raise AggregationError(f"{op} only supports numeric arguments")
    return args


def _round(args: Any) -> Any:
    value, places = (args + [0])[:2] if isinstance(args, list) else (args, 0)
    if value is None or value is _MISSING:
        return None
    if not _is_number(value):
        raise AggregationError("$round needs a number")
    # MongoDB rounds half to even, like Python's round()
    result = round(float(value), int(places))
    return int(result) if int(places) <= 0 and isinstance(value, int) else result


def _convert(value: Any, kind: Callable) -> Any:
    if value is None or value is _MISSING:
        return None
    try:
        if kind is bool:
            return _truthy(value)
        return kind(float(value)) if kind is int else kind(value)
    except (TypeError, ValueError) as exc:
        raise AggregationError(f"cannot convert {value!r}") from exc


def evaluate(expr: Any, doc: dict) -> Any:
    if isinstance(expr, str):
        if expr.startswith("$"):
            value = get_path(doc, expr[1:])
            return None if value is _MISSING else value
        return expr
    if isinstance(expr, list):
        return [evaluate(e, doc) for e in expr]
    if not isinstance(expr, dict):
        return expr
    if len(expr) == 1 and next(iter(expr)).startswith("$"):
        op, raw = next(iter(expr.items()))
        return _apply_operator(op, raw, doc)
    return {k: evaluate(v, doc) for k, v in expr.items()}


def _apply_operator(op: str, raw: Any, doc: dict) -> Any:
    if op == "$cond":
        if isinstance(raw, dict):
            cond, then, other = raw.get("if"), raw.get("then"), raw.get("else")
        else:
            cond, then, other = raw
        return evaluate(then, doc) if _truthy(evaluate(cond, doc)) else evaluate(other, doc)
    if op == "$ifNull":
        for item in raw[:-1]:
            value = evaluate(item, doc)
            if value is not None:
                return value
        return evaluate(raw[-1], doc)
    if op in {"$and", "$or"}:
        values = [_truthy(evaluate(e, doc)) for e in raw]
        return all(values) if op == "$and" else any(values)
    if op == "$not":
        arg = raw[0] if isinstance(raw, list) else raw
        return not _truthy(evaluate(arg, doc))
    args = evaluate(raw, doc)
    if op == "$round":
        return _round(args)
    if op in {"$toInt", "$toLong"}:
        return _convert(args[0] if isinstance(args, list) else args, int)
    if op == "$toDouble":
        return _convert(args[0] if isinstance(args, list) else args, float)
    if op == "$toBool":
        return _convert(args[0] if isinstance(args, list) else args, bool)
    if op in {"$abs", "$floor", "$ceil"}:
        value = args[0] if isinstance(args, list) else args
        if value is None:
            return None
        if not _is_number(value):
            raise AggregationError(f"{op} needs a number")
        return {"$abs": abs, "$floor": math.floor, "$ceil": math.ceil}[op](value)
    if op in {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte"}:
        a, b = args
        if op == "$eq":
            return values_equal(a, b)
        if op == "$ne":
            return not values_equal(a, b)
        c = _compare(a, b)
        return {"$gt": c > 0, "$gte": c >= 0, "$lt": c < 0, "$lte": c <= 0}[op]
    if op == "$in":
        value, array = args
        if not isinstance(array, list):
            raise AggregationError("$in needs an array as its second argument")
        return any(values_equal(value, item) for item in array)
    if op in {"$add", "$multiply"}:
        nums = _num_args(list(args), op)
        if nums is None:
            return None
        return sum(nums) if op == "$add" else math.prod(nums)
    if op in {"$subtract", "$divide", "$mod"}:
        nums = _num_args(list(args), op)
        if nums is None:
            return None
        a, b = nums
        if op == "$subtract":
            return a - b
        if b == 0:
            raise AggregationError(f"{op} by zero")
        return a / b if op == "$divide" else math.fmod(a, b)
    raise AggregationError(f"unsupported operator {op}")


# ------------------------------------------------------------------ $match
def _field_matches(value: Any, cond: Any) -> bool:
    if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
        return all(_operator_matches(value, op, arg) for op, arg in cond.items())
    return _eq_match(value, cond)


def _eq_match(value: Any, target: Any) -> bool:
    if isinstance(value, list) and not isinstance(target, list):
        return any(values_equal(v, target) for v in value)
    if target is None:
        return value is None or value is _MISSING
    return values_equal(value, target)


def _operator_matches(value: Any, op: str, arg: Any) -> bool:
    if op == "$exists":
        return (value is not _MISSING) == bool(arg)
    if op == "$eq":
        return _eq_match(value, arg)
    if op == "$ne":
        return not _eq_match(value, arg)
    if op == "$in":
        return any(_eq_match(value, a) for a in arg)
    if op == "$nin":
        return not any(_eq_match(value, a) for a in arg)
    if op == "$not":
        return not _field_matches(value, arg)
    if op in {"$gt", "$gte", "$lt", "$lte"}:
        candidates = value if isinstance(value, list) else [value]
        for v in candidates:
            if v is _MISSING or _type_rank(v) != _type_rank(arg):
                continue      # query comparisons only match values of the same type bracket
            c = _compare(v, arg)
            if {"$gt": c > 0, "$gte": c >= 0, "$lt": c < 0, "$lte": c <= 0}[op]:
                return True
        return False
    raise AggregationError(f"unsupported query operator {op}")


def matches(doc: dict, query: dict) -> bool:
    for key, cond in query.items():
        if key == "$and":
            ok = all(matches(doc, q) for q in cond)
        elif key == "$or":
            ok = any(matches(doc, q) for q in cond)
        elif key == "$nor":
            ok = not any(matches(doc, q) for q in cond)
        elif key == "$expr":
            ok = _truthy(evaluate(cond, doc))
        else:
            ok = _field_matches(get_path(doc, key), cond)
        if not ok:
            return False
    return True


# ------------------------------------------------------------------ stages
def _freeze(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _group(docs: list[dict], spec: dict) -> list[dict]:
    groups: dict[str, dict] = {}
    order: list[str] = []
    buckets: dict[str, list[dict]] = {}
    for doc in docs:
        key_value = evaluate(spec["_id"], doc)
        key = _freeze(key_value)
        if key not in groups:
            groups[key] = {"_id": key_value}
            buckets[key] = []
            order.append(key)
        buckets[key].append(doc)
    out = []
    for key in order:
        result = groups[key]
        members = buckets[key]
        for name, acc in spec.items():
            if name == "_id":
                continue
            op, arg = next(iter(acc.items()))
            result[name] = _accumulate(op, arg, members)
        out.append(result)
    return out


def _accumulate(op: str, arg: Any, members: list[dict]) -> Any:
    if op == "$count":
        return len(members)
    values = [evaluate(arg, m) for m in members]
    if op == "$sum":
        return sum(v for v in values if _is_number(v))
    if op == "$avg":
        nums = [v for v in values if _is_number(v)]
        return sum(nums) / len(nums) if nums else None
    present = [v for v in values if v is not None]
    if op == "$min":
        return min(present, key=sort_key) if present else None
    if op == "$max":
        return max(present, key=sort_key) if present else None
    if op == "$first":
        return values[0] if values else None
    if op == "$last":
        return values[-1] if values else None
    if op == "$push":
        return values
    if op == "$addToSet":
        seen: dict[str, Any] = {}
        for v in values:
            seen.setdefault(_freeze(v), v)
        return list(seen.values())
    raise AggregationError(f"unsupported accumulator {op}")


def _project(doc: dict, spec: dict) -> dict:
    inclusion = {k for k, v in spec.items() if v in (1, True) and not isinstance(v, float)}
    exclusion = {k for k, v in spec.items() if v in (0, False) and not isinstance(v, float)}
    computed = {k: v for k, v in spec.items() if k not in inclusion and k not in exclusion}
    if not inclusion and not computed:
        out = copy.deepcopy(doc)
        for path in exclusion:
            parts = path.split(".")
            target = out
            for part in parts[:-1]:
                target = target.get(part, {}) if isinstance(target, dict) else {}
            if isinstance(target, dict):
                target.pop(parts[-1], None)
        return out
    out: dict = {}
    if "_id" not in exclusion and "_id" in doc and "_id" not in computed:
        out["_id"] = doc["_id"]
    for path in inclusion:
        value = get_path(doc, path)
        if value is not _MISSING:
            set_path(out, path, copy.deepcopy(value))
    for path, expr in computed.items():
        set_path(out, path, evaluate(expr, doc))
    return out


def _sort(docs: list[dict], spec: dict) -> list[dict]:
    result = list(docs)
    for path, direction in reversed(list(spec.items())):
        result.sort(key=lambda d: sort_key(get_path(d, path)), reverse=direction == -1)
    return result


def _unwind(docs: list[dict], spec: Any) -> list[dict]:
    path = (spec["path"] if isinstance(spec, dict) else spec)[1:]
    keep = isinstance(spec, dict) and spec.get("preserveNullAndEmptyArrays", False)
    out = []
    for doc in docs:
        value = get_path(doc, path)
        if isinstance(value, list) and value:
            for item in value:
                new = copy.deepcopy(doc)
                set_path(new, path, item)
                out.append(new)
        elif isinstance(value, list) or value is None or value is _MISSING:
            if keep:
                new = copy.deepcopy(doc)
                if isinstance(value, list):
                    parts = path.split(".")
                    target = new
                    for part in parts[:-1]:
                        target = target[part]
                    target.pop(parts[-1], None)
                out.append(new)
        else:
            out.append(copy.deepcopy(doc))
    return out


def run_pipeline(docs: list[dict], pipeline: list) -> list[dict]:
    current = [copy.deepcopy(d) for d in docs]
    for stage in pipeline:
        name, spec = next(iter(stage.items()))
        if name == "$match":
            current = [d for d in current if matches(d, spec)]
        elif name == "$group":
            current = _group(current, spec)
        elif name == "$project":
            current = [_project(d, spec) for d in current]
        elif name in {"$addFields", "$set"}:
            for d in current:
                for path, expr in spec.items():
                    set_path(d, path, evaluate(expr, d))
        elif name == "$sort":
            current = _sort(current, spec)
        elif name == "$limit":
            current = current[:spec]
        elif name == "$skip":
            current = current[spec:]
        elif name == "$count":
            current = [{spec: len(current)}] if current else []
        elif name == "$unwind":
            current = _unwind(current, spec)
        elif name == "$sortByCount":
            grouped = _group(current, {"_id": spec, "count": {"$sum": 1}})
            current = _sort(grouped, {"count": -1})
        else:
            raise AggregationError(f"unsupported stage {name}")
    return current
