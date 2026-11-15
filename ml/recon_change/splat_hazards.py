#!/usr/bin/env python3
"""Possible trip hazards from ONE trained splat (shape only). OPTIONAL: easy to switch off.

hazards.py finds hazards from the refined map, which knows what moved. A trained splat
(video_map.py's trained.splat) is one snapshot and does not: this file guesses from shape alone.
It writes the same hazards.json, so the family app needs nothing else, and each hazard says it is a
guess ("method": "shape", "Possible trip hazard: ...").

    floor       the most crowded height band among the lowest Gaussians (as in hazards.py)
    low things  Gaussians from LOW_M above the floor up to KNEE_M, on a 10 cm floor grid
    not walls   a grid cell is dropped when the same column also holds something above TALL_M
                (walls, cupboards, door frames, curtains)
    objects     connected pieces of what is left; a piece is furniture (bed, sofa, counter) when its
                footprint is wider than FURNITURE_M or larger than FURNITURE_M2, and noise when it is
                narrower than MIN_ACROSS_M (table legs) or has few Gaussians
    in the way  within REACH_M of a night walk (bed to bathroom, front door, kitchen), as hazards.py

Known to go wrong: a footstool, a bin or a low basket that always stands there is flagged; a long
low thing (a rug edge, a cable) is missed. That is why it is optional. To switch it off: don't run
this file (or delete mesh/hazards.json), or set HAZARDS_ENABLED=0 for the backend, which then
answers /hazards with 404 and the family app hides "to clear up". Nothing else depends on it.

    python splat_hazards.py trained.splat places.json hazards.json [--frame room.json] [--up -y]

places.json: {"places": {"bed": [e, n, u], ...}, "walks": [["bed", "bathroom"], ...]} in the map's frame.
With --frame (room_frame.py) the splat is moved into that frame first and up is z.
"""
import argparse
import json
from datetime import datetime, timezone

import numpy as np

import hazards as hz
import room_frame
import splat_refine as sr

LOW_M = 0.03         # above the floor itself
KNEE_M = hz.KNEE_M   # up to knee height
TALL_M = 1.0         # a column with something this high is a wall or a cupboard, not a thing on the floor
FURNITURE_M = 0.9    # wider than this is furniture ...
FURNITURE_M2 = 0.45  # ... and so is a footprint larger than this
MIN_ACROSS_M = 0.08  # narrower is noise or a leg
MIN_GAUSSIANS = 40
MIN_ALPHA = 64       # trained splats keep faint Gaussians for the background
DROP_LARGEST = 0.01  # and a few huge ones (floaters)


def load(data, frame=None, up="-y"):
    """A .splat file's bytes -> (positions, up): faint and huge Gaussians left out, in the map's frame when given."""
    rec = np.frombuffer(bytes(data), sr.RECORD)
    keep = rec["rgba"][:, 3] >= MIN_ALPHA
    if keep.sum() > 100:
        size = rec["scale"].max(1)
        keep &= size <= np.quantile(size[keep], 1 - DROP_LARGEST)
    pos = rec["pos"][keep].astype(np.float64)
    if frame is not None:
        return room_frame.to_room(pos, frame), "z"
    return pos, up


def find(positions, places, walks=hz.NIGHT_WALKS, up="-y", floor=None):
    """Possible hazards from positions alone. Same answer shape as hazards.find, method "shape".
    floor: the floor's height, when known better than the points tell (see lowpoly.build)."""
    flat, height = hz.split_up(positions, up)
    floor = hz.floor_height(height, np.ones(len(height), bool)) if floor is None else float(floor)
    cell = np.floor(flat / hz.GRID_M).astype(int)
    tall = {tuple(c) for c in cell[height > floor + TALL_M]}
    low = (height > floor + LOW_M) & (height < floor + KNEE_M)
    low &= np.array([tuple(c) not in tall for c in cell])
    names = {k.lower(): np.asarray(v, float) for k, v in places.items()}
    walk_lines = [(a, b, hz.split_up(names[a][None], up)[0][0], hz.split_up(names[b][None], up)[0][0])
                  for a, b in walks if a in names and b in names]
    cell_of = {}
    idx = np.flatnonzero(low)
    for n, key in enumerate(map(tuple, cell[low])):
        cell_of.setdefault(key, []).append(n)
    found = []
    for piece in hz.pieces(cell_of):
        members = idx[np.concatenate([cell_of[c] for c in piece])]
        if len(members) < MIN_GAUSSIANS:
            continue
        f = flat[members]
        lo, hi = f.min(0), f.max(0)
        across = float(np.max(hi - lo))
        if across < MIN_ACROSS_M or across > FURNITURE_M or len(piece) * hz.GRID_M ** 2 > FURNITURE_M2:
            continue
        centre2 = (lo + hi) / 2
        near_walks = [(n, a, b) for n, (a, b, pa, pb) in enumerate(walk_lines)
                      if hz.segment_distance(centre2, pa, pb) - across / 2 < hz.REACH_M]
        nearest = min(names, key=lambda k: np.linalg.norm(hz.split_up(names[k][None], up)[0][0] - centre2)) if names else None
        words = hz.words(across, near_walks[0][1:] if near_walks else None, nearest)
        found.append({"centre": [round(float(v), 3) for v in positions[members].mean(0)], "across_m": round(across, 2),
                      "height_m": round(float(height[members].max() - floor), 2), "in_the_way": bool(near_walks),
                      "walk": [near_walks[0][1], near_walks[0][2]] if near_walks else None,
                      "walks": [[a, b] for _, a, b in near_walks], "near": nearest, "method": "shape",
                      "words": "Possible trip hazard: " + words[0].lower() + words[1:]})
    found.sort(key=lambda z: (not z["in_the_way"], -z["across_m"]))
    for n, z in enumerate(found, 1):
        z["id"] = f"h{n}"
    return {"floor": round(floor, 3), "up": up, "method": "shape (one splat, no change information)",
            "walks": [[a, b, [list(map(float, names[a])), list(map(float, names[b]))]] for a, b, _, _ in walk_lines],
            "hazards": found}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("splat"), ap.add_argument("places"), ap.add_argument("out")
    ap.add_argument("--frame", help="room frame JSON (room_frame.py): move the splat into the map's frame")
    ap.add_argument("--up", default="-y", help='the splat\'s up axis without --frame ("-y" for VGGT)')
    a = ap.parse_args()
    spec = json.load(open(a.places))
    positions, up = load(open(a.splat, "rb").read(), room_frame.load(a.frame) if a.frame else None, a.up)
    out = find(positions, spec["places"], [tuple(w) for w in spec.get("walks", hz.NIGHT_WALKS)], up)
    out.update(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), places=spec["places"])
    json.dump(out, open(a.out, "w"), indent=1)
    print(a.out, sum(z["in_the_way"] for z in out["hazards"]), "possibly in the way of", len(out["hazards"]))


if __name__ == "__main__":
    main()
