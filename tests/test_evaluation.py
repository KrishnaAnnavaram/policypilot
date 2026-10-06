import pytest

from policypilot.evaluation import GoldSetError, evaluate, format_report, load_gold
from policypilot.evaluation.gold import check_gold_queries, ground_truth, parse_items
from policypilot.evaluation.metrics import reciprocal_rank, recall_at_k, results_match, routing_report
from policypilot.llm.base import ScriptedLLM
from policypilot.safety.mongo_guard import PipelinePolicy


def test_execution_match_ignores_wording_but_not_numbers():
    # "There are 445 ..." vs "There are 10 ..." scored well under ROUGE; here it is simply wrong
    assert not results_match([[445]], [[10]])
    assert results_match([[445]], [[445.0]])
    assert results_match([["SUV", 445]], [[445]])                     # scalar with a label column
    assert results_match([[57200.004]], [[57200.0]])                  # tolerance
    assert results_match([[33.33]], [[33.3333333]])                   # rounded to 2 decimals
    assert not results_match([[33.4]], [[33.3333333]])


def test_execution_match_is_order_and_column_insensitive():
    gold = [["F", 10], ["M", 12]]
    assert results_match([[12, "M"], [10, "F"]], gold)
    assert not results_match([[12, "M"]], gold)
    assert not results_match([["M", 10], ["F", 12]], gold)
    assert not results_match([["M", 12], ["F", 10]], [["F", 10], ["M", 12]], ordered=True)
    assert results_match([[True]], [[1]])


def test_routing_report_and_retrieval_metrics():
    rep = routing_report(["sql", "sql", "pdf", "both"], ["sql", "nosql", "pdf", "both"],
                         ["sql", "nosql", "both", "pdf"])
    assert rep["accuracy"] == 0.75
    assert rep["per_route"]["sql"]["recall"] == 0.5 and rep["per_route"]["nosql"]["precision"] == 0.0
    assert rep["confusion"]["sql->nosql"] == 1
    assert recall_at_k(["a", "b", "c"], ["c"], 2) == 0.0 and recall_at_k(["a", "b", "c"], ["c"], 3) == 1.0
    assert reciprocal_rank(["a", "b"], ["b"]) == 0.5


def test_bundled_gold_set_is_valid_and_covers_every_route(make_service):
    items = load_gold()
    assert {i.route for i in items} == {"sql", "nosql", "both", "pdf"}
    service = make_service()
    check_gold_queries([i for i in items if i.is_data], service.sql_agent.policy, service.mongo_agent.policy)


def test_gold_queries_with_values_missing_from_the_data_are_rejected(sql_policy):
    # the prototype's test set filtered on mstatus = 'Single' and gender = 'Female'
    items = parse_items([{"id": "x", "route": "sql", "question": "q",
                          "gold_sql": "SELECT AVG(commute_minutes) FROM customers WHERE gender = 'Female'"}])
    with pytest.raises(GoldSetError, match="Female"):
        check_gold_queries(items, sql_policy, PipelinePolicy())


def test_gold_set_structure_errors():
    with pytest.raises(GoldSetError):
        parse_items([{"id": "a", "route": "sql", "question": "q"}])
    with pytest.raises(GoldSetError):
        parse_items([{"id": "a", "route": "pdf", "question": "q"}])
    with pytest.raises(GoldSetError, match="duplicate"):
        parse_items([{"id": "a", "route": "pdf", "question": "q", "relevant_sources": ["x"]}] * 2)


def test_ground_truth_is_computed_from_the_data(tmp_path, dataset, executor, store, sql_policy):
    from policypilot.backends.sql import SQLiteExecutor
    from policypilot.data.seed import write_sqlite
    from policypilot.data.synthetic import generate

    item = parse_items([{"id": "n", "route": "sql", "question": "How many single parents?",
                         "gold_sql": "SELECT COUNT(*) FROM customers WHERE single_parent = 1"}])[0]
    truth = ground_truth(item, executor, store, sql_policy, PipelinePolicy()).scalar()
    assert truth == sum(c["single_parent"] for c in dataset.customers)
    other = SQLiteExecutor(write_sqlite(generate(200, seed=99), tmp_path / "other.db"), ("customers",))
    other_truth = ground_truth(item, other, store, sql_policy, PipelinePolicy()).scalar()
    assert other_truth != truth           # a different database gives a different expected answer


def test_offline_end_to_end_evaluation(make_service):
    report = evaluate(make_service(), load_gold())
    assert report["routing"]["accuracy"] == 1.0
    assert report["execution_accuracy"] == 1.0
    assert set(report["execution_accuracy_by_route"]) == {"sql", "nosql", "both"}
    assert "en->zh" in report["retrieval"]["by_language_pair"]
    assert "routing accuracy" in format_report(report)


def test_wrong_queries_score_zero(make_service):
    def responder(system, user):
        if system.startswith("TASK: route"):
            return '{"route": "sql", "confidence": 0.9}'
        if system.startswith("TASK: sql"):
            return "SELECT 12345"
        return "irrelevant"

    items = [i for i in load_gold() if i.route == "sql"]
    report = evaluate(make_service(llm=ScriptedLLM(responder=responder)), items)
    assert report["execution_accuracy"] == 0.0


def test_evaluation_uses_the_services_own_backends(make_service):
    # issue: the old evaluation script used different databases/credentials than the app
    service = make_service()
    calls = []
    original = service.sql_agent.executor.execute

    def spy(sql, max_rows):
        calls.append(sql)
        return original(sql, max_rows)

    service.sql_agent.executor.execute = spy
    evaluate(service, [i for i in load_gold() if i.route == "sql"][:1])
    assert len(calls) == 2           # gold query + agent query, both on the service's executor
