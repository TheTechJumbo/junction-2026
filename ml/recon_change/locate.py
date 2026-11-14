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
camera faced.

Metres: the model has no real-world scale. With one known distance between two named places
(`known`, from the carer) the fix scales itself so that this distance comes out right. Without
it a typical viewing distance is assumed (typical_m), which is a guess about the room.

Up: the direction along which the most baseline points lie in one thin layer, which is the normal
of the largest level surface in view (a table top or the floor: every level surface has the same
normal). The average of the baseline cameras' own "up" is only the first guess and the fallback:
cameras that look down at a desk from one side tilt it by 8 to 19 degrees (eval/LOCATE_WALK_RESULTS.md).
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
# The assumed median viewing distance of the baseline frames, in metres, used when no known distance
# is given. Fitted to the three TUM rooms of eval/LOCATE_WALK_RESULTS.md, which are all desks seen from
# 1 to 2.5 m (the right value there was 1.30, 1.52 and 1.61). A large living room will need more.
TYPICAL_M = 1.5
# A known distance is not believed when it gives metres more than this many times away from the guess
# above, either way (a median viewing distance under 0.3 m or over 7.5 m): that is a typing mistake
# (24 for 2.4, centimetres for metres) or two places picked on the wrong things.
SCALE_LIMIT = 5.0
# Up from a level surface: it may differ from the cameras' average by at most UP_LIMIT degrees, the
# surface must hold at least UP_SHARE of the baseline's points, and a point is on it within UP_TOL of
# the baseline's median viewing distance. Otherwise the cameras' average is used. In the walking test
# the surface was 9 to 26 degrees from the cameras' average and held 28% to 41% of the points; a bare
# wall seen at a slant reaches about 5%.
UP_LIMIT, UP_SHARE, UP_TOL = 35.0, 0.15, 0.02
# A view more than this many degrees from every baseline view is mostly lost (walking test: found 94%
# under 15 degrees, 82% from 15 to 30, 14% beyond). Fitted to a camera that sees about 60 degrees across.
VIEW_REACH = 30.0


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


def angle(a, b):
    return float(np.degrees(np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12), -1, 1))))


