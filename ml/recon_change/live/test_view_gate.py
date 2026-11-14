#!/usr/bin/env python3
"""Offline test of the relay's cheap gate: the same view is answered without the model, a small change in
view is reported with its place, another view asks the model. Needs OpenCV; no cluster.

    python live/test_view_gate.py
"""
import json
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402

KEY = "test-key"
FIX = {"state": "found", "confidence": 0.9, "position": [0.0, 0.0, 0.0], "heading_deg": 0,
       "waypoints": [{"name": "pill box", "distance_m": 2.0, "bearing_deg": 0, "position": [0.0, 2.0, 0.0]}],
       "track": [[0, 0]], "map": {"points": [], "cameras": []}, "match_per_frame": [0.9], "seconds": {"reconstruct": 0.1},
       "items": [], "items_looked_for": []}


class Gateway:
    """Stands in for the cluster and counts how often the model is asked."""

    def __init__(self):
        self.asked = 0

    def call(self, header, blob=b""):
        if header["op"] == "locate":
            self.asked += 1
            return {"ok": True, "job": f"job{self.asked}", "result": json.loads(json.dumps(FIX))}, b""
        if header["op"] == "baseline":
            return {"ok": True, "job": "baseline", "result": {"frames": 24}}, b""
        return {"ok": True, "zones": ["kitchen"], "workers": {}}, b""


def room(seed):
    """A made-up view with plenty of pattern: blobs of grey on a gradient."""
    rng = np.random.default_rng(seed)
    picture = np.tile(np.linspace(60, 180, 640, dtype=np.float32), (360, 1))
    for _ in range(40):
        x, y, r = rng.integers(0, 640), rng.integers(0, 360), int(rng.integers(12, 50))
        cv2.circle(picture, (int(x), int(y)), r, float(rng.integers(20, 235)), -1)
    return cv2.cvtColor(picture.astype(np.uint8), cv2.COLOR_GRAY2BGR)


def jpeg(picture, quality=85):
    return cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


def again(picture, seed):
    """The same view a moment later: sensor noise, another exposure, harder compression."""
    rng = np.random.default_rng(seed)
    noisy = np.clip(picture.astype(np.float32) * rng.uniform(0.85, 1.15) + rng.normal(0, 5, picture.shape), 0, 255)
    return jpeg(noisy.astype(np.uint8), 60)


def main():
    gateway = Gateway()
    evidence = Path(tempfile.mkdtemp(prefix="beacon-gate-test-"))
    handler = relay.make_handler(gateway, evidence, KEY, None, pick=None)
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, raw=None):
        request = urllib.request.Request(url + path, data=raw, headers={"X-Key": KEY}, method="POST" if raw is not None else "GET")
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())

    kitchen = room(1)
    # The first photo of a zone always asks the model.
    fix = call("/locate?zone=kitchen", jpeg(kitchen))
    assert gateway.asked == 1 and fix["view"]["kind"] == "first" and fix["view"]["model_asked"] and not fix.get("reused"), fix["view"]

    # The same view again, three times: the last answer is given again and the model is not asked.
    for i in range(3):
        fix = call("/locate?zone=kitchen", again(kitchen, i))
        assert gateway.asked == 1 and fix["reused"] and fix["view"]["kind"] == "same" and not fix["view"]["model_asked"], fix["view"]
        assert fix["state"] == "found" and fix["waypoints"][0]["name"] == "pill box" and fix["seconds"]["total"] < 0.5, fix["seconds"]
    assert fix["view"]["counts"]["model_not_asked"] == 3
    assert call("/where?zone=kitchen")["reused"] is True

    # Something in view is taken away (a patch of the picture is painted over): the camera stood still, so it is
    # a change in view. The model is asked, and the place of the change comes back with a before and a now picture.
    moved = kitchen.copy()
    cv2.rectangle(moved, (400, 200), (470, 260), (128, 128, 128), -1)
    fix = call("/locate?zone=kitchen", again(moved, 7))
    view = fix["view"]
    assert gateway.asked == 2 and view["kind"] == "local" and view["model_asked"] and not fix.get("reused"), view
    u0, v0, u1, v1 = view["box"]
    assert u0 <= 400 / 640 and u1 >= 470 / 640 and v0 <= 200 / 360 and v1 >= 260 / 360 and (u1 - u0) < 0.5, view["box"]
    for name in ("before", "now"):
        assert (evidence / view["images"][name].removeprefix("/evidence/")).exists(), view["images"]
    listed = call("/changes?zone=kitchen")
    assert len(listed["changes"]) == 1 and listed["changes"][0]["box"] == view["box"] and listed["counts"]["something_moved"] == 1, listed

    # The changed view is now what the model has seen: the same view again is free once more.
    fix = call("/locate?zone=kitchen", again(moved, 8))
    assert gateway.asked == 2 and fix["reused"], fix["view"]

    # Another view altogether: the camera moved, the model is asked, and nothing is reported as moved.
    fix = call("/locate?zone=kitchen", jpeg(room(2)))
    assert gateway.asked == 3 and fix["view"]["kind"] == "moved" and len(call("/changes?zone=kitchen")["changes"]) == 1, fix["view"]

    # &fresh=1 asks the model whatever the view, and an answer older than the limit is not given again.
    call("/locate?zone=kitchen&fresh=1", jpeg(room(2)))
    assert gateway.asked == 4
    relay.REUSE_FOR_S = 0
    fix = call("/locate?zone=kitchen", jpeg(room(2)))
    assert gateway.asked == 5 and fix["view"]["kind"] == "same" and fix["view"]["model_asked"], fix["view"]
    relay.REUSE_FOR_S = 45

    # A new baseline starts the gate again; a zone the gate has not seen is not affected by another zone.
    call("/baseline?zone=kitchen", b"PK-stand-in")
    call("/locate?zone=kitchen", jpeg(room(2)))
    assert gateway.asked == 6

    server.shutdown()
    print("view gate: all checks passed")


if __name__ == "__main__":
    main()
