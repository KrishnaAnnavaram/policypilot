"""Shared agent result types and the bounded generate-validate-execute loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..backends.base import QueryError, QueryResult
from ..llm.base import LLM, LLMError
from ..safety.extract import ExtractionError
from ..safety.mongo_guard import PipelineValidationError
from ..safety.sql_guard import SQLValidationError
from . import prompts

RECOVERABLE = (ExtractionError, SQLValidationError, PipelineValidationError, QueryError)


@dataclass
class Attempt:
    query: str
    error: str = ""


@dataclass
class Source:
    ref: int
    source: str
    text: str
    score: float = 0.0


@dataclass
class AgentAnswer:
    route: str
    ok: bool
    text: str = ""
    query: str = ""
    result: QueryResult | None = None
    sources: list[Source] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    error: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class LoopOutcome:
    ok: bool
    query: str
    result: QueryResult | None
    attempts: list[Attempt]
    error: str = ""


def generate_and_run(
    llm: LLM,
    system: str,
    question: str,
    run: Callable[[str], tuple[str, QueryResult]],
    max_attempts: int,
    instructions: str = "",
) -> LoopOutcome:
    """Ask the model for a query, then validate and execute it via ``run(raw_text)``.

    Recoverable failures (unparseable output, validation errors, database errors) are
    fed back to the model; the loop makes at most ``max_attempts`` model calls and is a
    plain ``for`` loop, so there is no recursion and no unbounded retrying.
    """
    attempts: list[Attempt] = []
    feedback = ""
    for _ in range(max(1, max_attempts)):
        user = prompts.wrap_question(question, instructions=instructions, feedback=feedback)
        try:
            raw = llm.complete(system, user)
        except LLMError as exc:
            attempts.append(Attempt(query="", error=f"LLM error: {exc}"))
            return LoopOutcome(False, "", None, attempts, f"the language model is unavailable: {exc}")
        try:
            query, result = run(raw)
        except RECOVERABLE as exc:
            shown = raw.strip()[:1500]
            attempts.append(Attempt(query=shown, error=str(exc)))
            feedback = prompts.retry_feedback(shown, str(exc))
            continue
        attempts.append(Attempt(query=query))
        return LoopOutcome(True, query, result, attempts)
    last = attempts[-1].error if attempts else "no attempts"
    return LoopOutcome(False, "", None, attempts, f"no valid query after {len(attempts)} attempts: {last}")


class Answerer:
    """Turns a query result into a short answer. Falls back to a plain sentence if the LLM fails."""

    def __init__(self, llm: LLM):
        self.llm = llm

    def compose(self, question: str, result: QueryResult) -> str:
        records = result.records()
        try:
            text = self.llm.complete(prompts.ANSWER,
                                     prompts.wrap_question(question, result=prompts.result_block(records)))
        except LLMError:
            text = ""
        if not text.strip():
            from ..llm.offline import summarize

            text = summarize(question, records)
        if result.truncated:
            text += f" (Only the first {len(result.rows)} rows were returned.)"
        return text.strip()
