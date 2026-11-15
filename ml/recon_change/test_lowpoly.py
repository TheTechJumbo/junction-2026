#!/usr/bin/env python3
"""Check lowpoly.py (the room as blocks) and splat_mesh.simplify / tidy, on the example home.

  blocks     the example home becomes a few hundred triangles: a floor, low walls and blocks; the bed,
             the sofa and the bag stand at about their heights where they are; no hole in a flat top;
             a y-down copy of the same points gives the same room on its own axis
  simplify   the detailed mesh of the example home comes down to about the asked number of triangles
             and keeps the room's size; tidy drops a floating flake and keeps the room

    python test_lowpoly.py
"""
import sys

import numpy as np

import demo_room as dr
import lowpoly
import splat_mesh as smesh

failures = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def top_at(v, x, y, r=0.0, tris=None):
    """The highest flat top face that covers (x, y): blocks only have vertices at their corners."""
    tri = v[t_global if tris is None else tris]
    flat = np.ptp(tri[:, :, 2], axis=1) < 1e-6
    lo, hi = tri[:, :, :2].min(1) - r, tri[:, :, :2].max(1) + r
    over = flat & (lo[:, 0] <= x) & (x <= hi[:, 0]) & (lo[:, 1] <= y) & (y <= hi[:, 1])
    return float(tri[over, 0, 2].max()) if over.any() else 0.0


m = dr.make_map()
pos, rgb = m.splats["pos"].astype(np.float64), m.splats["rgba"][:, :3]
v, n, c, t = lowpoly.build(pos, rgb, up="z", cell=0.1)
t_global = t
check("a few hundred triangles", 100 < len(t) < 2000)
check("the bed stands at about 0.5 m", 0.4 <= top_at(v, 0.65, 3.3) <= 0.6)
check("the sofa seat at about 0.45 m", 0.35 <= top_at(v, 4.1, 0.75) <= 0.55)
check("the bag on the floor at about 0.3 m", 0.2 <= top_at(v, 1.67, 3.17) <= 0.4)
check("open floor stays flat", top_at(v, 2.4, 0.6) < 0.05)
check("no hole in the bed's flat top", all(top_at(v, x, y) >= 0.4 for x in np.arange(0.5, 1.1, 0.1) for y in np.arange(2.8, 3.5, 0.1)))  # clear of the wall strip
vy = np.stack([pos[:, 0], -pos[:, 2], pos[:, 1]], 1)  # the same room with y down
v2, _, _, t2 = lowpoly.build(vy, rgb, up="-y", cell=0.1)
check("a y-down room gives the same blocks on its own axis", len(t2) == len(t) and np.allclose(np.sort(-v2[:, 1]), np.sort(v[:, 2]), atol=1e-4))

# Detail is a quality level, not a triangle budget: the same room twice over gets about twice the triangles.
two = np.concatenate([pos, pos + np.array([5.5, 0, 0])])
_, _, _, t_two = lowpoly.build(two, np.concatenate([rgb, rgb]), up="z", cell=0.1)
# (a little over twice: the walls between the two rooms are inside the outline, so they become blocks too)
check("twice the room, about twice the triangles (quality, not a budget)", 1.6 <= len(t_two) / len(t) <= 3.0)
check("named quality levels go from fine to coarse", len(lowpoly.build(pos, rgb, "z", "high")[3]) > len(lowpoly.build(pos, rgb, "z", "medium")[3]) > len(lowpoly.build(pos, rgb, "z", "low")[3]))
# Hidden faces are never made, and what can be seen is the same.
vk, _, _, tk = lowpoly.build(pos, rgb, up="z", cell=0.1, hide=False)
check("hidden faces removed: fewer triangles", len(t) < len(tk))
check("... and the same tops", all(abs(top_at(v, x, y) - top_at(vk, x, y, tris=tk)) < 1e-6 for x in np.arange(0.5, 4.6, 0.25) for y in np.arange(0.5, 3.6, 0.25)))
# Wound for back-face culling: tops face up, walls face into the room.
fnorm = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
flat_faces = np.ptp(v[t][:, :, 2], axis=1) < 1e-6
check("every flat face (floor, tops) faces up", (fnorm[flat_faces][:, 2] > 0).all())
centre = v.mean(0)
walls = ~flat_faces & (np.abs(v[t][:, :, 0] - v[:, 0].min()).max(1) < 1e-6)  # the wall along the room's west edge
check("walls face into the room", walls.any() and (fnorm[walls][:, 0] > 0).all())

fv, fn, fc, ft = smesh.mesh(m)[:4]
sv, _, sc, st = smesh.simplify(fv, fc, ft, 3000)
check("simplify comes down to about the asked count", 2000 <= len(st) <= 4000 and len(st) < len(ft) / 5)
check("and keeps the room's size", np.allclose(sv.min(0), fv.min(0), atol=0.3) and np.allclose(sv.max(0), fv.max(0), atol=0.3))
check("every triangle points at real vertices", st.min() >= 0 and st.max() < len(sv) and len(sc) == len(sv))
flake_v = np.array([[9, 9, 9], [9.1, 9, 9], [9, 9.1, 9]], np.float32)
tv, tc, tt = smesh.tidy(np.concatenate([fv, flake_v]), np.concatenate([fc, np.zeros((3, 3), np.uint8)]),
                        np.concatenate([ft, np.array([[0, 1, 2]]) + len(fv)]), smooth=0)
check("tidy drops a floating flake and keeps the room", tv[:, 0].max() < 8 and len(tt) > 0.9 * len(ft))

print("\nAll checks passed." if not failures else f"\n{len(failures)} check(s) failed.")
sys.exit(1 if failures else 0)
