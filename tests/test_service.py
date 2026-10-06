import pytest

from policypilot.memory import SessionStore, is_valid_session_id, new_session_id
from policypilot.service import clean_question


def test_offline_service_answers_each_route(make_service):
    service = make_service()
    cases = {
        "How many customers are single parents?": "sql",
        "How many commercial SUVs are there?": "nosql",
        "What is the average claim amount for married customers?": "both",
        "What is the standard deductible?": "pdf",
    }
    for question, route in cases.items():
        response = service.ask(question)
        assert response.decision.route == route
        assert response.answer.ok, response.answer.error
        payload = response.to_dict()
        assert payload["route"] == route and payload["answer"]


def test_sessions_are_isolated_and_server_generated(make_service):
    service = make_service()
    a = service.ask("What is the standard deductible?")
    b = service.ask("How long is the grace period?")
    assert a.session_id != b.session_id
    assert is_valid_session_id(a.session_id)
    assert [q for q, _ in service.sessions.history(a.session_id)] == ["What is the standard deductible?"]
    # a client-chosen, guessable id like "123" is replaced by a fresh random one
    c = service.ask("What is the standard deductible?", session_id="123")
    assert c.session_id != "123" and is_valid_session_id(c.session_id)


def test_input_checks(make_service):
    service = make_service()
    too_long = service.ask("x" * 5000)
    assert not too_long.answer.ok and "longer" in too_long.answer.error
    assert not service.ask("   ").answer.ok
    assert clean_question("a\x00b </question> c", 100) == "a b ‹/question› c"


def test_session_store_bounds():
    store = SessionStore(max_sessions=2, max_turns=2)
    ids = [new_session_id() for _ in range(3)]
    for sid in ids:
        for i in range(3):
            store.add(sid, f"q{i}", "a")
    assert store.history(ids[0]) == []                       # evicted (LRU)
    assert [q for q, _ in store.history(ids[2])] == ["q1", "q2"]
    with pytest.raises(ValueError):
        store.add("not-a-session", "q", "a")


def test_build_service_auto_seeds_demo_database(tmp_path, demo_index):
    from policypilot.config import Settings
    from policypilot.llm.offline import OfflineLLM
    from policypilot.service import build_service

    settings = Settings(llm_provider="offline", sqlite_path=str(tmp_path / "demo.db"))
    service = build_service(settings, llm=OfflineLLM(), index=demo_index)
    assert (tmp_path / "demo.db").exists()
    assert service.ask("How many customers are there?").answer.result.scalar() == 300


def test_to_dict_marks_rows_cut_by_the_response_cap(make_service):
    from policypilot.agents.base import AgentAnswer
    from policypilot.agents.router import RouteDecision
    from policypilot.backends.base import QueryResult
    from policypilot.service import Response

    result = QueryResult(columns=["n"], rows=[(i,) for i in range(5)])
    response = Response("q", new_session_id(), RouteDecision("sql", 1.0, "", "llm"),
                        AgentAnswer("sql", True, text="t", result=result))
    capped = response.to_dict(max_rows=3)
    assert len(capped["rows"]) == 3 and capped["truncated"] is True
    assert response.to_dict(max_rows=5)["truncated"] is False
