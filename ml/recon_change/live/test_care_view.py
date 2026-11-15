#!/usr/bin/env python3
"""Offline test of the caretaker's page: the trained splat lined up with the room map (care_space.py) and the
relay's /care and /care/space. Synthetic cameras, no cluster, no OpenCV.

    python live/test_care_view.py
"""
import json
import math
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import care_space  # noqa: E402
import relay  # noqa: E402

KEY = "test-key"


def walk(n=40, seed=0):
    """A wearer turning round a small room: map positions (east, north, up) and headings, level cameras."""
    rng = np.random.default_rng(seed)
    headings = np.cumsum(np.r_[0.0, rng.uniform(4, 16, n - 1)]) % 360 - 180 + 180  # one way round, then on
    headings = (headings + 180) % 360 - 180
    t = np.linspace(0, 1, n)
    positions = np.stack([0.6 * np.sin(2 * math.pi * t), 0.3 * (1 - np.cos(2 * math.pi * t)), 0.02 * np.sin(6 * t)], 1)
    return positions, headings


def trained_cameras(positions, headings, turn, scale, shift):
    """The same cameras as a splat trained from the recording sees them: another rotation, scale and origin, OpenCV
    axes. Returns E (N, 3, 4) and the true matrix from trained to map axes."""
    c, s = math.cos(turn), math.sin(turn)
    tilt = np.array([[1, 0, 0], [0, math.cos(0.3), -math.sin(0.3)], [0, math.sin(0.3), math.cos(0.3)]])
    rot = tilt @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])  # map -> trained
    E = []
    for m, h in zip(positions, np.radians(headings)):
        axes_map = np.stack([[math.cos(h), -math.sin(h), 0], [0, 0, -1], [math.sin(h), math.cos(h), 0]])  # right, down, ahead
        R = axes_map @ rot.T  # world-to-camera rows in trained axes
        centre = rot @ m / scale + shift
        E.append(np.hstack([R, (-R @ centre)[:, None]]))
    truth = np.eye(4)
    truth[:3, :3] = scale * rot.T
    truth[:3, 3] = -scale * rot.T @ shift
    return np.array(E), truth


def check_space():
    positions, headings = walk()
    E, truth = trained_cameras(positions, headings, turn=0.7, scale=2.4, shift=np.array([0.2, -0.1, 0.4]))
    # The room map kept most views, not all, and a different set than the trained splat (both drop a few).
    keep_map = [i for i in range(len(positions)) if i not in (5, 17, 30)]
    cameras = [{"frame": k, "position": positions[i].round(3).tolist(), "heading_deg": round(float(headings[i]), 1)}
               for k, i in enumerate(keep_map)]
    keep_trained = [i for i in range(len(positions)) if i not in (9, 22)]
    fit = care_space.space(E[keep_trained], cameras)
    assert fit["ok"], fit
    assert fit["pairs"] >= len(keep_map) - 3, fit
    M = np.array(fit["matrix"])
    centres = np.array([-e[:, :3].T @ e[:, 3] for e in E])
    got = centres @ M[:3, :3].T + M[:3, 3]
    err = np.linalg.norm(got - positions, axis=1).max()
    assert err < 0.03, f"cameras land {err:.3f} m from where the room map has them"
    assert abs(np.linalg.det(M[:3, :3]) ** (1 / 3) - 2.4) < 0.02, fit["scale"]
    print(f"  lined up: {fit['pairs']} pairs, worst camera {err * 100:.1f} cm off, scale {fit['scale']}")
    same = sum(keep_map[f] == keep_trained[j] for f, j in fit["paired"])
    assert same >= len(fit["paired"]) - 1, f"only {same} of {len(fit['paired'])} pairs are the same photo"

    # Other pictures: headings that do not follow the same walk are refused, not forced into place.
    rng = np.random.default_rng(3)
    stranger = [{**c, "heading_deg": float(rng.uniform(-180, 180)), "position": (rng.normal(size=3) * 2).tolist()} for c in cameras]
    refused = care_space.space(E, stranger)
    assert not refused["ok"] and refused["why"], refused
    assert not care_space.space(E[:2], cameras)["ok"]
    print(f"  other pictures refused: {refused['why']}")


class Gateway:
    """Stands in for the SSH link: the zone's named places, as the gateway lists them."""
    waypoints = [{"name": "pill box", "frame": 0, "u": 0.5, "v": 0.5}, {"name": "door", "frame": 3, "u": 0.2, "v": 0.6},
                 {"name": "nowhere", "frame": 999, "u": 0.5, "v": 0.5}]

    def call(self, header, blob=b""):
        if header["op"] == "waypoints":
            return {"ok": True, "zone": header["zone"], "waypoints": self.waypoints}, b""
        return {"ok": False, "error": "not in this test"}, b""


