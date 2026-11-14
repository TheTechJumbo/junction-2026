"""Standalone isolated child. All input/output through pipes, no image files/network."""
import hashlib
import io
import json
import math
import sys
import warnings
from pathlib import Path

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 1024 * 1024
MAX_DIMENSION = 1280
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def transform(data):
    import cv2
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps

    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    model = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"
    if model.stat().st_size != 232589 or hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("Unverified detector model")
    if not 0 < len(data) <= MAX_INPUT_BYTES:
        raise ValueError("Input limit")
    with Image.open(io.BytesIO(data)) as source:
        width, height = source.size
        if (source.format not in {"JPEG", "PNG"} or getattr(source, "n_frames", 1) != 1
                or not 32 <= width <= MAX_DIMENSION or not 32 <= height <= MAX_DIMENSION
                or width * height > MAX_PIXELS):
            raise ValueError("Pixel limit")
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.load()
    width, height = image.size
    detector = cv2.FaceDetectorYN.create(
        str(model), "", (width, height), 0.6, 0.3, 5000,
        cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU,
    )
    _, faces = detector.detect(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR))
    boxes = []
    if faces is not None:
        if len(faces) > 64 or faces.ndim != 2 or faces.shape[1] != 15:
            raise ValueError("Invalid/excessive detector output")
        for face in faces:
            if not np.isfinite(face).all():
                raise ValueError("Nonfinite detector output")
            x, y, w, h = (float(value) for value in face[:4])
            if w <= 0 or h <= 0 or not .6 <= float(face[-1]) <= 1:
                raise ValueError("Invalid detector box")
            # Expand every detected box 35% in each direction to include its edges.
            box = (max(0, math.floor(x - .35 * w)), max(0, math.floor(y - .35 * h)),
                   min(width, math.ceil(x + 1.35 * w)), min(height, math.ceil(y + 1.35 * h)))
            left, top, right, bottom = box
            if left >= right or top >= bottom:
                raise ValueError("Detector box outside image")
            region = image.crop(box)
            # Strong local blur retains scene context. It is not an anonymity guarantee.
            region = region.filter(ImageFilter.GaussianBlur(radius=max(right-left, bottom-top) / 3))
            image.paste(region, box)
            boxes.append(box)
    # A fresh RGB image and PNG encoding discard source metadata including EXIF/XMP.
    clean = Image.new("RGB", image.size)
    clean.paste(image)
    output = io.BytesIO()
    clean.save(output, format="PNG")
    encoded = output.getvalue()
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError("Output limit")
    return {"face_boxes": boxes, "status": "detected_faces_redacted" if boxes else "no_faces_detected"}, encoded


def main():
    try:
        metadata, data = transform(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        sys.stdout.buffer.write(json.dumps(metadata, separators=(",", ":")).encode() + b"\n" + data)
    except Exception:
        # No originals, exception details, file paths or provider data in error output.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
