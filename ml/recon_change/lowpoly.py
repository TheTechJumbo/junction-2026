#!/usr/bin/env python3
"""The home as blocks: a floor, walls and flat-coloured blocks, the look of the family's 3D view.

splat_mesh.py follows every surface (hundreds of thousands of triangles, and every hole and floater
of the splat). This rebuilds the room from a few shapes instead, in the room map's frame (z up):

    floor   one quad at the floor height, in the floor's own colour
    walls   the room's outline (where the points stop), cut to WALL_M high, facing into the room
    blocks  the floor is cut into squares; each square with enough in it between the floor and below
            the ceiling becomes a column as tall as what stands there, in its colour. Neighbouring
            squares of the same height and about the same colour are merged into one box (greedily,
            row by row), so a sofa is a few boxes and a pair of slippers one small one.

Detail is a QUALITY level (the size of the squares and of the height steps), never a triangle budget:
a bigger home simply has more blocks at the same quality.

    high    7 cm squares, 5 cm steps   (the default: a real room is about 5-10k triangles)
    medium  12 cm squares, 8 cm steps
    low     30 cm squares, 15 cm steps

Made to be drawn cheaply:
- hidden faces are never made: a block's side is only built where it stands above its neighbour,
  so the faces between blocks that touch (most of them inside furniture) do not exist
- faces are wound consistently (blocks and floor face out, walls face into the room), so the viewer
  can cull back faces: half the work, and the wall between the camera and the room disappears by
  itself, like a doll's house cut open on the side you look from (the PLY says "comment cull")
- no normals are stored: flat faces have their own corners and the viewer works normals out

    python lowpoly.py trained.splat room.ply [--frame room.json] [--up -y] [--quality high]
"""
import argparse

import numpy as np

import hazards as hz
import room_frame
import splat_refine as sr
import splat_mesh as smesh

QUALITY = {  # square size, height step, colour tolerance for merging (0-255), Gaussians a square needs
    "high": {"cell": 0.07, "step": 0.05, "merge_dc": 45, "min_points": 12},
    "medium": {"cell": 0.12, "step": 0.08, "merge_dc": 60, "min_points": 12},
    "low": {"cell": 0.30, "step": 0.15, "merge_dc": 90, "min_points": 25},
}
WALL_M = 1.0        # walls are cut to this height
MIN_TOP_M = 0.05    # a square needs something at least this high
TALL_M = 1.5        # a square standing taller is wall, a curtain or a tall cupboard: shown as wall
OUTLINE_PCT = 3     # the room's outline: where all but this percent of the points are inside, each side
CEILING_GAP_M = 0.3  # ignore the top of the room (ceiling, lamps)
EDGE_M = 0.35       # squares this close to the outline are wall, not furniture
SIDE_SHADE = 0.82   # block sides a touch darker than their tops


def _quad(v, c, t, corners, colour):
    """Two triangles for a quad whose corners go anticlockwise seen from the side it faces."""
    base = len(v)
    v.extend(corners); c.extend([colour] * 4)
    t.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])


def _boxes(grid, merge_dc):
    """Greedy merge of the squares into boxes: [(i, j, w, d, top, colour)]."""
    used, boxes = set(), []
    for (i, j) in sorted(grid):
        if (i, j) in used:
            continue
        top, col, _ = grid[(i, j)]

        def ok(k):
            return k in grid and k not in used and abs(grid[k][0] - top) < 1e-6 and np.abs(grid[k][1] - col).max() <= merge_dc
        w = 1
        while ok((i + w, j)):
            w += 1
        d = 1
        while all(ok((i + a, j + d)) for a in range(w)):
            d += 1
        members = [(i + a, j + b) for a in range(w) for b in range(d)]
        used.update(members)
        boxes.append((i, j, w, d, top, tuple(np.mean([grid[m][1] for m in members], 0).astype(int))))
    return boxes


