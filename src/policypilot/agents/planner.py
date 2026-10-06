"""Planner for cross-source ("both") questions.

1. The LLM splits the question into a customer sub-question and a claims sub-question.
2. The SQL agent answers the customer part with a list of ``customer_id`` values.
3. Trusted code prepends ``{"$match": {"customer_id": {"$in": ids}}}`` to the validated
   claims pipeline, so the join on the shared key is done in code, not by the model.
"""
from __future__ import annotations

from ..llm.base import LLM, LLMError
from ..safety.extract import ExtractionError, extract_json_object
from . import prompts
from .base import AgentAnswer, Answerer
from .mongo_agent import MongoAgent
from .sql_agent import SQLAgent

ID_INSTRUCTIONS = "Return only the customer_id column of the matching customers (no aggregates, no LIMIT)."


class Planner:
    route = "both"

    def __init__(self, llm: LLM, sql_agent: SQLAgent, mongo_agent: MongoAgent, max_ids: int = 10_000,
                 answerer: Answerer | None = None):
        self.llm = llm
        self.sql_agent = sql_agent
        self.mongo_agent = mongo_agent
        self.max_ids = max_ids
        self.answerer = answerer or Answerer(llm)

    def plan(self, question: str) -> tuple[str, str]:
        try:
            obj = extract_json_object(self.llm.complete(prompts.PLAN, prompts.wrap_question(question),
                                                        json_mode=True))
            customer_q = str(obj.get("customer_question") or "").strip()
            claims_q = str(obj.get("claims_question") or "").strip()
        except (LLMError, ExtractionError):
            customer_q = claims_q = ""
        return customer_q or question, claims_q or question

    def run(self, question: str) -> AgentAnswer:
        customer_q, claims_q = self.plan(question)
        ids_outcome = self.sql_agent.query(customer_q, instructions=ID_INSTRUCTIONS, max_rows=self.max_ids)
        attempts = list(ids_outcome.attempts)
        if not ids_outcome.ok:
            return AgentAnswer(self.route, False,
                               text=f"Sorry, I could not select the customers ({ids_outcome.error}).",
                               attempts=attempts, error=ids_outcome.error)
        result = ids_outcome.result
        columns = [c.lower() for c in result.columns]
        if "customer_id" not in columns:
            error = "the customer query did not return a customer_id column"
            return AgentAnswer(self.route, False, text=f"Sorry, {error}.", attempts=attempts, error=error)
        idx = columns.index("customer_id")
        ids = sorted({int(row[idx]) for row in result.rows if row[idx] is not None})
        notes = [f"customer filter: {ids_outcome.query}", f"{len(ids)} matching customers"]
        if result.truncated:
            notes.append(f"customer list truncated at {self.max_ids}; the answer covers only those customers")
        prefix = [{"$match": {"customer_id": {"$in": ids}}}]
        claims_outcome = self.mongo_agent.query(claims_q, prefix=prefix)
        attempts += claims_outcome.attempts
        if not claims_outcome.ok:
            return AgentAnswer(self.route, False,
                               text=f"Sorry, I could not query the claims ({claims_outcome.error}).",
                               attempts=attempts, error=claims_outcome.error, notes=notes)
        text = self.answerer.compose(question, claims_outcome.result)
        query = f"SQL: {ids_outcome.query}\nPipeline (after customer_id $in filter): {claims_outcome.query}"
        return AgentAnswer(self.route, True, text=text, query=query, result=claims_outcome.result,
                           attempts=attempts, notes=notes)
