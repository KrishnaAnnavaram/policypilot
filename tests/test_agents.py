import json

from policypilot.agents.base import Answerer
from policypilot.agents.mongo_agent import MongoAgent
from policypilot.agents.planner import Planner
from policypilot.agents.rag_agent import RAGAgent
from policypilot.agents.sql_agent import SQLAgent
from policypilot.llm.base import LLMError, ScriptedLLM
from policypilot.safety.mongo_guard import PipelinePolicy


def _sql(text):
    return f"```sql\n{text}\n```"


def _answer_llm(responses):
    """Scripted query responses; any 'answer' prompt gets a canned sentence."""
    queue = list(responses)

    def responder(system, user):
        if system.startswith("TASK: answer"):
            return "canned answer"
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return ScriptedLLM(responder=responder)


def test_sql_agent_retries_after_rejected_query(executor, sql_policy):
    llm = _answer_llm([_sql("DROP TABLE customers"), _sql("SELECT COUNT(*) FROM customers")])
    ans = SQLAgent(llm, executor, sql_policy, max_attempts=3).run("How many customers?")
    assert ans.ok and ans.result.scalar() == 120
    assert len(ans.attempts) == 2 and ans.attempts[0].error
    retry_prompt = llm.calls[1].user
    assert "rejected" in retry_prompt and "DROP TABLE" in retry_prompt


def test_sql_agent_retry_loop_is_bounded(executor, sql_policy):
    llm = _answer_llm([_sql("SELECT * FROM customers")] * 10)
    ans = SQLAgent(llm, executor, sql_policy, max_attempts=3).run("everything")
    assert not ans.ok and len(ans.attempts) == 3
    assert len(llm.calls) == 3                      # exactly max_attempts model calls, no recursion
    assert "3 attempts" in ans.error


def test_sql_agent_feeds_database_errors_back(executor, sql_policy):
    # valid for the validator but fails at runtime in SQLite -> treated as recoverable
    llm = _answer_llm([_sql("SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY income) FROM customers"),
                       _sql("SELECT AVG(income) FROM customers")])
    ans = SQLAgent(llm, executor, sql_policy).run("median income")
    assert ans.ok and len(ans.attempts) == 2


def test_llm_outage_is_not_retried_by_the_agent(executor, sql_policy):
    llm = _answer_llm([LLMError("down")])
    ans = SQLAgent(llm, executor, sql_policy).run("x")
    assert not ans.ok and "unavailable" in ans.error and len(llm.calls) == 1


def test_mongo_agent_rejects_out_then_succeeds(store):
    bad = '[{"$match": {}}, {"$out": "stolen"}]'
    good = '```json\n[{"$match": {"car.type": {"$in": ["SUV", "Van"]}}}, {"$count": "n"}]\n```'
    llm = _answer_llm([bad, good])
    ans = MongoAgent(llm, store, PipelinePolicy(max_rows=20)).run("How many SUVs and vans?")
    assert ans.ok and ans.result.columns == ["n"]
    expected = sum(1 for d in store.documents if d["car"]["type"] in {"SUV", "Van"})
    assert ans.result.scalar() == expected
    assert "$out" in ans.attempts[0].error


def test_planner_joins_on_customer_id(executor, store, sql_policy):
    plan = json.dumps({"customer_question": "married customers", "claims_question": "average claim amount"})
    pipeline = '[{"$group": {"_id": null, "avg": {"$avg": "$claims.amount"}}}]'
    llm = _answer_llm([plan, _sql("SELECT customer_id FROM customers WHERE married = 1"), pipeline])
    sql_agent = SQLAgent(llm, executor, sql_policy)
    planner = Planner(llm, sql_agent, MongoAgent(llm, store), answerer=Answerer(llm))
    ans = planner.run("What is the average claim amount for married customers?")
    assert ans.ok
    truth = executor.execute("SELECT AVG(cl.claim_amount) FROM claims cl JOIN customers c "
                             "ON c.customer_id = cl.customer_id WHERE c.married = 1", 5).scalar()
    got = dict(zip(ans.result.columns, ans.result.rows[0]))["avg"]
    assert abs(got - truth) < 1e-9
    # the id list is NOT capped by the normal row limit (50 here)
    assert "LIMIT 10000" in ans.query


def test_planner_requires_customer_id_column(executor, store, sql_policy):
    llm = _answer_llm(["{}", _sql("SELECT COUNT(*) FROM customers")])
    planner = Planner(llm, SQLAgent(llm, executor, sql_policy), MongoAgent(llm, store))
    ans = planner.run("x")
    assert not ans.ok and "customer_id" in ans.error


def test_rag_agent_cites_and_handles_empty_index(demo_index):
    llm = ScriptedLLM(responder=lambda s, u: "The grace period is 15 days [1].")
    ans = RAGAgent(llm, demo_index).run("How long is the grace period for a missed premium payment?")
    assert ans.ok and ans.sources[0].source == "renewal_and_cancellation.md"
    assert not ans.notes

    from policypilot.rag.index import HybridIndex

    empty = RAGAgent(llm, HybridIndex()).run("anything")
    assert not empty.ok


def test_rag_agent_flags_bad_citations(demo_index):
    llm = ScriptedLLM(responder=lambda s, u: "Yes [9].")
    ans = RAGAgent(llm, demo_index).run("What is the deductible?")
    assert any("unknown passages" in n for n in ans.notes)


def test_rag_condenses_follow_ups_with_history(demo_index):
    def responder(system, user):
        if system.startswith("TASK: condense"):
            assert "grace period" in user
            return "How long is the grace period for a missed premium?"
        return "15 days [1]"

    agent = RAGAgent(ScriptedLLM(responder=responder), demo_index)
    ans = agent.run("and how long is it?", history=[("Is there a grace period?", "Yes.")])
    assert ans.query.startswith("How long is the grace period")
