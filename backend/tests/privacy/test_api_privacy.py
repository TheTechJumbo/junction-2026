"""Independent API privacy checks: real public-face processing, synthetic failures.

No live credentials, household imagery, model service or mocked success processor.
"""
import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from backend.privacy import LocalFaceRedactor, RedactionUnavailable
from backend.privacy import redactor as redactor_module
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

import backend.app as api_module
from backend.app import Settings, create_app
from backend.speech import ElevenLabsSpeechService

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
TOKEN = "synthetic-api-privacy"
MARKER = b"SYNTHETIC_PRIVATE_UPLOAD_MARKER_58f107ed"
FIXTURE_HASH = "88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5"


def observation():
    return {"id": "synthetic-face-observation", "type": "pillbox_open", "source": "sensor",
            "ts": NOW.isoformat(), "zone": "kitchen", "object": "pillbox",
            "evidence_url": None, "confidence": 1}


def png_pixels(image, *, metadata=False):
    output = io.BytesIO()
    info = None
    if metadata:
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", MARKER.decode())
    image.save(output, format="PNG", pnginfo=info)
    return output.getvalue()


def assert_not_on_disk(folder, *patterns):
    for path in Path(folder).glob("events.sqlite3*"):
        data = path.read_bytes()
        for pattern in patterns:
            assert pattern not in data, f"Source/private bytes persisted in {path.name}"


def test_actual_public_face_upload_blurs_face_retains_context_and_reuses_processor(monkeypatch, tmp_path):
    # Resolve relative to the actual imported backend, so this test can independently
    # run against the integrator's code without copying implementation into this tree.
    fixture = Path(redactor_module.__file__).resolve().parents[1] / "tests/privacy/fixtures/astronaut.png"
    source = fixture.read_bytes()
    assert hashlib.sha256(source).hexdigest() == FIXTURE_HASH
    with Image.open(io.BytesIO(source)) as image:
        original = image.convert("RGB")
    encoded_original = png_pixels(original)
    uploaded_original = png_pixels(original, metadata=True)
    assert MARKER in uploaded_original
    constructions, calls = [], []

    class RecordingRealRedactor(LocalFaceRedactor):
        def __init__(self):
            super().__init__(timeout_seconds=10)
            constructions.append(id(self))

        def redact(self, data):
            calls.append(id(self))
            return super().redact(data)  # actual bundled detector and child process

    monkeypatch.setattr(api_module, "LocalFaceRedactor", RecordingRealRedactor)
    app = create_app(Settings(api_token=TOKEN, local_data_dir=tmp_path),
                     clock=lambda: NOW, speech=ElevenLabsSpeechService())
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        assert client.post("/events", json=observation()).status_code == 200
        urls = []
        for _ in range(2):
            response = client.post("/events/synthetic-face-observation/evidence",
                                   content=uploaded_original, headers={"Content-Type": "image/png"})
            assert response.status_code == 200, response.text
            assert set(response.json()) == {"evidence_url"}, "Canonical receipt must not gain fields"
            assert response.headers["X-Privacy-Status"] == "detected_faces_redacted"
            assert response.headers["X-Faces-Redacted"] == "1"
            url = response.json()["evidence_url"]
            urls.append(url)
            downloaded = client.get(url)
            assert downloaded.status_code == 200
            assert downloaded.headers["Content-Type"] == "image/png"
            assert downloaded.content != encoded_original
            assert MARKER not in downloaded.content
            with Image.open(io.BytesIO(downloaded.content)) as transformed:
                assert transformed.size == original.size == (512, 512)
                assert transformed.info == {} and not transformed.getexif()
                after = np.asarray(transformed.convert("RGB"))
            before = np.asarray(original)
            # Independent known face extent; this does not use the detector's own mask.
            face_before = before[65:170, 175:270].astype(float)
            face_after = after[65:170, 175:270].astype(float)
            assert np.abs(face_before - face_after).mean() > 15
            assert face_after.var() < face_before.var() / 2
            # Keep substantial nonface evidence; whole-image blur/blanking fails this.
            assert np.array_equal(before[300:, :, :], after[300:, :, :])
            assert client.get(url, headers={"Authorization": ""}).status_code == 401
            assert_not_on_disk(tmp_path, MARKER, uploaded_original, encoded_original)
        assert len(constructions) == 1
        assert calls == [constructions[0], constructions[0]]
        timeline = client.get("/timeline").json()
        assert len(timeline["events"]) == 1
        assert timeline["events"][0]["evidence_url"] == urls[-1]
        answer = client.post("/ask", json={"question": "Did I take my pills?"}).json()
        assert "cannot confirm" in answer["answer"]
        assert answer["evidence"][0]["evidence_url"] == urls[-1]
        assert calls == [constructions[0], constructions[0]], "Core answer must not run redaction"


def test_processor_failure_never_persists_image_and_does_not_disable_core_answers(tmp_path):
    calls = []

    class BrokenProcessor:
        def redact(self, data):
            calls.append(data)
            raise RedactionUnavailable("synthetic private processing failure " + MARKER.decode())

    original = Image.new("RGB", (64, 48), "green")
    encoded_original = png_pixels(original)
    uploaded_original = png_pixels(original, metadata=True)
    app = create_app(Settings(api_token=TOKEN, local_data_dir=tmp_path),
                     clock=lambda: NOW, speech=ElevenLabsSpeechService(), redactor=BrokenProcessor())
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        assert client.post("/events", json=observation()).status_code == 200
        for _ in range(2):
            response = client.post("/events/synthetic-face-observation/evidence",
                                   content=uploaded_original, headers={"Content-Type": "image/png"})
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "privacy_unavailable"
            assert MARKER.decode() not in response.text and "processing failure" not in response.text
            assert response.headers.get("X-Privacy-Status") is None
            assert response.headers.get("X-Faces-Redacted") is None
            assert app.state.store.db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
            assert app.state.store.get("synthetic-face-observation").evidence_url is None
            assert_not_on_disk(tmp_path, MARKER, uploaded_original, encoded_original)
        assert len(calls) == 2
        assert all(MARKER not in data for data in calls), "Strip metadata before child processing"
        answer = client.post("/ask", json={"question": "Did I take my pills?"})
        assert answer.status_code == 200
        assert "cannot confirm" in answer.json()["answer"]
        assert answer.json()["evidence"][0]["evidence_url"] is None
        assert len(calls) == 2
        feed = client.get("/events").json()["events"]
        assert len(feed) == 1 and feed[0]["evidence_url"] is None
        assert client.get("/timeline").json()["alerts"] == []
