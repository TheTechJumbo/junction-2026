"""Pinned teammate source is exercised over real loopback HTTP, never GPU/SSH.

Run after `git fetch origin`:
    .venv/bin/python -m pytest -q -m integration backend/tests/api/test_actual_producers.py
These checks skip if the pinned source objects are absent; they do not fetch implicitly.
"""
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer

import httpx
import pytest
import uvicorn
from PIL import Image

from backend.app import Settings, create_app
from backend.integrations.vision_relay import AuthenticatedEventPoster, load_relay

STORE_COMMIT = "98eb82b"
DEMO_COMMIT = "804b821"
RELAY_COMMIT = "9aaf1e3"
TOKEN = "synthetic-producer-integration-only-token"
pytestmark = pytest.mark.integration


def source(commit, path):
    result = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True)
    if result.returncode:
        if os.environ.get("REQUIRE_SOURCE_FIXTURES") == "1":
            pytest.fail(f"Required pinned teammate source {commit}:{path} is unavailable")
        pytest.skip(f"Pinned teammate source {commit}:{path} is unavailable; fetch origin first")
    return result.stdout


@contextmanager
def central_server(tmp_path):
    settings = Settings(api_token=TOKEN, local_data_dir=tmp_path / "central")
    app = create_app(settings, clock=lambda: datetime(2026, 11, 14, 12, tzinfo=UTC))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}",
                          headers={"Authorization": f"Bearer {TOKEN}"}, trust_env=False) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive()


@contextmanager
def python_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_actual_ayman_demo_script_and_store_export(tmp_path):
    replay = tmp_path / "replay.py"
    replay.write_bytes(source(DEMO_COMMIT, "demo/ayman/replay.py"))
    fixture = source(DEMO_COMMIT, "demo/ayman/events.json")
    replay.with_name("events.json").write_bytes(fixture)
    store_path = tmp_path / "store.py"
    store_path.write_bytes(source(STORE_COMMIT, "unoq/store.py"))
    spec = importlib.util.spec_from_file_location("actual_ayman_store", store_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = module.Store(tmp_path / "uno", reserve=0)
    with central_server(tmp_path) as central, python_server(module.handler(store, TOKEN)) as uno:
        for original in json.loads(fixture):
            response = uno.post("/events", json=original, headers={"Authorization": f"Bearer {TOKEN}"})
            assert response.status_code == 200
        export = uno.get("/export/events", headers={"Authorization": f"Bearer {TOKEN}"}).json()
        assert all(event["evidence_url"] is None for event in export["events"])
        # Export is an envelope; POST /events deliberately accepts only an event or array.
        assert central.post("/events", json=export).status_code == 400
        assert central.post("/events", json=export["events"]).status_code == 200
        result = subprocess.run([sys.executable, str(replay), "--url", str(central.base_url)],
                                env={**os.environ, "UNOQ_TOKEN": TOKEN}, capture_output=True,
                                text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        page = central.get("/events").json()
        assert len(page["events"]) == 3  # actual demo script retries the same producer IDs
        assert central.post("/ask", json={"question": "Did I take my pills?"}).status_code == 200
        assert "cannot confirm" in central.post("/ask", json={"question": "Did I take my pills?"}).json()["answer"].lower()


def test_actual_seb_relay_http_check_through_authenticated_wrapper(tmp_path, monkeypatch):
    relay_path = tmp_path / "relay.py"
    relay_path.write_bytes(source(RELAY_COMMIT, "ml/recon_change/live/relay.py"))
    evidence = tmp_path / "relay-evidence"
    evidence.mkdir()
    bundle = io.BytesIO()
    picture = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(picture, "JPEG")
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("now.jpg", picture.getvalue())
    class Gateway:
        calls = []
        def call(self, request, blob=b""):
            self.calls.append(request["op"])
            assert request["op"] == "check"
            return {"ok": True, "job": "synthetic-job", "result": {
                "verdict": "PASS synthetic fixture", "appeared": [{"size_cm_approx": [10, 20, 30]}],
                "gone": []}}, bundle.getvalue()
    gateway = Gateway()
    with central_server(tmp_path) as central:
        destination = str(central.base_url).rstrip("/") + "/events"
        raw_spec = importlib.util.spec_from_file_location("actual_unwrapped_relay", relay_path)
        raw_relay = importlib.util.module_from_spec(raw_spec)
        raw_spec.loader.exec_module(raw_relay)
        no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        monkeypatch.setattr(urllib.request, "urlopen", no_proxy_opener.open)
        unwrapped = raw_relay.to_events({"verdict": "PASS", "appeared": [
            {"size_cm_approx": [10, 20, 30]}], "gone": []}, "kitchen", {})
        with pytest.raises(urllib.error.HTTPError) as rejected:
            raw_relay.post_events(destination, unwrapped)
        assert rejected.value.code == 401
        relay, poster = load_relay(relay_path, event_api_url=destination, token=TOKEN)
        try:
            handler = relay.make_handler(gateway, evidence, "synthetic-relay-key", destination, pick=None)
            with python_server(handler) as client:
                response = client.post("/check?zone=kitchen", content=b"PKsynthetic-frames",
                                       headers={"X-Key": "synthetic-relay-key"})
                assert response.status_code == 200
                report = response.json()
                assert report["posted_to_event_api"] == 200
                assert report["events"][0]["evidence_url"] == "/evidence/synthetic-job/now.jpg"
                events = central.get("/events").json()["events"]
                assert len(events) == 1
                assert events[0]["source"] == "vision"
                assert events[0]["id"].startswith("relay_")
                assert events[0]["evidence_url"] is None
                assert "size_cm" not in events[0]
                assert central.get("/timeline").json()["alerts"] == []
                # Exact original relay payload retries deduplicate at the central API.
                assert relay.post_events(destination, report["events"]) == 200
                assert len(central.get("/events").json()["events"]) == 1
                assert gateway.calls == ["check"]
                bad_poster = AuthenticatedEventPoster(destination, "synthetic-wrong-token")
                relay.post_events = bad_poster
                try:
                    failed = client.post("/check?zone=kitchen", content=b"PKsynthetic-frames",
                                         headers={"X-Key": "synthetic-relay-key"}).json()
                    assert failed["posted_to_event_api"].startswith("failed:")
                    assert "synthetic-wrong-token" not in failed["posted_to_event_api"]
                    assert len(central.get("/events").json()["events"]) == 1
                finally:
                    bad_poster.close()
        finally:
            poster.close()
