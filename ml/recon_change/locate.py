#!/usr/bin/env python3
"""Where is the wearer, and which way is a named place?

The room's baseline frames and a few frames of what the wearer sees now go through the model in
one pass (recon_change.reconstruct), so every camera comes back in one shared 3D frame. The
newest frame's camera is where the wearer stands and which way they face.

A place ("pill box", "bathroom door") is a spot picked once in a baseline picture: (frame, u, v),
with u and v from 0 to 1 across the picture. Its 3D position is read from that frame's depth in
the same pass, so the wearer and the places always share one frame and one scale.

How sure: the share of what the wearer sees that at least two baseline frames also saw at the
same depth AND looking alike there (the model's own image-patch features, Recon.feat), compared
with how well the baseline frames match each other. Depth alone is not enough: the model can lay
a photo of another room onto this room's surfaces (eval/LOCATE_RESULTS.md), but then the patches
that land on each other show different things. The yardstick (baseline frames against each
other) is measured with the same rule, so the score adapts to the room. A reconstruction saved
without features is scored on depth alone, as before.

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
# Appearance check (chosen in eval/LOCATE_LOOKALIKE_RESULTS.md): the image patch where a pixel lands
# must look like the pixel's own patch: cosine similarity of patch features at least LOOK_LIMIT. The
# best patch within LOOK_REACH patches of the landing place counts, because poses are not patch-perfect.
LOOK_LIMIT, LOOK_REACH = 0.5, 1


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


def map_frame(r, base, valid, typical_m=2.0):
    """The room map of one pass, and the ONE place its transform is defined: where the map's origin
    is in the pass's 3D frame, which way east, north and up point there (unit vectors, at right
    angles), and how many metres one unit of the pass is. Named places (locate) and lost items
    (items.py) both go through this, so swapping it for a stored room frame changes both."""
    up, north, east = axes(r.E, base)
    return {"origin": camera(r.E[base[0]])[0], "east": east, "north": north, "up": up,
            "metres": typical_m / float(np.median(r.depth[base][valid[base]]))}


def to_map(room, points):
    """Points in the pass's 3D frame -> (n, 3) map positions: east, north, up in rough metres."""
    d = (np.atleast_2d(points) - room["origin"]) * room["metres"]
    return np.stack([d @ room["east"], d @ room["north"], d @ room["up"]], 1)


def from_map(room, positions):
    """Map positions (east, north, up) -> (n, 3) points in the pass's 3D frame. The inverse of to_map."""
    p = np.atleast_2d(np.asarray(positions, float)) / room["metres"]
    return room["origin"] + p[:, :1] * room["east"] + p[:, 1:2] * room["north"] + p[:, 2:3] * room["up"]


def unit_features(r):
    """The patch features at length 1, so that a dot product is a cosine similarity. None if the
    reconstruction was saved without them."""
    if r.feat is None:
        return None
    f = r.feat.astype(np.float32)
    return f / (np.linalg.norm(f, axis=-1, keepdims=True) + 1e-6)


