#!/usr/bin/env python3
"""A synthetic room, drawn with NumPy and OpenCV, for testing the vision stage with no camera and no person.

A 5 x 4 m room with a table, a cabinet, a bookcase and a sofa, every surface strongly textured (the model
needs texture to match views), seen through a pinhole camera by ray casting. Because the room is made here,
every answer can be checked: the true position of each camera, of each named place and of the object that
is moved between "before" and "after".

    python synthetic_room.py OUT_FOLDER     writes baseline/, walk/, check/, still/ and truth.json into OUT_FOLDER

World axes: x east, y north, z up, in metres, floor at z = 0. Nothing here is used by the live code; it is
only read by verify_demo.py.
"""
import json
import math
import sys
import zlib
from pathlib import Path

import cv2
import numpy as np

ROOM = ((-2.5, 2.5), (-2.0, 2.0), (0.0, 2.6))  # x, y, z ranges of the room's inside
W, H, FOV_DEG = 1024, 768, 65.0  # a 4:3 phone-like picture
PX_PER_M = 220  # texture resolution

# Solid boxes in the room: name -> (x range, y range, z range, base colour BGR)
FURNITURE = {
    "table": ((0.6, 1.8), (1.0, 1.8), (0.0, 0.75), (60, 110, 160)),
    "cabinet": ((-2.4, -1.6), (-1.0, 0.6), (0.0, 1.2), (150, 110, 60)),
    "bookcase": ((-1.0, 0.2), (1.65, 1.98), (0.0, 1.9), (70, 70, 120)),
    "sofa": ((-0.6, 1.0), (-1.95, -1.25), (0.0, 0.8), (90, 140, 90)),
    "pill box": ((1.0, 1.25), (1.25, 1.42), (0.75, 0.86), (40, 150, 250)),
}
# The thing that is moved between "before" and "after": from the table to the top of the cabinet.
MOVED_BEFORE = ((1.38, 1.6), (1.18, 1.38), (0.75, 1.0), (50, 50, 220))
MOVED_AFTER = ((-2.25, -2.03), (-0.35, -0.13), (1.2, 1.45), (50, 50, 220))
DOOR = ((2.5, 2.5), (-1.3, -0.4), (0.0, 2.05))  # drawn on the east wall


