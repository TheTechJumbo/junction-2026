import hashlib
import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from backend.privacy import LocalFaceRedactor, RedactionUnavailable, redactor, worker

FIXTURE = Path(__file__).with_name("fixtures") / "astronaut.png"
ASTRONAUT_SHA256 = "88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5"


def png(image, metadata=None):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=metadata)
    return buffer.getvalue()


def decoded(data, *, check_metadata=True):
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        assert image.format == "PNG"
        if check_metadata:
            assert not image.info
        return np.array(image)


def test_real_public_face_detected_expanded_and_blurred_with_context_preserved():
    source = FIXTURE.read_bytes()
    assert hashlib.sha256(source).hexdigest() == ASTRONAUT_SHA256
    result = LocalFaceRedactor(timeout_seconds=10).redact(source)
    assert result.status == "detected_faces_redacted"
    assert result.faces_redacted == 1
    assert result.content_type == "image/png"
    before, after = decoded(source, check_metadata=False), decoded(result.image_bytes)
    assert before.shape == after.shape == (512, 512, 3)
    left, top, right, bottom = result.face_boxes[0]
    # Independently annotated visible face extent, not the detector's own answer.
    assert left < 175 and top < 65 and right > 270 and bottom > 170
    face_before, face_after = before[65:170, 175:270], after[65:170, 175:270]
    assert np.abs(face_before.astype(float) - face_after).mean() > 15
    assert face_after.astype(float).var() < face_before.astype(float).var() / 2
    mask = np.zeros(before.shape[:2], dtype=bool)
    mask[top:bottom, left:right] = True
    assert np.array_equal(before[~mask], after[~mask])
    assert mask.mean() < .25  # surrounding evidence retained, not whole-image blanking


def test_no_faces_is_explicit_and_preserves_scene_pixels_without_metadata():
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-location", "synthetic-test-only")
    image = Image.new("RGB", (64, 48), "green")
    result = LocalFaceRedactor(timeout_seconds=10).redact(png(image, metadata))
    assert result.status == "no_faces_detected"
    assert result.faces_redacted == 0
    assert result.face_boxes == ()
    assert np.array_equal(decoded(result.image_bytes), np.asarray(image))
    assert b"synthetic-test-only" not in result.image_bytes


@pytest.mark.parametrize("data", [b"", b"private malformed image", b"x" * (5*1024*1024+1)])
def test_invalid_or_oversize_bytes_rejected_before_subprocess(monkeypatch, data):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("child spawned"))
    with pytest.raises(RedactionUnavailable):
        LocalFaceRedactor().redact(data)


@pytest.mark.parametrize("size", [(1281, 5), (1025, 1024)])
def test_pixel_limit_rejects_compressible_image_before_child(monkeypatch, size):
    data = png(Image.new("RGB", size))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("child spawned"))
    with pytest.raises(RedactionUnavailable, match="pixel"):
        LocalFaceRedactor().redact(data)


def test_animation_rejected():
    buffer = io.BytesIO()
    frames = [Image.new("RGB", (32, 32), color) for color in ("red", "green")]
    frames[0].save(buffer, format="PNG", save_all=True, append_images=frames[1:])
    with pytest.raises(RedactionUnavailable):
        LocalFaceRedactor().redact(buffer.getvalue())


def test_busy_bound_rejects_immediately_without_spawning(monkeypatch):
    service = LocalFaceRedactor()
    for _ in range(2):
        assert service._slots.acquire(blocking=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("third child spawned"))
    try:
        with pytest.raises(RedactionUnavailable, match="busy"):
            service.redact(png(Image.new("RGB", (32, 32))))
    finally:
        for _ in range(2):
            service._slots.release()


def test_bundled_model_integrity_fails_closed(monkeypatch):
    monkeypatch.setattr(worker, "MODEL_SHA256", "wrong")
    with pytest.raises(ValueError, match="Unverified"):
        worker.transform(FIXTURE.read_bytes())


