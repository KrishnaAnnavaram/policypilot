"""Text-to-SQL agent: generate -> validate (sqlglot) -> execute read-only -> answer."""
from __future__ import annotations

import dataclasses

from ..backends.base import QueryResult, SQLExecutor
from ..llm.base import LLM
from ..safety.extract import extract_sql
from ..safety.sql_guard import SQLPolicy, validate_sql
from ..schema import describe_tables
from . import prompts
from .base import AgentAnswer, Answerer, generate_and_run


class SQLAgent:
    route = "sql"

    def __init__(self, llm: LLM, executor: SQLExecutor, policy: SQLPolicy, max_attempts: int = 3,
                 answerer: Answerer | None = None):
        self.llm = llm
        self.executor = executor
        self.policy = policy
        self.max_attempts = max_attempts
        self.answerer = answerer or Answerer(llm)
        self.system = prompts.SQL.format(dialect=policy.read_dialect,
                                         schema=describe_tables(tuple(policy.tables)))

    def query(self, question: str, instructions: str = "", max_rows: int | None = None):
        policy = self.policy if max_rows is None else dataclasses.replace(self.policy, max_rows=max_rows)

        def run(raw: str) -> tuple[str, QueryResult]:
            validated = validate_sql(extract_sql(raw), policy)
            return validated.sql, self.executor.execute(validated.sql, policy.max_rows)

        return generate_and_run(self.llm, self.system, question, run, self.max_attempts, instructions)

    def run(self, question: str) -> AgentAnswer:
        outcome = self.query(question)
        if not outcome.ok:
            return AgentAnswer(self.route, False, text=f"Sorry, I could not answer from the customer database "
                               f"({outcome.error}).", attempts=outcome.attempts, error=outcome.error)
        text = self.answerer.compose(question, outcome.result)
        return AgentAnswer(self.route, True, text=text, query=outcome.query, result=outcome.result,
                           attempts=outcome.attempts)
