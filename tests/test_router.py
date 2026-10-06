import pytest

from policypilot.agents.router import KeywordRouter, LLMRouter
from policypilot.llm.base import LLMError, ScriptedLLM


@pytest.mark.parametrize("question,route", [
    ("How many customers are single parents?", "sql"),
    ("What is the average income by education?", "sql"),
    ("How many commercial SUVs are there?", "nosql"),
    ("What is the total claim amount for vans?", "nosql"),
    ("What is the average claim amount for married customers?", "both"),
    ("How many red cars do customers with a PhD own?", "both"),
    ("How many claims do our customers have?", "nosql"),
    ("What is the standard deductible?", "pdf"),
    ("Am I covered if I use my car for deliveries?", "pdf"),
    ("重复保险如何赔偿？", "pdf"),
])
def test_keyword_router(question, route):
    assert KeywordRouter().route(question).route == route


def test_llm_router_uses_valid_json():
    llm = ScriptedLLM(['{"route": "nosql", "confidence": 0.9, "reason": "claims"}'])
    d = LLMRouter(llm).route("anything")
    assert (d.route, d.source) == ("nosql", "llm")
    assert llm.calls[0].json_mode


@pytest.mark.parametrize("response", [
    "I think it is SQL",                               # not JSON
    '{"route": "graphql", "confidence": 0.99}',        # unknown route
    '{"route": "sql", "confidence": "high"}',          # bad confidence
    LLMError("timeout"),                               # provider failure
])
def test_llm_router_falls_back_to_keywords(response):
    d = LLMRouter(ScriptedLLM([response])).route("What is the standard deductible?")
    assert d.route == "pdf" and d.source == "keywords-fallback"


def test_low_confidence_defers_to_keywords_when_they_disagree():
    llm = ScriptedLLM(['{"route": "sql", "confidence": 0.2}'])
    d = LLMRouter(llm, min_confidence=0.5).route("How many commercial SUVs are there?")
    assert d.route == "nosql" and d.source == "keywords-fallback"