def tilted(centre, reach, step):
    """Unit vectors within about `reach` degrees of `centre`, on a grid `step` degrees apart."""
    a = np.cross(centre, [1.0, 0.0, 0.0] if abs(centre[0]) < 0.9 else [0.0, 1.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(centre, a)
    ticks = np.arange(-reach, reach + step / 2, step)
    x, y = np.meshgrid(ticks, ticks)
    keep = x ** 2 + y ** 2 <= reach ** 2 + 1e-9
    d = centre + np.tan(np.radians(x[keep]))[:, None] * a + np.tan(np.radians(y[keep]))[:, None] * b
    return d / np.linalg.norm(d, axis=1, keepdims=True)


def fullest_layer(points, directions, thick):
    """For each direction: how many points lie in the fullest layer `thick` thick across it, and
    the height of that layer's middle."""
    count, middle = np.zeros(len(directions), np.int64), np.zeros(len(directions))
    for i, n in enumerate(directions):
        h = points @ n
        low = max(float(h.min()), -2000 * thick)  # points far away must not make the count table huge
        k = np.clip(np.floor((h - low) / (thick / 2)), 0, 8000).astype(np.int64)
        c = np.bincount(k, minlength=2)
        two = c[:-1] + c[1:]  # two half-layers next to each other: a full layer wherever it lies
        j = int(np.argmax(two))
        count[i], middle[i] = two[j], low + (j + 1) * thick / 2
    return count, middle


def surface_up(points, guess, tol, limit=UP_LIMIT, min_share=UP_SHARE):
    """Up from the room itself. points: (n, 3) of the baseline, guess: the cameras' average up,
    tol: how far a point may be from a surface and still be on it.
    Among the directions within `limit` degrees of the guess, take the one along which the most
    points lie in one thin layer, then fit a plane through that layer's points. Returns (up,
    {"from": "surface" or "cameras", "tilt_deg": the surface's normal against the guess,
    "share": the share of the points on the surface}). The guess comes back unchanged when the
    surface holds less than min_share of the points or its normal sits at the edge of the search
    (then the true answer may lie outside it, or the surface is a slope)."""
    guess = np.asarray(guess, np.float64) / np.linalg.norm(guess)
    info = {"from": "cameras", "tilt_deg": 0.0, "share": 0.0}
    pts = np.asarray(points, np.float64)
    if len(pts) < 200 or not tol > 0:
        return guess, info
    pts = pts - np.median(pts, 0)
    coarse = tilted(guess, limit, 2.0)
    count, _ = fullest_layer(pts[::max(1, len(pts) // 10000)], coarse, 2 * tol)
    fine = tilted(coarse[int(np.argmax(count))], 2.0, 0.5)
    count, middle = fullest_layer(pts, fine, 2 * tol)
    best = int(np.argmax(count))
    n = fine[best]
    on = np.abs(pts @ n - middle[best]) < tol
    if on.sum() >= 50:  # least-squares plane through the layer's points
        flat = pts[on] - pts[on].mean(0)
        normal = np.linalg.eigh(flat.T @ flat)[1][:, 0]
        normal = normal if normal @ n > 0 else -normal
        if angle(normal, n) < 2.0:
            n = normal
    info.update(tilt_deg=round(angle(n, guess), 1), share=round(float(on.mean()), 3))
    if info["share"] < min_share or info["tilt_deg"] > limit - 1.0:
        return guess, info
    return n, {**info, "from": "surface"}


def known_metres(spots, known, assumed, limit=SCALE_LIMIT):
    """Metres per unit of the pass from one known distance. spots: {place name: its position in the
    pass's 3D frame}; known: {"a": name, "b": name, "metres": the true distance between them};
    assumed: metres per unit from the assumed viewing distance, used when the known distance
    cannot be. Returns (metres per unit, "known distance" or "assumed viewing distance", a note
    saying why a given distance was not used, or None)."""
    guess = "assumed viewing distance"
    if not known:
        return assumed, guess, None
    by_name = {str(name).strip().lower(): np.asarray(x, np.float64) for name, x in spots.items()}
    try:
        a, b, true_m = (str(known[k]).strip().lower() for k in "ab"), None, float(known["metres"])
        a, b = (by_name.get(name) for name in a)
    except (KeyError, TypeError, ValueError):
        return assumed, guess, "The known distance could not be read."
    if a is None or b is None:
        return assumed, guess, "The known distance names a place that is not in this fix."
    gap = float(np.linalg.norm(a - b))
    if not (true_m > 0 and gap > 1e-6):
        return assumed, guess, "The known distance must be more than zero, between two different places."
    metres = true_m / gap
    if not assumed / limit <= metres <= assumed * limit:
        return assumed, guess, (f"The known distance makes everything {metres / assumed:.1f} times the usual guess, "
                                f"so it was not used. Check the number and the two places.")
    return metres, "known distance", None


def build_frame(r, base, valid, typical_m=None, up="surface", known=None, spots=None, points=None):
    """The room map of one pass, worked out from scratch (see map_frame). up: "surface" or "cameras".
    known and spots: one known distance and the places' positions in the pass (see known_metres).
    points: {baseline frame: its trusted points in the pass's 3D frame}, if the caller has them."""
    cam_up, north, east = axes(r.E, base)
    unit = float(np.median(r.depth[base][valid[base]]))  # the baseline's median viewing distance, in the pass's units
    use, how = cam_up, {"from": "cameras", "tilt_deg": 0.0, "share": 0.0}
    if up == "surface":
        cloud = np.concatenate([(points[q] if points else rc.unproject(r.depth[q], r.K[q], r.E[q])[valid[q]])[::47] for q in base])
        use, how = surface_up(cloud, cam_up, UP_TOL * unit)
        north, east = level_axes(r.E[base[0]][2, :3], use, north)
    metres, metres_from, note = known_metres(spots or {}, known, (TYPICAL_M if typical_m is None else typical_m) / unit)
    return {"origin": camera(r.E[base[0]])[0], "east": east, "north": north, "up": use, "metres": metres,
            "base": tuple(base), "metres_from": metres_from, "metres_note": note, "up_how": how}


def level_axes(forward, up, fallback):
    """North (a viewing direction, levelled) and east for a given up."""
    north = forward - (forward @ up) * up
    if np.linalg.norm(north) < 1e-6:
        north = fallback - (fallback @ up) * up
    north = north / np.linalg.norm(north)
    return north, np.cross(north, up)


def map_frame(r, base, valid, typical_m=None):
    """The room map of one pass, and the ONE place its transform is defined: where the map's origin
    is in the pass's 3D frame, which way east, north and up point there (unit vectors, at right
    angles), and how many metres one unit of the pass is. Named places (locate) and lost items
    (items.py) both go through this, so swapping it for a stored room frame changes both.

    locate() decides the frame of a pass (which up, which metres) and keeps it on the pass as
    r.room. Asked again for the same pass and baseline frames, this returns that frame whatever
    typical_m says, so that items always land on the same map as the places."""
    kept = getattr(r, "room", None)
    if isinstance(kept, dict) and kept.get("base") == tuple(base):
        return kept
    return build_frame(r, base, valid, typical_m)


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


def compass(direction, room):
    """A direction of the pass as a map heading in degrees: 0 = north, clockwise."""
    return float(np.degrees(np.arctan2(direction @ room["east"], direction @ room["north"])))


def wrap(deg):
    return (deg + 180) % 360 - 180


def view_gap(E, base, q, room):
    """How far frame q looks from the closest baseline view. Returns (the angle between the two
    viewing directions in degrees, the turn to the right and the tilt upwards in degrees that
    bring q's view onto that baseline view, the baseline frame)."""
    forward = E[q][2, :3]
    gaps = [angle(forward, E[j][2, :3]) for j in base]
    j = base[int(np.argmin(gaps))]
    there = E[j][2, :3]
    up = room["up"]
    return (min(gaps), wrap(compass(there, room) - compass(forward, room)),
            float(np.degrees(np.arcsin(np.clip(there @ up, -1, 1)) - np.arcsin(np.clip(forward @ up, -1, 1)))), j)


def hint(gap, turn, tilt, reach=VIEW_REACH):
    """What to tell a wearer who was not found, when the likely reason is that they look where the
    baseline never looked: None, or which way to turn back to the closest baseline view."""
    if gap <= reach:
        return None
    if abs(turn) >= 15 or abs(tilt) < 15:
        say = "Turn around" if abs(turn) > 135 else "Turn right" if turn > 0 else "Turn left"
    else:
        say = "Look up" if tilt > 0 else "Look down"
    return {"why": "not_filmed", "say": say, "turn_deg": int(round(turn)), "tilt_deg": int(round(tilt))}


def coverage(E, base, room, reach=VIEW_REACH):
    """Which directions a baseline covers, for telling the carer what else to film.
    A heading counts as covered when a baseline frame looks within `reach` degrees of it (to the
    left or right; headings are map headings: 0 = where the first baseline frame looks, clockwise).
    Returns {"headings_deg": every baseline frame's heading, "pitch_deg": [lowest, highest] the
    frames look (0 level, minus = down), "covered_deg": how much of the full turn is covered,
    "gaps": [{"from_deg", "to_deg", "middle_deg", "side"}]} with side = where the gap is, seen
    from the first baseline frame: "ahead", "right", "behind" or "left"."""
    heads = sorted(compass(E[j][2, :3], room) % 360 for j in base)
    pitch = [float(np.degrees(np.arcsin(np.clip(E[j][2, :3] @ room["up"], -1, 1)))) for j in base]
    gaps = []
    for a, b in zip(heads, heads[1:] + [heads[0] + 360]):
        if b - a > 2 * reach:
            middle = wrap((a + b) / 2)
            gaps.append({"from_deg": int(round(wrap(a + reach))), "to_deg": int(round(wrap(b - reach))), "middle_deg": int(round(middle)),
                         "width_deg": int(round(b - a - 2 * reach)),
                         "side": "ahead" if abs(middle) <= 45 else "behind" if abs(middle) >= 135 else "right" if middle > 0 else "left"})
    return {"headings_deg": [int(round(wrap(h))) for h in heads], "pitch_deg": [int(round(min(pitch))), int(round(max(pitch)))],
            "covered_deg": int(round(360 - sum(g["width_deg"] for g in gaps))), "gaps": gaps}


def state_of(sure, one_frame=0.0, rescue=True):
    """found: two baseline frames agree with enough of the view. unsure: fewer do, or only one
    baseline frame agrees with enough of it (rescue). lost: neither."""
    if sure >= FOUND:
        return "found"
    return "unsure" if sure >= UNSURE or (rescue and one_frame >= FOUND) else "lost"


def spot(r, place):
    """A place's position in the pass's 3D frame, from the depth at its pixel. None without depth there."""
    q = place["frame"]
    H, W = r.depth[q].shape
    x = int(np.clip(round(place["u"] * (W - 1)), 0, W - 1))
    y = int(np.clip(round(place["v"] * (H - 1)), 0, H - 1))
    window = r.depth[q][max(0, y - 3):y + 4, max(0, x - 3):x + 4]
    if not (window > 0).any():
        return None
    depth = float(np.median(window[window > 0]))
    K = r.K[q]
    cam = np.array([(x - K[0, 2]) / K[0, 0] * depth, (y - K[1, 2]) / K[1, 1] * depth, depth])
    return (cam - r.E[q][:, 3]) @ r.E[q][:, :3]


def locate(r, base, new, places=(), typical_m=None, conf_pct=25.0, tol=0.06, sample=6000, seed=0, look=True, need=2,
           known=None, up="surface", rescue=True):
    """r holds baseline frames (indices `base`) and the wearer's frames (indices `new`, oldest
    first). places: [{"name", "frame" (index into r), "u", "v"}].
    typical_m: the assumed median viewing distance of the baseline (None = TYPICAL_M), used for
    metres unless `known` gives one true distance: {"a": place name, "b": place name, "metres"}.
    up: "surface" takes up from the largest level surface (see surface_up), "cameras" from the
    average of the baseline cameras, as before.
    look=False scores on depth alone (the behaviour before the appearance check).
    need: how many baseline frames must match a pixel of the wearer's view for it to count, 2 or 1.
    2 is what runs and what "found" asks. rescue: a view that only reaches the found mark when one
    baseline frame is enough is "unsure" instead of "lost" (only with the appearance check: on
    depth alone one frame lets wrong rooms through). Measured in eval/LOCATE_WALK_RESULTS.md.
    Returns (result, marks, valid): a JSON-ready dict, coloured balls for the 3D view as
    (world position, radius, RGB colour), and the trusted-pixel mask."""
    valid = rc.valid_mask(r, conf_pct)
    near = rc.nearest_in_window(r.depth, 7)
    spots = [spot(r, place) for place in places]
    seen = {q: rc.unproject(r.depth[q], r.K[q], r.E[q])[valid[q]] for q in base}  # each baseline frame's trusted points
    room = build_frame(r, base, valid, typical_m, up, known, {p["name"]: x for p, x in zip(places, spots) if x is not None}, seen)
    try:
        r.room = room  # items.py asks map_frame for this pass's frame and must get this one
    except AttributeError:
        pass
    up, north, east, metres = room["up"], room["north"], room["east"], room["metres"]
    rng = np.random.default_rng(seed)
    trusted = float(valid[base].mean())

    def on_map(points):
        return to_map(room, points)

    feat = unit_features(r) if look else None

    def share(count, seen, k):
        """Share of a frame's view that at least k of the other frames matched."""
        return float((count >= k).mean()) * min(1.0, seen / max(trusted, 1e-6)) if len(count) else 0.0

    # How well does a baseline frame match the rest of the baseline? That is what a good fix looks like.
    probes = [base[i] for i in np.linspace(0, len(base) - 1, min(4, len(base))).round().astype(int)]
    own = [views(r, valid, near, feat, q, [j for j in base if j != q], tol, sample, rng) for q in probes]
    now = [views(r, valid, near, feat, q, base, tol, sample, rng) for q in new]

    def score(which, k=need):  # 0: same depth, 1: same depth and looking alike. The yardstick uses the same rule.
        usual = float(np.median([share(v[which], v[2], k) for v in own])) or 1.0
        return [min(1.0, share(v[which], v[2], k) / usual) for v in now]

    by_depth = score(0)  # the score before the appearance check
    matches = by_depth if feat is None else score(1)
    sure = matches[-1]
    one_frame = None if feat is None else score(1, 1)[-1]  # the same score when one baseline frame is enough
    agree, alike, _ = now[-1]  # of the newest frame's pixels that match by depth, the share that also look alike
    looks_alike = None if feat is None else float((alike[agree >= need] >= need).mean()) if (agree >= need).any() else 0.0

    position, forward = camera(r.E[new[-1]])
    here = on_map(position)[0]
    heading = float(np.degrees(np.arctan2(forward @ east, forward @ north)))  # 0 = north, clockwise

    ball = 0.07 / metres
    marks = [(position, 1.6 * ball, (40, 150, 255))]  # the wearer: blue
    level = forward - (forward @ up) * up
    level /= np.linalg.norm(level) + 1e-9
    marks += [(position + level * ball * 1.5 * i, 0.6 * ball, (160, 220, 255)) for i in range(1, 7)]  # where they face

    waypoints = []
    for place, world in zip(places, spots):
        if world is None:
            continue
        at = on_map(world)[0]
        east_m, north_m = at[0] - here[0], at[1] - here[1]
        bearing = (np.degrees(np.arctan2(east_m, north_m)) - heading + 180) % 360 - 180  # 0 ahead, + to the right
        waypoints.append({"name": place["name"], "distance_m": round(float(np.hypot(east_m, north_m)), 1),
                          "bearing_deg": int(round(float(bearing))), "position": [round(float(v), 2) for v in at]})
        marks.append((world, 1.4 * ball, (255, 210, 40)))  # a place: yellow

    # A floor plan to draw: the room's points seen from above, without the floor and the ceiling.
    cloud = on_map(np.concatenate([seen[q][::23] for q in base]))
    low, high = np.percentile(cloud[:, 2], [40, 90])
    cloud = cloud[(cloud[:, 2] >= low) & (cloud[:, 2] <= high)]
    if len(cloud) > 1500:
        cloud = cloud[rng.choice(len(cloud), 1500, replace=False)]

    state = state_of(sure, one_frame or 0.0, rescue and one_frame is not None)
    gap, turn, tilt, _ = view_gap(r.E, base, new[-1], room)
    result = {
        "state": state,
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
        # Since version 2 (eval/LOCATE_WALK_RESULTS.md):
        "metres_from": room["metres_from"],  # "known distance" or "assumed viewing distance"
        "up_from": room["up_how"]["from"],  # "surface" or "cameras"
        "up_tilt_deg": room["up_how"]["tilt_deg"],  # the surface's normal against the cameras' average up
        "up_surface_share": room["up_how"]["share"],
        "match_one_frame": None if one_frame is None else round(one_frame, 2),
        "one_frame_only": bool(state == "unsure" and sure < UNSURE),  # unsure only because one baseline frame agrees
        "view_gap_deg": int(round(gap)),  # the angle to the closest baseline view
        "hint": None if state == "found" else hint(gap, turn, tilt),  # which way to turn when the room was not filmed this way
        "baseline_views": coverage(r.E, base, room),
    }
    if room["metres_note"]:
        result["metres_note"] = room["metres_note"]
    return result, marks, valid
