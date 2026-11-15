#!/usr/bin/env python3
"""An example home as a refined splat map and its mesh, for the dashboard's 3D view in demo mode.

Anna's kitchen and living room, about 5 x 4 m, drawn as surfaces of discs (no model, no clips):
floor, walls with a window and the front door, the kitchen counter and the pill box shelf, the
table, the sofa and the bed corner are static (fine detail); a chair that moves about is movable
(LOW detail, coarse). The map goes through splat_mesh.write_ply exactly as a real one would.

    python demo_room.py ../../dashboard/fixtures/home.ply

Coordinates are metres with z up (the PLY says so in a comment, "up z"); real maps are VGGT's
(y down), which is the viewer's default.
"""
import sys

import numpy as np

import splat_mesh as smesh
import splat_refine as sr

STEP = 0.025  # disc spacing: one disc every 2.5 cm
FLAT = np.array([128, 255, 128, 128], np.uint8)  # disc orientation; the viewer works out normals from the mesh

FLOOR, WALL, TRIM = (182, 160, 132), (222, 220, 212), (150, 142, 130)
COUNTER, WOOD, SOFA, BED = (238, 238, 232), (138, 96, 62), (64, 92, 140), (242, 236, 226)
DOOR, WINDOW, PILLS, RUG, CHAIR = (120, 84, 56), (150, 190, 225), (242, 176, 74), (178, 58, 72), (196, 120, 60)
BAG = (92, 70, 120)


def rect(origin, u, v, colour):
    """A flat rectangle of discs from origin along vectors u and v."""
    origin, u, v = (np.asarray(a, float) for a in (origin, u, v))
    a = np.arange(0, 1 + 1e-9, STEP / max(np.linalg.norm(u), 1e-9))
    b = np.arange(0, 1 + 1e-9, STEP / max(np.linalg.norm(v), 1e-9))
    s, t = np.meshgrid(a, b)
    return origin + s.reshape(-1, 1) * u + t.reshape(-1, 1) * v, colour


