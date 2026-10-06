from __future__ import annotations

import pytest

from policypilot.backends.docstore import InMemoryDocStore
from policypilot.backends.sql import SQLiteExecutor
from policypilot.config import Settings
from policypilot.data.seed import build_documents, write_sqlite
from policypilot.data.synthetic import generate
from policypilot.llm.offline import OfflineLLM
from policypilot.rag.index import HybridIndex
from policypilot.rag.loaders import index_demo_documents
from policypilot.safety.sql_guard import SQLPolicy
from policypilot.service import build_service

TABLES = ("customers", "vehicles", "claims")


@pytest.fixture(scope="session")
def dataset():
    return generate(n_customers=120, seed=3)


@pytest.fixture(scope="session")
def db_path(dataset, tmp_path_factory):
    return write_sqlite(dataset, tmp_path_factory.mktemp("db") / "test.db")


@pytest.fixture()
def executor(db_path):
    return SQLiteExecutor(db_path, TABLES, timeout_s=2.0)


@pytest.fixture()
def store(dataset):
    return InMemoryDocStore(build_documents(dataset))


@pytest.fixture()
def sql_policy():
    return SQLPolicy.from_schema(TABLES, max_rows=50, read_dialect="postgres", write_dialect="sqlite")


@pytest.fixture(scope="session")
def demo_index():
    index = HybridIndex()
    index_demo_documents(index)
    return index


@pytest.fixture()
def settings(db_path):
    return Settings(llm_provider="offline", sqlite_path=str(db_path), max_rows=50)


@pytest.fixture()
def make_service(settings, demo_index):
    def _make(llm=None, **kwargs):
        return build_service(settings, llm=llm or OfflineLLM(), index=demo_index, auto_seed=False, **kwargs)

    return _make
