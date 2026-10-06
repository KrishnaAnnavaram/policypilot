import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from policypilot.api import create_app  # noqa: E402


def test_ask_requires_json_and_token(make_service, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret-token")
    client = TestClient(create_app(make_service()))
    assert client.post("/ask", json={"question": "What is the deductible?"}).status_code == 401
    ok = client.post("/ask", json={"question": "What is the standard deductible?"},
                     headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200 and ok.json()["route"] == "pdf"
    form = client.post("/ask", content="question=x", headers={"Authorization": "Bearer secret-token",
                                                              "Content-Type": "text/plain"})
    assert form.status_code in (415, 422)