def build(positions, colours, up="z", quality="high", cell=None, wall=WALL_M, hide=True, floor=None, outline=None):
    """Gaussians (positions, 0-255 colours) -> (vertices, normals, colours, triangles) on the given up
    axis. `cell` overrides the quality's square size; hide=False keeps hidden faces (for comparison).
    floor (a height) and outline ((x0, y0), (x1, y1) on the floor) override the ones worked out from
    the points: glasses mostly look at table tops, so a recording can see more table than floor."""
    q = dict(QUALITY[quality])
    if cell is not None:
        q["cell"] = cell
    cell = q["cell"]
    flat, height = hz.split_up(np.asarray(positions, float), up)
    rgb = np.asarray(colours, float)
    floor = hz.floor_height(height, np.ones(len(height), bool)) if floor is None else float(floor)
    h = height - floor
    top_of_room = float(np.percentile(h, 99))
    (x0, y0), (x1, y1) = outline if outline is not None else (np.percentile(flat, OUTLINE_PCT, axis=0), np.percentile(flat, 100 - OUTLINE_PCT, axis=0))
    v, c, t = [], [], []
    on_floor = np.abs(h) < 0.04
    floor_rgb = tuple(np.median(rgb[on_floor], 0).astype(int)) if on_floor.any() else (180, 160, 130)
    _quad(v, c, t, [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], floor_rgb)  # facing up
    near_edge = (flat[:, 0] < x0 + EDGE_M) | (flat[:, 0] > x1 - EDGE_M) | (flat[:, 1] < y0 + EDGE_M) | (flat[:, 1] > y1 - EDGE_M)
    walls = near_edge & (h > 0.5)
    wall_rgb = tuple(np.median(rgb[walls], 0).astype(int)) if walls.any() else (220, 218, 210)
    wall = min(wall, max(0.3, top_of_room))
    # Walls go clockwise round the room seen from above, so each faces into the room.
    for a, b in (((x0, y0), (x0, y1)), ((x0, y1), (x1, y1)), ((x1, y1), (x1, y0)), ((x1, y0), (x0, y0))):
        _quad(v, c, t, [(a[0], a[1], 0), (b[0], b[1], 0), (b[0], b[1], wall), (a[0], a[1], wall)], wall_rgb)

    # Squares: how high things stand on each, and their colour.
    inside = ~near_edge & (h > 0.04) & (h < top_of_room - CEILING_GAP_M)
    ij = np.floor((flat[inside] - (x0, y0)) / cell).astype(int)
    grid = {}
    if len(ij):
        order = np.lexsort((ij[:, 1], ij[:, 0]))
        keys, starts = np.unique(ij[order], axis=0, return_index=True)
        bounds = list(starts[1:]) + [len(order)]
        hs, cs = h[inside][order], rgb[inside][order]
        for key, s, e in zip(map(tuple, keys), starts, bounds):
            if e - s >= q["min_points"]:
                top = float(np.percentile(hs[s:e], 90))
                if top >= MIN_TOP_M:
                    if top > TALL_M:  # walls inside the outline, curtains, tall cupboards: wall, like the outer ones
                        grid[key] = (wall, np.array(wall_rgb, float), e - s)
                    else:
                        grid[key] = (max(q["step"], round(top / q["step"]) * q["step"]), np.median(cs[s:e], 0), e - s)
    near = ((1, 0), (-1, 0), (0, 1), (0, -1))
    # Pockmarks: an empty square with filled squares on at least three sides is filled like them.
    for k in {(i + di, j + dj) for (i, j) in grid for di, dj in near} - set(grid):
        around = [grid[(k[0] + di, k[1] + dj)] for di, dj in near if (k[0] + di, k[1] + dj) in grid]
        if len(around) >= 3:
            tops = [g[0] for g in around]
            top = max(set(tops), key=tops.count)  # the most common neighbouring height
            grid[k] = (top, np.mean([g[1] for g in around if g[0] == top], 0), 2 * q["min_points"])
    # Lone specks (no neighbour, few Gaussians) are noise.
    grid = {k: g for k, g in grid.items() if g[2] >= 2 * q["min_points"] or any((k[0] + di, k[1] + dj) in grid for di, dj in near)}

    boxes = _boxes(grid, q["merge_dc"])
    top_of = {}
    for i, j, w, d, z, _ in boxes:
        for a in range(w):
            for b in range(d):
                top_of[(i + a, j + b)] = z
    for i, j, w, d, z, colour in boxes:
        ax, ay = x0 + i * cell, y0 + j * cell
        bx, by = ax + w * cell, ay + d * cell
        _quad(v, c, t, [(ax, ay, z), (bx, ay, z), (bx, by, z), (ax, by, z)], colour)  # top, facing up
        shade = tuple(int(x * SIDE_SHADE) for x in colour)
        # Sides, anticlockwise round the box (each faces out). Along each side, only the part above
        # the neighbouring square is built; runs with the same neighbour height are one quad.
        sides = [  # (cells along the side as (start corner of that cell's edge, end corner, neighbour square))
            [((ax + a * cell, ay), (ax + (a + 1) * cell, ay), (i + a, j - 1)) for a in range(w)],
            [((bx, ay + b * cell), (bx, ay + (b + 1) * cell), (i + w, j + b)) for b in range(d)],
            [((ax + (a + 1) * cell, by), (ax + a * cell, by), (i + a, j + d)) for a in reversed(range(w))],
            [((ax, ay + (b + 1) * cell), (ax, ay + b * cell), (i - 1, j + b)) for b in reversed(range(d))],
        ]
        for side in sides:
            run = None
            for p, q2, nb in side + [(None, None, None)]:
                bottom = (top_of.get(nb, 0.0) if hide else 0.0) if nb is not None else None
                if run and (bottom != run[2]):
                    if run[2] < z:
                        _quad(v, c, t, [(run[0][0], run[0][1], run[2]), (run[1][0], run[1][1], run[2]),
                                        (run[1][0], run[1][1], z), (run[0][0], run[0][1], z)], shade)
                    run = None
                if p is None:
                    break
                run = [run[0], q2, bottom] if run else [p, q2, bottom]

    verts = np.array(v, np.float32)
    verts[:, 2] += floor  # back to the frame's own heights
    tris = np.array(t, np.int64)
    if up != "z":  # the blocks are built level; put them back on the given axis
        axis, sign = hz.AXES[up]
        rest = [i for i in range(3) if i != axis]
        out = np.zeros_like(verts)
        out[:, rest[0]], out[:, rest[1]], out[:, axis] = verts[:, 0], verts[:, 1], sign * verts[:, 2]
        m = np.zeros((3, 3)); m[rest[0], 0] = m[rest[1], 1] = 1; m[axis, 2] = sign
        if np.linalg.det(m) < 0:  # a mirror turns faces inside out: wind them the other way
            tris = tris[:, ::-1]
        verts = out
    return verts, _normals(verts, tris), np.array(c, np.uint8), tris


