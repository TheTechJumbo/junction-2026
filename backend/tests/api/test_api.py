import io
import json
import socket
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import Settings, create_app
from contracts.interfaces import SpeechUnavailable
from contracts.models import Answer


class Engine:
    def classify(self, question):
        return "pills" if "pill" in question.lower() else "open"

    def answer(self, question, events, now, timezone):
        return Answer(answer="An opening is not ingestion.", evidence=list(events))

    def derive_alerts(self, events, day, timezone):
        return []

    def summarize(self, events, alerts, questions, day, timezone):
        return f"{len(events)} events, {len(questions)} questions"


class Speech:
    async def synthesize(self, text):
        raise SpeechUnavailable()


def event(id="evt1", **changes):
    return {"id": id, "type": "pillbox_open", "source": "sensor",
            "ts": "2026-11-14T08:00:00+02:00", "zone": "kitchen", "object": "pillbox",
            "evidence_url": None, "confidence": 1, **changes}


def image_bytes(format="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(stream, format)
    return stream.getvalue()


@pytest.fixture
def context(tmp_path):
    clock = SimpleNamespace(now=datetime(2026, 11, 14, 10, tzinfo=UTC))
    settings = Settings(api_token="test-token", local_data_dir=tmp_path,
                        configured_devices=("uno",), evidence_retention_seconds=60,
                        stream_poll_seconds=.01, stream_keepalive_seconds=.03,
                        device_tokens={"uno": "device-token"})
    app = create_app(settings, clock=lambda: clock.now, engine=Engine(), speech=Speech())
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-token"
        yield SimpleNamespace(client=client, app=app, clock=clock, settings=settings)


def test_auth_errors_and_no_secret(context):
    client = context.client
    for path in ("/events", "/timeline", "/summary", "/evidence/test", "/events/stream"):
        response = client.get(path, headers={"Authorization": ""})
        assert response.status_code == 401
        assert set(response.json()) == {"error"}
    assert client.get("/health", headers={"Authorization": ""}).json() == {"status": "ok"}
    for payload in ({}, [], [event(), {"id": "broken"}], [event()] * 101,
                    event(confidence=True), event(confidence=float("inf"))):
        # JSON must represent non-finite values as text for this negative parser test.
        result = client.post("/events", content=json.dumps(payload))
        assert result.status_code == 400
    assert client.get("/events").json()["events"] == []
    assert client.get("/events?limit=0").status_code == 400
    assert client.get("/events?limit=wat").status_code == 400
    assert client.post("/ask", json={"question": "", "secret": "never-echo"}).status_code == 400
    assert "never-echo" not in client.post("/ask", json={"secret": "never-echo"}).text


def test_atomic_conflict_and_read_your_writes(context):
    client = context.client
    assert client.post("/events", json=event()).json()["durability"] == "local"
    result = client.post("/events", json=[event("evt2"), event(type="door_open")])
    assert result.status_code == 409
    assert [e["id"] for e in client.get("/events").json()["events"]] == ["evt1"]
    assert client.post("/events", json=event()).status_code == 200
    assert len(client.get("/events").json()["events"]) == 1
    assert client.post("/events", json=[event("new"), event("new", zone="changed")]).status_code == 409
    assert len(client.get("/events").json()["events"]) == 1


def test_legacy_adapter_and_canonical_output(context):
    payload = event(source="vision", object=None)
    del payload["id"]
    payload["size_cm"] = [10, 20, 30]
    first = context.client.post("/events", json=[payload, payload]).json()["events"]
    assert first[0]["id"] != first[1]["id"]
    assert "size_cm" not in first[0]
    assert context.client.post("/events", json=[payload, payload]).json()["events"] == first
    assert len(context.client.get("/events").json()["events"]) == 2


def test_replay_late_equal_events_and_cursors(context):
    client = context.client
    client.post("/events", json=event())
    first = client.get("/events").json()
    cursor = first["next_cursor"]
    assert client.get("/events", params={"cursor": cursor}).json() == {
        "events": [], "next_cursor": cursor}
    client.post("/events", json=[event("late", ts="2026-11-13T10:00:00Z"), event("equal")])
    result = client.get("/events", params={"cursor": cursor, "limit": 1}).json()
    assert result["events"][0]["id"] == "late"
    assert client.get("/events", params={"cursor": result["next_cursor"]}).json()["events"][0]["id"] == "equal"
    assert client.get("/events", params={"since": "2026-11-14T06:00:00Z"}).json()["events"][0]["id"] == "evt1"
    assert client.get("/events", params={"cursor": cursor, "since": "2026-11-14T00:00:00Z"}).status_code == 400
    assert client.get("/events?since=2026-11-14").status_code == 400
    assert client.get("/events?cursor=garbage").status_code == 400
    context.app.state.store.prune_replay(result["next_cursor"])
    assert client.get("/events", params={"cursor": cursor}).status_code == 410
    assert client.get("/events/stream", headers={"Last-Event-ID": cursor}).status_code == 410


def test_evidence_raw_multipart_retry_and_expiry(context):
    client = context.client
    client.post("/events", json=event())
    cursor = client.get("/events").json()["next_cursor"]
    upload = client.post("/events/evt1/evidence", content=image_bytes(),
                         headers={"Content-Type": "image/png"})
    assert upload.status_code == 200, upload.text
    url = upload.json()["evidence_url"]
    assert client.get(url).content == image_bytes()
    assert client.get(url, headers={"Authorization": ""}).status_code == 401
    retry = client.post("/events", json=event())
    assert retry.status_code == 200
    assert retry.json()["events"][0]["evidence_url"] == url
    update = client.get("/events", params={"cursor": cursor}).json()
    assert len(update["events"]) == 1
    assert update["events"][0]["evidence_url"] == url
    multipart = client.post("/events/evt1/evidence",
                            files={"file": ("synthetic.jpg", image_bytes("JPEG"), "image/jpeg")})
    assert multipart.status_code == 200, multipart.text
    new_url = multipart.json()["evidence_url"]
    assert client.get(new_url).headers["content-type"] == "image/jpeg"
    context.clock.now += timedelta(seconds=61)
    assert client.get(url).status_code == 404
    assert client.get(new_url).status_code == 404
    assert client.get("/timeline").json()["events"][0]["evidence_url"] is None
    assert all(x["evidence_url"] is None for x in client.get("/events").json()["events"])
    assert client.post("/events", json=event()).status_code == 200


def test_invalid_evidence_and_disabled_mode(context):
    client = context.client
    client.post("/events", json=event())
    assert client.post("/events/evt1/evidence", content=b"fake", headers={"Content-Type": "image/png"}).status_code == 415
    assert client.post("/events/evt1/evidence", content=image_bytes(), headers={"Content-Type": "image/jpeg"}).status_code == 415
    assert client.post("/events/evt1/evidence", content=b"x" * (5*1024*1024+1), headers={"Content-Type": "image/png"}).status_code == 413
    context.settings.evidence_mode = "cloud"
    assert client.post("/events/evt1/evidence", content=image_bytes()).status_code == 503


def test_questions_summary_timezone_and_heartbeat(context):
    client = context.client
    client.post("/events", json=[event(), event("next", ts="2026-11-14T22:00:00Z"),
                                event("boundary", ts="2026-11-13T22:00:00Z")])
    for question in ("pills?", "PILLS please", "hello"):
        assert client.post("/ask", json={"question": question}).status_code == 200
    timeline = client.get("/timeline").json()
    assert [x["id"] for x in timeline["events"]] == ["boundary", "evt1"]
    assert timeline["baseline"] is None
    assert timeline["sensors"] == [{"id": "uno", "last_seen": None, "status": "unknown"}]
    assert {x["intent"]: x["count"] for x in timeline["questions"]} == {"pills": 2, "open": 1}
    assert client.get("/summary").json() == {"text": "2 events, 3 questions"}
    assert client.post("/heartbeat", json={"device_id": "uno"}).json()["status"] == "online"
    context.clock.now += timedelta(seconds=30)
    assert client.get("/timeline").json()["sensors"][0]["status"] == "online"
    context.clock.now += timedelta(microseconds=1)
    assert client.get("/timeline").json()["sensors"][0]["status"] == "offline"
    assert client.post("/heartbeat", json={"device_id": "other"}, headers={"Authorization": "Bearer device-token"}).status_code == 401
    assert client.post("/heartbeat", json={"device_id": "uno"}, headers={"Authorization": "Bearer device-token"}).status_code == 200
    assert len(client.get("/events").json()["events"]) == 3
    assert client.post("/speak", json={"text": "hello"}).status_code == 503


def test_storage_failure_does_not_acknowledge(context, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("sensitive upstream detail")
    monkeypatch.setattr(context.app.state.store, "ingest", fail)
    result = context.client.post("/events", json=event())
    assert result.status_code == 503
    assert "sensitive" not in result.text


def test_real_sse_reconnect_update_and_keepalive(tmp_path):
    settings = Settings(api_token="test-token", local_data_dir=tmp_path,
                        stream_poll_seconds=.01, stream_keepalive_seconds=.02)
    app = create_app(settings, engine=Engine(), speech=Speech())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
                          headers={"Authorization": "Bearer test-token"}, timeout=3) as client:
            client.post("/events", json=event())
            with client.stream("GET", "/events/stream") as response:
                lines = response.iter_lines()
                sequence = next(lines).removeprefix("id: ")
                assert next(lines) == "event: event"
                assert json.loads(next(lines).removeprefix("data: "))["id"] == "evt1"
            client.post("/events/evt1/evidence", content=image_bytes(), headers={"Content-Type": "image/png"})
            with client.stream("GET", "/events/stream", headers={"Last-Event-ID": sequence}) as response:
                lines = response.iter_lines()
                assert next(lines) != f"id: {sequence}"
                assert next(lines) == "event: event"
                assert json.loads(next(lines).removeprefix("data: "))["evidence_url"].startswith("/evidence/")
                assert next(lines) == ""
                assert next(lines) == ": keepalive"
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive()
