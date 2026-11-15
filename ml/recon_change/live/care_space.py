#!/usr/bin/env python3
"""One space for the caretaker's page (/care): a splat trained on the GPU server, put into the room map's axes.

The wearer's position fixes, the named places and the room map (worker.room_map) share one set of axes: x east,
y north, z up, in metres from the first baseline camera. A splat trained from a recording (video_map.py) has its
own: the recording's first keyframe at the origin, OpenCV axes (x right, y down, z ahead), in the model's units.
So the 3D page drew the wearer on the room map only (viewer.html: "wearer not drawn: other axes").

When the baseline was recorded with the glasses or the phone (Record room), both come from the same pictures: the
trained splat's keyframes and the room map's cameras are the same views in the same order, with a few dropped on
either side. space() pairs them by heading, in order, and returns the similarity that takes the trained splat
into map axes: the turn about up from the paired headings, the scale and the shift from the paired positions.

    python live/care_space.py ROOM.json CAMERAS.npz   # how well a trained splat fits a room map
"""
import json
import math
import sys

import numpy as np

GAP_DEG = 12.0  # leaving a view unpaired costs as much as a pair this far apart in heading (20 let a run slip by one)
PAIR_DEG = 25.0  # pairs further apart than this are not used
MIN_PAIRS = 4
TRY_EVERY_DEG = 10  # the turn between the two first cameras is searched in steps this size, then refined
GOOD_RMS_M = 0.5  # camera positions further off than this on average: the two do not show the same pictures


def load_cameras(path):
    """A trained splat's keyframe cameras, world-to-camera (N, 3, 4), from video_map.py's cameras.npz."""
    with np.load(path) as got:
        return np.asarray(got["E"], float)


def load_keyframes(path):
    """The recording frame number of each of a trained splat's keyframes (cameras.npz "keyframes"), or None."""
    with np.load(path) as got:
        return [int(n) for n in got["keyframes"]] if "keyframes" in got.files else None


def wrap(deg):
    return (np.asarray(deg, float) + 180.0) % 360.0 - 180.0


def level_axes(E):
    """East, north and up of a trained splat's cameras as rows, in its own axes: up from the cameras' mean up,
    north the first keyframe's view direction levelled (as locate.level_axes does for the room map)."""
    up = -E[:, 1, :3].mean(0)  # OpenCV: a camera's y points down
    up = up / np.linalg.norm(up)
    ahead = E[0, 2, :3]
    north = ahead - (ahead @ up) * up
    if np.linalg.norm(north) < 1e-6:  # the first camera looks straight up or down
        north = np.cross(up, [1.0, 0.0, 0.0]) if abs(up[0]) < 0.9 else np.cross(up, [0.0, 1.0, 0.0])
    north = north / np.linalg.norm(north)
    return np.stack([np.cross(north, up), north, up])


