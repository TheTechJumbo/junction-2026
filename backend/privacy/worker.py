"""Standalone isolated child. All input/output through pipes, no image files/network."""
import hashlib
import io
import json
import math
import sys
import warnings
from pathlib import Path

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000
DETECTOR_EDGE = 1280
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"



class ModelIntegrityError(ValueError):
    pass


class InputByteLimitError(ValueError):
    pass


class ImagePixelLimitError(ValueError):
    pass


class DetectorShapeError(ValueError):
    pass


class DetectorNonfiniteError(ValueError):
    pass


class DetectorGeometryError(ValueError):
    pass


class DetectorScoreError(ValueError):
    pass


class DetectorOutsideImageError(ValueError):
    pass


class EncodedOutputLimitError(ValueError):
    pass

def transform(data):
    import cv2
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps

    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    model = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"
    if model.stat().st_size != 232589 or hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ModelIntegrityError("Unverified detector model")
    if not 0 < len(data) <= MAX_INPUT_BYTES:
        raise InputByteLimitError("Input limit")
    with Image.open(io.BytesIO(data)) as source:
        width, height = source.size
        if (source.format not in {"JPEG", "PNG"} or getattr(source, "n_frames", 1) != 1
                or width < 1 or height < 1
                or width * height > MAX_PIXELS):
            raise ImagePixelLimitError("Pixel limit")
        output_format = source.format
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.load()
    width, height = image.size
    scale = min(1.0, DETECTOR_EDGE / max(width, height))
    detector_width, detector_height = max(1, round(width * scale)), max(1, round(height * scale))
    detector_image = image.resize((detector_width, detector_height), Image.Resampling.LANCZOS)
    pixels = cv2.cvtColor(np.asarray(detector_image), cv2.COLOR_RGB2BGR)
    pixels = cv2.copyMakeBorder(pixels, 0, max(0, 32-detector_height),
                               0, max(0, 32-detector_width), cv2.BORDER_CONSTANT)
    detector = cv2.FaceDetectorYN.create(
        str(model), "", (pixels.shape[1], pixels.shape[0]), 0.6, 0.3, 5000,
        cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU,
    )
    _, faces = detector.detect(pixels)
    boxes = []
    if faces is not None:
        if len(faces) > 64 or faces.ndim != 2 or faces.shape[1] != 15:
            raise DetectorShapeError("Invalid/excessive detector output")
        for face in faces:
            if not np.isfinite(face).all():
                raise DetectorNonfiniteError("Nonfinite detector output")
            x, y, w, h = (float(value) for value in face[:4])
            if w <= 0 or h <= 0:
                raise DetectorGeometryError("Invalid detector box")
            if not .6 <= float(face[-1]) <= 1:
                raise DetectorScoreError("Invalid detector score")
            # Map actual resized axes back independently (rounding may alter aspect).
            x, w = x * width / detector_width, w * width / detector_width
            y, h = y * height / detector_height, h * height / detector_height
            # Expand every detected box 35% in each direction to include its edges.
            box = (max(0, math.floor(x - .35 * w)), max(0, math.floor(y - .35 * h)),
                   min(width, math.ceil(x + 1.35 * w)), min(height, math.ceil(y + 1.35 * h)))
            left, top, right, bottom = box
            if left >= right or top >= bottom:
                raise DetectorOutsideImageError("Detector box outside image")
            region = image.crop(box)
            # Strong local blur retains scene context. It is not an anonymity guarantee.
            region = region.filter(ImageFilter.GaussianBlur(radius=max(right-left, bottom-top) / 3))
            image.paste(region, box)
            boxes.append(box)
    # A fresh RGB image discards source metadata including EXIF/XMP.
    clean = Image.new("RGB", image.size)
    clean.paste(image)
    output = io.BytesIO()
    if output_format == "JPEG":
        clean.save(output, format="JPEG", quality=90)
    else:
        clean.save(output, format="PNG")
    encoded = output.getvalue()
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise EncodedOutputLimitError("Output limit")
    return {"face_boxes": boxes, "status": "detected_faces_redacted" if boxes else "no_faces_detected",
            "content_type": "image/jpeg" if output_format == "JPEG" else "image/png"}, encoded


def main():
    try:
        metadata, data = transform(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        sys.stdout.buffer.write(json.dumps(metadata, separators=(",", ":")).encode() + b"\n" + data)
    except Exception as error:
        # Exception class only; never emit messages, paths, image bytes or a traceback.
        allowed = {"AttributeError", "ImportError", "ModuleNotFoundError", "ValueError",
                   "OSError", "MemoryError", "RuntimeError", "UnidentifiedImageError",
                   "DecompressionBombError", "DecompressionBombWarning", "error",
                   "ModelIntegrityError", "InputByteLimitError", "ImagePixelLimitError",
                   "DetectorShapeError", "DetectorNonfiniteError", "DetectorGeometryError",
                   "DetectorScoreError", "DetectorOutsideImageError", "EncodedOutputLimitError"}
        name = type(error).__name__
        sys.stderr.write(name if name in allowed else "UnexpectedError")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
