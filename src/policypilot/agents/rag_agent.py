"""Policy-document agent: (condense follow-up) -> hybrid retrieval -> cited answer."""
from __future__ import annotations

import re

from ..llm.base import LLM, LLMError
from ..rag.index import Hit, HybridIndex, merge_hits
from . import prompts
from .base import AgentAnswer, Source

_CITATION = re.compile(r"\[(\d+)\]")


def format_history(history: list[tuple[str, str]]) -> str:
    return "\n".join(f"User: {q}\nAssistant: {a[:400]}" for q, a in history)


class RAGAgent:
    route = "pdf"

    def __init__(self, llm: LLM, index: HybridIndex, top_k: int = 4):
        self.llm = llm
        self.index = index
        self.top_k = top_k

    def condense(self, question: str, history: list[tuple[str, str]]) -> str:
        if not history:
            return question
        try:
            text = self.llm.complete(prompts.CONDENSE,
                                     prompts.wrap_question(question, history=format_history(history)))
        except LLMError:
            return question
        text = text.strip().strip('"')
        return text if 0 < len(text) <= 1000 else question

    def retrieve(self, question: str, extra_indexes: list[HybridIndex] | None = None) -> list[Hit]:
        results = [self.index.search(question, self.top_k)]
        for extra in extra_indexes or []:
            results.append(extra.search(question, self.top_k))
        return merge_hits(results, self.top_k)

    def run(self, question: str, history: list[tuple[str, str]] | None = None,
            extra_indexes: list[HybridIndex] | None = None) -> AgentAnswer:
        standalone = self.condense(question, history or [])
        hits = self.retrieve(standalone, extra_indexes)
        if not hits:
            return AgentAnswer(self.route, False, text="No policy documents are indexed that match this question.",
                               error="no retrieval hits", query=standalone)
        sources = [Source(i + 1, h.chunk.source, h.chunk.text, h.score) for i, h in enumerate(hits)]
        context = "\n".join(f'<context id="{s.ref}" source="{s.source}">{s.text}</context>' for s in sources)
        try:
            text = self.llm.complete(prompts.RAG, prompts.wrap_question(standalone) + "\n" + context)
        except LLMError as exc:
            return AgentAnswer(self.route, False, text="The language model is unavailable.", sources=sources,
                               error=str(exc), query=standalone)
        cited = {int(n) for n in _CITATION.findall(text)}
        notes = []
        invalid = sorted(n for n in cited if not 1 <= n <= len(sources))
        if invalid:
            notes.append(f"answer cites unknown passages {invalid}")
        if not cited:
            notes.append("answer has no citations")
        return AgentAnswer(self.route, True, text=text.strip(), query=standalone, sources=sources, notes=notes)
