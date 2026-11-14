import io
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn
from PIL import Image

from backend.integrations.attach_local_evidence import (
    AttachmentUnavailable,
    attach,
    read_image,
    validate_target,
)
from backend.run import NAVIGATION_ENV, create_application

TOKEN = "synthetic-local-evidence-token"
EVENT_ID = "synthetic_board-1"
URL = "/evidence/" + "a" * 32


def event(**changes):
    return {"id": EVENT_ID, "type": "pillbox_open", "source": "sensor",
            "ts": datetime.now(UTC).isoformat(), "zone": "kitchen", "object": "pillbox",
            "confidence": 1, "evidence_url": None, **changes}


@pytest.fixture
def selected_image(tmp_path):
    path = tmp_path / "explicit-synthetic-selection.png"
    Image.new("RGB", (3, 2), "blue").save(path, "PNG")
    return path


@pytest.mark.parametrize("target", [
    "http://localhost:8000", "http://192.168.1.10:8000", "https://remote.example",
    "http://127.0.0.1.evil.example", "http://2130706433", "file:///tmp/image",
    "http://user:secret@127.0.0.1", "http://127.0.0.1/?key=secret",
    "http://127.0.0.1/#fragment", "http://127.0.0.1/events", "http://127.0.0.1:99999",
    "http://127.0.0.1\n", "http://127.0.0.1:0",
])
def test_only_literal_loopback_origins(target):
    with pytest.raises(ValueError) as error:
        validate_target(target)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("target", ["http://127.0.0.1:8000", "https://127.0.0.1", "http://[::1]:8000/"])
def test_valid_local_origins(target):
    assert validate_target(target) == target.rstrip("/")


def test_selected_file_is_regular_bounded_and_decoded(selected_image, tmp_path):
    payload, media = read_image(selected_image)
    assert payload == selected_image.read_bytes()
    assert media == "image/png"
    link = tmp_path / "symlink.png"
    link.symlink_to(selected_image)
    invalid = tmp_path / "not-really-a-photo.jpg"
    invalid.write_bytes(b"not an image")
    oversized = tmp_path / "large.png"
    with oversized.open("wb") as stream:
        stream.truncate(5 * 1024 * 1024 + 1)
    for path in (link, invalid, oversized, tmp_path, tmp_path / "missing.png"):
        with pytest.raises(AttachmentUnavailable) as error:
            read_image(path)
        assert str(path) not in str(error.value)


def test_exact_event_preflight_paginated_before_upload(selected_image):
    calls = []
    uploaded = False
    def handler(request):
        nonlocal uploaded
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        if request.method == "GET":
            assert request.extensions["timeout"]["read"] == 2
            cursor = request.url.params.get("cursor")
            if cursor is None:
                return httpx.Response(200, json={"events": [event(id="unrelated")], "next_cursor": "next"})
            if cursor == "next":
                return httpx.Response(200, json={"events": [event()], "next_cursor": "original"})
            if cursor == "original":
                assert uploaded
                return httpx.Response(200, json={"events": [event(evidence_url=URL)], "next_cursor": "attached"})
            assert cursor == "attached"
            return httpx.Response(200, json={"events": [], "next_cursor": cursor})
        assert request.method == "POST"
        assert request.extensions["timeout"]["read"] == 12
        assert request.url.raw_path == b"/events/synthetic_board-1/evidence"
        assert request.headers["Content-Type"] == "image/png"
        assert request.content == selected_image.read_bytes()
        uploaded = True
        return httpx.Response(200, json={"evidence_url": URL})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert attach("http://127.0.0.1:8000", EVENT_ID, selected_image, TOKEN, client=client) == URL
    assert [request.method for request in calls] == ["GET", "GET", "POST", "GET", "GET", "GET", "GET"]


@pytest.mark.parametrize("event_id", ["../another", "contains/slash", "query?x=y", "fragment#x", "a%2Fb"])
def test_invalid_ids_cannot_change_upload_path(event_id, selected_image):
    def handler(request):
        pytest.fail("Invalid event IDs must never make a request")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            attach("http://127.0.0.1", event_id, selected_image, TOKEN, client=client)


@pytest.mark.parametrize("preflight", [httpx.Response(401, text="private response"),
                                     httpx.Response(200, json={"events": [], "next_cursor": None}),
                                     httpx.Response(200, json={"private": "response"})])
def test_wrong_auth_missing_event_and_bad_preflight_never_upload(selected_image, preflight):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        return preflight
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AttachmentUnavailable) as error:
            attach("http://127.0.0.1", EVENT_ID, selected_image, TOKEN, client=client)
    assert len(calls) == 1
    assert "private response" not in str(error.value)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("redirect_at", ["GET", "POST"])