def views(r, valid, near, feat, q, others, tol, sample, rng, limit=None, reach=None):
    """Look up a sample of frame q's trusted pixels in the other frames. Returns, per pixel,
      agree   in how many of the other frames it lands at the same depth
      alike   in how many of those the image patch there also looks like its own, counted only
              up to two, which is all the score asks (None without features)
    and the share of frame q's pixels that are trusted."""
    limit = LOOK_LIMIT if limit is None else limit
    reach = LOOK_REACH if reach is None else reach
    H, W = r.depth.shape[1:]
    pix = np.flatnonzero(valid[q])
    if len(pix) > sample:
        pix = pix[rng.choice(len(pix), sample, replace=False)]
    pts = rc.unproject(r.depth[q], r.K[q], r.E[q]).reshape(-1, 3)[pix]
    agree = np.zeros(len(pix), np.int32)
    alike = None
    if feat is not None:
        h, w = feat.shape[1:3]
        ph, pw = H // h, W // w
        own = feat[q][np.minimum(pix // W // ph, h - 1), np.minimum(pix % W // pw, w - 1)]
        alike = np.zeros(len(pix), np.int32)
        steps = sorted(((dv, du) for dv in range(-reach, reach + 1) for du in range(-reach, reach + 1)),
                       key=lambda step: abs(step[0]) + abs(step[1]))  # the landing patch first, then the ones around it
    for j in others:
        gap, _ = rc.depth_gap(pts, r.E[j], r.K[j], r.depth[j], near[j], valid[j])
        ok = np.abs(gap) < tol
        agree += ok
        if feat is None:
            continue
        todo = np.flatnonzero(ok & (alike < 2))  # two frames that look alike are enough: skip the pixels that have them
        E, K = r.E[j], r.K[j]
        cam = pts[todo] @ E[:, :3].T + E[:, 3]
        pu = (np.rint(K[0, 0] * cam[:, 0] / cam[:, 2] + K[0, 2]) // pw).astype(np.int64)
        pv = (np.rint(K[1, 1] * cam[:, 1] / cam[:, 2] + K[1, 2]) // ph).astype(np.int64)
        mine = own[todo]
        for dv, du in steps:  # and stop asking about a pixel once one patch looks alike
            if len(todo) == 0:
                break
            hit = np.einsum("ij,ij->i", mine, feat[j][np.clip(pv + dv, 0, h - 1), np.clip(pu + du, 0, w - 1)]) >= limit
            alike[todo[hit]] += 1
            todo, pu, pv, mine = todo[~hit], pu[~hit], pv[~hit], mine[~hit]
    return agree, alike, float(valid[q].mean())


def locate(r, base, new, places=(), typical_m=2.0, conf_pct=25.0, tol=0.06, sample=6000, seed=0, look=True):
    """r holds baseline frames (indices `base`) and the wearer's frames (indices `new`, oldest
    first). places: [{"name", "frame" (index into r), "u", "v"}].
    look=False scores on depth alone (the behaviour before the appearance check).
    Returns (result, marks, valid): a JSON-ready dict, coloured balls for the 3D view as
    (world position, radius, RGB colour), and the trusted-pixel mask."""
    valid = rc.valid_mask(r, conf_pct)
    near = rc.nearest_in_window(r.depth, 7)
    room = map_frame(r, base, valid, typical_m)
    up, north, east, metres = room["up"], room["north"], room["east"], room["metres"]
    rng = np.random.default_rng(seed)
    trusted = float(valid[base].mean())

    def on_map(points):
        return to_map(room, points)

    feat = unit_features(r) if look else None

    def share(count, seen):
        """Share of a frame's view that at least two of the other frames matched."""
        return float((count >= 2).mean()) * min(1.0, seen / max(trusted, 1e-6)) if len(count) else 0.0

    # How well does a baseline frame match the rest of the baseline? That is what a good fix looks like.
    probes = [base[i] for i in np.linspace(0, len(base) - 1, min(4, len(base))).round().astype(int)]
    own = [views(r, valid, near, feat, q, [j for j in base if j != q], tol, sample, rng) for q in probes]
    now = [views(r, valid, near, feat, q, base, tol, sample, rng) for q in new]

    def score(which):  # 0: same depth, 1: same depth and looking alike. The yardstick uses the same rule.
        usual = float(np.median([share(v[which], v[2]) for v in own])) or 1.0
        return [min(1.0, share(v[which], v[2]) / usual) for v in now]

    by_depth = score(0)  # the score before the appearance check
    matches = by_depth if feat is None else score(1)
    sure = matches[-1]
    agree, alike, _ = now[-1]  # of the newest frame's pixels that match by depth, the share that also look alike
    looks_alike = None if feat is None else float((alike[agree >= 2] >= 2).mean()) if (agree >= 2).any() else 0.0

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
        # As 64-bit numbers, so 1.23 is written as 1.23 and not as 1.2300000190734863 (the fix stays small).
        "map": {"points": np.round(cloud[:, :2].astype(np.float64), 2).tolist(),
                "cameras": np.round(on_map(np.array([camera(r.E[q])[0] for q in base]))[:, :2].astype(np.float64), 2).tolist()},
        "match_per_frame": [round(m, 2) for m in matches],
        "looks_alike": None if looks_alike is None else round(looks_alike, 2),  # None: no features, depth only
        "match_by_depth": round(by_depth[-1], 2),
        "metres_per_unit": round(metres, 3),
    }
    return result, marks, valid
