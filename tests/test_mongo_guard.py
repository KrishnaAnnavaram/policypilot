import pytest

from policypilot.safety.mongo_guard import (ACCUMULATORS, ALLOWED_STAGES, EXPRESSION_OPERATORS, PipelinePolicy,
                                            PipelineValidationError, validate_pipeline)

GOOD = [
    [{"$match": {"car_use": "Commercial", "car.type": "SUV"}}, {"$count": "n"}],
    [{"$match": {"car.red": True}}, {"$group": {"_id": None, "avg": {"$avg": "$claims.amount"}}}],
    [{"$group": {"_id": "$car.type", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}],
    [{"$match": {"$or": [{"car.type": {"$in": ["SUV", "Van"]}}, {"claims.mvr_points": {"$gte": 3}}]}},
     {"$project": {"_id": 0, "vehicle_id": 1, "value": {"$multiply": ["$bluebook_value", 0.9]}}}],
    [{"$addFields": {"high": {"$cond": [{"$gt": ["$claims.amount", 10000]}, 1, 0]}}},
     {"$group": {"_id": "$high", "n": {"$count": {}}}}],
    [{"$sortByCount": "$urbanicity"}],
]

MALICIOUS = [
    ([{"$out": "stolen"}], "\\$out"),
    ([{"$match": {}}, {"$merge": {"into": "x"}}], "\\$merge"),
    ([{"$lookup": {"from": "users", "localField": "a", "foreignField": "b", "as": "u"}}], "\\$lookup"),
    ([{"$unionWith": "users"}], "\\$unionWith"),
    ([{"$match": {"$where": "sleep(10000)"}}], "\\$where"),
    ([{"$match": {"$or": [{"car_use": "Private"}, {"$where": "1"}]}}], "\\$where"),
    ([{"$addFields": {"x": {"$function": {"body": "f", "args": [], "lang": "js"}}}}], "\\$function"),
    ([{"$group": {"_id": None, "x": {"$accumulator": {}}}}], "\\$accumulator"),
    ([{"$match": {"car_use": {"$regex": "^(a+)+$"}}}], "\\$regex"),
    ([{"$project": {"r": "$$ROOT"}}], "variables"),
    ([{"$match": {"password": "x"}}], "unknown field"),
    ([{"$group": {"_id": "$secret_field"}}], "unknown field"),
    ([{"$match": {"car_use": "Personal"}}], "invalid value"),
    ([{"$match": {"car.type": {"$in": ["SUV", "Truck"]}}}], "invalid value"),
    ([{"$match": {"car_use": {"$where": "1"}}}], "\\$where"),
    ([{"$collStats": {}}], "not allowed"),
    ([{"$match": {}, "$out": "x"}], "exactly one key"),
    ({"$match": {}}, "JSON array"),
    ([], "non-empty"),
    ([{"$limit": -1}], "\\$limit"),
    ([{"$match": {"car_use": {"nested": {"$gt": 1}}}}], "nested objects"),
]


@pytest.mark.parametrize("pipeline", GOOD)
def test_valid_pipelines_pass(pipeline):
    out = validate_pipeline(pipeline)
    assert "$limit" in out.pipeline[-1]


@pytest.mark.parametrize("pipeline,message", MALICIOUS)
def test_malicious_pipelines_are_rejected(pipeline, message):
    with pytest.raises(PipelineValidationError, match=message):
        validate_pipeline(pipeline)


def test_limit_is_appended_or_capped_without_mutating_input():
    pipeline = [{"$match": {"car_use": "Private"}}, {"$limit": 10_000}]
    out = validate_pipeline(pipeline, PipelinePolicy(max_rows=25))
    assert out.pipeline[-1] == {"$limit": 25} and out.limit == 25
    assert pipeline[-1] == {"$limit": 10_000}
    out = validate_pipeline([{"$count": "n"}], PipelinePolicy(max_rows=25))
    assert out.pipeline == [{"$count": "n"}, {"$limit": 25}]


def test_fields_created_by_earlier_stages_are_known():
    validate_pipeline([{"$group": {"_id": "$car.type", "total": {"$sum": "$claims.amount"}}},
                       {"$sort": {"total": -1}}])
    with pytest.raises(PipelineValidationError, match="unknown field"):
        # after $group only _id and total exist
        validate_pipeline([{"$group": {"_id": "$car.type", "total": {"$sum": 1}}}, {"$sort": {"car.age": 1}}])


def test_size_limits():
    with pytest.raises(PipelineValidationError, match="stages"):
        validate_pipeline([{"$match": {}}] * 20)
    deep: dict = {"$add": [1, 1]}
    for _ in range(20):
        deep = {"$add": [deep, 1]}
    with pytest.raises(PipelineValidationError, match="deeply|large"):
        validate_pipeline([{"$addFields": {"x": deep}}])


def test_operator_sets_do_not_overlap_forbidden():
    from policypilot.safety.mongo_guard import FORBIDDEN_OPERATORS

    assert not (ALLOWED_STAGES | EXPRESSION_OPERATORS | ACCUMULATORS) & FORBIDDEN_OPERATORS
