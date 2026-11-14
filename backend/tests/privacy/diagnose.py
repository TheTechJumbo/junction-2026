"""Bounded synthetic-only YuNet matrix; no household input paths accepted.

Run: python backend/tests/privacy/diagnose.py --repetitions 10
Compare the production 32px minimum with adjacent 64px input, without changing
production processing. Every sample gets a fresh isolated interpreter/CPU model.
"""
import argparse
import collections
import io
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

CASES = ("green8_jpeg", "orange_navy32x24_png", "blue3x2_png")
LABELS = {"ok_no_faces_detected", "ok_detected_faces_redacted", "ModelIntegrityError",
          "InputByteLimitError", "ImagePixelLimitError", "DetectorShapeError",
          "DetectorNonfiniteError", "DetectorGeometryError", "DetectorScoreError",
          "DetectorOutsideImageError", "EncodedOutputLimitError", "AttributeError",
          "ImportError", "ModuleNotFoundError", "ValueError", "OSError", "MemoryError",
          "RuntimeError", "error", "UnexpectedError", "timeout", "worker_exit"}


def child(case, padding):
    try:
        import cv2
        from PIL import Image

        if case == "green8_jpeg":
            image, format = Image.new("RGB", (8, 8), "green"), "JPEG"
        elif case == "orange_navy32x24_png":
            image, format = Image.new("RGB", (32, 24), "orange"), "PNG"
            image.paste("navy", (16, 0, 32, 24))
        else:
            image, format = Image.new("RGB", (3, 2), "blue"), "PNG"
        data = io.BytesIO()
        image.save(data, format=format)
        # Match the API's fresh RGB encode after decoded/metadata-normalized input.
        with Image.open(io.BytesIO(data.getvalue())) as source:
            normalized = Image.new("RGB", source.size)
            normalized.paste(source.convert("RGB"))
        data = io.BytesIO()
        normalized.save(data, format=format)
        border = cv2.copyMakeBorder

        def padded(pixels, top, bottom, left, right, kind):
            return border(pixels, top, max(bottom, padding-pixels.shape[0]),
                          left, max(right, padding-pixels.shape[1]), kind)

        cv2.copyMakeBorder = padded
        worker = runpy.run_path(str(Path(__file__).resolve().parents[2] / "privacy" / "worker.py"))
        metadata, _ = worker["transform"](data.getvalue())
        label = "ok_" + metadata["status"]
    except Exception as error:
        name = type(error).__name__
        label = name if name in LABELS else "UnexpectedError"
    print(label, flush=True)


def matrix(repetitions):
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    environment.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
                       MKL_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    failed = False
    for case in CASES:
        for padding in (32, 64):
            counts = collections.Counter()
            for _ in range(repetitions):
                try:
                    result = subprocess.run(
                        [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                         "--child", "--case", case, "--padding", str(padding)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, env=environment, timeout=3, check=False,
                    )
                    label = result.stdout.strip().decode("ascii", errors="replace")
                    if result.returncode or label not in LABELS:
                        label = "worker_exit"
                except subprocess.TimeoutExpired:
                    label = "timeout"
                counts[label] += 1
                if padding == 32 and not label.startswith("ok_"):
                    failed = True
            print(json.dumps({"case": case, "minimum_padding": padding,
                              "repetitions": repetitions, "categories": dict(counts)},
                             sort_keys=True), flush=True)
    return int(failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--case", choices=CASES, default=CASES[0], help=argparse.SUPPRESS)
    parser.add_argument("--padding", type=int, choices=(32, 64), default=32, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 20:
        parser.error("repetitions must be in [1, 20]")
    if args.child:
        child(args.case, args.padding)
        return 0
    return matrix(args.repetitions)


if __name__ == "__main__":
    raise SystemExit(main())
