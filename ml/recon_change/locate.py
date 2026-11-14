#!/usr/bin/env python3
"""Where is the wearer, and which way is a named place?

The room's baseline frames and a few frames of what the wearer sees now go through the model in
one pass (recon_change.reconstruct), so every camera comes back in one shared 3D frame. The
newest frame's camera is where the wearer stands and which way they face.

A place ("pill box", "bathroom door") is a spot picked once in a baseline picture: (frame, u, v),
with u and v from 0 to 1 across the picture. Its 3D position is read from that frame's depth in
the same pass, so the wearer and the places always share one frame and one scale.

How sure: the share of what the wearer sees that at least two baseline frames also saw at the
same depth, compared with how well the baseline frames match each other. A view of another room
matches almost nothing.

Map axes: x east, y north, z up, in metres from the first baseline camera. "North" is where that
camera faced. Metres are rough: the model has no real-world scale, so a typical viewing distance
is assumed (typical_m).
"""
import numpy as np

import recon_change as rc

# Match with the map: at least FOUND is a fix, below UNSURE the wearer is lost. On the PASLCD benchmark
# (eval/LOCATE_RESULTS.md, 24 baseline photos) a wrong room never scored above 0.23 and the right room
# never below 0.49, so 0.3 turns every wrong-room answer seen there into "lost" at no cost.
FOUND, UNSURE = 0.5, 0.3


def camera(E):
    """A camera's position and viewing direction in the world."""
    return -E[:, :3].T @ E[:, 3], E[2, :3]


def axes(E, base):
    """Unit vectors for up, north and east, from the baseline cameras."""
    up = -np.mean([E[q][1, :3] for q in base], 0)  # cameras are held roughly upright, and their y axis points down
    up /= np.linalg.norm(up)
    north = E[base[0]][2, :3] - (E[base[0]][2, :3] @ up) * up  # the first camera's viewing direction, levelled
    north /= np.linalg.norm(north)
    return up, north, np.cross(north, up)


def locate(r, base, new, places=(), typical_m=2.0, conf_pct=25.0, tol=0.06, sample=6000, seed=0):
    """r holds baseline frames (indices `base`) and the wearer's frames (indices `new`, oldest
    first). places: [{"name", "frame" (index into r), "u", "v"}].
    Returns (result, marks, valid): a JSON-ready dict, coloured balls for the 3D view as
    (world position, radius, RGB colour), and the trusted-pixel mask."""
    valid = rc.valid_mask(r, conf_pct)
    near = rc.nearest_in_window(r.depth, 7)
    up, north, east = axes(r.E, base)
    metres = typical_m / float(np.median(r.depth[base][valid[base]]))
    origin = camera(r.E[base[0]])[0]
    rng = np.random.default_rng(seed)
    trusted = float(valid[base].mean())

    def on_map(points):
        d = (np.atleast_2d(points) - origin) * metres
        return np.stack([d @ east, d @ north, d @ up], 1)

    def match(q, others):
        """Share of frame q's view that at least two of the other frames saw at the same depth."""
        pts = rc.unproject(r.depth[q], r.K[q], r.E[q])[valid[q]]
        if len(pts) == 0:
            return 0.0
        if len(pts) > sample:
            pts = pts[rng.choice(len(pts), sample, replace=False)]
        agree, _, _ = rc.votes(r, valid, near, pts, others, tol, 2 * tol)
        return float((agree >= 2).mean()) * min(1.0, float(valid[q].mean()) / max(trusted, 1e-6))

    # How well does a baseline frame match the rest of the baseline? That is what a good fix looks like.
    probes = [base[i] for i in np.linspace(0, len(base) - 1, min(4, len(base))).round().astype(int)]
    usual = float(np.median([match(q, [j for j in base if j != q]) for q in probes])) or 1.0
    matches = [min(1.0, match(q, base) / usual) for q in new]
    sure = matches[-1]

    position, forward = camera(r.E[new[-1]])
    here = on_map(position)[0]
    heading = float(np.degrees(np.arctan2(forward @ east, forward @ north)))  # 0 = north, clockwise

    ball = 0.07 / metres
    marks = [(position, 1.6 * ball, (40, 150, 255))]  # the wearer: blue
    level = forward - (forward @ up) * up
    level /= np.linalg.norm(level) + 1e-9
    marks += [(position + level * ball * 1.5 * i, 0.6 * ball, (160, 220, 255)) for i in range(1, 7)]  # where they face

    waypoints = []
    for place in places:
        q = place["frame"]
        H, W = r.depth[q].shape
        x = int(np.clip(round(place["u"] * (W - 1)), 0, W - 1))
        y = int(np.clip(round(place["v"] * (H - 1)), 0, H - 1))
        window = r.depth[q][max(0, y - 3):y + 4, max(0, x - 3):x + 4]
        if not (window > 0).any():
            continue
        depth = float(np.median(window[window > 0]))
        K = r.K[q]
        cam = np.array([(x - K[0, 2]) / K[0, 0] * depth, (y - K[1, 2]) / K[1, 1] * depth, depth])
        world = (cam - r.E[q][:, 3]) @ r.E[q][:, :3]
        at = on_map(world)[0]
        east_m, north_m = at[0] - here[0], at[1] - here[1]
        bearing = (np.degrees(np.arctan2(east_m, north_m)) - heading + 180) % 360 - 180  # 0 ahead, + to the right
        waypoints.append({"name": place["name"], "distance_m": round(float(np.hypot(east_m, north_m)), 1),
                          "bearing_deg": int(round(float(bearing))), "position": [round(float(v), 2) for v in at]})
        marks.append((world, 1.4 * ball, (255, 210, 40)))  # a place: yellow

    # A floor plan to draw: the room's points seen from above, without the floor and the ceiling.
    cloud = on_map(np.concatenate([rc.unproject(r.depth[q], r.K[q], r.E[q])[valid[q]][::23] for q in base]))
    low, high = np.percentile(cloud[:, 2], [40, 90])
    cloud = cloud[(cloud[:, 2] >= low) & (cloud[:, 2] <= high)]
    if len(cloud) > 1500:
        cloud = cloud[rng.choice(len(cloud), 1500, replace=False)]

    result = {
        "state": "found" if sure >= FOUND else "unsure" if sure >= UNSURE else "lost",
        "confidence": round(sure, 2),
        "position": [round(float(v), 2) for v in here],
        "heading_deg": int(round(heading)),
        "waypoints": waypoints,
        "track": [[round(float(v), 2) for v in on_map(camera(r.E[q])[0])[0][:2]] for q in new],
        "map": {"points": np.round(cloud[:, :2], 2).tolist(),
                "cameras": np.round(on_map(np.array([camera(r.E[q])[0] for q in base]))[:, :2], 2).tolist()},
        "match_per_frame": [round(m, 2) for m in matches],
        "metres_per_unit": round(metres, 3),
    }
    return result, marks, valid