def _normals(verts, tris):
    n = np.zeros_like(verts)
    fn = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]], verts[tris[:, 2]] - verts[tris[:, 0]])
    for k in range(3):
        np.add.at(n, tris[:, k], fn)
    return (n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-9)).astype(np.float32)


def from_splat(data, frame=None, up="-y", quality="high", cell=None, wall=WALL_M):
    """A trained .splat file -> the room as blocks (faint and huge Gaussians left out, as splat_mesh.from_splat)."""
    rec = np.frombuffer(bytes(data), sr.RECORD)
    keep = rec["rgba"][:, 3] >= 64
    if keep.sum() > 100:
        size = rec["scale"].max(1)
        keep &= size <= np.quantile(size[keep], 0.99)
    rec = rec[keep]
    pos = rec["pos"].astype(np.float64)
    if frame is not None:
        pos, up = room_frame.to_room(pos, frame), "z"
    return build(pos, rec["rgba"][:, :3], up, quality, cell, wall), up


def write(v, n, c, t, path, up, extra=()):
    """The PLY the family app reads: no normals (the viewer works them out), back faces may be culled."""
    smesh.write_mesh(v, n, c, t, path, comments=[f"up {up}", "blocks", "cull", *extra], normals=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("splat"), ap.add_argument("out")
    ap.add_argument("--frame"), ap.add_argument("--up", default="-y")
    ap.add_argument("--quality", choices=list(QUALITY), default="high")
    ap.add_argument("--cell", type=float, help="square size in metres, instead of the quality's")
    ap.add_argument("--wall", type=float, default=WALL_M)
    a = ap.parse_args()
    (v, n, c, t), up = from_splat(open(a.splat, "rb").read(), room_frame.load(a.frame) if a.frame else None, a.up, a.quality, a.cell, a.wall)
    write(v, n, c, t, a.out, up)
    print(a.out, len(v), "vertices", len(t), "triangles")


if __name__ == "__main__":
    main()