def test_redirects_cannot_exfiltrate_token_or_image(selected_image, redirect_at):
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == redirect_at:
            return httpx.Response(307, headers={"Location": "https://remote.invalid/collect"})
        return httpx.Response(200, json={"events": [event()], "next_cursor": "next"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(AttachmentUnavailable):
            attach("http://127.0.0.1", EVENT_ID, selected_image, TOKEN, client=client)
    assert all(request.url.host == "127.0.0.1" for request in calls)
    assert len(calls) == (1 if redirect_at == "GET" else 2)


@pytest.mark.parametrize("receipt", [{}, {"evidence_url": "https://remote.invalid/image"},
                                    {"evidence_url": "//remote.invalid/image"},
                                    {"evidence_url": "/evidence/../../private"},
                                    {"evidence_url": URL + "?key=secret"},
                                    {"evidence_url": URL, "unexpected": "secret"}])
def test_malformed_receipt_never_claims_success_or_retries(selected_image, receipt):
    writes = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"events": [event()], "next_cursor": "next"})
        writes.append(request)
        return httpx.Response(200, json=receipt)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AttachmentUnavailable) as error:
            attach("http://127.0.0.1", EVENT_ID, selected_image, TOKEN, client=client)
    assert len(writes) == 1
    assert "secret" not in str(error.value)


def test_cli_real_http_updates_same_dashboard_event_and_authenticates_photo(monkeypatch, tmp_path, selected_image):
    for name in (*NAVIGATION_ENV, "DATABASE_URL", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_TOKEN", TOKEN)
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path / "central"))
    app = create_application()
    original_attach = app.state.store.attach_evidence
    def slow_privacy_processing(*args, **kwargs):
        # Synthetic stand-in for bounded face processing; no detector/model needed.
        time.sleep(2.2)
        return original_attach(*args, **kwargs)
    monkeypatch.setattr(app.state.store, "attach_evidence", slow_privacy_processing)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    try:
        with httpx.Client(base_url=origin, headers={"Authorization": "Bearer " + TOKEN}, trust_env=False) as client:
            assert client.post("/events", json=event()).status_code == 200
            cursor = client.get("/events").json()["next_cursor"]
            command = [sys.executable, "-m", "backend.integrations.attach_local_evidence",
                       "--target", origin, "--event-id", EVENT_ID, "--image", str(selected_image)]
            bad = subprocess.run(command, env={**os.environ, "API_TOKEN": "wrong-synthetic-token"},
                                 capture_output=True, text=True, timeout=15)
            assert bad.returncode == 1
            assert client.get("/timeline").json()["events"][0]["evidence_url"] is None
            missing = command.copy()
            missing[missing.index(EVENT_ID)] = "missing-event"
            result = subprocess.run(missing, capture_output=True, text=True, timeout=15)
            assert result.returncode == 1
            assert client.get("/timeline").json()["events"][0]["evidence_url"] is None
            started = time.monotonic()
            success = subprocess.run(command, capture_output=True, text=True, timeout=15)
            assert success.returncode == 0, success.stderr
            assert time.monotonic() - started > 2
            assert client.get("/dashboard/").status_code == 200
            timeline = client.get("/timeline").json()["events"]
            assert len(timeline) == 1 and timeline[0]["id"] == EVENT_ID
            url = timeline[0]["evidence_url"]
            photo = client.get(url)
            assert photo.status_code == 200
            with Image.open(io.BytesIO(photo.content)) as image:
                assert image.size == (3, 2)
                assert image.getpixel((0, 0)) == (0, 0, 255)
            assert client.get(url, headers={"Authorization": ""}).status_code == 401
            update = client.get("/events", params={"cursor": cursor}).json()["events"]
            assert len(update) == 1 and update[0]["id"] == EVENT_ID
            assert update[0]["evidence_url"] == url
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
    assert not thread.is_alive()


@pytest.mark.parametrize("latest_url", [None, "/evidence/" + "b" * 32])
def test_syntactically_valid_receipt_must_match_latest_event(selected_image, latest_url):
    uploaded = False
    writes = 0
    def handler(request):
        nonlocal uploaded, writes
        if request.method == "POST":
            writes += 1
            uploaded = True
            return httpx.Response(200, json={"evidence_url": URL})
        cursor = request.url.params.get("cursor")
        if not uploaded:
            return httpx.Response(200, json={"events": [event()], "next_cursor": "before"})
        if cursor is None:
            # A stale matching snapshot cannot substantiate the returned receipt.
            return httpx.Response(200, json={"events": [event(evidence_url=URL)], "next_cursor": "old"})
        if cursor == "old":
            return httpx.Response(200, json={"events": [event(evidence_url=latest_url)], "next_cursor": "new"})
        return httpx.Response(200, json={"events": [], "next_cursor": "new"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AttachmentUnavailable, match="latest event"):
            attach("http://127.0.0.1", EVENT_ID, selected_image, TOKEN, client=client)
    assert writes == 1


def test_decoded_pixel_limit_is_checked_before_loading(selected_image, monkeypatch):
    data = bytearray(selected_image.read_bytes())
    # Change only the PNG IHDR dimensions and checksum, preserving a tiny bounded fixture.
    data[16:24] = struct.pack(">II", 4001, 4000)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
    selected_image.write_bytes(data)
    def no_load(*args, **kwargs):
        pytest.fail("Oversized pixel buffer must never be allocated")
    monkeypatch.setattr("PIL.PngImagePlugin.PngImageFile.load", no_load)
    with pytest.raises(AttachmentUnavailable, match="16 megapixel"):
        read_image(selected_image)
