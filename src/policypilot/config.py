"""Runtime configuration, read once from environment variables.

The Streamlit UI, the HTTP API, the CLI and the evaluation harness all build their
service from :func:`Settings.from_env`, so evaluation always measures the same
configuration that ships (no second set of credentials, prompts or parsers).
Secrets are only ever read from the environment; nothing here has a default password.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value.strip() if value is not None else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"environment variable {name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"environment variable {name} must be a number, got {raw!r}") from exc


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env(name)
    if not raw:
        return default
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def load_dotenv_file(path: str | os.PathLike = ".env") -> None:
    """Minimal ``.env`` reader (KEY=VALUE lines). Existing variables are never overwritten."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    # LLM (any OpenAI-compatible chat-completions endpoint, e.g. Groq or OpenAI)
    llm_provider: str = "offline"            # "openai" (compatible HTTP API) or "offline"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = "llama-3.3-70b-versatile"
    llm_timeout_s: float = 30.0

    # Relational back end: a SQLAlchemy-style URL is not needed; we take a DSN or a SQLite path
    sql_backend: str = "sqlite"              # "sqlite" or "postgres"
    sqlite_path: str = "data/policypilot.db"
    postgres_dsn: str = field(default="", repr=False)   # must point at a READ-ONLY role
    sql_dialect: str = "postgres"            # dialect the LLM is asked to write
    sql_allowed_tables: tuple[str, ...] = ("customers", "vehicles", "claims")

    # Document back end
    doc_backend: str = "memory"              # "memory" or "mongo"
    mongo_uri: str = field(default="", repr=False)      # must use a read-only user
    mongo_db: str = "policypilot"
    mongo_collection: str = "policies"

    # RAG
    docs_dir: str = ""                       # extra policy documents to index at start-up
    embedder: str = "hashing"                # "hashing" (offline) or "sentence-transformers"
    embedding_model: str = "intfloat/multilingual-e5-small"
    rag_top_k: int = 4

    # Guard rails
    max_rows: int = 200
    query_timeout_s: float = 5.0
    max_attempts: int = 3
    max_question_chars: int = 1000
    router_min_confidence: float = 0.5

    @classmethod
    def from_env(cls, dotenv: str | None = ".env") -> "Settings":
        if dotenv:
            load_dotenv_file(dotenv)
        provider = _env("LLM_PROVIDER", "")
        api_key = _env("LLM_API_KEY")
        if not provider:
            provider = "openai" if api_key else "offline"
        return cls(
            llm_provider=provider.lower(),
            llm_base_url=_env("LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=api_key,
            llm_model=_env("LLM_MODEL", cls.llm_model),
            llm_timeout_s=_env_float("LLM_TIMEOUT_S", cls.llm_timeout_s),
            sql_backend=_env("SQL_BACKEND", cls.sql_backend).lower(),
            sqlite_path=_env("SQLITE_PATH", cls.sqlite_path),
            postgres_dsn=_env("POSTGRES_DSN"),
            sql_dialect=_env("SQL_DIALECT", cls.sql_dialect).lower(),
            sql_allowed_tables=_env_list("SQL_ALLOWED_TABLES", cls.sql_allowed_tables),
            doc_backend=_env("DOC_BACKEND", cls.doc_backend).lower(),
            mongo_uri=_env("MONGO_URI"),
            mongo_db=_env("MONGO_DB", cls.mongo_db),
            mongo_collection=_env("MONGO_COLLECTION", cls.mongo_collection),
            docs_dir=_env("DOCS_DIR"),
            embedder=_env("EMBEDDER", cls.embedder).lower(),
            embedding_model=_env("EMBEDDING_MODEL", cls.embedding_model),
            rag_top_k=_env_int("RAG_TOP_K", cls.rag_top_k),
            max_rows=_env_int("MAX_ROWS", cls.max_rows),
            query_timeout_s=_env_float("QUERY_TIMEOUT_S", cls.query_timeout_s),
            max_attempts=_env_int("MAX_ATTEMPTS", cls.max_attempts),
            max_question_chars=_env_int("MAX_QUESTION_CHARS", cls.max_question_chars),
            router_min_confidence=_env_float("ROUTER_MIN_CONFIDENCE", cls.router_min_confidence),
        )

    def validate(self) -> None:
        if self.llm_provider not in {"openai", "offline"}:
            raise ValueError(f"LLM_PROVIDER must be 'openai' or 'offline', got {self.llm_provider!r}")
        if self.llm_provider == "openai" and not self.llm_api_key:
            raise ValueError("LLM_PROVIDER=openai requires LLM_API_KEY")
        if self.sql_backend not in {"sqlite", "postgres"}:
            raise ValueError(f"SQL_BACKEND must be 'sqlite' or 'postgres', got {self.sql_backend!r}")
        if self.sql_backend == "postgres" and not self.postgres_dsn:
            raise ValueError("SQL_BACKEND=postgres requires POSTGRES_DSN (use a read-only role)")
        if self.doc_backend not in {"memory", "mongo"}:
            raise ValueError(f"DOC_BACKEND must be 'memory' or 'mongo', got {self.doc_backend!r}")
        if self.doc_backend == "mongo" and not self.mongo_uri:
            raise ValueError("DOC_BACKEND=mongo requires MONGO_URI (use a read-only user)")
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("MAX_ATTEMPTS must be between 1 and 5")
        if not 1 <= self.max_rows <= 10_000:
            raise ValueError("MAX_ROWS must be between 1 and 10000")
        if self.query_timeout_s <= 0:
            raise ValueError("QUERY_TIMEOUT_S must be positive")
