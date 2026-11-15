"""The example living room (ml/recon_change/demo_room.py) three ways, for the three-panel view:

    home/actual.ply        the room as it really is: the exact rectangles and boxes it is made of
    home/home.splat        the same room as a Gaussian splat (the 32-byte .splat records), what the
                           vision stage would hand over
    home/blocks_*.ply      the block mesh our code makes FROM that .splat file (lowpoly.from_splat),
                           at high, medium and low quality
    home/night.json        the example night (the family app's fixture), in the same frame
    home/camera.json       where the splat viewer starts

Everything is in the room's frame: metres, z up, floor at 0.

    python home.py
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ml" / "recon_change"))  # the repo's vision code
import demo_room as dr  # noqa: E402
import lowpoly  # noqa: E402
import splat_mesh as smesh  # noqa: E402

OUT = HERE / "home"
OUT.mkdir(exist_ok=True)

# 1. The actual room: record every rectangle the room is built from, as it is built.
quads = []
make_rect = dr.rect


def recording_rect(origin, u, v, colour):
    quads.append((np.asarray(origin, float), np.asarray(u, float), np.asarray(v, float), colour))
    return make_rect(origin, u, v, colour)


dr.rect = recording_rect
m = dr.make_map()
dr.rect = make_rect
v, c, t = [], [], []
for o, u, w, colour in quads:
    base = len(v)
    v += [o, o + u, o + u + w, o + w]
    c += [colour] * 4
    t += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
v, c, t = np.array(v, np.float32), np.array(c, np.uint8), np.array(t, np.int64)
smesh.write_mesh(v, np.zeros_like(v), c, t, OUT / "actual.ply", comments=["up z", "the example room as built"], normals=False)
print(f"actual.ply: {len(t)} triangles from {len(quads)} rectangles")

# 2. The Gaussian splat of it: the map's Gaussians, a little varied so it renders like a splat.
rng = np.random.default_rng(7)
rec = m.splats.copy()
rec["rgba"][:, :3] = np.clip(rec["rgba"][:, :3].astype(int) + rng.integers(-7, 8, (len(rec), 3)), 0, 255)
rec["scale"] = (dr.STEP * 0.65 * rng.uniform(0.85, 1.15, (len(rec), 1))).astype(np.float32) * np.ones((1, 3), np.float32)
rec["rot"] = (128, 128, 128, 255)  # no turn (w, x, y, z packed as value*128+128)
rec["rot"][:, 0] = 255
(OUT / "home.splat").write_bytes(rec.tobytes())
print(f"home.splat: {len(rec)} Gaussians, {len(rec) * 32 / 1e6:.1f} MB")

# 3. The block mesh, made from that .splat file exactly as from a trained one.
data = (OUT / "home.splat").read_bytes()
for quality in ("high", "medium", "low"):
    # A Gaussian every 2.5 cm is sparse next to a trained splat: a square needs only a few.
    (bv, bn, bc, bt), up = lowpoly.from_splat(data, up="z", quality=quality, floor=0.0, min_points=3)
    lowpoly.write(bv, bn, bc, bt, OUT / f"blocks_{quality}.ply", up, ["from home.splat"])
    print(f"blocks_{quality}.ply: {len(bt)} triangles")

# 4. The example night and the splat viewer's start.
shutil.copy(HERE.parents[1] / "dashboard" / "fixtures" / "night.json", OUT / "night.json")
json.dump({"up": [0, 0, 1], "eye": [6.2, -2.6, 3.6], "look": [2.4, 2.0, 0.4]}, open(OUT / "camera.json", "w"))
