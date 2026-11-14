"""Actual teammate source plus combined local HTTP boundaries, never live credentials.

Git objects are pinned; missing objects skip explicitly rather than substitute a mock.
Servers use disposable synthetic data and loopback sockets only.
"""
import asyncio
import io
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import types
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image

from backend.answer_engine.adapters import CachedNavigationAdapter, NavigationPolicy
from backend.app import Settings, create_app
from backend.integrations.unoq_export import ExportUnavailable, transfer
from backend.speech import ElevenLabsSpeechService

UNO_REF = "98eb82b773e2986fd2ab8a60be3eb3f2b735fa4a"
DEMO_REF = "804b821dd6e51171567310f1ffc1cda32fb3547d"
RELAY_REF = "9aaf1e377e50bd0faeece3c4582a48b17eb976b8"
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
TOKEN = "synthetic-team-review-token"


def source_at(ref, path):
    result = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT,
                            capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.skip(f"required teammate source object unavailable: {ref}:{path}")
    return result.stdout


def teammate_module(ref, path, tmp_path):
    source = source_at(ref, path)
    module = types.ModuleType("synthetic_teammate_review")
    module.__file__ = str(tmp_path / Path(path).name)
    previous_path = list(sys.path)
    try:
        exec(compile(source, f"{ref}:{path}", "exec"), module.__dict__)
    finally:
        sys.path[:] = previous_path
    return module


