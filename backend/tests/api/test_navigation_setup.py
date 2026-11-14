import json
import os
import subprocess
import sys
import threading
import time
import types
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from backend.run import NAVIGATION_ENV, create_application, navigation_from_env

TOKEN = "synthetic-navigation-key"
RELAY_REF = "9aaf1e377e50bd0faeece3c4582a48b17eb976b8"


def configuration(origin="http://127.0.0.1:8090"):
    return dict(VISION_RELAY_URL=origin, VISION_RELAY_KEY=TOKEN, VISION_RELAY_ZONE="kitchen",
                NAVIGATION_MAX_AGE_SECONDS="5", NAVIGATION_MIN_CONFIDENCE="0.8")


@pytest.fixture
def app_environment(monkeypatch, tmp_path):
    for key in (*NAVIGATION_ENV, "DATABASE_URL", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("API_TOKEN", "synthetic-central-token")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path / "central"))


def configure(monkeypatch, origin):
    for key, value in configuration(origin).items():
        monkeypatch.setenv(key, value)


def test_no_config_is_offline_by_default(app_environment, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unconfigured entrypoint must not construct a navigation adapter")
    monkeypatch.setattr("backend.answer_engine.adapters.CachedNavigationAdapter", forbidden)
    with TestClient(create_application()) as client:
        client.headers["Authorization"] = "Bearer synthetic-central-token"
        assert client.app.state.engine.navigation_adapter is None
        assert "reliable current location" in client.post("/ask", json={"question": "Where am I?"}).json()["answer"]
        assert "cannot confirm" in client.post("/ask", json={"question": "Did I take my pills?"}).json()["answer"]


@pytest.mark.parametrize("missing", NAVIGATION_ENV)
def test_partial_settings_stop_startup_without_exposing_values(app_environment, monkeypatch, missing):
    for key, value in configuration().items():
        if key != missing:
            monkeypatch.setenv(key, value)
    with pytest.raises(ValueError) as error:
        create_application()
    assert "all five settings" in str(error.value)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(("key", "value"), [
    ("VISION_RELAY_URL", "http://192.168.1.2:8090"),
    ("VISION_RELAY_URL", "http://127.0.0.1:99999"),
    ("VISION_RELAY_URL", "https://synthetic-navigation-key@host.invalid"),
    ("VISION_RELAY_URL", "https://host.invalid?key=synthetic-navigation-key"),
    ("VISION_RELAY_URL", "https://host.invalid/path"),
    ("VISION_RELAY_URL", "https://host.invalid\n"),
    ("VISION_RELAY_KEY", "synthetic-navigation-key\n"),
    ("VISION_RELAY_ZONE", "../kitchen"),
    ("NAVIGATION_MAX_AGE_SECONDS", "0"),
    ("NAVIGATION_MAX_AGE_SECONDS", "nan"),
    ("NAVIGATION_MAX_AGE_SECONDS", "inf"),
    ("NAVIGATION_MIN_CONFIDENCE", "-0.1"),
    ("NAVIGATION_MIN_CONFIDENCE", "1.1"),
    ("NAVIGATION_MIN_CONFIDENCE", "nan"),
    ("NAVIGATION_MIN_CONFIDENCE", "synthetic-navigation-key"),
])
def test_invalid_settings_are_sanitized(key, value):
    settings = configuration()
    settings[key] = value
    with pytest.raises(ValueError) as error:
        navigation_from_env(settings)
    assert str(error.value) == "Invalid cached navigation configuration; check the five settings"
    assert error.value.__suppress_context__


def test_explicit_policy_is_preserved_and_empty_values_disable():
    settings = configuration()
    settings.update(NAVIGATION_MAX_AGE_SECONDS="1.25", NAVIGATION_MIN_CONFIDENCE="0")
    adapter = navigation_from_env(settings)
    assert adapter.policy.max_age_s == 1.25
    assert adapter.policy.min_confidence == 0
    assert navigation_from_env(dict.fromkeys(NAVIGATION_ENV, "")) is None


@contextmanager
def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def exercise_entrypoint(monkeypatch, origin, calls):
    configure(monkeypatch, origin)
    with TestClient(create_application()) as client:
        client.headers["Authorization"] = "Bearer synthetic-central-token"
        assert calls == []  # setup is passive
        for question in ("Did I take my pills?", "Did I lock the front door?"):
            response = client.post("/ask", json={"question": question})
            assert response.status_code == 200
            assert "cannot confirm" in response.json()["answer"]
        assert calls == []
        result = client.post("/ask", json={"question": "Where is the bathroom?"}).json()
        assert "2.5 metres to your left" in result["answer"]
        assert result["evidence"] == []
        assert calls == ["/where?zone=kitchen"]
        for question in ("Did I take my pills?", "Did I lock the front door?"):
            assert client.post("/ask", json={"question": question}).status_code == 200
        assert calls == ["/where?zone=kitchen"]


def test_assembled_entrypoint_only_reads_cached_http(monkeypatch, app_environment):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            assert urlsplit(self.path).path == "/where"
            assert parse_qs(urlsplit(self.path).query) == {"zone": ["kitchen"]}
            assert self.headers.get("X-Key") == TOKEN
            body = json.dumps({"state": "found", "zone": "kitchen", "age_s": .1,
                               "confidence": .95, "waypoints": [
                                   {"name": "bathroom", "distance_m": 2.5, "bearing_deg": -45}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    with serve(Handler) as origin:
        exercise_entrypoint(monkeypatch, origin, calls)


@pytest.mark.integration
def test_assembled_entrypoint_actual_relay_cache(monkeypatch, app_environment, tmp_path):
    result = subprocess.run(["git", "show", f"{RELAY_REF}:ml/recon_change/live/relay.py"],
                            capture_output=True, text=True)
    if result.returncode:
        if os.environ.get("REQUIRE_SOURCE_FIXTURES") == "1":
            pytest.fail("Required actual relay source is unavailable")
        pytest.skip("Pinned actual relay source unavailable; fetch origin first")
    relay = types.ModuleType("actual_navigation_relay")
    relay.__file__ = str(tmp_path / "relay.py")
    prior_path = list(sys.path)
    try:
        exec(compile(result.stdout, relay.__file__, "exec"), relay.__dict__)
    finally:
        sys.path[:] = prior_path
    class NoGateway:
        def call(self, *args, **kwargs):
            pytest.fail("Cached navigation must never contact a GPU/gateway")
    fix = dict(state="found", zone="kitchen", confidence=.95, at=time.time(),
               ts="2026-11-14T10:00:00Z", waypoints=[
                   dict(name="bathroom", distance_m=2.5, bearing_deg=-45)])
    (tmp_path / "where.json").write_text(json.dumps({"kitchen": fix}))
    calls = []
    handler = relay.make_handler(NoGateway(), tmp_path, TOKEN, pick=None)
    class RecordingHandler(handler):
        def do_GET(self):
            calls.append(self.path)
            assert self.headers.get("X-Key") == TOKEN
            return super().do_GET()
        def log_message(self, *args):
            pass
    with serve(RecordingHandler) as origin:
        exercise_entrypoint(monkeypatch, origin, calls)
