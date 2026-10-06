import httpx
import pytest

from policypilot.config import Settings, load_dotenv_file
from policypilot.llm.base import LLMError
from policypilot.llm.openai_compat import OpenAICompatibleLLM

ENV_VARS = ["LLM_PROVIDER", "LLM_API_KEY", "SQL_BACKEND", "POSTGRES_DSN", "MAX_ROWS", "SQL_ALLOWED_TABLES"]


@pytest.fixture()
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_are_offline_and_secret_free(clean_env):
    s = Settings.from_env(dotenv=None)
    assert s.llm_provider == "offline" and s.llm_api_key == "" and s.postgres_dsn == ""
    s.validate()
    assert "api_key" not in repr(s) and "postgres_dsn" not in repr(s)


def test_api_key_switches_provider_and_lists_parse(clean_env):
    clean_env.setenv("LLM_API_KEY", "test-key")
    clean_env.setenv("SQL_ALLOWED_TABLES", "Customers, claims")
    s = Settings.from_env(dotenv=None)
    assert s.llm_provider == "openai" and s.sql_allowed_tables == ("customers", "claims")


@pytest.mark.parametrize("env,message", [
    ({"SQL_BACKEND": "postgres"}, "POSTGRES_DSN"),
    ({"LLM_PROVIDER": "openai"}, "LLM_API_KEY"),
    ({"MAX_ROWS": "0"}, "MAX_ROWS"),
])
def test_invalid_configuration_fails_fast(clean_env, env, message):
    for k, v in env.items():
        clean_env.setenv(k, v)
    with pytest.raises(ValueError, match=message):
        Settings.from_env(dotenv=None).validate()


def test_dotenv_never_overrides_existing_variables(clean_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MAX_ROWS=7\nLLM_PROVIDER=offline\n# comment\n", encoding="utf-8")
    clean_env.setenv("MAX_ROWS", "9")
    load_dotenv_file(env_file)
    import os

    assert os.environ["MAX_ROWS"] == "9" and os.environ["LLM_PROVIDER"] == "offline"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openai_client_success_and_json_mode():
    seen = {}

    def handler(request):
        seen["body"] = request.read()
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    llm = OpenAICompatibleLLM("http://llm.test/v1", "k", "m", client=_client(handler))
    assert llm.complete("s", "u", json_mode=True) == "hello"
    assert b"json_object" in seen["body"] and seen["auth"] == "Bearer k"


def test_openai_client_retries_are_bounded(monkeypatch):
    monkeypatch.setattr("policypilot.llm.openai_compat.time.sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    llm = OpenAICompatibleLLM("http://llm.test/v1", "k", "m", max_retries=2, client=_client(handler))
    with pytest.raises(LLMError, match="503"):
        llm.complete("s", "u")
    assert len(calls) == 3


def test_openai_client_does_not_retry_client_errors():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    with pytest.raises(LLMError):
        OpenAICompatibleLLM("http://x/v1", "k", "m", client=_client(handler)).complete("s", "u")
    assert len(calls) == 1
