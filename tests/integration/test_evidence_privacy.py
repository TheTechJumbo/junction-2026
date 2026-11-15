"""Independent privacy storage regressions; synthetic images only, no face-blur claim.

Exercise real routes, decoded pixel behavior, multipart handling and SQLite files.
"""
import io
import random
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageChops, ImageOps, ImageStat, PngImagePlugin
from PIL.TiffImagePlugin import IFDRational

from backend.app import Settings, create_app
from backend.speech import ElevenLabsSpeechService
from backend.storage import SQLiteStore
from contracts.models import Event

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
PRIVATE = b"SYNTHETIC_PRIVATE_MARKER_6ef0d570_MUST_NOT_PERSIST"


def event(id="synthetic-privacy"):
    return Event(id=id, type="pillbox_open", source="sensor", ts=NOW,
                 zone="kitchen", object="pillbox", evidence_url=None, confidence=1)


@pytest.fixture
def context(tmp_path):
    clock = SimpleNamespace(now=NOW)
    settings = Settings(api_token="synthetic-privacy-token", local_data_dir=tmp_path,
                        evidence_retention_seconds=5)
    app = create_app(settings, clock=lambda: clock.now, speech=ElevenLabsSpeechService())
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer synthetic-privacy-token"
        assert client.post("/events", json=event().model_dump(mode="json")).status_code == 200
        yield SimpleNamespace(client=client, app=app, clock=clock, settings=settings)


def upload(context, data, media_type, *, multipart=False):
    if multipart:
        response = context.client.post("/events/synthetic-privacy/evidence",
                                       files={"file": ("synthetic-photo", data, media_type)})
    else:
        response = context.client.post("/events/synthetic-privacy/evidence", content=data,
                                       headers={"Content-Type": media_type})
    assert response.status_code == 200, response.text
    url = response.json()["evidence_url"]
    downloaded = context.client.get(url)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == media_type
    return url, downloaded.content


def png_with_metadata():
    pixels = Image.new("RGB", (32, 24), "orange")
    pixels.paste("navy", (16, 0, 32, 24))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", PRIVATE.decode())
    metadata.add_itxt("Location", "synthetic-private-household-location")
    metadata.add_text("Description", "synthetic-compressed-comment", zip=True)
    exif = Image.Exif()
    exif[315] = "synthetic-private-artist"
    output = io.BytesIO()
    pixels.save(output, "PNG", pnginfo=metadata, exif=exif)
    return pixels, output.getvalue() + b"SYNTHETIC_TRAILING_PRIVATE_BYTES_94783"


@pytest.mark.parametrize("multipart", [False, True])
def test_png_keeps_visible_pixels_and_drops_all_metadata_and_trailing_data(context, multipart):
    expected, original = png_with_metadata()
    assert PRIVATE in original
    _, stored = upload(context, original, "image/png", multipart=multipart)
    decoded = Image.open(io.BytesIO(stored))
    assert decoded.size == expected.size
    assert ImageChops.difference(decoded.convert("RGB"), expected).getbbox() is None
    assert decoded.info == {}
    assert not decoded.getexif()
    for private in (PRIVATE, b"synthetic-private", b"synthetic-compressed-comment",
                    b"SYNTHETIC_TRAILING_PRIVATE_BYTES_94783"):
        assert private not in stored
    # Also inspect the exact durable BLOB, rather than only a sanitized download view.
    row = context.app.state.store.db.execute("SELECT data FROM evidence").fetchone()
    assert bytes(row[0]) == stored


@pytest.mark.parametrize("multipart", [False, True])
def test_jpeg_honors_exif_orientation_then_removes_exif_gps_and_trailer(context, multipart):
    original_pixels = Image.new("RGB", (96, 64), "red")
    original_pixels.paste("green", (48, 0, 96, 64))
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 degrees clockwise for display
    exif[315] = PRIVATE.decode()
    exif[34853] = {1: "N", 2: (IFDRational(60), IFDRational(10), IFDRational(1)),
                   3: "E", 4: (IFDRational(24), IFDRational(50), IFDRational(1))}
    output = io.BytesIO()
    original_pixels.save(output, "JPEG", quality=98, exif=exif)
    original = output.getvalue() + b"SYNTHETIC_JPEG_PRIVATE_TRAILER_46208"
    before = Image.open(io.BytesIO(original))
    assert before.getexif().get_ifd(34853), "fixture must actually contain GPS"
    expected = ImageOps.exif_transpose(before).convert("RGB")
    _, stored = upload(context, original, "image/jpeg", multipart=multipart)
    decoded = Image.open(io.BytesIO(stored))
    assert decoded.size == expected.size == (64, 96)
    assert not decoded.getexif()
    assert "exif" not in decoded.info and "comment" not in decoded.info
    assert PRIVATE not in stored and b"SYNTHETIC_JPEG_PRIVATE_TRAILER_46208" not in stored
    # JPEG reencoding is lossy; check useful pixels/orientation, not byte equality.
    mean_error = ImageStat.Stat(ImageChops.difference(decoded.convert("RGB"), expected)).mean
    assert max(mean_error) < 10, mean_error