def texture(w, h, seed, colour, door=None):
    """A strongly textured picture of w x h pixels: noise at several scales around a base colour, shapes and lines."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.float32)
    for cell, weight in ((3, 0.35), (12, 0.6), (48, 0.8)):  # mostly brightness, a little colour, like real surfaces
        light = rng.random((h // cell + 2, w // cell + 2, 1), np.float32)
        tint = rng.random((h // cell + 2, w // cell + 2, 3), np.float32)
        img += weight * cv2.resize(0.8 * light + 0.2 * tint, (w, h), interpolation=cv2.INTER_CUBIC)
    img = (img - img.mean()) / (img.std() + 1e-6)
    img = np.clip(np.array(colour, np.float32) + 45 * img, 0, 255).astype(np.uint8)
    area = w * h / (PX_PER_M * PX_PER_M)  # square metres
    for _ in range(int(10 * area) + 3):  # small shapes: a large flat one would leave nothing to match inside it
        c = tuple(int(v) for v in rng.integers(0, 256, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        r = int(rng.integers(3, max(5, min(40, min(w, h) // 5))))
        kind = rng.random()
        if kind < 0.4:
            cv2.circle(img, (x, y), r, c, -1, cv2.LINE_AA)
        elif kind < 0.75:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(4, 2 * r + 5)), y + int(rng.integers(4, r + 5))), c, -1)
        else:
            cv2.line(img, (x, y), (x + int(rng.integers(-3 * r, 3 * r)), y + int(rng.integers(-3 * r, 3 * r))), c,
                     int(rng.integers(1, 5)), cv2.LINE_AA)
    if door is not None:  # a dark door with a light frame and a handle
        x0, y0, x1, y1 = door
        cv2.rectangle(img, (x0 - 8, y0 - 8), (x1 + 8, y1), (220, 220, 225), -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), (40, 60, 90), -1)
        for k in range(3):
            cv2.rectangle(img, (x0 + 18, y0 + 20 + k * (y1 - y0) // 3), (x1 - 18, y0 + (k + 1) * (y1 - y0) // 3 - 10), (60, 85, 120), 3)
        cv2.circle(img, (x1 - 25, (y0 + y1) // 2), 9, (40, 200, 230), -1)
    return img


class Scene:
    """Axis-aligned boxes, each face with its own texture. moved: which state of the moved object to draw."""

    def __init__(self, moved="before"):
        self.boxes = [(name, *spec) for name, spec in FURNITURE.items()]
        self.boxes.append(("moved", *(MOVED_BEFORE if moved == "before" else MOVED_AFTER)))
        self.textures = {}

    def face_texture(self, key, extent_u, extent_v, colour, door=None):
        if key not in self.textures:
            w, h = max(8, int(extent_u * PX_PER_M)), max(8, int(extent_v * PX_PER_M))
            self.textures[key] = texture(w, h, zlib.crc32(key.encode()), colour, door)
        return self.textures[key]

    def render(self, position, yaw_deg, pitch_deg, size=(W, H), supersample=1):
        """The picture seen from position (x, y, z) looking along yaw (0 = north, 90 = east) and pitch (minus = down)."""
        w, h = size[0] * supersample, size[1] * supersample
        o = np.asarray(position, np.float64)
        f, r, d = axes(yaw_deg, pitch_deg)
        fx = w / 2 / math.tan(math.radians(FOV_DEG) / 2)
        u, v = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
        rays = f[None, None] + ((u - w / 2) / fx)[..., None] * r + ((v - h / 2) / fx)[..., None] * d
        rays = rays.reshape(-1, 3)
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / rays
        n = len(rays)
        best_t = np.full(n, np.inf)
        best_id = np.full(n, -1)
        best_axis = np.zeros(n, int)
        best_side = np.zeros(n, int)
        surfaces = []  # (name, axis, side, box)
        # the room's walls, floor and ceiling: the ray leaves the inside of a box
        for axis in range(3):
            lo, hi = ROOM[axis]
            t_hi, t_lo = (hi - o[axis]) * inv[:, axis], (lo - o[axis]) * inv[:, axis]
            for side, t in ((1, t_hi), (0, t_lo)):
                ok = (t > 1e-6) & (t < best_t)
                k = len(surfaces)
                surfaces.append((f"room{axis}{side}", axis, side, ROOM))
                best_t[ok], best_id[ok], best_axis[ok], best_side[ok] = t[ok], k, axis, side
        for name, xs, ys, zs, colour in self.boxes:
            box = (xs, ys, zs)
            lo = np.array([b[0] for b in box]) - o
            hi = np.array([b[1] for b in box]) - o
            with np.errstate(invalid="ignore"):
                t1, t2 = lo * inv, hi * inv
            near, far = np.minimum(t1, t2), np.maximum(t1, t2)
            t_near, axis = near.max(1), near.argmax(1)
            ok = (t_near <= far.min(1)) & (t_near > 1e-6) & (t_near < best_t)
            side = (rays[np.arange(n), axis] < 0).astype(int)  # entering through the high face when moving down that axis
            base = len(surfaces)
            for a in range(3):
                for s in range(2):
                    surfaces.append((f"{name}{a}{s}", a, s, box, colour))
            best_t[ok], best_axis[ok], best_side[ok] = t_near[ok], axis[ok], side[ok]
            best_id[ok] = base + axis[ok] * 2 + side[ok]
        hit = o + rays * best_t[:, None]
        img = np.zeros((n, 3), np.uint8)
        for k, surface in enumerate(surfaces):
            sel = np.flatnonzero(best_id == k)
            if not len(sel):
                continue
            name, axis, side, box = surface[:4]
            others = [a for a in range(3) if a != axis]
            pu, pv = hit[sel, others[0]] - box[others[0]][0], hit[sel, others[1]] - box[others[1]][0]
            extent_u, extent_v = box[others[0]][1] - box[others[0]][0], box[others[1]][1] - box[others[1]][0]
            if name.startswith("room"):
                colour = {"room20": (90, 120, 150), "room21": (225, 225, 220)}.get(name, None) or \
                    tuple(int(c) for c in np.random.default_rng(zlib.crc32(name.encode())).integers(90, 210, 3))
                door = None
                if name == "room01":  # the east wall: u along y, v along z
                    door = (int((DOOR[1][0] - box[1][0]) * PX_PER_M), int(DOOR[2][0] * PX_PER_M),
                            int((DOOR[1][1] - box[1][0]) * PX_PER_M), int(DOOR[2][1] * PX_PER_M))
                tex = self.face_texture(name, extent_u, extent_v, colour, door)
            else:
                tex = self.face_texture(name, extent_u, extent_v, surface[4])
            th, tw = tex.shape[:2]
            iu = np.clip((pu / extent_u * tw).astype(int), 0, tw - 1)
            iv = np.clip((pv / extent_v * th).astype(int), 0, th - 1)
            shade = {2: 1.0 if side == 1 else 0.9, 0: 0.82, 1: 0.72}[axis]
            shade = shade * (0.85 + 0.15 * np.exp(-best_t[sel] / 4))[:, None]
            img[sel] = np.clip(tex[iv, iu].astype(np.float32) * shade, 0, 255).astype(np.uint8)
        img = img.reshape(h, w, 3)
        return cv2.resize(img, size, interpolation=cv2.INTER_AREA) if supersample > 1 else img

    def hit(self, position, yaw_deg, pitch_deg, px, py, size=(W, H)):
        """The world point seen at pixel (px, py) of that view (ray cast without textures)."""
        o = np.asarray(position, np.float64)
        f, r, d = axes(yaw_deg, pitch_deg)
        fx = size[0] / 2 / math.tan(math.radians(FOV_DEG) / 2)
        ray = f + (px - size[0] / 2) / fx * r + (py - size[1] / 2) / fx * d
        best = math.inf
        for axis in range(3):
            if abs(ray[axis]) > 1e-9:
                for bound in ROOM[axis]:
                    t = (bound - o[axis]) / ray[axis]
                    if 1e-6 < t < best:
                        best = t
        for _, xs, ys, zs, _ in self.boxes:
            lo, hi = np.array([xs[0], ys[0], zs[0]]) - o, np.array([xs[1], ys[1], zs[1]]) - o
            with np.errstate(divide="ignore", invalid="ignore"):
                t1, t2 = lo / ray, hi / ray
            near, far = np.nanmax(np.minimum(t1, t2)), np.nanmin(np.maximum(t1, t2))
            if near <= far and 1e-6 < near < best:
                best = near
        return o + ray * best


def axes(yaw_deg, pitch_deg):
    """Camera forward, right and down (OpenCV: x right, y down, z forward) in world axes."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    f = np.array([math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), math.sin(pitch)])
    r = np.array([math.cos(yaw), -math.sin(yaw), 0.0])
    return f, r, np.cross(f, r)


