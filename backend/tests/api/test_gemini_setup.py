import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.run import GEMINI_ENV, NAVIGATION_ENV, create_application, gemini_from_env

KEY = "synthetic-provider-key"
MODEL = "synthetic-test-model"


@pytest.fixture
def environment(monkeypatch, tmp_path):
    for name in (*GEMINI_ENV, *NAVIGATION_ENV, "DATABASE_URL", "ELEVENLABS_API_KEY",
                 "ELEVENLABS_VOICE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_TOKEN", "synthetic-api-token")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path / "central"))


def provider(monkeypatch, handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        kwargs["transport"] = transport
        return real_client(**kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client)


def configure_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("GEMINI_MODEL", MODEL)


def configure_navigation(monkeypatch):
    for name, value in {
        "VISION_RELAY_URL": "http://127.0.0.1:8090", "VISION_RELAY_KEY": "synthetic-relay-key",
        "VISION_RELAY_ZONE": "kitchen", "NAVIGATION_MAX_AGE_SECONDS": "5",
        "NAVIGATION_MIN_CONFIDENCE": "0.8",
    }.items():
        monkeypatch.setenv(name, value)


def test_missing_configuration_uses_offline_fallback(environment, monkeypatch):
    def no_network(request):
        pytest.fail("Unconfigured Gemini must never make a provider request")
    provider(monkeypatch, no_network)
    with TestClient(create_application()) as client:
        client.headers["Authorization"] = "Bearer synthetic-api-token"
        assert client.app.state.engine.open_adapter is None
        result = client.post("/ask", json={"question": "Tell me a friendly greeting"}).json()
        assert result == {"answer": "I do not have enough information to answer that question.", "evidence": []}
    assert gemini_from_env({"GEMINI_API_KEY": "", "GEMINI_MODEL": ""}) is None


@pytest.mark.parametrize("present", GEMINI_ENV)
def test_partial_configuration_fails_without_calls(environment, monkeypatch, present):
    monkeypatch.setenv(present, KEY if present == "GEMINI_API_KEY" else MODEL)
    calls = []
    provider(monkeypatch, lambda request: calls.append(request))
    with pytest.raises(ValueError) as error:
        create_application()
    assert str(error.value) == "Open-question Gemini requires both GEMINI_API_KEY and GEMINI_MODEL"
    assert KEY not in str(error.value)
    assert calls == []


@pytest.mark.parametrize(("name", "value"), [
    ("GEMINI_API_KEY", KEY + "\n"), ("GEMINI_API_KEY", "non ascii ü"),
    ("GEMINI_API_KEY", "contains space"), ("GEMINI_MODEL", "models/model-name"),
    ("GEMINI_MODEL", "https://provider.invalid/model?key=" + KEY),
    ("GEMINI_MODEL", "bad-model\n"), ("GEMINI_MODEL", "x" * 129),
])
def test_invalid_configuration_is_sanitized(name, value):
    settings = {"GEMINI_API_KEY": KEY, "GEMINI_MODEL": MODEL, name: value}
    with pytest.raises(ValueError) as error:
        gemini_from_env(settings)
    assert str(error.value) == "Invalid open-question Gemini configuration; check GEMINI_API_KEY and GEMINI_MODEL"
    assert error.value.__suppress_context__


def test_assembled_adapters_coexist_and_only_open_question_goes_to_gemini(environment, monkeypatch):
    configure_gemini(monkeypatch)
    configure_navigation(monkeypatch)
    calls = []
    def serve(request):
        calls.append(request)
        if request.url.host == "127.0.0.1":
            assert request.method == "GET" and request.url.path == "/where"
            assert request.headers["X-Key"] == "synthetic-relay-key"
            assert "x-goog-api-key" not in request.headers
            return httpx.Response(200, json={"state": "found", "zone": "kitchen", "age_s": 0,
                                             "confidence": .95, "waypoints": []})
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.method == "POST"
        assert request.url.path == f"/v1beta/models/{MODEL}:generateContent"
        assert request.headers["x-goog-api-key"] == KEY
        assert not request.url.query
        payload = json.loads(request.content)
        assert payload["contents"] == [{"role": "user", "parts": [{"text": "Tell me a friendly greeting"}]}]
        assert "private-synthetic-event" not in request.content.decode()
        assert "evidence_url" not in request.content.decode()
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": "Hello, I hope your day is pleasant."}]}}]})
    provider(monkeypatch, serve)
    app = create_application()
    assert calls == []
    assert app.state.engine.open_adapter.model == MODEL
    assert app.state.engine.navigation_adapter is not None
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer synthetic-api-token"
        assert calls == []  # neither construction nor lifespan probes providers
        result = client.post("/events", json={"id": "private-synthetic-event", "type": "pillbox_open",
            "source": "sensor", "ts": datetime.now(UTC).isoformat(), "zone": "kitchen",
            "object": "pillbox", "confidence": 1, "evidence_url": None})
        assert result.status_code == 200
        for question in ("Did I take my pills?", "Did I lock the door?"):
            assert "cannot confirm" in client.post("/ask", json={"question": question}).json()["answer"]
        assert calls == []
        navigation = client.post("/ask", json={"question": "Where am I?"}).json()
        assert "kitchen" in navigation["answer"]
        assert [request.url.host for request in calls] == ["127.0.0.1"]
        answer = client.post("/ask", json={"question": "Tell me a friendly greeting"}).json()
        assert answer == {"answer": "Hello, I hope your day is pleasant.", "evidence": []}
        assert [request.url.host for request in calls] == ["127.0.0.1", "generativelanguage.googleapis.com"]
        for question in ("Did I take my pills?", "Did I lock the door?", "Where is the bedroom?"):
            assert client.post("/ask", json={"question": question}).status_code == 200
        assert len(calls) == 2


def test_navigation_without_relay_never_falls_through_to_gemini(environment, monkeypatch):
    configure_gemini(monkeypatch)
    calls = []
    provider(monkeypatch, lambda request: calls.append(request))
    with TestClient(create_application()) as client:
        client.headers["Authorization"] = "Bearer synthetic-api-token"
        response = client.post("/ask", json={"question": "Where is the bathroom?"})
        assert "reliable current location" in response.json()["answer"]
    assert calls == []


@pytest.mark.parametrize("response", [httpx.Response(503, text="private provider failure"),
                                    httpx.Response(200, json={"candidates": []}),
                                    httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})])
def test_provider_failure_preserves_fixed_fallback(environment, monkeypatch, response):
    configure_gemini(monkeypatch)
    provider(monkeypatch, lambda request: response)
    with TestClient(create_application()) as client:
        client.headers["Authorization"] = "Bearer synthetic-api-token"
        result = client.post("/ask", json={"question": "Tell me a friendly greeting"})
        assert result.status_code == 200
        assert result.json() == {"answer": "I do not have enough information to answer that question.", "evidence": []}
        assert "private provider failure" not in result.text
