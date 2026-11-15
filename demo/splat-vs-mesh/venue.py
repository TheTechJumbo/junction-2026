"""Seb's venue splat (made from our Ray-Ban Meta glasses recording) -> the block mesh, in metres, level.

The splat is in VGGT's frame: the first glasses camera at the origin, x right, y down, z forward, in
VGGT's own units. Up is the hall's flattest direction (checked against the camera's "down"). The
scale comes from the glasses themselves: the first camera is at head height, about 1.6 m above the
floor. Writes venue/room_blocks_fine.ply (high quality), venue/room_blocks_mid.ply and
venue/camera.json (the splat viewer starts where the glasses started, looking where they looked).

    python venue.py venue/venue-kitchen-glasses-anon.splat venue
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml" / "recon_change"))  # the repo's vision code
import hazards as hz  # noqa: E402
import lowpoly  # noqa: E402
import room_frame  # noqa: E402
import splat_refine as sr  # noqa: E402

EYE_M = 1.6  # the glasses' height above the floor

src, out = sys.argv[1], Path(sys.argv[2])
rec = np.fromfile(src, sr.RECORD)
solid = rec[rec["rgba"][:, 3] >= 64]
lo, hi = np.percentile(solid["pos"], 2, axis=0), np.percentile(solid["pos"], 98, axis=0)
core = solid["pos"][((solid["pos"] >= lo) & (solid["pos"] <= hi)).all(1)].astype(np.float64)
centre = np.median(core, 0)
_, vecs = np.linalg.eigh(np.cov((core - centre).T))
up = vecs[:, 0] if vecs[:, 0] @ np.array([0, -1.0, 0]) > 0 else -vecs[:, 0]  # flattest direction, pointing away from the camera's "down"
east = vecs[:, 2] - (vecs[:, 2] @ up) * up
east /= np.linalg.norm(east)
north = np.cross(up, east)
frame = {"origin": np.zeros(3), "east": east, "north": north, "up": up, "metres": 1.0}
level = room_frame.to_room(core, frame)
# The floor is the LOWEST real band (2nd percentile): the busiest low band in a hacking hall is desk tops.
floor = float(np.percentile(level[:, 2], 2))
camera_height = 0.0 - floor  # the first camera is at the origin
frame["metres"] = EYE_M / camera_height
frame["origin"] = np.zeros(3)
print(f"up {up.round(3)}; camera {camera_height:.3f} units above the floor -> 1 unit = {frame['metres']:.2f} m")

keep = ((rec["pos"] >= lo) & (rec["pos"] <= hi)).all(1)
room = rec[keep]
for name, quality in (("room_blocks_fine.ply", "high"), ("room_blocks_mid.ply", "medium")):
    (v, n, c, t), _ = lowpoly.from_splat(room.tobytes(), frame=frame, quality=quality, floor=floor * frame['metres'])
    lowpoly.write(v, n, c, t, out / name, "z", ["from venue-kitchen-glasses-anon.splat (Ray-Ban Meta glasses, anonymised)"])
    span = v.max(0) - v.min(0)
    print(f"{name}: {len(t)} triangles, {len(v)} vertices; hall {span[0]:.1f} x {span[1]:.1f} m, walls {span[2]:.1f} m")

# The splat viewer: start where the glasses started, looking where they looked (VGGT: +z forward).
json.dump({"up": up.round(4).tolist(), "eye": [0.0, 0.0, 0.0], "look": [0.0, 0.0, 0.6]}, open(out / "camera.json", "w"))
json.dump({k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in frame.items()}, open(out / "room_frame.json", "w"), indent=1)
