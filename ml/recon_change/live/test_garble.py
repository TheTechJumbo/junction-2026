#!/usr/bin/env python3
"""Offline test of the keyed colour garbling (garble.py): it is a bijection of the colour cube that the key undoes, the
fast LUT path agrees with the exact transform, garbled frames are far from the photo and no colour matrix brings them
back, different keys give different codes, the reference values for other implementations (PHONE_SPEC.md) hold, and a
frame takes milliseconds. Needs OpenCV and NumPy; no network, no cluster, no real pictures (made-up rooms).

    python live/test_garble.py
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import garble  # noqa: E402
from test_anonymise import room  # noqa: E402

KEY = bytes(range(32))
# G(colour) for KEY, to 6 decimals: the values PHONE_SPEC.md gives another implementation to check itself against.
REFERENCE = {(0.2, 0.5, 0.8): None, (0.0, 0.0, 0.0): None, (1.0, 1.0, 1.0): None, (0.9, 0.1, 0.4): None}
REFERENCE_FILE = Path(__file__).resolve().parent / "garble_reference.txt"
LIMIT_MS = 80  # a 960 x 720 frame; about 12 ms on the laptop when it is not busy


def check_bijection():
    g = garble.Garbler(KEY)
    x = np.random.default_rng(1).random((100_000, 3))
    y = g.forward(x)
    assert y.min() >= 0 and y.max() <= 1, (y.min(), y.max())
    back = g.inverse(y)
    assert np.abs(back - x).max() < 1e-9, np.abs(back - x).max()
    corners = np.array([[a, b, c] for a in (0, 1) for b in (0, 1) for c in (0, 1)], float)
    assert np.allclose(g.inverse(g.forward(corners)), corners, atol=1e-9)
    assert np.allclose(g.decode_colours(g.encode_colours(x * 255)), x * 255, atol=1e-6)
    print(f"bijection: 100k colours there and back, worst {np.abs(back - x).max():.1e}; cube corners and splat colours too")


def check_keys():
    a, b = garble.Garbler(KEY), garble.Garbler(KEY)
    other = garble.Garbler(bytes(range(1, 33)))
    x = np.random.default_rng(2).random((5000, 3))
    assert np.array_equal(a.forward(x), b.forward(x)), "the same key must give the same code"
    gap = np.abs(a.forward(x) - other.forward(x)).mean()
    assert gap > 0.1, gap
    wrong = other.inverse(a.forward(x))  # undone with the wrong key
    assert np.abs(wrong - x).mean() > 0.1
    for bad in (b"", b"short", "not bytes", None):
        try:
            garble.Garbler(bad)
            raise AssertionError(f"{bad!r} accepted as a key")
        except ValueError:
            pass
    print(f"keys: same key same code; another key differs by {gap:.2f} on average and does not undo it; short keys refused")


def check_reference():
    """Fixed values for KEY, so a second implementation (the phone app) can prove it builds the same G."""
    g = garble.Garbler(KEY)
    points = np.array(list(REFERENCE), float)
    values = g.forward(points)
    lines = [" ".join(f"{v:.6f}" for v in p) + " -> " + " ".join(f"{v:.6f}" for v in out) for p, out in zip(points, values)]
    stream = garble._stream(KEY, "tone_in", 4)
    lines.append("stream(tone_in)[0:4] = " + " ".join(f"{v:.10f}" for v in stream))
    lines.append("normal(tone_in)[0:4] = " + " ".join(f"{v:.10f}" for v in garble._normal(KEY, "tone_in", 4)))
    lines.append(f"order = {g.order.tolist()} first = {g.target[0]} flips = {[int(v) for v in np.r_[g.flip_in, g.flip_out, g.flip_layer]]}")
    text = "\n".join(lines) + "\n"
    if REFERENCE_FILE.exists():
        assert REFERENCE_FILE.read_text() == text, "garble.py no longer builds the code PHONE_SPEC.md describes:\n" + text
    else:
        REFERENCE_FILE.write_text(text)
    print("reference: key 00..1f gives the values in garble_reference.txt\n  " + "\n  ".join(lines[:2]))


def oracle_affine_psnr(garbled, plain):
    """The best 3 x 3 colour matrix plus offset from garbled to true colours, fitted knowing the true picture."""
    X = np.c_[garbled.reshape(-1, 3) / 255.0, np.ones(garbled.size // 3)]
    Y = plain.reshape(-1, 3) / 255.0
    A, *_ = np.linalg.lstsq(X, Y, rcond=None)
    return garble.psnr(np.clip(X @ A, 0, 1) * 255, Y * 255)


def check_pictures():
    g = garble.Garbler(KEY)
    worst_lut, worst_back, worst_jpeg, best_affine, closest = 99.0, 99.0, 99.0, 0.0, 0.0
    for seed in range(5):
        picture = room(seed)
        hidden = g.garble_bgr(picture)
        assert hidden.shape == picture.shape and hidden.dtype == np.uint8
        exact = np.clip(g.forward(picture[..., ::-1].reshape(-1, 3) / 255.0) * 255 + 0.5, 0, 255).astype(np.uint8)
        worst_lut = min(worst_lut, garble.psnr(hidden, exact.reshape(picture.shape)[..., ::-1]))
        worst_back = min(worst_back, garble.psnr(g.ungarble_bgr(hidden), picture))
        sent = cv2.imdecode(np.frombuffer(garble.encode_jpeg(hidden), np.uint8), cv2.IMREAD_COLOR)
        worst_jpeg = min(worst_jpeg, garble.psnr(g.ungarble_bgr(sent), picture))
        best_affine = max(best_affine, oracle_affine_psnr(hidden, picture))
        closest = max(closest, garble.psnr(hidden, picture))
    assert worst_lut > 45, worst_lut
    assert worst_back > 38, worst_back
    assert worst_jpeg > 30, worst_jpeg
    assert closest < 15, closest
    assert best_affine < 33, best_affine
    data = g.garble_jpeg(cv2.imencode(".jpg", room(9))[1].tobytes())
    assert data[:2] == b"\xff\xd8" and b"Exif" not in data
    for junk in (b"", b"not a picture"):
        try:
            g.garble_jpeg(junk)
            raise AssertionError("junk garbled")
        except ValueError:
            pass
    print(f"pictures: LUT within {worst_lut:.1f} dB of exact, back again {worst_back:.1f} dB (through JPEG {worst_jpeg:.1f} dB); "
          f"garbled frame vs photo at most {closest:.1f} dB; best colour matrix fitted with the photo known: {best_affine:.1f} dB")


def check_speed():
    t0 = time.perf_counter()
    g = garble.Garbler(bytes(range(7, 39)))
    built = 1000 * (time.perf_counter() - t0)
    picture = room(3)
    g.garble_bgr(picture)
    times = []
    for _ in range(10):
        t0 = time.perf_counter()
        g.garble_bgr(picture)
        times.append(1000 * (time.perf_counter() - t0))
    ms = float(np.median(times))
    assert ms < LIMIT_MS, ms
    print(f"speed: built in {built:.0f} ms, {ms:.1f} ms a 960 x 720 frame (one thread)")


def main():
    check_bijection()
    check_keys()
    check_reference()
    check_pictures()
    check_speed()
    print("garble: all checks passed")


if __name__ == "__main__":
    main()
