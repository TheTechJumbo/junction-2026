"""The public Mip-NeRF 360 room (data/room.splat, ?scene=room) -> room/*.ply with our splat_mesh code, levelled, plus the camera for the splat viewer.

The example is in its capture's own frame (not level), so it is levelled first the way rooms usually
are: the thinnest direction of the room (floor to ceiling) is up, and the floor is the side with
more in it near the bottom. Our pipeline gets "up" from the relay's room frame instead.
"""
import json
import sys
from pathlib import Path
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml" / "recon_change"))  # the repo's vision code
import splat_mesh as smesh  # noqa: E402
import splat_refine as sr  # noqa: E402

src, folder = sys.argv[1], sys.argv[2]
rec = np.fromfile(src, sr.RECORD)
solid = rec[rec["rgba"][:, 3] >= 64]
lo, hi = np.percentile(solid["pos"], 3, axis=0), np.percentile(solid["pos"], 97, axis=0)
core = solid["pos"][((solid["pos"] >= lo) & (solid["pos"] <= hi)).all(1)].astype(np.float64)
centre = np.median(core, 0)
w, vecs = np.linalg.eigh(np.cov((core - centre).T))
up, long_axis, short_axis = vecs[:, 0], vecs[:, 2], vecs[:, 1]   # smallest spread: floor to ceiling
h = (core - centre) @ up
if np.median(h) > (np.percentile(h, 2) + np.percentile(h, 98)) / 2:  # more stuff low down: that side is the floor
    up = -up
short_axis = np.cross(up, long_axis)
frame = {"origin": centre, "east": long_axis, "north": short_axis, "up": up, "metres": 1.0}

# Crop to the room in the levelled frame, then mesh with our code (from_splat with a room frame).
lv = (rec["pos"] - centre) @ np.stack([long_axis, short_axis, up], 1)
lo2, hi2 = np.percentile(lv, [2, 2, 1], axis=0) if False else (np.percentile(lv, 2, axis=0), np.percentile(lv, 98, axis=0))
keep = ((lv >= lo2) & (lv <= hi2)).all(1)
room = rec[keep]
extent = float(np.max(hi2 - lo2))
for name, divisions in (("room_mesh.ply", 140), ("room_mesh_fine.ply", 220)):
    t = time.time()
    v, n, c, tri = smesh.from_splat(room.tobytes(), voxel=extent / divisions, frame=frame)
    smesh.write_mesh(v, n, c, tri, f"{folder}/{name}", comments=["up z", "from room.splat (Mip-NeRF 360 room), levelled"])
    print(f"{name}: voxel {extent / divisions:.3f}, {len(v)} vertices, {len(tri)} triangles, {time.time() - t:.1f}s")

# The splat viewer's camera: same up, standing near the middle of the room, looking along it.
height = float(hi2[2] - lo2[2])
eye = centre - long_axis * 0.30 * float(hi2[0] - lo2[0]) + up * 0.10 * height
look = centre + long_axis * 0.25 * float(hi2[0] - lo2[0]) - up * 0.05 * height
json.dump({"up": up.round(4).tolist(), "eye": eye.round(3).tolist(), "look": look.round(3).tolist()}, open(f"{folder}/camera.json", "w"))
print("kept", int(keep.sum()), "of", len(rec), "Gaussians; up", up.round(3), "room", (hi2 - lo2).round(2))

# Very low-poly: the same room through lowpoly.py, scaled to a usual 2.6 m ceiling so its metre
# settings (20 cm squares, 1 m walls) mean what they say.
import lowpoly  # noqa: E402
frame["metres"] = 2.6 / height
lowpoly.MERGE_DC = 90
(v, n, c, tri), up_axis = lowpoly.from_splat(room.tobytes(), frame=frame, cell=0.3)
smesh.write_mesh(v, n, c, tri, f"{folder}/room_lowpoly.ply", comments=["up z", "low-poly, from room.splat"])
print(f"room_lowpoly.ply: {len(v)} vertices, {len(tri)} triangles")

# In between: the detailed surface mesh, simplified to about 10k triangles (splat_mesh.simplify).
for target in (10_000, 20_000):
    v, n, c, tri = smesh.from_splat(room.tobytes(), voxel=extent / 220, frame=frame)
    t0 = time.time()
    v, c, tri = smesh.tidy(v, c, tri)
    v, n, c, tri = smesh.simplify(v, c, tri, target)
    smesh.write_mesh(v, n, c, tri, f"{folder}/room_{target // 1000}k.ply", comments=["up z", f"simplified to about {target} triangles"])
    print(f"room_{target // 1000}k.ply: {len(tri)} triangles in {time.time() - t0:.1f}s")

# In between, block style: finer squares and height steps than room_lowpoly.ply.
for name, cell, step, dc in (("room_blocks_fine.ply", 0.07, 0.05, 45), ("room_blocks_mid.ply", 0.12, 0.08, 60)):
    lowpoly.STEP_M, lowpoly.MERGE_DC, lowpoly.MIN_POINTS = step, dc, 12
    (v, n, c, tri), _ = lowpoly.from_splat(room.tobytes(), frame=frame, cell=cell)
    smesh.write_mesh(v, n, c, tri, f"{folder}/{name}", comments=["up z", f"blocks {cell} m"])
    print(f"{name}: {len(tri)} triangles")
