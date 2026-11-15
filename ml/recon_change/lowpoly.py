#!/usr/bin/env python3
"""A very low-poly room from Gaussians: a floor, low walls and flat-coloured blocks.

splat_mesh.py follows every surface (hundreds of thousands of triangles, and every hole and floater
of the splat). For the family's 3D view a calm, readable room is better: this rebuilds it from a
handful of shapes, in the room map's frame (z up):

    floor   one quad at the floor height, in the floor's own colour
    walls   the room's outline (where the points stop) as four quads, cut to WALL_M high so the
            room can be seen into, like a doll's house
    blocks  the floor is cut into CELL_M squares; each square with enough in it between the floor
            and below the ceiling becomes a column as tall as what stands there, in its colour.
            Neighbouring squares of about the same height and colour are merged into one box
            (greedily, row by row), so a sofa is a few boxes and a pair of slippers one small one.

A room is usually a few hundred to a few thousand triangles. Faces are written separately so they
are shaded flat. The same blocks are what hazards look for, so the view and the check agree.

    python lowpoly.py trained.splat room.ply [--frame room.json] [--up -y] [--cell 0.2]
"""
import argparse

import numpy as np

import hazards as hz
import room_frame
import splat_refine as sr
import splat_mesh as smesh

CELL_M = 0.07       # floor squares (a real room comes out around 10k triangles)
WALL_M = 1.0        # walls are cut to this height
MIN_POINTS = 12     # a square needs this many Gaussians to be a block
MIN_TOP_M = 0.05    # and something at least this high
STEP_M = 0.05       # heights snap to steps of this, so neighbours line up and merge
MERGE_DC = 45       # neighbours of the same height merge when their colours differ less than this (0-255)
TALL_M = 1.5        # a square standing taller is wall, a curtain or a tall cupboard: shown as low wall
OUTLINE_PCT = 3     # the room's outline: where all but this percent of the points are inside, each side
CEILING_GAP_M = 0.3  # ignore the top of the room (ceiling, lamps)
EDGE_M = 0.35       # squares this close to the outline are wall, not furniture


def _face(v, c, quad, colour):
    """Two triangles for a quad (corners in order), with their own vertices so the face is shaded flat."""
    base = len(v)
    v.extend(quad); c.extend([colour] * 4)
    return [(base, base + 1, base + 2), (base, base + 2, base + 3)]