def turn_about_up(deg):
    """Adds deg to every heading (measured from north towards east), in map axes."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])


def pair_in_order(a, b, gap=GAP_DEG):
    """Pairs two heading sequences in order (each item at most once), the least total heading difference with
    gap for every item left out. Returns [(i, j), ...]."""
    n, m = len(a), len(b)
    diff = np.abs(wrap(np.subtract.outer(a, b)))
    cost = np.zeros((n + 1, m + 1))
    cost[:, 0] = np.arange(n + 1) * gap
    cost[0, :] = np.arange(m + 1) * gap
    move = np.zeros((n + 1, m + 1), np.int8)  # 0 pair, 1 leave out a[i], 2 leave out b[j]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = (cost[i - 1, j - 1] + diff[i - 1, j - 1], cost[i - 1, j] + gap, cost[i, j - 1] + gap)
            k = int(np.argmin(options))
            cost[i, j], move[i, j] = options[k], k
    move[1:, 0], move[0, 1:] = 1, 2
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        if move[i, j] == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif move[i, j] == 1:
            i -= 1
        else:
            j -= 1
    return pairs[::-1], float(cost[n, m])


def by_number(cameras, numbers):
    """Pairs that are the same frame of the same recording: numbers = (the room map's keyframe_numbers, by baseline
    picture; the trained splat's keyframes, by keyframe), both frame numbers in the recording. [(i, j), ...]"""
    map_numbers, trained_numbers = numbers
    where = {int(n): j for j, n in enumerate(trained_numbers)}
    pairs = []
    for i, c in enumerate(cameras):
        f = int(c.get("frame", i))
        if 0 <= f < len(map_numbers) and int(map_numbers[f]) in where:
            pairs.append((i, where[int(map_numbers[f])]))
    return pairs


def space(E, cameras, scale_hint=None, numbers=None):
    """E: the trained splat's keyframe cameras, world-to-camera (N, 3, 4) in its own axes (video_map.py
    cameras.npz). cameras: the room map's cameras, [{"position": [east, north, up], "heading_deg"}, ...]
    (GET /room). scale_hint: metres per trained unit to use when the cameras hardly move (the room map's median
    viewing distance over the trained splat's). numbers: when both come from the same recording, the frame numbers
    of the room map's pictures and of the trained keyframes (see by_number): the pairs are then the very same
    photos, which pairing by heading cannot promise when each picked its own frames. Returns the 4x4 matrix (rows)
    that takes a point of the trained splat to map axes, and how well the cameras agree; "ok" is False when they
    do not show the same views."""
    E = np.asarray(E, float)
    if E.ndim != 3 or E.shape[1:] != (3, 4) or len(E) < MIN_PAIRS or len(cameras) < MIN_PAIRS:
        return {"ok": False, "why": "Too few cameras to line the trained splat up with the room map."}
    axes = level_axes(E)
    ahead = E[:, 2, :3] @ axes.T
    trained_heading = np.degrees(np.arctan2(ahead[:, 0], ahead[:, 1]))
    map_heading = np.array([float(c.get("heading_deg") or 0.0) for c in cameras])
    pairs = by_number(cameras, numbers) if numbers else []
    paired_by = "frame numbers"
    if len(pairs) < MIN_PAIRS:
        # The two first cameras usually are the same view (turn 0), but need not be: try every turn, keep the best.
        best = None
        for turn in range(-180, 180, TRY_EVERY_DEG):
            pairs, total = pair_in_order(map_heading, trained_heading + turn)
            if best is None or total < best[0]:
                best = (total, turn, pairs)
        pairs, paired_by = best[2], "headings"
    diffs = wrap([map_heading[i] - trained_heading[j] for i, j in pairs])
    turn = math.degrees(math.atan2(np.sin(np.radians(diffs)).mean(), np.cos(np.radians(diffs)).mean()))
    kept = [(i, j) for (i, j), d in zip(pairs, diffs) if abs(wrap(d - turn)) <= PAIR_DEG]
    if len(kept) < MIN_PAIRS:
        return {"ok": False, "why": f"Only {len(kept)} of the room map's views match the trained splat's.", "pairs": len(kept)}
    diffs = wrap([map_heading[i] - trained_heading[j] for i, j in kept])
    turn = math.degrees(math.atan2(np.sin(np.radians(diffs)).mean(), np.cos(np.radians(diffs)).mean()))
    rotation = turn_about_up(turn) @ axes
    centres = np.array([-e[:, :3].T @ e[:, 3] for e in E])
    p = centres[[j for _, j in kept]] @ rotation.T
    m = np.array([[float(v) for v in (list(cameras[i]["position"]) + [0.0, 0.0, 0.0])[:3]] for i, _ in kept])
    spread = ((p - p.mean(0)) ** 2).sum()
    scale = float(((m - m.mean(0)) * (p - p.mean(0))).sum() / spread) if spread > 1e-9 else 0.0
    scale_from = "camera positions"
    if not scale > 0 or (scale_hint and not scale_hint / 4 < scale < scale_hint * 4):
        scale, scale_from = float(scale_hint or 1.0), "viewing distance" if scale_hint else "none"
    shift = m.mean(0) - scale * p.mean(0)
    rms = float(np.sqrt(((m - (scale * p + shift)) ** 2).sum(1).mean()))
    matrix = np.eye(4)
    matrix[:3, :3], matrix[:3, 3] = scale * rotation, shift
    return {"ok": rms <= GOOD_RMS_M, "why": None if rms <= GOOD_RMS_M else
            f"The cameras are {rms:.1f} m apart on average after lining up: not the same pictures.",
            "matrix": [[round(float(v), 6) for v in row] for row in matrix],
            "scale": round(scale, 4), "scale_from": scale_from, "turn_deg": round(turn, 1),
            "pairs": len(kept), "paired_by": paired_by, "of": [len(cameras), len(E)], "rms_m": round(rms, 3),
            "heading_rms_deg": round(float(np.sqrt((wrap(diffs - turn) ** 2).mean())), 1),
            # [baseline picture number, trained keyframe]: the same photo (see place_points)
            "paired": [[int(cameras[i].get("frame", i)), int(j)] for i, j in kept]}


def load_views(folder):
    """A trained splat's keyframes as video_map.py exports them next to it (views.json, depths.bin; also made by
    --labels-only): trusted depth (S, h, w, 0 where not trusted), K (S, 3, 3), E (S, 3, 4), picture size (w, h)."""
    v = json.loads((folder / "views.json").read_text())
    w, h = v["depth_size"]
    depth = np.fromfile(folder / v["depths"], np.float16).reshape(-1, h, w).astype(np.float32)
    return depth, np.asarray(v["K"], float).reshape(-1, 3, 3), np.asarray(v["E"], float).reshape(-1, 3, 4), tuple(v["size"])


def place_points(waypoints, paired, views, matrix, reach=(2, 5)):
    """Named places in map axes without a position fix or a new room map. A place is a spot clicked in a baseline
    picture (GET /waypoints: frame, u, v). When the baseline was recorded (Record room), that picture is a keyframe of
    the trained splat (paired, from space(); the same 518 px photo), so its depth there is known: the median trusted
    depth around the spot (a few depth pixels, more if none), along the camera's ray, through `matrix` into map axes.
    Returns [{"name", "position": [east, north, up], "from": "trained depth"}] for the places that can be placed."""
    depth, K, E, size = views
    keyframe = {int(f): int(j) for f, j in paired}
    m = np.asarray(matrix, float)
    out = []
    for w in waypoints:
        try:
            j = keyframe.get(int(w["frame"]))
            u, v = float(w["u"]), float(w["v"])
        except (KeyError, TypeError, ValueError):
            continue
        if j is None or j >= len(depth) or not (0 <= u <= 1 and 0 <= v <= 1):
            continue
        rows, cols = depth[j].shape
        cx, cy = round(u * (cols - 1)), round(v * (rows - 1))
        z = None
        for r in reach:
            patch = depth[j][max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1]
            good = patch[np.isfinite(patch) & (patch > 0)]
            if good.size:
                z = float(np.median(good))
                break
        if z is None:
            continue
        ray = np.linalg.inv(K[j]) @ np.array([u * size[0], v * size[1], 1.0])
        R, t = E[j][:, :3], E[j][:, 3]
        point = R.T @ (ray * z - t)  # depth is along the camera's axis: ray has 1 there
        placed = m[:3, :3] @ point + m[:3, 3]
        out.append({"name": w["name"], "position": [round(float(c), 2) for c in placed], "from": "trained depth"})
    return out


if __name__ == "__main__":
    room = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(json.dumps(space(load_cameras(sys.argv[2]), room["cameras"], float(sys.argv[3]) if len(sys.argv) > 3 else None), indent=1))
