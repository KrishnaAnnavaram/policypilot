import pytest

from policypilot.backends.aggregation import run_pipeline
from policypilot.backends.base import QueryResult
from policypilot.backends.docstore import InMemoryDocStore
from policypilot.safety.mongo_guard import ACCUMULATORS, EXPRESSION_OPERATORS, validate_pipeline

DOCS = [
    {"vehicle_id": 1, "customer_id": 1, "car_use": "Private", "car": {"type": "SUV", "red": True, "age": 3},
     "claims": {"amount": 0, "flag": False, "mvr_points": 1}, "bluebook_value": 20000},
    {"vehicle_id": 2, "customer_id": 1, "car_use": "Commercial", "car": {"type": "Van", "red": False, "age": 9},
     "claims": {"amount": 5000, "flag": True, "mvr_points": 4}, "bluebook_value": 12000},
    {"vehicle_id": 3, "customer_id": 2, "car_use": "Commercial", "car": {"type": "SUV", "red": False, "age": None},
     "claims": {"amount": 1000, "flag": True, "mvr_points": 0}, "bluebook_value": 15000},
]


def test_match_group_avg_and_count():
    out = run_pipeline(DOCS, [{"$match": {"car_use": "Commercial"}},
                              {"$group": {"_id": None, "avg": {"$avg": "$claims.amount"}, "n": {"$sum": 1}}}])
    assert out == [{"_id": None, "avg": 3000.0, "n": 2}]
    assert run_pipeline(DOCS, [{"$match": {"car.type": "Truck"}}, {"$count": "n"}]) == []   # like MongoDB


def test_bool_and_number_are_not_equal():
    assert len(run_pipeline(DOCS, [{"$match": {"claims.flag": True}}])) == 2
    assert run_pipeline(DOCS, [{"$match": {"claims.flag": 1}}]) == []


def test_query_comparisons_are_type_bracketed_and_null_aware():
    assert [d["vehicle_id"] for d in run_pipeline(DOCS, [{"$match": {"car.age": {"$gte": 0}}}])] == [1, 2]
    assert [d["vehicle_id"] for d in run_pipeline(DOCS, [{"$match": {"car.age": None}}])] == [3]
    assert len(run_pipeline(DOCS, [{"$match": {"missing": {"$exists": False}}}])) == 3


def test_project_sort_unwind_and_sort_by_count():
    out = run_pipeline(DOCS, [{"$project": {"_id": 0, "car.type": 1, "v": {"$divide": ["$bluebook_value", 1000]}}},
                              {"$sort": {"v": -1}}])
    assert out[0] == {"car": {"type": "SUV"}, "v": 20.0}
    grouped = run_pipeline(DOCS, [{"$group": {"_id": "$customer_id", "ids": {"$push": "$vehicle_id"}}},
                                  {"$unwind": "$ids"}])
    assert len(grouped) == 3
    assert run_pipeline(DOCS, [{"$sortByCount": "$car.type"}])[0] == {"_id": "SUV", "count": 2}


def test_expression_operators():
    out = run_pipeline(DOCS[:1], [{"$project": {
        "a": {"$round": [{"$divide": [10, 3]}, 2]}, "b": {"$cond": {"if": "$car.red", "then": "y", "else": "n"}},
        "c": {"$ifNull": ["$nope", 7]}, "d": {"$in": ["$car.type", ["SUV"]]}, "e": {"$toDouble": "$car.age"},
    }}])
    assert out[0] == {"a": 3.33, "b": "y", "c": 7, "d": True, "e": 3.0}


@pytest.mark.parametrize("op", sorted(EXPRESSION_OPERATORS))
def test_every_allowed_expression_operator_is_implemented(op):
    arg = {"$cond": [True, 1, 0], "$ifNull": [None, 1], "$not": [False], "$and": [True], "$or": [True],
           "$in": [1, [1]], "$round": [1.5, 0]}.get(op, [6, 3] if op not in {"$abs", "$floor", "$ceil", "$toInt",
                                                                            "$toDouble", "$toLong", "$toBool"} else 3)
    pipeline = [{"$project": {"x": {op: arg}}}]
    validate_pipeline(pipeline)
    run_pipeline(DOCS[:1], pipeline)


@pytest.mark.parametrize("op", sorted(ACCUMULATORS))
def test_every_allowed_accumulator_is_implemented(op):
    pipeline = [{"$group": {"_id": None, "x": {op: {} if op == "$count" else "$bluebook_value"}}}]
    validate_pipeline(pipeline)
    assert len(run_pipeline(DOCS, pipeline)) == 1


def test_store_results_are_json_safe():
    class FakeObjectId:
        def __str__(self):
            return "65f0c0ffee"

    result = QueryResult.from_records([{"_id": FakeObjectId(), "car": {"type": "SUV"}}])
    assert result.columns == ["_id", "car.type"]
    assert '"65f0c0ffee"' in result.to_json()


def test_in_memory_store_truncates():
    s = InMemoryDocStore(DOCS)
    res = s.aggregate([{"$match": {}}], max_rows=2)
    assert len(res.rows) == 2 and res.truncated