def build(positions, colours, up="z", cell=CELL_M, wall=WALL_M):
    """Gaussians (positions, 0-255 colours) -> (vertices, normals, colours, triangles), z up."""
    flat, height = hz.split_up(np.asarray(positions, float), up)
    rgb = np.asarray(colours, float)
    floor = hz.floor_height(height, np.ones(len(height), bool))
    h = height - floor
    top_of_room = float(np.percentile(h, 99))
    (x0, y0), (x1, y1) = np.percentile(flat, OUTLINE_PCT, axis=0), np.percentile(flat, 100 - OUTLINE_PCT, axis=0)
    v, c, t = [], [], []
    on_floor = np.abs(h) < 0.04
    floor_rgb = tuple(np.median(rgb[on_floor], 0).astype(int)) if on_floor.any() else (180, 160, 130)
    t += _face(v, c, [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], floor_rgb)
    near_edge = (flat[:, 0] < x0 + EDGE_M) | (flat[:, 0] > x1 - EDGE_M) | (flat[:, 1] < y0 + EDGE_M) | (flat[:, 1] > y1 - EDGE_M)
    walls = near_edge & (h > 0.5)
    wall_rgb = tuple(np.median(rgb[walls], 0).astype(int)) if walls.any() else (220, 218, 210)
    wall = min(wall, max(0.3, top_of_room))
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        t += _face(v, c, [(a[0], a[1], 0), (b[0], b[1], 0), (b[0], b[1], wall), (a[0], a[1], wall)], wall_rgb)

    # Blocks: per floor square, how high things stand and their colour.
    inside = ~near_edge & (h > 0.04) & (h < top_of_room - CEILING_GAP_M)
    ij = np.floor((flat[inside] - (x0, y0)) / cell).astype(int)
    grid = {}
    order = np.lexsort((ij[:, 1], ij[:, 0]))
    keys, starts = np.unique(ij[order], axis=0, return_index=True)
    bounds = list(starts[1:]) + [len(order)]
    hs, cs = h[inside][order], rgb[inside][order]
    wall_colour = np.array(wall_rgb, float)
    for key, s, e in zip(map(tuple, keys), starts, bounds):
        if e - s >= MIN_POINTS:
            top = float(np.percentile(hs[s:e], 90))
            if top >= MIN_TOP_M:
                if top > TALL_M:  # walls inside the outline, curtains, tall cupboards: a low wall, like the outer ones
                    grid[key] = (wall, wall_colour, e - s)
                else:
                    grid[key] = (max(STEP_M, round(top / STEP_M) * STEP_M), np.median(cs[s:e], 0), e - s)
    # Pockmarks: an empty square with filled squares on at least three sides is filled like them
    # (sparse spots in a flat top would otherwise show as holes).
    near = ((1, 0), (-1, 0), (0, 1), (0, -1))
    holes = {(i + di, j + dj) for (i, j) in grid for di, dj in near} - set(grid)
    for k in holes:
        around = [grid[(k[0] + di, k[1] + dj)] for di, dj in near if (k[0] + di, k[1] + dj) in grid]
        if len(around) >= 3:
            tops = [g[0] for g in around]
            top = max(set(tops), key=tops.count)  # the most common neighbouring height
            grid[k] = (top, np.mean([g[1] for g in around if g[0] == top], 0), 2 * MIN_POINTS)
    # Lone specks (no neighbour, few Gaussians) are noise.
    grid = {k: g for k, g in grid.items() if g[2] >= 2 * MIN_POINTS or any((k[0] + di, k[1] + dj) in grid for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)))}
    used = set()
    for (i, j) in sorted(grid):
        if (i, j) in used:
            continue
        top, col, _ = grid[(i, j)]

        def ok(k):
            return k in grid and k not in used and abs(grid[k][0] - top) < 1e-6 and np.abs(grid[k][1] - col).max() <= MERGE_DC
        w = 1
        while ok((i + w, j)):
            w += 1
        d = 1
        while all(ok((i + a, j + d)) for a in range(w)):
            d += 1
        members = [(i + a, j + b) for a in range(w) for b in range(d)]
        used.update(members)
        z = float(np.mean([grid[m][0] for m in members]))
        colour = tuple(np.mean([grid[m][1] for m in members], 0).astype(int))
        ax, ay = x0 + i * cell, y0 + j * cell
        bx, by = ax + w * cell, ay + d * cell
        t += _face(v, c, [(ax, ay, z), (bx, ay, z), (bx, by, z), (ax, by, z)], colour)                      # top
        shade = tuple(int(x * 0.82) for x in colour)                                                         # sides a touch darker
        for a, b in (((ax, ay), (bx, ay)), ((bx, ay), (bx, by)), ((bx, by), (ax, by)), ((ax, by), (ax, ay))):
            t += _face(v, c, [(a[0], a[1], 0), (b[0], b[1], 0), (b[0], b[1], z), (a[0], a[1], z)], shade)
    verts = np.array(v, np.float32)
    verts[:, 2] += floor  # back to the frame's own heights
    tris = np.array(t, np.int64)
    n = np.zeros_like(verts)
    fn = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]], verts[tris[:, 2]] - verts[tris[:, 0]])
    for k in range(3):
        np.add.at(n, tris[:, k], fn)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
    if up != "z":  # the blocks are built level; put them back on the given axis
        axis, sign = hz.AXES[up]
        rest = [i for i in range(3) if i != axis]
        out = np.zeros_like(verts); out[:, rest[0]], out[:, rest[1]], out[:, axis] = verts[:, 0], verts[:, 1], sign * verts[:, 2]
        nout = np.zeros_like(n); nout[:, rest[0]], nout[:, rest[1]], nout[:, axis] = n[:, 0], n[:, 1], sign * n[:, 2]
        verts, n = out, nout
    return verts, n.astype(np.float32), np.array(c, np.uint8), tris


def from_splat(data, frame=None, up="-y", cell=CELL_M, wall=WALL_M):
    """A trained .splat file -> low-poly room (faint and huge Gaussians left out, as splat_mesh.from_splat)."""
    rec = np.frombuffer(bytes(data), sr.RECORD)
    keep = rec["rgba"][:, 3] >= 64
    if keep.sum() > 100:
        size = rec["scale"].max(1)
        keep &= size <= np.quantile(size[keep], 0.99)
    rec = rec[keep]
    pos = rec["pos"].astype(np.float64)
    if frame is not None:
        pos, up = room_frame.to_room(pos, frame), "z"
    return build(pos, rec["rgba"][:, :3], up, cell, wall), up


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("splat"), ap.add_argument("out")
    ap.add_argument("--frame"), ap.add_argument("--up", default="-y")
    ap.add_argument("--cell", type=float, default=CELL_M), ap.add_argument("--wall", type=float, default=WALL_M)
    a = ap.parse_args()
    (v, n, c, t), up = from_splat(open(a.splat, "rb").read(), room_frame.load(a.frame) if a.frame else None, a.up, a.cell, a.wall)
    smesh.write_mesh(v, n, c, t, a.out, comments=[f"up {up}", "low-poly"])
    print(a.out, len(v), "vertices", len(t), "triangles")


if __name__ == "__main__":
    main()