def project(point, position, yaw_deg, pitch_deg, size=(W, H)):
    """Pixel (px, py) where a world point appears in a view, or None behind the camera."""
    f, r, d = axes(yaw_deg, pitch_deg)
    p = np.asarray(point, np.float64) - np.asarray(position, np.float64)
    z = p @ f
    if z <= 0:
        return None
    fx = size[0] / 2 / math.tan(math.radians(FOV_DEG) / 2)
    return size[0] / 2 + fx * (p @ r) / z, size[1] / 2 + fx * (p @ d) / z


def baseline_poses(n=24):
    """Turning once around near the middle of the room, stepping back from each wall looked at."""
    poses = []
    for i in range(n):
        yaw = 360.0 * i / n
        x, y = 0.15 - 0.7 * math.sin(math.radians(yaw)), 0.0 - 0.6 * math.cos(math.radians(yaw))
        poses.append(((x, y, 1.45 + 0.08 * math.sin(i * 1.7)), yaw, -14.0 + 5.0 * math.cos(i * 2.3)))
    return poses


def turn_poses():
    """Standing still and turning right, 2 degrees a frame: what a camera that keeps running sends (instant fixes)."""
    return [((-0.2, -0.35, 1.44), 10.0 + 2 * i, -15.0) for i in range(30)]


def walk_poses():
    """A short walk from the sofa towards the table, looking where the baseline looked (north-east to east)."""
    out = []
    for i in range(8):
        s = i / 7
        x, y = -0.4 + 0.75 * s, -0.55 + 0.6 * s
        yaw = 20 + 55 * math.sin(i * 0.9) ** 2
        out.append(((x, y, 1.42 + 0.05 * math.sin(i)), yaw, -15.0 + 3 * math.cos(i)))
    return out


def check_poses():
    """Four views for the change check: two of the table (the object is gone from it), two of the cabinet (it appeared there)."""
    return [((0.05, -0.45, 1.47), 30.0, -18.0), ((0.25, -0.5, 1.45), 48.0, -16.0),
            ((0.6, -0.05, 1.46), 262.0, -14.0), ((0.75, 0.25, 1.44), 285.0, -15.0)]


VERSION = 2  # change when the pictures change, so that verify_demo.py draws them again


