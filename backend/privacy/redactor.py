"""Run pixel processing in a killable child; never write source imagery to a file."""
import io
import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 1024 * 1024
MAX_DIMENSION = 1280


class RedactionUnavailable(Exception):
    """Reject storage when processing cannot finish; never fall back to original bytes."""


@dataclass(frozen=True)
class RedactionResult:
    image_bytes: bytes
    face_boxes: tuple[tuple[int, int, int, int], ...]
    status: str  # detected_faces_redacted | no_faces_detected; neither means face-free
    content_type: str = "image/png"

    @property
    def faces_redacted(self):
        return len(self.face_boxes)


class LocalFaceRedactor:
    """Use one instance per app. Synchronous: call from a bounded worker thread.

    Two simultaneous child processes at most; saturation rejects immediately.
    The wall budget covers spawn/decode/detection/blur/encode. This object neither
    persists data nor calls a network service. Review zero detections explicitly.
    """

    def __init__(self, *, timeout_seconds: float = 3):
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 10:
            raise ValueError("Redaction timeout must be finite and in (0, 10]")
        self.timeout_seconds = timeout_seconds
        self._slots = threading.BoundedSemaphore(2)

    def redact(self, image_bytes: bytes) -> RedactionResult:
        deadline = time.monotonic() + self.timeout_seconds
        if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= MAX_INPUT_BYTES:
            raise RedactionUnavailable("Image input is empty or exceeds the byte limit")
        # Cheap header check prevents oversized rasters reaching native decoding.
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
                if (image.format not in {"JPEG", "PNG"} or getattr(image, "n_frames", 1) != 1
                        or not 32 <= width <= MAX_DIMENSION or not 32 <= height <= MAX_DIMENSION
                        or width * height > MAX_PIXELS):
                    raise ValueError()
        except Exception:
            raise RedactionUnavailable("Image format or pixel dimensions are unsupported") from None
        if not self._slots.acquire(blocking=False):
            raise RedactionUnavailable("Face redaction is busy; retry later")
        try:
            return self._process(image_bytes, deadline)
        finally:
            self._slots.release()

    def _process(self, image_bytes, deadline):
        worker = Path(__file__).with_name("worker.py")
        process = None
        try:
            # Isolated interpreter ignores PYTHONPATH and user-site packages. Do not
            # inherit application/provider credentials or write imagery to tempfiles.
            environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT")
                           if key in os.environ}
            environment.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
                               MKL_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", str(worker)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment,
                close_fds=True,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, self.timeout_seconds)
            output, _ = process.communicate(image_bytes, timeout=remaining)
            if process.returncode or not 0 < len(output) <= MAX_OUTPUT_BYTES + 8192:
                raise ValueError()
            header, data = output.split(b"\n", 1)
            metadata = json.loads(header)
            boxes = tuple(tuple(box) for box in metadata["face_boxes"])
            if (metadata["status"] not in {"detected_faces_redacted", "no_faces_detected"}
                    or bool(boxes) != (metadata["status"] == "detected_faces_redacted")
                    or len(data) > MAX_OUTPUT_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n")):
                raise ValueError()
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(process.args, self.timeout_seconds)
            return RedactionResult(data, boxes, metadata["status"])
        except subprocess.TimeoutExpired:
            raise RedactionUnavailable("Face redaction timed out; image was not accepted") from None
        except Exception:
            raise RedactionUnavailable("Face redaction failed; image was not accepted") from None
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate()  # reap child, close pipes; no lingering native work
