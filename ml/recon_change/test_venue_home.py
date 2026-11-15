#!/usr/bin/env python3
"""Check venue_home.py (a recorded place as the family's home in 3D) without a model, GPU or relay.

  frame     the relay's /care/space matrix (scale, turn, shift) becomes a room frame that moves
            points exactly as the matrix does, also when the matrix is rounded
  floor     with far more table than floor in view, the floor is still the floor (hazards.floor_height
            would pick the table top)
  overlay   recordings laid over each other by vote: agreement stands, one recording's noise is outvoted
  pipeline  the example home written as a trained splat, with a /care/space reply that places it
            (scaled, turned and shifted), comes out as a floor plan of blocks (the table where it is),
            hazards and a simulated night in the room's axes: the night starts and ends at --base, visits each place of --trips, the door
            opens while the wearer is at it, and every file says what it is

    python test_venue_home.py
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

import demo_room as dr
import hazards as hz
import room_frame
import venue_home as vh

failures = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def similarity(scale=2.5, turn_deg=30.0, shift=(1.0, -2.0, 0.5)):
    a = np.radians(turn_deg)
    turn = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]]) @ np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])
    m = np.eye(4)
    m[:3, :3], m[:3, 3] = scale * turn, shift
    return m


def test_frame():
    m = similarity()
    p = np.random.default_rng(1).normal(size=(50, 3))
    frame = room_frame.from_matrix(m.round(6).tolist())
    check("the frame moves points as the matrix does", np.abs(room_frame.to_room(p, frame) - ((m[:3, :3] @ p.T).T + m[:3, 3])).max() < 1e-4)
    check("the frame's metres are the matrix's scale", abs(frame["metres"] - 2.5) < 1e-6)
    with tempfile.TemporaryDirectory() as d:
        Path(d, "space.json").write_text(json.dumps({"ok": True, "matrix": m.tolist()}))
        check("a saved /care/space reply loads as a frame", np.allclose(room_frame.load(Path(d, "space.json"))["up"], frame["up"]))


def test_floor():
    rng = np.random.default_rng(2)
    h = np.concatenate([rng.normal(-0.8, 0.01, 3000), rng.normal(-0.3, 0.02, 60000), rng.uniform(-0.8, 3.0, 40000)])
    check(f"the floor under the tables ({vh.floor_of(h):.2f})", abs(vh.floor_of(h) + 0.8) < 0.05)
    check("(the home's own guess would be the table top)", abs(hz.floor_height(h, np.ones(len(h), bool)) + 0.3) < 0.05)


def test_overlay():
    seen = np.ones((4, 4), bool)
    seen[3, 3] = False               # the first two never saw (3, 3)
    main, other = np.zeros((4, 4)), np.zeros((4, 4))
    main[0, 0] = main[1, 1] = 0.5   # (0, 0): a table both saw; (1, 1): noise in the main recording only
    other[0, 0] = 0.6
    only = np.zeros((4, 4), bool)
    only[3, 3] = True
    third = np.zeros((4, 4))
    third[3, 3] = 1.0                # (3, 3): only a third recording saw it, and it stands
    top = vh.overlay_tops([(main, seen), (other, seen), (third, only)])
    check("overlay: what two recordings agree on stands, at their median height", abs(top[0, 0] - 0.55) < 1e-9)
    check("overlay: noise in one recording is outvoted where the other saw floor", top[1, 1] == 0)
    check("overlay: a square only one recording saw is that recording's", top[3, 3] == 1.0)


def test_pipeline():
    m = similarity()
    inverse = np.linalg.inv(m)
    one = dr.make_map().splats
    rec = np.concatenate([one] * 4)  # a trained splat stacks several Gaussians on each spot of a surface
    rec["pos"] += np.random.default_rng(3).normal(0, 0.01, rec["pos"].shape).astype(np.float32)
    rec["pos"] = ((inverse[:3, :3] @ rec["pos"].T).T + inverse[:3, 3]).astype(np.float32)  # the splat's own frame
    places = [{"name": "bed" if k == "bed" else k.title() if k == "kitchen" else k, "position": v} for k, v in dr.PLACES.items()]
    cameras = [{"frame": i, "position": [0.7 + 0.27 * i, 1.2, 1.5], "heading_deg": 0} for i in range(10)]  # along the rug
    space = {"ok": True, "kind": "trained", "matrix": m.tolist(), "splat": "/evidence/train-example-1/trained.splat",
             "places": places, "cameras": cameras, "trained": {"session": "example", "psnr": 30.0}}
    trips = "02:47 bathroom/6; 03:09 front door/4, Kitchen/2"
    with tempfile.TemporaryDirectory() as d:
        Path(d, "space.json").write_text(json.dumps(space))
        Path(d, "trained.splat").write_bytes(rec.tobytes())
        argv = sys.argv
        sys.argv = ["venue_home.py", str(Path(d, "out")), "--space", str(Path(d, "space.json")), "--splat", str(Path(d, "trained.splat")),
                    "--base", "bed", "--trips", trips, "--name", "Example flat", "--fixture"]
        try:
            vh.main()
        finally:
            sys.argv = argv
        out = Path(d, "out")
        ply = out.joinpath("home.ply").read_bytes()
        header = ply[:ply.index(b"end_header")].decode()
        check("home.ply: blocks, z up, back faces can be culled", all(w in header for w in ("comment up z", "comment cull", "comment blocks")))
        faces = int(header.split("element face ")[1].split()[0])
        check(f"a calm plan: tens to a few thousand triangles ({faces})", 30 < faces < 20000)
        vert = np.frombuffer(ply[ply.index(b"end_header\n") + 11:], np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")]),
                             count=int(header.split("element vertex ")[1].split()[0]))
        check("the blocks are in the room's axes (5 x 4 m, floor at 0)", vert["x"].min() > -1 and vert["x"].max() < 6.2 and vert["y"].max() < 5.2
              and abs(float(np.percentile(vert["z"], 1))) < 0.1)
        on_table = (np.abs(vert["x"] - 2.2) < 0.7) & (np.abs(vert["y"] - 1.8) < 0.5)
        check("the table stands where it is, at table height", np.any(on_table & (np.abs(vert["z"] - vh.LEVELS[vh.TABLE][1]) < 0.05)))
        h = json.loads(out.joinpath("hazards.json").read_text())
        check("hazards say what they are and where from", h["label"].startswith("Example flat") and h["source"]["session"] == "example"
              and h["method"].startswith("shape") and h["up"] == "z")
        check("walks go from the base to every other place, names as given", {w[1] for w in h["walks"]} == {p["name"] for p in places} - {"bed"})
        n = json.loads(out.joinpath("night.json").read_text())
        check("the night is labelled simulated, from the bed", n["simulated"] and n["base"] == "bed" and "simulated" in n["label"])
        check(f"two trips ({n['summary']['trips']})", n["summary"]["trips"] == 2)
        check(f"each place of the trips visited, in order ({n['summary']['places']})", n["summary"]["places"] == ["bathroom", "front door", "Kitchen"])
        check("it starts and ends at the bed", min(np.linalg.norm(np.array(p["p"][:2]) - dr.PLACES["bed"][:2]) for p in n["points"][:1] + n["points"][-1:]) < 0.8)
        door = [e for e in n["events"] if e["type"] == "door_open"]
        stop = next((s for s in n["stops"] if s["place"] == "front door"), None)
        check("the door opens while the wearer is at it", door and stop and stop["t"] <= door[0]["t"] <= stop["until"])
        check("no more points than the dashboard draws", len(n["points"]) <= 1500)


if __name__ == "__main__":
    test_frame()
    test_floor()
    test_overlay()
    test_pipeline()
    print(f"{len(failures)} failed" if failures else "all passed")
    sys.exit(1 if failures else 0)
