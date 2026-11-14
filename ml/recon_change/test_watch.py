#!/usr/bin/env python3
"""Check watch.py without a GPU, on a synthetic wall with exact (lightly noised) depth.

A textured wall with a small red box painted on it (the watched spot). Six baseline cameras and
two new ones look at it from slightly different places. Cases:

  same         nothing changed                                  -> "same"
  lighting     nothing changed, the new frames are brighter     -> "same"
  changed      the box is gone (the wall shows through), flat   -> "changed"
  blocked      a board stands half way in front of the spot     -> "not_seen", "blocked"
  away         the new cameras look elsewhere                   -> "not_seen", "out of view"
  far          the new cameras are six times farther away       -> "not_seen", "too small in view"

and the place where the spot lands in a new frame must match the exact projection.

    python test_watch.py
"""
import sys

import numpy as np

from recon_change import Recon
from watch import check

H, W, FOCAL = 294, 518, 400.0
K = np.array([[FOCAL, 0, W / 2], [0, FOCAL, H / 2], [0, 0, 1]], np.float32)
SPOT = (0.20, 0.10, 0.12, 0.08)  # the box on the wall: centre x, centre y, half width, half height (metres)
BASE_CAMS = [(-0.25, 0.0, -2.0), (-0.15, 0.05, -2.0), (-0.05, -0.05, -2.05), (0.05, 0.05, -1.95), (0.15, -0.05, -2.0), (0.25, 0.0, -2.0)]
NEW_CAMS = [(-0.10, 0.10, -1.9), (0.12, -0.08, -2.1)]


def wall(X, Y, with_box):
    """Colour of the wall at world position X, Y: a smooth pattern with tiles, and the red box."""
    tiles = ((np.floor(X * 6) + np.floor(Y * 6)) % 2) * 40
    rgb = np.stack([120 + 50 * np.sin(5 * X + 1) * np.cos(4 * Y) + tiles, 130 + 50 * np.sin(4 * X - 2 * Y) + tiles,
                    110 + 50 * np.cos(6 * Y + X) + tiles], -1)
    if with_box:
        cx, cy, hw, hh = SPOT
        inside = (np.abs(X - cx) < hw) & (np.abs(Y - cy) < hh)
        stripes = ((np.floor((X - cx) * 40)) % 2) * 60
        rgb[inside] = np.stack([200 + 0 * X, 40 + stripes, 40 + stripes], -1)[inside]
    return rgb


def render(cam, with_box=True, board=False, turn=0.0, gain=1.0, rng=None):
    """One frame from a camera at `cam`, turned `turn` radians to the right. Returns depth, rgb, E."""
    c, s = np.cos(turn), np.sin(turn)
    R = np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]])  # world -> camera
    pos = np.array(cam, float)
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    rays = np.stack([(u - K[0, 2]) / FOCAL, (v - K[1, 2]) / FOCAL, np.ones(u.shape)], -1) @ R  # camera z-depth = ray parameter
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(rays[..., 2] > 1e-6, -pos[2] / rays[..., 2], 0.0)  # the wall is the plane z = 0
    hit = pos + rays * t[..., None]
    rgb = wall(hit[..., 0], hit[..., 1], with_box)
    depth = t.copy()
    if board:  # a grey board at z = -1, in front of the spot
        t2 = np.where(rays[..., 2] > 1e-6, (-1.0 - pos[2]) / rays[..., 2], 0.0)
        hit2 = pos + rays * t2[..., None]
        on = (np.abs(hit2[..., 0] - 0.1) < 0.4) & (np.abs(hit2[..., 1] - 0.05) < 0.3) & (t2 > 0)
        rgb[on] = 90 + 30 * np.sin(20 * hit2[..., 0])[on][:, None]
        depth[on] = t2[on]
    rgb = np.clip(rgb * gain + rng.normal(0, 2, rgb.shape), 0, 255).astype(np.uint8)
    depth = (depth * (1 + 0.003 * rng.standard_normal(depth.shape))).astype(np.float32)
    E = np.concatenate([R, (-R @ pos)[:, None]], 1).astype(np.float32)
    return depth, rgb, E


def scene(**new_kwargs):
    rng = np.random.default_rng(0)
    cams = new_kwargs.pop("cams", NEW_CAMS)
    frames = [render(c, rng=rng) for c in BASE_CAMS] + [render(c, rng=rng, **new_kwargs) for c in cams]
    depth, rgb, E = (np.stack(x) for x in zip(*frames))
    return Recon(depth=depth, conf=np.ones_like(depth), K=np.stack([K] * len(frames)), E=E, rgb=rgb, feat=None)


def project(E, X, Y):
    cam = E[:, :3] @ np.array([X, Y, 0.0]) + E[:, 3]
    return (FOCAL * cam[0] / cam[2] + W / 2 + 0.5) / W, (FOCAL * cam[1] / cam[2] + H / 2 + 0.5) / H


def exact_box(E):
    cx, cy, hw, hh = SPOT
    (u0, v0), (u1, v1) = project(E, cx - hw, cy - hh), project(E, cx + hw, cy + hh)
    return [u0, v0, u1, v1]


def main():
    base, new = list(range(len(BASE_CAMS))), [len(BASE_CAMS), len(BASE_CAMS) + 1]
    failures = []

    def run(name, want_state, want_why=None, **kwargs):
        r = scene(**kwargs)
        spot = {"name": "pill box", "frame": 2, "box": exact_box(r.E[2])}
        got = check(r, base, new, [spot])[0]
        ok = got["state"] == want_state and (want_why is None or got["why"] == want_why)
        print(f"{'ok  ' if ok else 'FAIL'} {name:9s} -> {got['state']:8s} score {got['score']}  why: {got['why']}  frame {got['frame']}")
        if not ok:
            failures.append(name)
        return r, got

    r, got = run("same", "same")
    want = exact_box(r.E[got["frame"]])
    off = max(abs(a - b) for a, b in zip(got["box_new"], want))
    print(f"     projection: box_new {got['box_new']} against exact {[round(x, 4) for x in want]} (largest error {off:.4f})")
    if off > 0.01:
        failures.append("projection")
    same_score = got["score"]
    run("lighting", "same", gain=1.25)
    _, got = run("changed", "changed", with_box=False)
    if not got["score"] > 3 * max(same_score, 0.1):
        failures.append("changed is not clearly above same")
    run("blocked", "not_seen", "blocked", board=True)
    run("away", "not_seen", "out of view", turn=1.2)
    run("far", "not_seen", "too small in view", cams=[(0.0, 0.0, -12.0), (0.1, 0.0, -12.5)])
    if failures:
        print("FAILED: " + ", ".join(failures))
        sys.exit(1)
    print("All watched-spot checks passed.")


if __name__ == "__main__":
    main()