def get(base, path, key=KEY):
    req = urllib.request.Request(base + path, headers={"X-Key": key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as reply:
            return reply.status, reply.headers.get("Content-Type"), reply.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Content-Type"), err.read()


def check_relay():
    positions, headings = walk()
    E, _ = trained_cameras(positions, headings, turn=-1.2, scale=2.0, shift=np.zeros(3))
    cameras = [{"frame": i, "position": p.round(3).tolist(), "heading_deg": round(float(h), 1)} for i, (p, h) in enumerate(zip(positions, headings))]
    with tempfile.TemporaryDirectory() as tmp:
        evidence = Path(tmp)
        for name, files in {"job1": ["room.splat"], "job2": ["room.splat"], "train-kitchen-x-1": ["trained.splat"]}.items():
            (evidence / name).mkdir()
            for f in files:
                (evidence / name / f).write_bytes(bytes(32))
        (evidence / "train-kitchen-x-1" / "labels.json").write_text('{"objects": [{"name": "cup", "position": [0, 0, 1]}]}')
        # views.json and depths.bin as video_map.py exports them: every keyframe sees a wall 1 unit ahead (2 m)
        K = np.array([[400.0, 0, 259], [0, 400.0, 259], [0, 0, 1]])
        (evidence / "train-kitchen-x-1" / "views.json").write_text(json.dumps(
            {"size": [518, 518], "depth_size": [130, 130], "K": [K.tolist()] * len(E), "E": E.tolist(), "depths": "depths.bin"}))
        np.full((len(E), 130, 130), 1.0, np.float16).tofile(evidence / "train-kitchen-x-1" / "depths.bin")
        np.savez(evidence / "train-kitchen-x-1" / "cameras.npz", E=E.astype(np.float32), K=np.zeros((len(E), 3, 3)), keyframes=np.arange(len(E)))
        room = {"job": "job1", "splat": "/evidence/job1/room.splat", "cameras": cameras, "places": [{"name": "pill box", "position": [1.0, 2.0, 0.0]}],
                "at": time.time(), "median_depth_m": 2.0, "fov_deg": 55,
                "trained": {"splat": "/evidence/train-kitchen-x-1/trained.splat", "median_depth": 1.0, "gaussians": 10, "psnr": 24.0}}
        (evidence / "rooms.json").write_text(json.dumps({"kitchen": room, "hall": {**room, "job": "job2", "splat": "/evidence/job2/room.splat", "trained": None}}))
        handler = relay.make_handler(Gateway(), evidence, KEY, None, pick=None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            code, kind, body = get(base, "/care?zone=kitchen")
            assert code == 200 and kind.startswith("text/html") and b"/care/space" in body, (code, kind)
            assert get(base, "/care?zone=kitchen", key="")[0] == 403
            assert get(base, "/care/space?zone=kitchen", key="wrong")[0] == 403

            code, _, body = get(base, "/care/space?zone=kitchen")
            space = json.loads(body)
            assert code == 200 and space["kind"] == "trained", space
            assert space["splat"].endswith("/trained.splat") and space["room_splat"] == "/evidence/job1/room.splat"
            assert space["fit"]["pairs"] == len(cameras) and space["fit"]["rms_m"] < 0.02, space["fit"]
            assert space["places"][0]["name"] == "pill box"
            # labels made again after training sit next to the splat and are found there (rooms.json has none)
            assert space["trained"]["labels"] == "/evidence/train-kitchen-x-1/labels.json", space["trained"]
            assert json.loads(get(base, "/care/space?zone=kitchen")[2]) == space  # worked out once, then the same reply
            # Named places get a position from the trained depth: 2 m straight ahead of the camera they were named in.
            named = {p["name"]: p["position"] for p in space["named"]}
            assert set(named) == {"pill box", "door"}, space["named"]
            h = np.radians(headings[0])
            ahead = positions[0] + 2.0 * np.array([np.sin(h), np.cos(h), 0])
            assert np.linalg.norm(np.array(named["pill box"]) - ahead) < 0.05, (named["pill box"], ahead.round(2))
            d = np.linalg.norm(np.array(named["door"][:2]) - positions[3][:2])
            assert 1.9 < d < 2.4, d  # off to the left of camera 3, a little further than straight ahead

            code, _, body = get(base, "/care/space?zone=hall")
            hall = json.loads(body)
            assert code == 200 and hall["kind"] == "map" and hall["matrix"] is None and hall["why"], hall
            assert hall["named"] == []

            assert get(base, "/care/space?zone=nowhere")[0] == 404
            # The splat itself comes through the relay's evidence route, as on the page.
            assert get(base, space["splat"])[0] == 200
            print("  relay: /care page, /care/space trained and room-map replies, named places from depth, key required")
        finally:
            server.shutdown()


if __name__ == "__main__":
    check_space()
    check_relay()
    print("care view: all checks passed")
