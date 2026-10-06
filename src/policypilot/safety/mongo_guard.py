"""Allow-list validation of LLM-written MongoDB aggregation pipelines.

Rules enforced before anything reaches the database:

* the pipeline is a non-empty JSON array of single-key stage objects, at most
  ``max_stages`` long, with bounded nesting depth and size;
* only read-only analytical stages are allowed (``$match``, ``$group``, ``$project``,
  ``$sort``, ``$limit``, ``$skip``, ``$count``, ``$unwind``, ``$addFields``/``$set``,
  ``$sortByCount``) - in particular ``$out``, ``$merge``, ``$lookup``, ``$unionWith``,
  ``$function``, ``$accumulator`` and ``$where`` are rejected wherever they appear;
* every ``$operator`` is on an allow-list, ``$$variables`` are not allowed;
* every field path read by the pipeline exists in the document model (or was created
  by an earlier stage), and enum fields are compared with valid values;
* a final ``$limit`` no larger than ``max_rows`` is enforced.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..schema import DOC_FIELDS


class PipelineValidationError(ValueError):
    pass


ALLOWED_STAGES = frozenset({
    "$match", "$group", "$project", "$sort", "$limit", "$skip", "$count", "$unwind",
    "$addFields", "$set", "$sortByCount",
})
FORBIDDEN_OPERATORS = frozenset({
    "$out", "$merge", "$lookup", "$graphLookup", "$unionWith", "$function", "$accumulator", "$where",
    "$facet", "$collStats", "$indexStats", "$currentOp", "$listSessions", "$planCacheStats",
    "$documents", "$changeStream", "$listLocalSessions", "$jsonSchema", "$regex", "$text",
})
QUERY_OPERATORS = frozenset({"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$exists", "$not"})
LOGICAL_QUERY_OPERATORS = frozenset({"$and", "$or", "$nor"})
ACCUMULATORS = frozenset({"$sum", "$avg", "$min", "$max", "$first", "$last", "$push", "$addToSet", "$count"})
EXPRESSION_OPERATORS = frozenset({
    "$add", "$subtract", "$multiply", "$divide", "$mod", "$round", "$abs", "$floor", "$ceil",
    "$toInt", "$toDouble", "$toLong", "$toBool", "$cond", "$ifNull",
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$and", "$or", "$not", "$in",
})


@dataclass
class PipelinePolicy:
    fields: tuple[str, ...] = tuple(f.path for f in DOC_FIELDS)
    enums: dict[str, frozenset[str]] = field(
        default_factory=lambda: {f.path: frozenset(f.enum) for f in DOC_FIELDS if f.enum})
    max_rows: int = 200
    max_stages: int = 12
    max_depth: int = 12
    max_nodes: int = 600
    max_skip: int = 100_000


@dataclass(frozen=True)
class ValidatedPipeline:
    pipeline: list
    limit: int


def _is_known(path: str, known: set[str]) -> bool:
    for k in known:
        if path == k or path.startswith(k + ".") or k.startswith(path + "."):
            return True
    return False


class _Checker:
    def __init__(self, policy: PipelinePolicy):
        self.policy = policy
        self.known: set[str] = set(policy.fields) | {"_id"}
        self.nodes = 0

    # -- generic helpers
    def _count(self, depth: int) -> None:
        self.nodes += 1
        if self.nodes > self.policy.max_nodes:
            raise PipelineValidationError("pipeline is too large")
        if depth > self.policy.max_depth:
            raise PipelineValidationError("pipeline is nested too deeply")

    def _operator(self, key: str, allowed: frozenset[str], where: str) -> None:
        if key in FORBIDDEN_OPERATORS:
            raise PipelineValidationError(f"operator {key} is not allowed")
        if key not in allowed:
            raise PipelineValidationError(f"operator {key} is not allowed in {where}")

    def _field_path(self, path: str) -> None:
        if not path or path.startswith("$") or any(not part for part in path.split(".")):
            raise PipelineValidationError(f"invalid field path {path!r}")
        if not _is_known(path, self.known):
            raise PipelineValidationError(f"unknown field {path!r}; known fields: {sorted(self.known)}")

    def _output_name(self, name: str) -> None:
        if not isinstance(name, str) or not name or name.startswith("$") or "\x00" in name:
            raise PipelineValidationError(f"invalid output field name {name!r}")

    def _literal(self, value: object, depth: int) -> None:
        """Literal values in $match: may be scalars or lists of scalars, never operator objects."""
        self._count(depth)
        if isinstance(value, dict):
            raise PipelineValidationError("nested objects are not allowed as $match values")
        if isinstance(value, list):
            for item in value:
                self._literal(item, depth + 1)
        elif isinstance(value, str) and len(value) > 200:
            raise PipelineValidationError("string literal too long")

    def _enum(self, path: str, value: object) -> None:
        allowed = self.policy.enums.get(path)
        if not allowed:
            return
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, str) and v not in allowed:
                raise PipelineValidationError(f"invalid value {v!r} for {path}; allowed values: {sorted(allowed)}")

    # -- expressions
    def expr(self, value: object, depth: int = 0) -> None:
        self._count(depth)
        if isinstance(value, str):
            if value.startswith("$$"):
                raise PipelineValidationError(f"variables such as {value!r} are not allowed")
            if value.startswith("$"):
                self._field_path(value[1:])
        elif isinstance(value, list):
            for item in value:
                self.expr(item, depth + 1)
        elif isinstance(value, dict):
            op_keys = [k for k in value if isinstance(k, str) and k.startswith("$")]
            if op_keys:
                if len(value) != 1:
                    raise PipelineValidationError("an operator object must have exactly one key")
                key = op_keys[0]
                self._operator(key, EXPRESSION_OPERATORS, "expressions")
                self.expr(value[key], depth + 1)
            else:
                for k, v in value.items():
                    self._output_name(k)
                    self.expr(v, depth + 1)
        elif not (value is None or isinstance(value, (bool, int, float))):
            raise PipelineValidationError(f"unsupported value {value!r}")

    # -- $match
    def query(self, cond: object, depth: int = 0) -> None:
        self._count(depth)
        if not isinstance(cond, dict):
            raise PipelineValidationError("$match needs an object")
        for key, value in cond.items():
            if key in LOGICAL_QUERY_OPERATORS:
                if not isinstance(value, list) or not value:
                    raise PipelineValidationError(f"{key} needs a non-empty array")
                for sub in value:
                    self.query(sub, depth + 1)
            elif key == "$expr":
                self.expr(value, depth + 1)
            elif key.startswith("$"):
                self._operator(key, frozenset(), "the top level of $match")
            else:
                self._field_path(key)
                self._field_condition(key, value, depth + 1)

    def _field_condition(self, path: str, value: object, depth: int) -> None:
        if isinstance(value, dict) and value and all(isinstance(k, str) and k.startswith("$") for k in value):
            for op, arg in value.items():
                self._operator(op, QUERY_OPERATORS, "$match field conditions")
                if op == "$not":
                    self._field_condition(path, arg, depth + 1)
                    continue
                if op in {"$in", "$nin"} and not isinstance(arg, list):
                    raise PipelineValidationError(f"{op} needs an array")
                self._literal(arg, depth + 1)
                if op in {"$eq", "$ne", "$in", "$nin"}:
                    self._enum(path, arg)
        else:
            self._literal(value, depth)
            self._enum(path, value)

    # -- stages
    def stage(self, stage: object) -> None:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise PipelineValidationError("each stage must be an object with exactly one key")
        name, body = next(iter(stage.items()))
        if name in FORBIDDEN_OPERATORS:
            raise PipelineValidationError(f"stage {name} is not allowed")
        if name not in ALLOWED_STAGES:
            raise PipelineValidationError(f"stage {name} is not allowed; allowed stages: {sorted(ALLOWED_STAGES)}")
        getattr(self, "_stage_" + name[1:])(body)

    def _stage_match(self, body: object) -> None:
        self.query(body)

    def _stage_group(self, body: object) -> None:
        if not isinstance(body, dict) or "_id" not in body:
            raise PipelineValidationError("$group needs an object with an _id")
        self.expr(body["_id"], 1)
        outputs = {"_id"}
        for name, acc in body.items():
            if name == "_id":
                continue
            self._output_name(name)
            if "." in name:
                raise PipelineValidationError("$group output names cannot contain '.'")
            if not isinstance(acc, dict) or len(acc) != 1:
                raise PipelineValidationError(f"$group field {name!r} needs one accumulator")
            op, arg = next(iter(acc.items()))
            self._operator(op, ACCUMULATORS, "$group")
            if op == "$count":
                if arg != {}:
                    raise PipelineValidationError("$count accumulator takes {}")
            else:
                self.expr(arg, 2)
            outputs.add(name)
        self.known = outputs

    def _stage_project(self, body: object) -> None:
        if not isinstance(body, dict) or not body:
            raise PipelineValidationError("$project needs a non-empty object")
        shaped: set[str] = set()
        excluded: set[str] = set()
        for name, spec in body.items():
            self._output_name(name)
            if spec in (0, False) and not isinstance(spec, float):
                excluded.add(name)
            elif spec in (1, True) and not isinstance(spec, float):
                self._field_path(name)
                shaped.add(name)
            else:
                self.expr(spec, 1)
                shaped.add(name)
        if shaped:
            self.known = shaped | ({"_id"} - excluded)
        else:
            self.known -= excluded

    def _stage_addFields(self, body: object) -> None:
        if not isinstance(body, dict) or not body:
            raise PipelineValidationError("$addFields needs a non-empty object")
        for name, spec in body.items():
            self._output_name(name)
            self.expr(spec, 1)
        self.known |= set(body)

    _stage_set = _stage_addFields

    def _stage_sort(self, body: object) -> None:
        if not isinstance(body, dict) or not body:
            raise PipelineValidationError("$sort needs a non-empty object")
        for name, direction in body.items():
            self._field_path(name)
            if direction not in (1, -1) or isinstance(direction, bool):
                raise PipelineValidationError("$sort directions must be 1 or -1")

    def _positive_int(self, body: object, stage: str, maximum: int) -> int:
        if not isinstance(body, int) or isinstance(body, bool) or body < 0 or body > maximum:
            raise PipelineValidationError(f"{stage} must be an integer between 0 and {maximum}")
        return body

    def _stage_limit(self, body: object) -> None:
        if self._positive_int(body, "$limit", 10**9) < 1:
            raise PipelineValidationError("$limit must be at least 1")

    def _stage_skip(self, body: object) -> None:
        self._positive_int(body, "$skip", self.policy.max_skip)

    def _stage_count(self, body: object) -> None:
        self._output_name(body)  # type: ignore[arg-type]
        if "." in body:  # type: ignore[operator]
            raise PipelineValidationError("$count name cannot contain '.'")
        self.known = {body}  # type: ignore[arg-type]

    def _stage_unwind(self, body: object) -> None:
        path = body.get("path") if isinstance(body, dict) else body
        if isinstance(body, dict) and set(body) - {"path", "preserveNullAndEmptyArrays"}:
            raise PipelineValidationError("$unwind only supports path and preserveNullAndEmptyArrays")
        if not isinstance(path, str) or not path.startswith("$") or path.startswith("$$"):
            raise PipelineValidationError("$unwind needs a field path like '$field'")
        self._field_path(path[1:])

    def _stage_sortByCount(self, body: object) -> None:
        self.expr(body, 1)
        self.known = {"_id", "count"}


def validate_pipeline(pipeline: object, policy: PipelinePolicy | None = None) -> ValidatedPipeline:
    policy = policy or PipelinePolicy()
    if not isinstance(pipeline, list) or not pipeline:
        raise PipelineValidationError("the pipeline must be a non-empty JSON array of stages")
    if len(pipeline) > policy.max_stages:
        raise PipelineValidationError(f"the pipeline has more than {policy.max_stages} stages")
    checker = _Checker(policy)
    for stage in pipeline:
        checker.stage(stage)
    safe = copy.deepcopy(pipeline)
    last = safe[-1]
    if "$limit" in last:
        last["$limit"] = min(last["$limit"], policy.max_rows)
        limit = last["$limit"]
    else:
        safe.append({"$limit": policy.max_rows})
        limit = policy.max_rows
    return ValidatedPipeline(pipeline=safe, limit=limit)
