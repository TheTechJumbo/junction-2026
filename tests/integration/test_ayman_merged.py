"""Run the merged UNO Q store and assembled backend as real local processes.

These tests execute working-tree files, not pinned git objects or substitute handlers.
Only synthetic observations/images and disposable loopback services are used.
"""
import io
import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
UNO_TOKEN = "synthetic-merged-uno-token-at-least-32-characters"
API_TOKEN = "synthetic-merged-central-token-at-least-32-characters"
pytestmark = pytest.mark.integration


def clean_environment(tmp_path):
    env = dict(os.environ)
    for name in list(env):
        if (name.startswith(("GEMINI_", "VISION_", "NAVIGATION_", "ELEVENLABS_", "FACE_"))
                or name in {"DATABASE_URL", "TEST_DATABASE_URL", "API_TOKEN", "LOCAL_DATA_DIR",
                            "UNOQ_TOKEN", "UNOQ_DATA", "UNOQ_RETENTION_SECONDS", "ALLOWED_ORIGINS",
                            "SENSOR_IDS", "APP_TIMEZONE", "EVIDENCE_MODE", "HTTP_PROXY",
                            "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"}):
            env.pop(name, None)
    env.update(API_TOKEN=API_TOKEN, UNOQ_TOKEN=UNO_TOKEN,
               UNOQ_DATA=str(tmp_path / "uno"), LOCAL_DATA_DIR=str(tmp_path / "central"),
               APP_TIMEZONE="Europe/Helsinki", EVIDENCE_MODE="local_only")
    return env


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalProcess:
    def __init__(self, command, origin, token, env):
        self.command, self.origin, self.token, self.env = command, origin, token, env
        self.process = None

    def start(self):
        assert self.process is None
        self.process = subprocess.Popen(self.command, cwd=ROOT, env=self.env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        with httpx.Client(timeout=.2, trust_env=False) as client:
            while time.monotonic() < deadline:
                assert self.process.poll() is None, "Merged local service exited before readiness"
                try:
                    response = client.get(self.origin + "/health",
                                          headers={"Authorization": "Bearer " + self.token})
                    if response.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(.02)
        raise AssertionError("Merged local service did not become ready")

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None


@pytest.fixture
def merged_services(tmp_path):
    assert (ROOT / "unoq/store.py").is_file(), "The merged UNO Q store must be present"
    assert (ROOT / "backend/run.py").is_file(), "The assembled backend must be present"
    env = clean_environment(tmp_path)
    uno_port, api_port = unused_port(), unused_port()
    while api_port == uno_port:
        api_port = unused_port()
    uno_origin, api_origin = f"http://127.0.0.1:{uno_port}", f"http://127.0.0.1:{api_port}"
    uno_process = LocalProcess([sys.executable, str(ROOT / "unoq/store.py"),
                                "--host", "127.0.0.1", "--port", str(uno_port)],
                               uno_origin, UNO_TOKEN, env)
    api_process = LocalProcess([sys.executable, "-m", "uvicorn", "backend.run:create_application",
                                "--factory", "--host", "127.0.0.1", "--port", str(api_port),
                                "--log-level", "error", "--no-access-log"], api_origin, API_TOKEN, env)
    try:
        uno_process.start()
        api_process.start()
        with httpx.Client(base_url=uno_origin, headers={"Authorization": "Bearer " + UNO_TOKEN},
                          timeout=3, trust_env=False) as uno, httpx.Client(
                              base_url=api_origin, headers={"Authorization": "Bearer " + API_TOKEN},
                              timeout=3, trust_env=False) as api:
            yield SimpleNamespace(uno=uno, api=api, uno_process=uno_process,
                                  uno_origin=uno_origin, api_origin=api_origin,
                                  env=env, root=tmp_path)
    finally:
        uno_process.stop()
        api_process.stop()


def observation(event_id, **changes):
    return {"id": event_id, "type": "pillbox_open", "source": "sensor",
            "ts": datetime.now(UTC).isoformat(), "zone": "kitchen", "object": "pillbox",
            "confidence": 1, "evidence_url": None, **changes}


def import_cli(services, **env_changes):
    result = subprocess.run(
        [sys.executable, "-m", "backend.integrations.unoq_export",
         "--source", services.uno_origin, "--target", services.api_origin],
        cwd=ROOT, env={**services.env, **env_changes}, capture_output=True, text=True, timeout=15,
    )
    assert UNO_TOKEN not in result.stdout + result.stderr
    assert API_TOKEN not in result.stdout + result.stderr
    return result


def seed(services, event_id, **changes):
    event = observation(event_id, **changes)
    response = services.uno.post("/events", json=event)
    assert response.status_code == 200, response.text
    return event


def assert_central_empty(services):
    assert services.api.get("/events").json()["events"] == []
    assert services.api.get("/timeline").json()["events"] == []


def test_merged_store_image_stays_local_and_real_export_retries_deduplicate(merged_services):
    services = merged_services
    event = seed(services, "merged-store-photo-event",
                 ts=datetime.now(UTC).strftime("%Y%m%dT%H%M%S+0000"))
    image = io.BytesIO()
    Image.new("RGB", (4, 3), "blue").save(image, "PNG")
    raw = image.getvalue()
    uploaded = services.uno.post(f"/events/{event['id']}/evidence", content=raw,
                                 headers={"Content-Type": "image/png"})
    assert uploaded.status_code == 201
    local_url = uploaded.json()["evidence_url"]
    assert local_url.startswith("/artifacts/")
    assert services.uno.get(local_url).content == raw
    assert services.uno.get(local_url, headers={"Authorization": ""}).status_code == 401
    source_event = services.uno.get("/events").json()["events"][0]
    assert source_event["evidence_url"] == local_url
    exported = services.uno.get("/export/events").json()["events"]
    assert exported == [event]
    assert_central_empty(services)

    first = import_cli(services)
    assert first.returncode == 0, first.stderr
    assert "Transferred 1 scalar observations; no images copied." in first.stdout
    page = services.api.get("/events").json()
    assert len(page["events"]) == 1
    assert page["events"][0]["id"] == event["id"]
    assert datetime.fromisoformat(page["events"][0]["ts"].replace("Z", "+00:00")) == datetime.fromisoformat(event["ts"])
    assert page["events"][0]["evidence_url"] is None
    cursor = page["next_cursor"]
    second = import_cli(services)
    assert second.returncode == 0, second.stderr
    assert services.api.get("/events", params={"cursor": cursor}).json() == {
        "events": [], "next_cursor": cursor}
    assert len(services.api.get("/timeline").json()["events"]) == 1
    # The source artifact remains accessible only at the source service. The central
    # evidence table contains no copied blob, and no source artifact URL was published.
    assert services.uno.get(local_url).content == raw
    assert services.api.get(local_url).status_code == 404
    with sqlite3.connect(services.root / "central/events.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    assert all(item["evidence_url"] is None for item in services.api.get("/events").json()["events"])


def test_real_export_bad_source_and_target_auth_never_create_central_events(merged_services):
    services = merged_services
    seed(services, "merged-auth-event")
    bad_source = import_cli(services, UNOQ_TOKEN="wrong-synthetic-source-token")
    assert bad_source.returncode == 1
    assert "export was not available" in bad_source.stderr
    assert "wrong-synthetic-source-token" not in bad_source.stderr
    assert_central_empty(services)
    bad_target = import_cli(services, API_TOKEN="wrong-synthetic-target-token")
    assert bad_target.returncode == 1
    assert "did not accept" in bad_target.stderr
    assert "wrong-synthetic-target-token" not in bad_target.stderr
    assert_central_empty(services)
    assert len(services.uno.get("/events").json()["events"]) == 1
    recovered = import_cli(services)
    assert recovered.returncode == 0, recovered.stderr
    assert len(services.api.get("/events").json()["events"]) == 1


def test_source_restart_new_event_and_retry_reach_answers_and_dashboard(merged_services):
    services = merged_services
    first = seed(services, "merged-before-restart")
    assert import_cli(services).returncode == 0
    cursor = services.api.get("/events").json()["next_cursor"]
    services.uno_process.stop()
    unavailable = import_cli(services)
    assert unavailable.returncode == 1
    assert len(services.api.get("/events").json()["events"]) == 1
    services.uno_process.start()
    assert services.uno.get("/events").json()["events"] == [first]
    second = seed(services, "merged-after-restart")
    for _ in range(2):
        result = import_cli(services)
        assert result.returncode == 0, result.stderr
        assert "Transferred 2 scalar observations" in result.stdout
    delayed = services.api.get("/events", params={"cursor": cursor}).json()["events"]
    assert [event["id"] for event in delayed] == [second["id"]]
    timeline = services.api.get("/timeline").json()
    assert {event["id"] for event in timeline["events"]} == {first["id"], second["id"]}
    assert all(event["evidence_url"] is None for event in timeline["events"])
    answer = services.api.post("/ask", json={"question": "Did I take my pills?"}).json()
    assert "opened at" in answer["answer"]
    assert "cannot confirm" in answer["answer"]
    assert [event["id"] for event in answer["evidence"]] == [second["id"]]
    dashboard = services.api.get("/dashboard/")
    assert dashboard.status_code == 200
    assert "text/html" in dashboard.headers["content-type"]
    after_question = services.api.get("/timeline").json()
    assert after_question["questions"][0]["intent"] == "pills"
    assert after_question["questions"][0]["count"] == 1
    assert services.api.get("/summary").status_code == 200