def cuboid(lo, hi, colour, bottom=False):
    """The visible faces of a box from lo to hi (no bottom face unless asked)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    faces = [rect((x0, y0, z1), (x1 - x0, 0, 0), (0, y1 - y0, 0), colour),
             rect((x0, y0, z0), (x1 - x0, 0, 0), (0, 0, z1 - z0), colour),
             rect((x0, y1, z0), (x1 - x0, 0, 0), (0, 0, z1 - z0), colour),
             rect((x0, y0, z0), (0, y1 - y0, 0), (0, 0, z1 - z0), colour),
             rect((x1, y0, z0), (0, y1 - y0, 0), (0, 0, z1 - z0), colour)]
    if bottom:
        faces.append(rect((x0, y0, z0), (x1 - x0, 0, 0), (0, y1 - y0, 0), colour))
    return faces


W, D, H = 5.0, 4.0, 2.5
# Named places (metres, z up), as the carer would name them on the relay's /places page.
PLACES = {"bed": [0.65, 3.3, 0.0], "bathroom": [1.95, 3.85, 0.0], "front door": [0.15, 1.1, 0.0],
          "kitchen": [3.6, 3.0, 0.0], "pill box": [4.57, 3.75, 1.56], "sofa": [4.1, 0.8, 0.0], "table": [2.2, 1.8, 0.0]}
WALKS = [["bed", "bathroom"], ["bed", "front door"], ["bed", "kitchen"]]


def room():
    """(points, colour) groups for the static room, and what was moved: the chair and a bag."""
    static = [rect((0, 0, 0), (W, 0, 0), (0, D, 0), FLOOR),
              rect((0, D, 0), (W, 0, 0), (0, 0, H), WALL),       # back wall
              rect((0, 0, 0), (0, D, 0), (0, 0, H), WALL),       # left wall
              rect((0.02, 2.75, 1.1), (0, 1.05, 0), (0, 0, 0.8), WINDOW),     # window over the bed
              rect((1.55, D - 0.02, 0.0), (0.8, 0, 0), (0, 0, 2.05), DOOR),   # bathroom door
              rect((0.02, 0.6, 0.0), (0, 0.95, 0), (0, 0, 2.05), DOOR),       # front door
              rect((1.2, 1.0, 0.005), (2.2, 0, 0), (0, 1.6, 0), RUG)]
    static += cuboid((2.9, D - 0.65, 0), (4.95, D - 0.05, 0.9), COUNTER)          # kitchen counter
    static += cuboid((4.2, D - 0.45, 1.45), (4.95, D - 0.05, 1.5), WOOD)          # shelf
    static += cuboid((4.45, D - 0.35, 1.5), (4.7, D - 0.15, 1.62), PILLS)          # the pill box
    static += cuboid((1.6, 1.4, 0.72), (2.8, 2.2, 0.76), WOOD)                     # table top
    for x, y in ((1.65, 1.45), (2.7, 1.45), (1.65, 2.1), (2.7, 2.1)):
        static += cuboid((x, y, 0), (x + 0.06, y + 0.06, 0.72), WOOD)
    static += cuboid((3.4, 0.2, 0), (4.9, 1.1, 0.45), SOFA)                        # sofa seat
    static += cuboid((3.4, 0.2, 0.45), (4.9, 0.4, 0.9), SOFA)                      # sofa back
    static += cuboid((0.1, 2.6, 0), (1.2, 3.95, 0.5), BED)                         # bed corner
    movable = cuboid((2.95, 1.6, 0), (3.4, 2.05, 0.46), CHAIR) + cuboid((3.3, 1.6, 0.46), (3.4, 2.05, 0.95), CHAIR)
    movable += cuboid((1.5, 3.05, 0), (1.85, 3.3, 0.3), BAG)                       # a bag left on the way to the bathroom
    return static, movable


def night_track(day="2026-11-15"):
    """An example night: up at 02:47 for the bathroom (past the bag), and at 03:09 to the front door
    (the door sensor fires at 03:11), the kitchen, then back to bed. A fix every 5 s, as the relay keeps."""
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(f"{day}T02:40:00+02:00")
    legs = [  # (minutes from 02:40, where) - walking between points at about 0.6 m/s
        (0, (0.7, 3.3)), (7.0, (0.7, 3.3)), (7.2, (1.3, 3.2)), (7.6, (1.4, 3.45)), (7.9, (1.9, 3.55)), (8.1, (1.95, 3.85)),
        (15.0, (1.95, 3.85)), (15.4, (1.4, 3.5)), (15.8, (1.3, 3.2)), (16.0, (0.7, 3.3)),
        (29.0, (0.7, 3.3)), (29.3, (1.3, 3.0)), (29.8, (1.0, 2.4)), (30.3, (0.6, 1.6)), (30.7, (0.35, 1.15)),
        (34.0, (0.35, 1.15)), (34.6, (1.1, 1.0)), (35.4, (2.9, 2.6)), (35.8, (3.5, 3.0)),
        (37.5, (3.5, 3.0)), (38.2, (2.4, 3.0)), (38.7, (1.3, 3.15)), (39.0, (0.7, 3.3)), (44.0, (0.7, 3.3))]
    track = []
    for (m0, a), (m1, b) in zip(legs, legs[1:]):
        steps = max(1, int((m1 - m0) * 12))
        for s in range(steps):
            f = s / steps
            x, y = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
            wobble = 0.03 * np.sin(len(track) * 1.7) if a != b else 0.0
            track.append({"t": (t0 + timedelta(minutes=m0 + (m1 - m0) * f)).isoformat(timespec="seconds"),
                          "p": [round(x + wobble, 3), round(y - wobble, 3), 0.0]})
    events = [{"t": f"{day}T03:11:00+02:00", "type": "door_open", "place": "front door"},
              {"t": f"{day}T03:13:00+02:00", "type": "door_closed", "place": "front door"}]
    return track, events


def make_map():
    static, movable = room()
    # Static parts one step below HIGH: 5 cm voxels keep the file near 2.5 MB. The chair moves: LOW.
    groups = [(p, c, sr.HIGH - 1, False) for p, c in static] + [(p, c, sr.LOW, True) for p, c in movable]
    pos, rgb, owner, cells = [], [], [], {}
    for pts, colour, level, is_movable in groups:
        ids = sr.cell_ids(pts, sr.CELL)
        pos.append(pts); rgb.append(np.tile(colour, (len(pts), 1))); owner.append(ids)
        for i in np.unique(ids):
            old = cells.get(i)
            cells[i] = (min(level, old[0]), is_movable or old[1]) if old else (level, is_movable)
    keys = np.array(sorted(cells), np.int64)
    rec = np.zeros(sum(len(p) for p in pos), sr.RECORD)
    rec["pos"], rec["scale"], rec["rot"] = np.concatenate(pos), STEP / 2, FLAT
    rec["rgba"][:, :3], rec["rgba"][:, 3] = np.concatenate(rgb), 235
    n = len(keys)
    return sr.Map(sr.CELL, keys, np.ones((n, 3), np.float32), np.array([cells[k][0] for k in keys], np.int8),
                  np.zeros(n, np.int16), np.array([cells[k][1] for k in keys]), np.zeros(n, np.int16),
                  np.full(n, 500, np.int32), np.zeros(0, np.int64), rec, np.concatenate(owner), 1)


if __name__ == "__main__":
    import json
    from pathlib import Path

    import hazards
    import night

    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    m = make_map()
    summary = smesh.write_ply(m, out / "home.ply", comments=["up z", "example home, not a real recording"])
    found = hazards.find(m, PLACES, [tuple(w) for w in WALKS], up="z")
    found.update(generated_at="2026-11-15T07:30:00+02:00", places=PLACES, example=True)
    json.dump(found, open(out / "hazards.json", "w"), indent=1)
    track, events = night_track()
    walked = night.analyse(track, PLACES, events, up="z", day="2026-11-15")
    walked.update(places=PLACES, example=True)
    json.dump(walked, open(out / "night.json", "w"), indent=1)
    print(summary["total"], [z["words"] for z in found["hazards"]], walked["summary"])
