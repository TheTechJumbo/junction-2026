#!/usr/bin/env python3
"""Offline tests of splat_locate.py: a photo placed against a trained splat's keyframes, and the steady pose.

A synthetic room (three walls and a floor with a patchwork texture) is drawn from known cameras as a trained splat
exports its keyframes (views.json, depths.bin at a quarter size, kfNNN.jpg). A photo from a camera between two
keyframes must come back at that camera. No GPU, no model, no recording.

    python test_splat_locate.py
"""
import json
import math
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import splat_locate as sl  # noqa: E402

SIDE, F = 518, 430.0
K = np.array([[F, 0, SIDE / 2], [0, F, SIDE / 2], [0, 0, 1.0]])
# planes as (a point, normal, in-plane u axis, in-plane v axis, half sizes): x right, y down, z ahead at the start
PLANES = [((0, 0, 4.0), (0, 0, -1), (1, 0, 0), (0, 1, 0), (4, 2)),     # the far wall
          ((-3.0, 0, 1.5), (1, 0, 0), (0, 0, 1), (0, 1, 0), (4, 2)),   # the left wall
          ((3.0, 0, 1.5), (-1, 0, 0), (0, 0, 1), (0, 1, 0), (4, 2)),   # the right wall
          ((0, 1.5, 1.5), (0, -1, 0), (1, 0, 0), (0, 0, 1), (4, 4))]   # the floor


def texture(seed, size=1024):
    """A patchwork of rectangles and blobs: corners for SIFT everywhere, no repeats."""
    rng = np.random.default_rng(seed)
    img = np.full((size, size), 128, np.uint8)
    for _ in range(900):
        x, y = rng.integers(0, size, 2)
        w, h = rng.integers(8, 70, 2)
        cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), int(rng.integers(0, 255)), -1)
    for _ in range(300):
        cv2.circle(img, tuple(int(v) for v in rng.integers(0, size, 2)), int(rng.integers(4, 20)), int(rng.integers(0, 255)), -1)
    return cv2.GaussianBlur(img, (3, 3), 0)


TEXTURES = [texture(i) for i in range(len(PLANES))]


def look(centre, yaw_deg, pitch_deg=0.0):
    """World-to-camera (3 x 4) for a camera at `centre` turned yaw to the right and pitch up (OpenCV axes)."""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), math.sin(p)], [0, -math.sin(p), math.cos(p)]])
    R_cw = Ry @ Rx  # camera axes in the world
    R = R_cw.T
    return np.hstack([R, (-R @ np.asarray(centre, float))[:, None]])


def render(E):
    """The room seen by camera E: grey picture and depth (along the camera's z), SIDE x SIDE."""
    R, t = E[:, :3], E[:, 3]
    C = -R.T @ t
    v, u = np.mgrid[0:SIDE, 0:SIDE].astype(float)
    rays = np.stack([(u - K[0, 2]) / F, (v - K[1, 2]) / F, np.ones_like(u)], -1) @ R  # world directions, z = 1 in the camera
    best_t = np.full(u.shape, np.inf)
    grey = np.zeros(u.shape, np.uint8)
    for (p, n, ua, va, half), tex in zip(PLANES, TEXTURES):
        p, n, ua, va = (np.asarray(a, float) for a in (p, n, ua, va))
        denom = rays @ n
        with np.errstate(divide="ignore", invalid="ignore"):
            tt = ((p - C) @ n) / denom
        tt = np.where(np.isfinite(tt), tt, -1.0)  # parallel to the plane: no hit
        hit = C + tt[..., None] * rays
        a, b = (hit - p) @ ua, (hit - p) @ va
        ok = (tt > 0.05) & (np.abs(a) <= half[0]) & (np.abs(b) <= half[1]) & (tt < best_t)
        tu = np.clip(((a + half[0]) / (2 * half[0]) * (tex.shape[1] - 1)).astype(int), 0, tex.shape[1] - 1)
        tv = np.clip(((b + half[1]) / (2 * half[1]) * (tex.shape[0] - 1)).astype(int), 0, tex.shape[0] - 1)
        grey[ok] = tex[tv[ok], tu[ok]]
        best_t[ok] = tt[ok]
    depth = np.where(np.isfinite(best_t), best_t, 0.0)  # rays have z = 1 in the camera, so t is the depth
    return grey, depth


