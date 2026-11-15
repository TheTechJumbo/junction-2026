#!/usr/bin/env python3
"""Check splat_mesh.py without a model or GPU.

A map is put together by hand: a flat red square of discs (seen from above) and a small box of
discs that is movable. Cases:

  plane      the mesh lies on the square, covers it, has its colour, normals point up (from the
             disc orientation), faces point outwards, and is closed (every edge in two triangles)
  detail     the same square at HIGH has several times the triangles it has at LOW
  movable    the movable box is meshed coarsely, or left out with include_movable=False
  ply        the file has the header, vertex and face counts and size it says
  refined    a map refined from four synthetic clips (test_splat_refine.py) meshes end to end

    python test_splat_mesh.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

import splat_mesh as smesh
import splat_refine as sr

DOWN = np.array([128, 255, 128, 128], np.uint8)  # packed quaternion: 180 degrees about x, so the disc's thin axis is -z
RED, BLUE = (200, 50, 50), (40, 60, 220)


def plane(z=0.1, step=0.01):
    x, y = np.meshgrid(np.arange(0, 1, step), np.arange(0, 1, step))
    return np.stack([x.ravel(), y.ravel(), np.full(x.size, z)], 1)


def box(centre, side=0.2, step=0.01):
    g = np.arange(-side / 2, side / 2 + 1e-9, step)
    x, y, z = np.meshgrid(g, g, g)
    return np.stack([x.ravel(), y.ravel(), z.ravel()], 1) + centre


def make_map(groups, cell=sr.CELL):
    """groups: (points, colour, level, movable). One map, every cell of a group at its level."""
    pos, rgb, owner, cells = [], [], [], {}
    for pts, colour, level, movable in groups:
        ids = sr.cell_ids(pts, cell)
        pos.append(pts)
        rgb.append(np.tile(colour, (len(pts), 1)))
        owner.append(ids)
        cells.update({i: (level, movable) for i in np.unique(ids)})
    keys = np.array(sorted(cells), np.int64)
    rec = np.zeros(sum(len(p) for p in pos), sr.RECORD)
    rec["pos"] = np.concatenate(pos)
    rec["scale"] = 0.005
    rec["rgba"][:, :3], rec["rgba"][:, 3] = np.concatenate(rgb), 235
    rec["rot"] = DOWN
    n = len(keys)
    return sr.Map(cell, keys, np.ones((n, 3), np.float32), np.array([cells[k][0] for k in keys], np.int8), np.zeros(n, np.int16),
                  np.array([cells[k][1] for k in keys]), np.zeros(n, np.int16), np.full(n, 500, np.int32), np.zeros(0, np.int64),
                  rec, np.concatenate(owner), 1)


def edges_twice(t):
    e = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), 1)
    _, n = np.unique(e, axis=0, return_counts=True)
    return bool((n == 2).all())


def main():
    failures = []

    def check(ok, what):
        print(("  ok   " if ok else "  FAIL ") + what)
        if not ok:
            failures.append(what)

    print("A flat red square at HIGH:")
    v, nrm, c, t, summary = smesh.mesh(make_map([(plane(), RED, sr.HIGH, False)]))
    voxel = sr.CELL / smesh.VOXELS_PER_CELL[sr.HIGH]
    print(f"    {summary}")
    check(len(t) > 1000, "it has triangles")
    check(np.abs(v[:, 2] - 0.1).max() < 1.5 * voxel, "every vertex is within a voxel and a half of the square")
    check(np.abs(v[:, :2].min(0) - 0).max() < 2 * voxel and np.abs(v[:, :2].max(0) - 1).max() < 2 * voxel, "and the mesh covers it")
    check((c == RED).all(), "vertex colours are the disc colour")
    check((nrm[:, 2] > 0.99).all(), "vertex normals point up, towards the camera (from the disc orientation)")
    a, b, d = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    face = np.cross(b - a, d - a)
    top = (a + b + d)[:, 2] / 3 > (v[:, 2].min() + v[:, 2].max()) / 2
    flat = np.abs(face[:, 2]) > 0.9 * np.linalg.norm(face, axis=1)  # the two sheets, not the rim between them
    check((face[top & flat, 2] > 0).all() and (face[~top & flat, 2] < 0).all() and flat.mean() > 0.9,
          "faces point outwards (top sheet up, bottom sheet down)")
    check(edges_twice(t), "the mesh is closed: every edge is in exactly two triangles")

    print("Detail follows the level:")
    high = len(t)
    low = len(smesh.mesh(make_map([(plane(), RED, sr.LOW, False)]))[3])
    print(f"    triangles at HIGH {high}, at LOW {low}")
    check(high > 4 * low, "HIGH has over four times the triangles of LOW")

    print("A movable box beside the square:")
    where = np.array([2.0, 2.0, 0.2])
    m = make_map([(plane(), RED, sr.HIGH, False), (box(where), BLUE, sr.LOW, True)])
    v, _, c, t, summary = smesh.mesh(m)
    print(f"    {summary}")
    at_box = np.linalg.norm(v - where, axis=1) < 0.3
    check(at_box.any() and (c[at_box] == BLUE).all(), "it is meshed, in its colour")
    check(summary["movable"]["voxel"] == round(sr.CELL / smesh.VOXELS_PER_CELL[sr.LOW], 4), "at the coarse (LOW) voxel size")
    check(summary["movable"]["triangles"] < summary[f"level {sr.HIGH}"]["triangles"] / 10, "with few triangles")
    v, *_ = smesh.mesh(m, include_movable=False)
    check(not (np.linalg.norm(v - where, axis=1) < 0.3).any(), "and left out with include_movable=False")

    print("PLY file:")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mesh.ply"
        summary = smesh.write_ply(m, path)
        data = path.read_bytes()
        head, body = data.split(b"end_header\n", 1)
        lines = head.decode().splitlines()
        nv = int(next(x for x in lines if x.startswith("element vertex")).split()[-1])
        nf = int(next(x for x in lines if x.startswith("element face")).split()[-1])
        check(lines[0] == "ply" and "property uchar red" in lines, "header has colours")
        check(nv == summary["total"]["vertices"] and nf == summary["total"]["triangles"], "counts match the summary")
        check(len(body) == 27 * nv + 13 * nf, "body size is 27 bytes per vertex and 13 per triangle")
        faces = np.frombuffer(body[27 * nv:], [("n", "u1"), ("i", "<i4", 3)])
        check((faces["n"] == 3).all() and faces["i"].min() >= 0 and faces["i"].max() < nv, "faces are triangles with valid indices")

    print("A map refined from four synthetic clips:")
    from test_splat_refine import BOX_A, clip
    m = sr.Map()
    for s in range(4):
        sr.step(m, clip(BOX_A, s), conf_pct=0)
    v, _, c, t, summary = smesh.mesh(m)
    print(f"    {summary}")
    check(max(summary, key=lambda k: summary[k]["triangles"]) == f"level {sr.HIGH}", "most triangles are in HIGH cells")
    floor = np.abs(v[:, 2]) < 0.1
    check(floor.mean() > 0.3 and (np.abs(v[:, 1] - 3.0) < 0.1).any(), "the floor and the back wall are there")

    print("A trained .splat file (the 32-byte records video_map.py writes):")
    import demo_room
    room = demo_room.make_map().splats
    faint = np.zeros(200, sr.RECORD)
    faint["pos"], faint["rgba"][:, 3] = np.random.default_rng(0).uniform(0, 4, (200, 3)), 10   # near-transparent
    blob = np.zeros(1, sr.RECORD)
    blob["pos"], blob["scale"], blob["rgba"][:, 3] = (2.0, 2.0, 3.4), 3.0, 255                   # a big floater above the room
    v, nrm, c, t = smesh.from_splat(np.concatenate([room, faint, blob]).tobytes(), voxel=0.05)
    check(len(t) > 10000 and len(v) == len(nrm) == len(c), "a trained splat becomes a coloured mesh")
    check(v[:, 2].max() < 2.7, "the floater above the room is left out")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "trained.ply"
        smesh.write_mesh(v, nrm, c, t, path, comments=["up z"])
        head = path.read_bytes()[:300].decode("latin1")
        check(head.startswith("ply") and "comment up z" in head, "written as the PLY the dashboard reads")

    print("\nAll checks passed." if not failures else f"\n{len(failures)} check(s) failed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