def write_all(out):
    """Draw every picture into out/baseline, out/walk, out/check and out/still, and write out/truth.json."""
    out = Path(out)
    for sub in ("baseline", "walk", "check", "still", "turn"):
        (out / sub).mkdir(parents=True, exist_ok=True)
        for old in (out / sub).glob("*.jpg"):
            old.unlink()
    before, after = Scene("before"), Scene("after")
    truth = {"version": VERSION, "room": ROOM, "fov_deg": FOV_DEG, "size": [W, H]}
    for sub, scene, poses in (("baseline", before, baseline_poses()), ("walk", after, walk_poses()), ("check", after, check_poses()),
                              ("still", None, [STILL, STILL]), ("turn", after, turn_poses())):
        truth[sub] = []
        for i, (pos, yaw, pitch) in enumerate(poses):
            drawn = (before if i == 0 else after) if scene is None else scene  # still: before, then after the move
            cv2.imwrite(str(out / sub / f"{i:03d}.jpg"), drawn.render(pos, yaw, pitch), [cv2.IMWRITE_JPEG_QUALITY, 92])
            truth[sub].append({"position": list(pos), "yaw_deg": yaw, "pitch_deg": pitch})
    truth["moved"] = {"before": [list(r) for r in MOVED_BEFORE[:3]], "after": [list(r) for r in MOVED_AFTER[:3]]}
    (out / "truth.json").write_text(json.dumps(truth, indent=1))
    return truth


STILL = ((0.2, -0.3, 1.45), 38.0, -20.0)  # the camera standing still while the object is moved (the cheap gate)
PLACES = {"pill box": (1.125, 1.335, 0.86), "front door": (2.5, -0.85, 1.0)}  # a point on each, in the world


def places_for(kept):
    """Named places and a watched spot as the carer would pick them, in the baseline pictures the worker kept.
    kept: the numbers of the baseline pictures that were kept, in order (the worker keeps an evenly spread
    subset). Returns {"places": {name: {"frame" (index into kept), "u", "v", "point"}}, "distance_m": the true
    distance between the two places, "watch": {"frame", "box"} around the object that is moved, or None}."""
    scene, poses = Scene("before"), baseline_poses()
    found = {}
    for name, point in PLACES.items():
        best = None
        for k, i in enumerate(kept):
            pos, yaw, pitch = poses[i]
            px = project(point, pos, yaw, pitch)
            if px is None or not (0.12 * W < px[0] < 0.88 * W and 0.12 * H < px[1] < 0.88 * H):
                continue
            seen = scene.hit(pos, yaw, pitch, *px)
            if np.linalg.norm(seen - np.array(point)) > 0.05:  # something else is in front of it
                continue
            off = math.hypot(px[0] / W - 0.5, px[1] / H - 0.5)
            if best is None or off < best[0]:
                best = (off, k, px[0] / W, px[1] / H, seen)
        if best is None:
            raise RuntimeError(f"no kept baseline picture shows the {name}")
        found[name] = {"frame": best[1], "u": round(best[2], 4), "v": round(best[3], 4), "point": [round(float(v), 3) for v in best[4]]}
    a, b = (np.array(found[k]["point"]) for k in PLACES)
    watch = None
    for k, i in enumerate(kept):  # the box of the moved object in the first kept picture that shows all of it
        pos, yaw, pitch = poses[i]
        corners = [project((x, y, z), pos, yaw, pitch) for x in MOVED_BEFORE[0] for y in MOVED_BEFORE[1] for z in MOVED_BEFORE[2]]
        if any(c is None for c in corners):
            continue
        us, vs = [c[0] / W for c in corners], [c[1] / H for c in corners]
        if min(us) > 0.08 and max(us) < 0.92 and min(vs) > 0.08 and max(vs) < 0.92:
            pad = 0.03
            watch = {"frame": k, "box": [round(min(us) - pad, 4), round(min(vs) - pad, 4), round(max(us) + pad, 4), round(max(vs) + pad, 4)]}
            break
    return {"places": found, "distance_m": round(float(np.linalg.norm(a - b)), 3), "watch": watch}


def truth_of_fix(view, point):
    """The true floor distance (m) and bearing (degrees, 0 ahead, + to the right) from a view to a world point."""
    pos, yaw = np.array(view["position"]), view["yaw_deg"]
    east, north = point[0] - pos[0], point[1] - pos[1]
    bearing = (math.degrees(math.atan2(east, north)) - yaw + 180) % 360 - 180
    return math.hypot(east, north), bearing


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    write_all(sys.argv[1])
    print(json.dumps(places_for(list(range(len(baseline_poses())))), indent=1))
