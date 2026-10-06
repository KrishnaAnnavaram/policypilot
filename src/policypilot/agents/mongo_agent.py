"""Text-to-aggregation agent: generate -> extract -> allow-list validate -> execute -> answer."""
from __future__ import annotations

import json

from ..backends.base import DocumentStore, QueryResult
from ..llm.base import LLM
from ..safety.extract import extract_pipeline
from ..safety.mongo_guard import PipelinePolicy, validate_pipeline
from ..schema import describe_documents
from . import prompts
from .base import AgentAnswer, Answerer, generate_and_run


class MongoAgent:
    route = "nosql"

    def __init__(self, llm: LLM, store: DocumentStore, policy: PipelinePolicy | None = None,
                 max_attempts: int = 3, answerer: Answerer | None = None):
        self.llm = llm
        self.store = store
        self.policy = policy or PipelinePolicy()
        self.max_attempts = max_attempts
        self.answerer = answerer or Answerer(llm)
        self.system = prompts.PIPELINE.format(schema=describe_documents())

    def query(self, question: str, prefix: list | None = None):
        """``prefix`` stages are added by trusted code (e.g. the planner's customer filter)
        AFTER the model-written pipeline has been validated."""

        def run(raw: str) -> tuple[str, QueryResult]:
            validated = validate_pipeline(extract_pipeline(raw), self.policy)
            pipeline = list(prefix or []) + validated.pipeline
            shown = json.dumps(validated.pipeline)
            return shown, self.store.aggregate(pipeline, self.policy.max_rows)

        return generate_and_run(self.llm, self.system, question, run, self.max_attempts)

    def run(self, question: str) -> AgentAnswer:
        outcome = self.query(question)
        if not outcome.ok:
            return AgentAnswer(self.route, False, text=f"Sorry, I could not answer from the claims database "
                               f"({outcome.error}).", attempts=outcome.attempts, error=outcome.error)
        text = self.answerer.compose(question, outcome.result)
        return AgentAnswer(self.route, True, text=text, query=outcome.query, result=outcome.result,
                           attempts=outcome.attempts)