@contextmanager
def server_for(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started, "local synthetic server did not start"
    try:
        yield origin
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive(), "test server failed to stop"


@pytest.fixture
def app_context(tmp_path):
    settings = Settings(api_token=TOKEN, local_data_dir=tmp_path / "central",
                        evidence_retention_seconds=30, stream_poll_seconds=.01,
                        stream_keepalive_seconds=.03)
    clock = SimpleNamespace(now=NOW)
    app = create_app(settings, clock=lambda: clock.now, speech=ElevenLabsSpeechService())
    try:
        yield SimpleNamespace(app=app, settings=settings, clock=clock)
    finally:
        try:
            app.state.store.close()
        except sqlite3.Error:
            pass  # TestClient lifespan already closed an owned store.


def demo_events():
    return json.loads(source_at(DEMO_REF, "demo/ayman/events.json"))


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(buffer, format="PNG")
    return buffer.getvalue()


def test_actual_unoq_export_to_api_answer_and_summary(app_context, tmp_path):
    uno = teammate_module(UNO_REF, "unoq/store.py", tmp_path)
    store = uno.Store(tmp_path / "uno", reserve=0)
    original = demo_events()
    for event in original:
        store.put(event)
    store.upload(png_bytes(), "image/png", original[0]["id"])
    assert store.events()[0]["evidence_url"].startswith("/artifacts/")
    exported = store.export()
    assert all(e["evidence_url"] is None for e in exported)
    with TestClient(app_context.app) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        receipt = client.post("/events", json=exported)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["durability"] == "local"
        for _ in range(2):
            answer = client.post("/ask", json={"question": "Did I take my pills?"}).json()
            # Actual teammate fixture uses UTC: 08:40Z is 10:40 Helsinki, not 08:40 local.
            assert "opened at 10:40" in answer["answer"]
            assert "cannot confirm" in answer["answer"]
            assert [e["id"] for e in answer["evidence"]] == ["synthetic-demo-repeat"]
        timeline = client.get("/timeline", params={"day": "2026-11-14"}).json()
        assert len(timeline["events"]) == 3
        assert len(timeline["alerts"]) == 1
        assert timeline["alerts"][0]["event_ids"] == ["synthetic-demo-door"]
        assert "05:10" in timeline["alerts"][0]["message"]
        assert timeline["questions"][0]["count"] == 2
        assert "2 pill box opening(s)" in client.get("/summary").json()["text"]
        # Synthetic UNO export is replay-safe and never transfers local artifact references.
        assert client.post("/events", json=store.export()).status_code == 200
        assert len(client.get("/timeline").json()["events"]) == 3


def test_actual_relay_vision_conversion_ingests_without_ground_truth(app_context, tmp_path):
    relay = teammate_module(RELAY_REF, "ml/recon_change/live/relay.py", tmp_path)
    report = {"verdict": "PASS synthetic", "appeared": [{"size_cm_approx": [20, 30, 40]}],
              "gone": [{"size_cm_approx": [30, 40, 50]}]}
    payload = relay.to_events(report, "kitchen", {"now": "/evidence/synthetic/now.jpg",
                                               "before": "/evidence/synthetic/before.jpg"})
    assert all("id" not in e and "size_cm" in e and e["object"] is None for e in payload)
    with TestClient(app_context.app) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        response = client.post("/events", json=payload)
        assert response.status_code == 200, response.text
        events = response.json()["events"]
        assert all(e["source"] == "vision" and "size_cm" not in e for e in events)
        assert len({e["id"] for e in events}) == 2
        assert client.post("/events", json=payload).json()["events"] == events
        assert client.get("/timeline").json()["alerts"] == []
        answer = client.post("/ask", json={"question": "Did I take my pills?"}).json()
        assert answer["evidence"] == [] and "cannot confirm" in answer["answer"]


def test_actual_relay_cache_navigation_never_requests_gpu(app_context, tmp_path):
    relay = teammate_module(RELAY_REF, "ml/recon_change/live/relay.py", tmp_path)
    class NoGPU:
        def call(self, *args, **kwargs):
            pytest.fail("cached navigation must never request the GPU")
    fix = dict(state="found", confidence=.95, age_s=0, zone="kitchen", ts=NOW.isoformat(),
               at=time.time(), position=[0, 0, 0], heading_deg=0,
               waypoints=[dict(name="bathroom", distance_m=2.5, bearing_deg=-45)])
    (tmp_path / "where.json").write_text(json.dumps({"kitchen": fix}))
    handler = relay.make_handler(NoGPU(), tmp_path, "synthetic-relay-key", pick=None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = CachedNavigationAdapter(base_url=f"http://127.0.0.1:{server.server_port}",
                                       zone="kitchen", token="synthetic-relay-key",
                                       policy=NavigationPolicy(max_age_s=5, min_confidence=.8))
    app_context.app.state.engine.navigation_adapter = adapter
    try:
        with TestClient(app_context.app) as client:
            client.headers["Authorization"] = "Bearer " + TOKEN
            answer = client.post("/ask", json={"question": "Where is the bathroom?"}).json()
            assert "2.5 metres to your left" in answer["answer"]
            assert answer["evidence"] == []
            assert client.get("/timeline").json()["questions"][0]["intent"] == "navigation"
            assert client.get("/timeline").json()["alerts"] == []
            # Advance only the relay's synthetic clock; its handler derives age_s from at.
            relay.time = SimpleNamespace(time=lambda: fix["at"] + 60, strftime=time.strftime)
            stale = client.post("/ask", json={"question": "Where is the bathroom?"}).json()
            assert "reliable current location" in stale["answer"]
    finally:
        server.shutdown()
        thread.join(2)
        server.server_close()


def test_relay_egress_requires_central_authentication(app_context, tmp_path):
    relay = teammate_module(RELAY_REF, "ml/recon_change/live/relay.py", tmp_path)
    with server_for(app_context.app) as origin:
        from urllib.error import HTTPError
        # Existing teammate egress sends only Content-Type, so direct integration is rejected.
        with pytest.raises(HTTPError) as error:
            relay.post_events(origin + "/events", demo_events())
        assert error.value.code == 401
        with httpx.Client(base_url=origin, headers={"Authorization": "Bearer " + TOKEN}) as client:
            assert client.get("/events").json()["events"] == []


def test_replay_evidence_update_reconnect_and_expiry_combined(app_context):
    with server_for(app_context.app) as origin:
        with httpx.Client(base_url=origin, headers={"Authorization": "Bearer " + TOKEN}, timeout=3) as client:
            item = demo_events()[0]
            assert client.post("/events", json=item).status_code == 200
            with client.stream("GET", "/events/stream") as response:
                lines = response.iter_lines()
                original_cursor = next(lines).removeprefix("id: ")
                assert next(lines) == "event: event"
                assert json.loads(next(lines).removeprefix("data: "))["evidence_url"] is None
            answer_before = client.post("/ask", json={"question": "pills?"}).json()
            upload = client.post(f"/events/{item['id']}/evidence",
                                 files={"file": ("synthetic.png", png_bytes(), "image/png")})
            assert upload.status_code == 200, upload.text
            url = upload.json()["evidence_url"]
            with client.stream("GET", "/events/stream", headers={"Last-Event-ID": original_cursor}) as response:
                lines = response.iter_lines()
                updated_cursor = next(lines).removeprefix("id: ")
                assert updated_cursor != original_cursor
                assert next(lines) == "event: event"
                updated = json.loads(next(lines).removeprefix("data: "))
                assert updated["id"] == item["id"] and updated["evidence_url"] == url
            answer_after = client.post("/ask", json={"question": "pills?"}).json()
            assert answer_before["answer"] == answer_after["answer"]
            assert answer_after["evidence"][0]["evidence_url"] == url
            assert client.get(url).content == png_bytes()
            assert client.get(url, headers={"Authorization": ""}).status_code == 401
            assert client.post("/events", json=item).status_code == 200
            app_context.clock.now += timedelta(seconds=31)
            assert client.get(url).status_code == 404
            replay = client.get("/events", params={"cursor": updated_cursor}).json()
            assert replay["events"][0]["id"] == item["id"]
            assert replay["events"][0]["evidence_url"] is None
            assert all(e["evidence_url"] is None for e in client.get("/events").json()["events"])
            assert "cannot confirm" in client.post("/ask", json={"question": "pills?"}).json()["answer"]


def test_actual_speech_service_route_and_provider_failure_preserve_question_count(app_context):
    calls = []
    # Two structurally complete synthetic MP3 frames test the boundary, not audibility.
    mp3 = (b"\xff\xfb\x90\x00" + bytes(413)) * 2
    def transport(request):
        calls.append(request)
        if len(calls) > 1:
            return httpx.Response(429, json={"error": "synthetic upstream rejection"})
        return httpx.Response(200, content=mp3, headers={"Content-Type": "audio/mpeg"})
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    speech = ElevenLabsSpeechService(api_key="synthetic-not-live", voice_id="synthetic-voice",
                                     client=upstream)
    # Build with the real service; the provider transport alone is synthetic.
    app = create_app(app_context.settings, clock=lambda: NOW, speech=speech)
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = "Bearer " + TOKEN
            assert client.post("/events", json=demo_events()[0]).status_code == 200
            answer = client.post("/ask", json={"question": "Did I take pills?"}).json()
            assert calls == [], "ask must not wait for speech"
            first = client.post("/speak", json={"text": answer["answer"]})
            assert first.status_code == 200 and first.content == mp3
            assert json.loads(calls[0].content)["text"] == answer["answer"]
            assert "cannot confirm" in json.loads(calls[0].content)["text"]
            failed = client.post("/speak", json={"text": "A different synthetic phrase"})
            assert failed.status_code == 503
            assert failed.json()["error"]["code"] == "speech_unavailable"
            assert "upstream rejection" not in failed.text
            assert client.get("/timeline").json()["questions"][0]["count"] == 1
    finally:
        asyncio.run(speech.aclose())
        asyncio.run(upstream.aclose())


def test_real_dashboard_api_client_against_local_http(app_context):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required to execute the actual dashboard API client")
    with server_for(app_context.app) as origin:
        with httpx.Client(base_url=origin, headers={"Authorization": "Bearer " + TOKEN}) as client:
            item = demo_events()[0]
            assert client.post("/events", json=item).status_code == 200
            upload = client.post(f"/events/{item['id']}/evidence", content=png_bytes(),
                                 headers={"Content-Type": "image/png"})
            assert upload.status_code == 200
        code = '''
import {ApiClient,timeLabel} from './dashboard/client.js';
const client = new ApiClient({origin:process.argv[1]});
client.setToken(process.argv[2]);
const view = await client.day('2026-11-14');
if (view.timeline.events.length !== 1) throw new Error('missing event');
if (timeLabel(view.timeline.events[0].ts) !== '10:00') throw new Error('wrong local time');
const image = await client.evidence(view.timeline.events[0].evidence_url);
if (image.type !== 'image/png' || image.size < 1) throw new Error('missing image');
client.clearToken();
try { await client.day('2026-11-14'); throw new Error('auth guard absent'); }
catch (error) { if (error.status !== 401) throw error; }
console.log(JSON.stringify({events:view.timeline.events.length,imageBytes:image.size}));
'''
        result = subprocess.run([node, "--input-type=module", "-e", code, origin, TOKEN],
                                cwd=ROOT, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["events"] == 1


def test_actual_sqlite_write_denial_not_acknowledged_then_recovery(app_context):
    with TestClient(app_context.app) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        store = app_context.app.state.store
        with store._lock:
            store.db.execute("PRAGMA query_only=ON")
        item = demo_events()[0]
        response = client.post("/events", json=item)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "durability_unavailable"
        assert client.get("/events").json()["events"] == []
        with store._lock:
            store.db.execute("PRAGMA query_only=OFF")
        assert client.post("/events", json=item).status_code == 200
        assert client.post("/ask", json={"question": "pills?"}).json()["evidence"][0]["id"] == item["id"]


def test_real_postgres_refusal_and_api_restart_keep_acknowledged_observation(app_context):
    app_context.settings.database_url = "postgresql://synthetic:synthetic@127.0.0.1:1/unavailable"
    first = create_app(app_context.settings, clock=lambda: NOW, speech=ElevenLabsSpeechService())
    item = demo_events()[0]
    with TestClient(first) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        assert client.post("/events", json=item).json()["durability"] == "local"
        import psycopg
        with pytest.raises(psycopg.Error):
            first.state.store.replicate_once()
        cursor = client.get("/events").json()["next_cursor"]
        pending = first.state.store.pending_count()
        assert pending == 1
    second = create_app(app_context.settings, clock=lambda: NOW, speech=ElevenLabsSpeechService())
    with TestClient(second) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        assert second.state.store.pending_count() == pending
        assert client.get("/events", params={"cursor": cursor}).json()["events"] == []
        answer = client.post("/ask", json={"question": "pills?"}).json()
        assert answer["evidence"][0]["id"] == item["id"]
        assert "cannot confirm" in answer["answer"]
        assert client.post("/events", json=item).status_code == 200
        assert len(client.get("/timeline").json()["events"]) == 1



def test_actual_unoq_service_transfer_to_central_api_with_separate_credentials(app_context, tmp_path):
    uno = teammate_module(UNO_REF, "unoq/store.py", tmp_path)
    source = uno.Store(tmp_path / "real-uno-service", reserve=0)
    items = demo_events()
    for item in items:
        source.put(item)
    source.upload(png_bytes(), "image/png", items[0]["id"])
    source_token = "synthetic-source-credential-32-bytes"
    service = ThreadingHTTPServer(("127.0.0.1", 0), uno.handler(source, source_token))
    worker = threading.Thread(target=service.serve_forever, daemon=True)
    worker.start()
    source_origin = f"http://127.0.0.1:{service.server_port}"
    try:
        with server_for(app_context.app) as target_origin:
            with pytest.raises(ExportUnavailable):
                transfer(source_origin, target_origin, "wrong-source-credential", TOKEN)
            with httpx.Client(base_url=target_origin, headers={"Authorization": "Bearer " + TOKEN}) as central:
                assert central.get("/events").json()["events"] == []
                assert transfer(source_origin, target_origin, source_token, TOKEN) == 3
                assert transfer(source_origin, target_origin, source_token, TOKEN) == 3
                timeline = central.get("/timeline").json()
                assert len(timeline["events"]) == 3
                assert all(event["evidence_url"] is None for event in timeline["events"])
                assert len(timeline["alerts"]) == 1
                answer = central.post("/ask", json={"question": "Did I take my pills?"}).json()
                assert "opened at 10:40" in answer["answer"]
                assert "cannot confirm" in answer["answer"]
    finally:
        service.shutdown()
        worker.join(2)
        service.server_close()
