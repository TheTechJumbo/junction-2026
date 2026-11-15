#!/usr/bin/env python3
"""Offline test of the worker's side of named places, on the synthetic room of test_synthetic.py (exact depth and
cameras stand in for the model, so no GPU and no weights): a place named in a picture and a place with a map position
of its own both come back as waypoints of a fix; a spot picked on the fix's photo (req "point") lands where the same
thing is on the map; op "spots" places picture spots in the room map's axes without building a splat; a fix told not
to grow (req "grow": false) adds no picture.

    python live/test_places_worker.py
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import test_synthetic as syn  # noqa: E402
import worker  # noqa: E402

STEP = 8  # each stand-in picture is one grey level: its synthetic view's number times STEP
VIEW = 30  # the synthetic view the wearer's photo shows (one of the "after" views: the box is still at A there)


def picture(n):
    return np.full((syn.H, syn.W, 3), n * STEP, np.uint8)


def where_on(E, point):
    """A 3D point of the synthetic room as u, v (0..1) in a view with cameras E."""
    x = E[:, :3] @ point + E[:, 3]
    return float((syn.FOCAL * x[0] / x[2] + syn.W / 2) / syn.W), float((syn.FOCAL * x[1] / x[2] + syn.H / 2) / syn.H)


def main():
    scene = syn.scene(syn.BOX_A, features=True)  # 36 views, the box at A in all of them

    def reconstruct(model, frames, long_side=None):  # the views these pictures stand for, with their exact depth
        pick = [int(round(float(f.mean()) / STEP)) for f in frames]
        return syn.Recon(scene.depth[pick], scene.conf[pick], scene.K[pick], scene.E[pick], scene.rgb[pick], scene.feat[pick])

    worker.rc.reconstruct = reconstruct
    root = Path(tempfile.mkdtemp(prefix="beacon-places-worker-"))
    worker.BASELINES = root / "baselines"
    zone = worker.BASELINES / "synth"
    zone.mkdir(parents=True)
    for n in range(24):
        cv2.imwrite(str(zone / f"{n:02d}.jpg"), picture(n), [cv2.IMWRITE_JPEG_QUALITY, 100])
    top = syn.BOX_A + np.array([0, 0, 0.15])  # the middle of the box's top
    u0, v0 = where_on(scene.E[0], top)
    door = [1.25, 2.5, 0.9]
    (zone / "waypoints.json").write_text(json.dumps([{"name": "box", "frame": 0, "u": u0, "v": v0},
                                                     {"name": "door", "position": door, "source": "live"}]))
    failures = []

    def check(ok, what):
        print(("  ok   " if ok else "  FAIL ") + what)
        if not ok:
            failures.append(what)

    # A fix: the wearer's photo is view VIEW; the spot picked on it is the top of the box.
    job = root / "fix"
    job.mkdir()
    with zipfile.ZipFile(job / "clip.zip", "w") as bundle:
        bundle.writestr("0000.jpg", cv2.imencode(".jpg", picture(VIEW), [cv2.IMWRITE_JPEG_QUALITY, 100])[1].tobytes())
    u1, v1 = where_on(scene.E[VIEW], top)
    worker.ITEMS = []
    result = worker.where(None, job, {"op": "locate", "zone": "synth", "file": "clip.zip", "point": {"u": u1, "v": v1},
                                      "grow": False})
    ways = {w["name"]: w for w in result["waypoints"]}
    check(result["state"] in ("found", "unsure"), f"the fix is placed ({result['state']})")
    check(set(ways) == {"box", "door"}, f"both places are waypoints of the fix ({sorted(ways)})")
    check(ways.get("door", {}).get("position") == [round(v, 2) for v in door], "the place with a position of its own is used as it is")
    box = np.array(ways["box"]["position"]) if "box" in ways else np.full(3, np.nan)
    point = np.array(result.get("point") or [np.nan] * 3)
    check(np.linalg.norm(point - box) < 0.15, f"the spot picked on the photo lands on the box's place "
          f"({np.round(point, 2).tolist()} vs {np.round(box, 2).tolist()})")
    check("picture_added" not in result and len(list(zone.glob("*.jpg"))) == 24, "a fix told not to grow adds no picture")

    # op "spots": the same places in the room map's axes, and a picture spot placed, with no splat written.
    job = root / "spots"
    job.mkdir()
    got = worker.room_map(None, job, {"op": "spots", "zone": "synth", "spots": [{"frame": 0, "u": u0, "v": v0}, {"frame": 5, "u": 0.5, "v": 0.02}]})
    named = {p["name"]: p["position"] for p in got["places"]}
    check(set(named) == {"box", "door"} and named["door"] == door, f"spots lists both places ({named})")
    check(got["spots"][0] is not None and np.linalg.norm(np.array(got["spots"][0]) - np.array(named["box"])) < 1e-3,
          "a picture spot lands where the place named at it is")
    check(np.linalg.norm(np.array(named["box"]) - box) < 0.15, "the room map's pass and the fix agree where the box is")
    check(not (job / "out" / "room.splat").exists(), "spots builds no splat")
    print("all checks passed" if not failures else f"{len(failures)} FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