def make_splat(folder, poses):
    """Keyframes as video_map.export_views writes them, for the given cameras."""
    folder = Path(folder)
    step = 4
    w = h = (SIDE + step - 1) // step
    depths = np.zeros((len(poses), h, w), np.float16)
    for q, E in enumerate(poses):
        grey, depth = render(E)
        cv2.imwrite(str(folder / f"kf{q:03d}.jpg"), cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
        depths[q] = cv2.resize(depth.astype(np.float32), (w, h), interpolation=cv2.INTER_NEAREST)
    depths.tofile(folder / "depths.bin")
    (folder / "views.json").write_text(json.dumps({
        "size": [SIDE, SIDE], "depth_size": [w, h], "K": [K.tolist()] * len(poses), "E": [E.tolist() for E in poses],
        "files": [f"kf{q:03d}.jpg" for q in range(len(poses))], "depths": "depths.bin", "median_depth": 3.0}))


def test_photo_between_keyframes():
    poses = [look((x, 0.0, z), yaw) for x, z, yaw in
             [(-1.5, -0.5, -25), (-1.0, -0.4, -15), (-0.5, -0.3, -5), (0.0, -0.2, 5), (0.5, -0.1, 15), (1.0, 0.0, 25),
              (1.5, 0.1, 35), (1.0, 0.3, -40), (0.0, 0.4, 0)]]
    with tempfile.TemporaryDirectory() as tmp:
        make_splat(tmp, poses)
        index = sl.SplatIndex(tmp)
        assert index.usable.sum() >= 8, index.usable
        for truth in (look((-0.25, -0.25, 0.0), 0.0, 4.0), look((0.75, -0.05, 0.1), 20.0, -3.0)):
            grey, _ = render(truth)
            photo = cv2.copyMakeBorder(cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR), 200, 200, 0, 0, cv2.BORDER_REPLICATE)  # tall, as the glasses send
            fix = index.locate(photo, focal_scale=(1.0, 0.9, 1.12))
            assert fix["ok"], fix
            C = -truth[:, :3].T @ truth[:, 3]
            error = float(np.linalg.norm(fix["centre"] - C))
            turn = math.degrees(math.acos(np.clip(fix["forward"] @ truth[2, :3], -1, 1)))
            assert error < 0.05, (error, fix["inliers"])  # 5 cm in a 6 m room
            assert turn < 1.0, turn
            assert fix["focal_scale"] == 1.0, fix["focal_tries"]
            assert fix["sigma"] > 0 and fix["inliers"] >= sl.MIN_INLIERS
        # a picture of something else is not placed
        rng = np.random.default_rng(5)
        noise = cv2.resize(rng.integers(0, 255, (60, 60)).astype(np.uint8), (SIDE, SIDE), interpolation=cv2.INTER_NEAREST)
        assert not index.locate(cv2.cvtColor(noise, cv2.COLOR_GRAY2BGR))["ok"]


def test_to_map():
    turn = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]], float)  # splat (x right, y down, z ahead) -> map (east, north, up)
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = 2.0 * turn, [1.0, 2.0, 0.5]
    E = look((0.5, 0.0, 1.0), 90.0)  # looking along splat +x, which is map -north... heading 180
    fix = {"centre": -E[:, :3].T @ E[:, 3], "forward": E[2, :3], "cov": np.eye(3) * 0.01}
    m = sl.to_map(fix, M.tolist())
    assert np.allclose(m["position"], [1.0 + 2.0, 2.0 - 1.0, 0.5]), m["position"]
    assert abs(abs(m["heading_deg"]) - 180) < 1e-6, m["heading_deg"]
    assert abs(m["sigma_m"] - 0.2) < 1e-9 and abs(m["scale"] - 2.0) < 1e-9


def fix_at(east, north, sigma=0.05, heading=0.0):
    return {"position": [east, north, 0.0], "heading_deg": heading, "pitch_deg": 0.0, "cov": np.eye(3) * sigma ** 2, "turn_sd_deg": 2.0}


def test_filter_walks_and_holds_back_a_wrong_fix():
    f = sl.PoseFilter()
    rng = np.random.default_rng(1)
    for i in range(12):  # walking north at 1 m/s, a fix every 1/3 s, 5 cm of noise
        t = i / 3
        assert f.update(fix_at(rng.normal(0, 0.05), t + rng.normal(0, 0.05)), t) in ("start", "agree")
    now = f.current(11 / 3)
    assert abs(now["position"][1] - 11 / 3) < 0.15 and now["state"] == "found", now
    assert now["radius95_m"] < 0.4, now
    # one wrong fix 4 m to the east: held back, the pose does not jump there, and the ring says how unsure it is
    assert f.update(fix_at(4.0, 4.0), 4.0) == "held"
    held = f.current(4.0)
    assert abs(held["position"][0]) < 0.2, held
    assert held["radius95_m"] >= 1.5, held
    # the next fix agrees with the walk: the wrong one is forgotten
    assert f.update(fix_at(0.0, 13 / 3), 13 / 3) == "agree"
    assert f.current(13 / 3)["radius95_m"] < 0.5
    # two fixes that agree with each other far away: the wearer is there (moved while not seen)
    assert f.update(fix_at(6.0, 4.4), 14 / 3) == "held"
    assert f.update(fix_at(6.05, 4.45), 15 / 3) == "jump"
    assert abs(f.current(5.0)["position"][0] - 6.05) < 0.1
    # no photo placed for a long time: lost
    assert f.current(5.0 + sl.LOST_S + 1)["state"] == "lost"
    again = sl.PoseFilter(f.dump())  # and it carries over a restart
    assert again.current(5.0)["position"] == f.current(5.0)["position"]


def test_no_move_faster_than_walking():
    f = sl.PoseFilter()
    f.update(fix_at(0, 0), 0.0)
    f.update(fix_at(0, 0.05), 0.33)
    kind = f.update(fix_at(0, 2.5), 0.66)  # 2.5 m in 0.33 s with 5 cm fixes: no person does that
    assert kind == "held" and f.current(0.66)["position"][1] < 0.3


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print(f"{name}: ok")