def test_multipart_over_one_megabyte_never_rolls_raw_image_to_disk(context, monkeypatch):
    pixels = Image.frombytes("RGB", (768, 768), random.Random(62041).randbytes(768 * 768 * 3))
    output = io.BytesIO()
    pixels.save(output, "PNG")
    data = output.getvalue()
    assert 1024 * 1024 < len(data) < 5 * 1024 * 1024
    rollovers = []
    def forbidden_rollover(self):
        rollovers.append(True)
        raise AssertionError("Raw evidence was spooled to disk before privacy processing")
    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", forbidden_rollover)
    _, stored = upload(context, data, "image/png", multipart=True)
    assert rollovers == []
    decoded = Image.open(io.BytesIO(stored)).convert("RGB")
    assert ImageChops.difference(decoded, pixels).getbbox() is None


def marker_files(path):
    return [file.name for file in Path(path).parent.glob(Path(path).name + "*")
            if file.is_file() and PRIVATE in file.read_bytes()]


def test_expiry_erases_marker_from_sqlite_and_wal_but_preserves_durable_records(tmp_path):
    path = tmp_path / "events.sqlite3"
    store = SQLiteStore(path)
    original = event()
    store.ingest([(original, {})])
    store.record("pills", "synthetic question", NOW)
    # Direct storage isolates erasure from upstream metadata stripping: blob itself has marker.
    url = store.attach_evidence(original.id, b"synthetic-image-bytes:" + PRIVATE,
                                 "image/png", NOW + timedelta(seconds=5))
    cursor = store.replay()[1]
    assert marker_files(path), "fixture must put marker in a real SQLite storage file"
    store.expire_evidence(NOW + timedelta(seconds=6))
    assert marker_files(path) == []
    assert store.get(original.id).evidence_url is None
    replay, _ = store.replay(cursor)
    assert replay[-1][1].id == original.id and replay[-1][1].evidence_url is None
    assert all(item.evidence_url is None for _, item in store.replay()[0])
    installation = store.installation
    pending = store.pending_count()
    assert pending >= 4
    store.close()
    reopened = SQLiteStore(path)
    try:
        assert reopened.installation == installation
        assert reopened.get(original.id).evidence_url is None
        assert reopened.pending_count() == pending
        assert reopened.replay(cursor)[0][-1][1].evidence_url is None
        assert len(reopened.list_questions(NOW - timedelta(seconds=1),
                                           NOW + timedelta(seconds=1))) == 1
        assert marker_files(path) == []
        from backend.storage import MissingEvent
        with pytest.raises(MissingEvent):
            reopened.get_evidence(url.rsplit("/", 1)[1], NOW + timedelta(seconds=6))
    finally:
        reopened.close()


def test_busy_wal_reader_defers_physical_purge_and_next_expiry_pass_retries(tmp_path):
    path = tmp_path / "events.sqlite3"
    store = SQLiteStore(path)
    reader = None
    try:
        original = event()
        store.ingest([(original, {})])
        store.attach_evidence(original.id, b"synthetic-image-bytes:" + PRIVATE,
                              "image/png", NOW + timedelta(seconds=5))
        reader = sqlite3.connect(path)
        reader.execute("BEGIN")
        assert PRIVATE in reader.execute("SELECT data FROM evidence").fetchone()[0]
        store.expire_evidence(NOW + timedelta(seconds=6))
        assert store.get(original.id).evidence_url is None
        assert marker_files(path), "active old reader should pin historical image bytes"
        reader.rollback()
        reader.close()
        reader = None
        # There are no newly expired rows now: this must retry a previously busy checkpoint.
        store.expire_evidence(NOW + timedelta(seconds=7))
        assert marker_files(path) == []
        assert store.get(original.id).evidence_url is None
    finally:
        if reader is not None:
            reader.close()
        store.close()


def test_pixel_limit_rejects_before_full_decode(monkeypatch):
    from backend.app import APIError, verify_image

    image = Image.new("RGB", (4096, 4096), "white")
    encoded = io.BytesIO()
    image.save(encoded, "PNG")

    def forbidden_decode(self, *args, **kwargs):
        raise AssertionError("oversized evidence must be rejected before pixel allocation")

    monkeypatch.setattr(Image.Image, "load", forbidden_decode)
    with pytest.raises(APIError) as exc:
        verify_image(encoded.getvalue(), "image/png")
    assert exc.value.status == 413
