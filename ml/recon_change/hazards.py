#!/usr/bin/env python3
"""Trip hazards: things on the floor in the way of the walks that matter, for the carer to clear up.

The refined splat map (splat_refine.py) marks the cells where something appeared or moved as
"movable". A trip hazard is a movable thing whose body is near floor level (a bag, a shoe, a chair
pulled out), lying close to one of the walks that matter at night: bed to bathroom, bed to front
door. Everything is worked out on the floor plane from the map's own Gaussians:

    floor      the height most static Gaussians sit at, among the lowest (the floor is the largest
               flat thing seen from above)
    objects    movable Gaussians from the floor up to KNEE_M, grouped on a 10 cm floor grid into
               connected pieces; tiny pieces (fewer than MIN_GAUSSIANS) are noise
    in the way an object whose footprint comes within REACH_M of a walk (the straight line between
               two named places; the relay's routes go round furniture, an object on the floor is
               what they would not know about)

The answer is a suggestion for the family ("something on the floor between the bed and the
bathroom, about 35 cm across"), never an alert: vision is evidence, not ground truth.

    python hazards.py map.npz places.json hazards.json [up]

places.json: {"places": {"bed": [x, y, z], ...}, "walks": [["bed", "bathroom"], ...]}. up is the map's
up axis ("-y" for VGGT maps, the default; "z" for the example room).
"""
import json
import sys
from datetime import datetime, timezone

import numpy as np

import splat_refine as sr

KNEE_M = 0.6        # only things up to knee height trip someone (a table top is above it)
REACH_M = 0.35      # an object this close to a walk is in the way
GRID_M = 0.10       # the floor grid objects are grouped on
MIN_GAUSSIANS = 40  # smaller pieces are noise
MIN_ACROSS_M = 0.08  # narrower pieces are the edges of things that did not move (a table leg sharing a cell with a moved chair)
NIGHT_WALKS = (("bed", "bathroom"), ("bed", "front door"), ("bed", "kitchen"))
AXES = {"y": (1, 1), "-y": (1, -1), "z": (2, 1), "-z": (2, -1), "x": (0, 1), "-x": (0, -1)}


def split_up(points, up):
    """(N, 3) points -> (N, 2) floor coordinates and (N,) heights, for an up axis like "-y"."""
    axis, sign = AXES[up]
    rest = [i for i in range(3) if i != axis]
    return points[:, rest], sign * points[:, axis]


def floor_height(heights, static):
    """The floor: the most crowded 5 cm height band among the lowest third of static Gaussians."""
    h = heights[static] if static.any() else heights
    low = h[h <= np.percentile(h, 33)]
    bins = np.arange(low.min(), low.max() + 0.1, 0.05)
    counts, edges = np.histogram(low, bins=bins) if len(bins) > 1 else (np.array([len(low)]), np.array([low.min(), low.min() + 0.05]))
    k = int(np.argmax(counts))
    return float(np.median(low[(low >= edges[k]) & (low <= edges[k + 1])]))


def pieces(cells):
    """Connected pieces (8-neighbour) of a set of (i, j) floor cells."""
    left, out = set(cells), []
    while left:
        stack, piece = [left.pop()], []
        while stack:
            i, j = stack.pop()
            piece.append((i, j))
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    n = (i + di, j + dj)
                    if n in left:
                        left.remove(n); stack.append(n)
        out.append(piece)
    return out


def segment_distance(p, a, b):
    """Distance from point p to the segment a-b (2D)."""
    ab, ap = b - a, p - a
    t = np.clip(np.dot(ap, ab) / max(np.dot(ab, ab), 1e-9), 0, 1)
    return float(np.linalg.norm(ap - t * ab))


def find(m, places, walks=NIGHT_WALKS, up="-y"):
    """Movable things on the floor, each with its size, the nearest place and the walk it is in.
    Returns {"floor", "up", "walks": [[name, name, [p0, p1]]...], "hazards": [...]}."""
    pos = m.splats["pos"].astype(np.float64)
    at = np.clip(np.searchsorted(m.keys, m.owner), 0, len(m.keys) - 1)
    movable = m.movable[at] & (m.keys[at] == m.owner)
    flat, height = split_up(pos, up)
    floor = floor_height(height, ~movable)
    names = {k.lower(): np.asarray(v, float) for k, v in places.items()}
    walk_lines = [(a, b, split_up(names[a][None], up)[0][0], split_up(names[b][None], up)[0][0])
                  for a, b in walks if a in names and b in names]
    # Above the floor itself (a cell with something new in it is movable, floor included) and below the knee.
    low = movable & (height > floor + 0.03) & (height < floor + KNEE_M)
    grid = np.floor(flat[low] / GRID_M).astype(int)
    cell_of = {}
    for n, key in enumerate(map(tuple, grid)):
        cell_of.setdefault(key, []).append(n)
    idx = np.flatnonzero(low)
    hazards = []
    for piece in pieces(cell_of):
        members = idx[np.concatenate([cell_of[c] for c in piece])]
        if len(members) < MIN_GAUSSIANS:
            continue
        f, h = flat[members], height[members]
        lo, hi = f.min(0), f.max(0)
        centre2, across = (lo + hi) / 2, float(np.max(hi - lo))
        top = float(h.max() - floor)
        if across < MIN_ACROSS_M:
            continue
        # The walks it is in, most important first (the order they are given in: bed to bathroom first).
        near_walks = [(n, a, b) for n, (a, b, pa, pb) in enumerate(walk_lines)
                      if segment_distance(centre2, pa, pb) - across / 2 < REACH_M]
        centre3 = pos[members].mean(0)
        nearest = min(names, key=lambda k: np.linalg.norm(split_up(names[k][None], up)[0][0] - centre2)) if names else None
        hazards.append({
            "centre": [round(float(v), 3) for v in centre3],
            "across_m": round(across, 2), "height_m": round(top, 2),
            "in_the_way": bool(near_walks),
            "walk": [near_walks[0][1], near_walks[0][2]] if near_walks else None,
            "walks": [[a, b] for _, a, b in near_walks],
            "near": nearest,
            "words": words(across, near_walks[0][1:] if near_walks else None, nearest),
        })
    hazards.sort(key=lambda z: (not z["in_the_way"], -z["across_m"]))
    for n, z in enumerate(hazards, 1):
        z["id"] = f"h{n}"
    return {"floor": round(floor, 3), "up": up, "walks": [[a, b, [list(map(float, names[a])), list(map(float, names[b]))]] for a, b, _, _ in walk_lines],
            "hazards": hazards}


def the(name):
    """"the bed", but "our table" (a place named with our, my or the keeps its own word)."""
    return name if name.split(" ", 1)[0] in ("our", "my", "the") else f"the {name}"


def words(across, walk, near):
    size = f"about {int(round(across * 100, -1))} cm across" if across < 1 else f"about {across:.1f} m across"
    if walk:
        return f"Something on the floor between {the(walk[0])} and {the(walk[1])}, {size}."
    return f"Something moved on the floor near {the(near)}, {size}. Not in a walking route." if near else f"Something moved on the floor, {size}."


def main(argv):
    m = sr.Map.load(argv[1])
    spec = json.load(open(argv[2]))
    out = find(m, spec["places"], [tuple(w) for w in spec.get("walks", NIGHT_WALKS)], argv[4] if len(argv) > 4 else "-y")
    out["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    json.dump(out, open(argv[3], "w"), indent=1)
    print(argv[3], sum(z["in_the_way"] for z in out["hazards"]), "in the way of", len(out["hazards"]))


if __name__ == "__main__":
    main(sys.argv)
