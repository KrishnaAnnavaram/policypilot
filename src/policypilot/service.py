"""The QA service and the ONE factory that wires it from :class:`~policypilot.config.Settings`.

The UI, the API, the CLI and the evaluation harness all call :func:`build_service`, so
the evaluation exercises exactly the prompts, parsers, validators and connections
that users get.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .agents import (AgentAnswer, Answerer, KeywordRouter, LLMRouter, MongoAgent, Planner, RAGAgent,
                     RouteDecision, SQLAgent)
from .backends.base import DocumentStore, SQLExecutor
from .config import Settings
from .llm.base import LLM
from .memory import SessionStore, is_valid_session_id, new_session_id
from .rag.index import HybridIndex
from .safety.mongo_guard import PipelinePolicy
from .safety.sql_guard import SQLPolicy

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_question(question: str, max_chars: int) -> str:
    text = unicodedata.normalize("NFKC", question or "")
    text = _CONTROL.sub(" ", text).strip()
    text = text.replace("<", "‹").replace(">", "›")   # cannot close our <question> delimiters
    if not text:
        raise ValueError("the question is empty")
    if len(text) > max_chars:
        raise ValueError(f"the question is longer than {max_chars} characters")
    return text


@dataclass
class Response:
    question: str
    session_id: str
    decision: RouteDecision
    answer: AgentAnswer
    extra: dict = field(default_factory=dict)

    def to_dict(self, max_rows: int = 50) -> dict:
        a = self.answer
        return {
            "session_id": self.session_id,
            "route": self.decision.route,
            "route_confidence": self.decision.confidence,
            "route_source": self.decision.source,
            "ok": a.ok,
            "answer": a.text,
            "query": a.query,
            "columns": a.result.columns if a.result else [],
            "rows": a.result.records()[:max_rows] if a.result else [],
            "truncated": bool(a.result and a.result.truncated),
            "sources": [{"ref": s.ref, "source": s.source, "text": s.text} for s in a.sources],
            "attempts": len(a.attempts),
            "notes": a.notes,
            "error": a.error,
        }


class QAService:
    def __init__(self, router, sql_agent: SQLAgent, mongo_agent: MongoAgent, planner: Planner,
                 rag_agent: RAGAgent, sessions: SessionStore | None = None, max_question_chars: int = 1000):
        self.router = router
        self.sql_agent = sql_agent
        self.mongo_agent = mongo_agent
        self.planner = planner
        self.rag_agent = rag_agent
        self.sessions = sessions or SessionStore()
        self.max_question_chars = max_question_chars

    def answer(self, question: str, route: str, history=None, extra_indexes=None) -> AgentAnswer:
        if route == "sql":
            return self.sql_agent.run(question)
        if route == "nosql":
            return self.mongo_agent.run(question)
        if route == "both":
            return self.planner.run(question)
        return self.rag_agent.run(question, history=history, extra_indexes=extra_indexes)

    def ask(self, question: str, session_id: str | None = None,
            extra_indexes: list[HybridIndex] | None = None) -> Response:
        if not is_valid_session_id(session_id):
            session_id = new_session_id()
        try:
            text = clean_question(question, self.max_question_chars)
        except ValueError as exc:
            decision = RouteDecision("none", 0.0, "rejected input", "input-check")
            return Response(question, session_id, decision, AgentAnswer("none", False, text=str(exc), error=str(exc)))
        decision = self.router.route(text)
        history = self.sessions.history(session_id)
        answer = self.answer(text, decision.route, history=history, extra_indexes=extra_indexes)
        self.sessions.add(session_id, text, answer.text)
        return Response(text, session_id, decision, answer)


def ensure_demo_database(path: str | Path, n_customers: int = 300, seed: int = 7) -> Path:
    """Create the synthetic SQLite database if it does not exist yet."""
    path = Path(path)
    if not path.exists():
        from .data.seed import write_sqlite
        from .data.synthetic import generate

        write_sqlite(generate(n_customers=n_customers, seed=seed), path)
    return path


def build_backends(settings: Settings, auto_seed: bool = True) -> tuple[SQLExecutor, DocumentStore]:
    if settings.sql_backend == "postgres":
        from .backends.sql import PostgresExecutor

        executor: SQLExecutor = PostgresExecutor(settings.postgres_dsn, settings.query_timeout_s)
    else:
        from .backends.sql import SQLiteExecutor

        if auto_seed:
            ensure_demo_database(settings.sqlite_path)
        executor = SQLiteExecutor(settings.sqlite_path, settings.sql_allowed_tables, settings.query_timeout_s)
    if settings.doc_backend == "mongo":
        from .backends.docstore import MongoDocStore

        store: DocumentStore = MongoDocStore(settings.mongo_uri, settings.mongo_db, settings.mongo_collection,
                                             settings.query_timeout_s)
    else:
        from .backends.docstore import InMemoryDocStore
        from .data.seed import build_documents, read_sqlite

        if settings.sql_backend != "sqlite":
            raise ValueError("DOC_BACKEND=memory builds its documents from the SQLite database; "
                             "use DOC_BACKEND=mongo with SQL_BACKEND=postgres")
        store = InMemoryDocStore(build_documents(read_sqlite(settings.sqlite_path)))
    return executor, store


def build_index(settings: Settings) -> HybridIndex:
    from .rag.embeddings import build_embedder
    from .rag.loaders import index_demo_documents, index_path

    index = HybridIndex(build_embedder(settings.embedder, settings.embedding_model))
    index_demo_documents(index)
    if settings.docs_dir:
        index_path(index, settings.docs_dir)
    return index


def build_service(settings: Settings | None = None, llm: LLM | None = None,
                  executor: SQLExecutor | None = None, store: DocumentStore | None = None,
                  index: HybridIndex | None = None, auto_seed: bool = True) -> QAService:
    settings = settings or Settings.from_env()
    settings.validate()
    if llm is None:
        from .llm import build_llm

        llm = build_llm(settings)
    if executor is None or store is None:
        default_executor, default_store = build_backends(settings, auto_seed=auto_seed)
        executor = executor or default_executor
        store = store or default_store
    index = index if index is not None else build_index(settings)

    sql_policy = SQLPolicy.from_schema(settings.sql_allowed_tables, max_rows=settings.max_rows,
                                       read_dialect=settings.sql_dialect, write_dialect=executor.dialect)
    pipeline_policy = PipelinePolicy(max_rows=settings.max_rows)
    answerer = Answerer(llm)
    sql_agent = SQLAgent(llm, executor, sql_policy, settings.max_attempts, answerer)
    mongo_agent = MongoAgent(llm, store, pipeline_policy, settings.max_attempts, answerer)
    router = LLMRouter(llm, KeywordRouter(), settings.router_min_confidence)
    return QAService(
        router=router,
        sql_agent=sql_agent,
        mongo_agent=mongo_agent,
        planner=Planner(llm, sql_agent, mongo_agent, answerer=answerer),
        rag_agent=RAGAgent(llm, index, settings.rag_top_k),
        max_question_chars=settings.max_question_chars,
    )
