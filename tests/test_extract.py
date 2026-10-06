import pytest

from policypilot.safety.extract import ExtractionError, extract_json_object, extract_pipeline, extract_sql


def test_nested_arrays_are_parsed_completely():
    # a lazy regex like \[[\s\S]*?\] stops at the first "]" and truncates this pipeline
    text = ('db.collection.aggregate([{"$match": {"car.type": {"$in": ["SUV", "Van"]}}}, '
            '{"$group": {"_id": null, "n": {"$sum": 1}}}])')
    pipeline = extract_pipeline(text)
    assert len(pipeline) == 2
    assert pipeline[0]["$match"]["car.type"]["$in"] == ["SUV", "Van"]


def test_pipeline_from_fence_object_or_prose():
    assert extract_pipeline('```json\n[{"$count": "n"}]\n```') == [{"$count": "n"}]
    assert extract_pipeline('{"pipeline": [{"$count": "n"}]}') == [{"$count": "n"}]
    assert extract_pipeline('Here you go: [{"$count": "n"}] hope it helps') == [{"$count": "n"}]
    assert extract_pipeline('<think>maybe [1, 2]? no</think>[{"$count": "n"}]') == [{"$count": "n"}]


@pytest.mark.parametrize("text", ["", "I cannot help with that", "[{$match: {a: 1}}]", "<think>only thoughts"])
def test_missing_or_invalid_pipeline_raises_clean_error(text):
    with pytest.raises(ExtractionError):
        extract_pipeline(text)


def test_extract_sql_variants():
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("Sure! SELECT COUNT(*) FROM customers") == "SELECT COUNT(*) FROM customers"
    assert extract_sql("```\nWITH x AS (SELECT 1) SELECT * FROM x\n```").startswith("WITH")
    with pytest.raises(ExtractionError):
        extract_sql("DROP TABLE customers")
    with pytest.raises(ExtractionError):
        extract_sql("")


def test_extract_json_object():
    assert extract_json_object('noise {"route": "sql", "confidence": 0.9} more')["route"] == "sql"
    with pytest.raises(ExtractionError):
        extract_json_object("route: sql")