@pytest.mark.parametrize("fault", ["timeout", "crash", "malformed", "spawn_elapsed"])
def test_child_failures_never_return_original_and_release_slot(monkeypatch, fault):
    source = png(Image.new("RGB", (32, 32)))
    clock = [100.0]
    monkeypatch.setattr(redactor.time, "monotonic", lambda: clock[0])
    monkeypatch.setenv("ELEVENLABS_API_KEY", "synthetic-private-key")
    instances = []

    class Child:
        def __init__(self, args, **kwargs):
            self.args = args
            self.returncode = None
            self.killed = False
            self.calls = 0
            assert "-I" in args and "-B" in args
            assert "ELEVENLABS_API_KEY" not in kwargs["env"]
            assert kwargs["stdin"] == subprocess.PIPE
            assert kwargs["stdout"] == subprocess.PIPE
            assert kwargs["stderr"] == subprocess.DEVNULL
            if fault == "spawn_elapsed":
                clock[0] += 4
            instances.append(self)

        def communicate(self, data=None, timeout=None):
            self.calls += 1
            if self.calls == 1 and fault == "timeout":
                assert timeout == 3
                raise subprocess.TimeoutExpired(self.args, timeout)
            if fault == "crash":
                self.returncode = 1
            elif not self.killed:
                self.returncode = 0
            return b"private-invalid-output", None

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", Child)
    service = LocalFaceRedactor()
    with pytest.raises(RedactionUnavailable) as caught:
        service.redact(source)
    assert "private" not in str(caught.value)
    assert "synthetic" not in str(caught.value)
    assert service._slots.acquire(blocking=False)
    assert service._slots.acquire(blocking=False)
    if fault in {"timeout", "spawn_elapsed"}:
        assert instances[0].killed
        assert instances[0].calls >= 1  # cleanup reaps the killed process


def test_detection_error_not_treated_as_zero_faces(monkeypatch):
    import cv2

    def fail(*args):
        raise cv2.error("synthetic private native failure")

    monkeypatch.setattr(cv2.FaceDetectorYN, "create", fail)
    with pytest.raises(cv2.error):
        worker.transform(FIXTURE.read_bytes())


def test_model_hash_matches_bundled_file():
    model = Path(worker.__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"
    assert hashlib.sha256(model.read_bytes()).hexdigest() == worker.MODEL_SHA256


def test_exif_orientation_and_metadata_are_not_retained():
    image = Image.new("RGB", (64, 32), "blue")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "synthetic private note"
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    result = LocalFaceRedactor(timeout_seconds=10).redact(buffer.getvalue())
    assert decoded(result.image_bytes).shape == (64, 32, 3)
    assert b"synthetic private" not in result.image_bytes


def test_worker_response_header_is_small_and_explicit():
    metadata, image = worker.transform(png(Image.new("RGB", (32, 32))))
    assert len(json.dumps(metadata)) < 8192
    assert image.startswith(b"\x89PNG\r\n\x1a\n")


def test_multiple_public_faces_are_each_masked():
    with Image.open(FIXTURE) as source:
        photo = source.convert("RGB")
    pair = Image.new("RGB", (1024, 512))
    pair.paste(photo, (0, 0))
    pair.paste(photo, (512, 0))
    result = LocalFaceRedactor(timeout_seconds=10).redact(png(pair))
    assert result.faces_redacted == 2
    assert any(left < 175 and right > 270 for left, _, right, _ in result.face_boxes)
    assert any(left < 687 and right > 782 for left, _, right, _ in result.face_boxes)


def test_real_timeout_reaps_native_child(monkeypatch):
    spawn = subprocess.Popen
    children = []

    def track(*args, **kwargs):
        child = spawn(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", track)
    service = LocalFaceRedactor(timeout_seconds=.01)
    with pytest.raises(RedactionUnavailable, match="timed out"):
        service.redact(png(Image.new("RGB", (32, 32))))
    assert len(children) == 1
    assert children[0].poll() is not None
    assert service._slots.acquire(blocking=False)
    assert service._slots.acquire(blocking=False)


@pytest.mark.parametrize("bad", [np.nan, -5, 0])
def test_invalid_detector_output_cannot_be_treated_as_no_faces(monkeypatch, bad):
    import cv2

    class Detector:
        def detect(self, image):
            result = np.ones((1, 15), dtype=np.float32)
            result[0, 2] = bad
            result[0, -1] = .9
            return 1, result

    monkeypatch.setattr(cv2.FaceDetectorYN, "create", lambda *args: Detector())
    with pytest.raises(ValueError):
        worker.transform(png(Image.new("RGB", (64, 64))))
