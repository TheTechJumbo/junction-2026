#!/usr/bin/env python3
"""Check locate.py's appearance check without a GPU, on a synthetic room with exact depth.

A floor and a back wall, six baseline cameras and one query camera. What an image patch "looks
like" is a smooth function of where its surface is in the world, so two views of one place look
alike and two different places do not. Three queries, all from the same camera with the same depth:

  same room     its patches look like the room -> found
  wrong room    the failure seen on PASLCD: the depth lies on the room's surfaces, but the patches
                show somewhere else -> lost, although the depth-only score would accept it
  no features   Recon.feat is None (an older saved reconstruction) -> the depth-only score, as before

Position and heading must be the same in all three.

    python eval/test_locate_lookalike.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import locate  # noqa: E402
from recon_change import Recon  # noqa: E402

H, W, FOCAL, PATCH, DIM = 154, 210, 180.0, 14, 64
K = np.array([[FOCAL, 0, W / 2], [0, FOCAL, H / 2], [0, 0, 1]], np.float32)
TARGET = np.array([0.0, 2.2, 0.6])  # every camera looks here


def camera(pos):
    """World -> camera (OpenCV: x right, y down, z forward) for a camera at pos looking at TARGET. World z is up."""
    fwd = TARGET - pos
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    R = np.stack([right, np.cross(fwd, right), fwd])
    return np.concatenate([R, (-R @ pos)[:, None]], 1).astype(np.float32)


def render(E):
    """Exact z-depth of a floor (z = 0) and a back wall (y = 3)."""
    R, t = E[:, :3].astype(float), E[:, 3].astype(float)
    origin = -R.T @ t
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    rays = np.stack([(u - K[0, 2]) / FOCAL, (v - K[1, 2]) / FOCAL, np.ones_like(u, float)], -1) @ R  # z-depth = ray parameter
    with np.errstate(divide="ignore", invalid="ignore"):
        floor = np.where(rays[..., 2] < 0, -origin[2] / rays[..., 2], np.inf)
        wall = np.where(rays[..., 1] > 0, (3.0 - origin[1]) / rays[..., 1], np.inf)
    return np.minimum(floor, wall).astype(np.float32)


def looks(depth, E, weights, phase):
    """Patch features (h, w, DIM): a smooth function of where each patch's surface is in the world."""
    v, u = np.mgrid[0:H // PATCH, 0:W // PATCH] * PATCH + PATCH // 2
    z = depth[v, u]
    cam = np.stack([(u - K[0, 2]) / FOCAL * z, (v - K[1, 2]) / FOCAL * z, z], -1)
    world = (cam - E[:, 3]) @ E[:, :3]
    return np.cos(world @ weights.T + phase)


def main():
    rng = np.random.default_rng(0)
    room = rng.normal(0, 2.0, (DIM, 3)), rng.uniform(0, 2 * np.pi, DIM)  # what this room looks like
    elsewhere = rng.normal(0, 2.0, (DIM, 3)), rng.uniform(0, 2 * np.pi, DIM)  # what another room looks like
    E = np.stack([camera(np.array([x, -1.0 + 0.1 * (i % 2), 1.4])) for i, x in enumerate(np.linspace(-1.2, 1.2, 6))]
                 + [camera(np.array([0.3, -0.8, 1.4]))])
    depth = np.stack([render(e) for e in E])
    conf = rng.uniform(1, 2, depth.shape).astype(np.float32)
    base, new = list(range(6)), [6]

    def recon(query_looks):
        feat = None if query_looks is None else np.stack(
            [looks(depth[i], E[i], *(query_looks if i == 6 else room)) for i in range(7)]).astype(np.float16)
        return Recon(depth, conf, np.stack([K] * 7), E, None, feat)

    same = locate.locate(recon(room), base, new)[0]
    wrong = locate.locate(recon(elsewhere), base, new)[0]
    plain = locate.locate(recon(None), base, new)[0]
    off = locate.locate(recon(elsewhere), base, new, look=False)[0]
    for name, r in (("same room", same), ("wrong room", wrong), ("no features", plain), ("check switched off", off)):
        print(f"{name:>18}: {r['state']:6} match {r['confidence']:.2f} | by depth {r['match_by_depth']:.2f} | "
              f"looks alike {r['looks_alike']} | limit {r['look_limit']}")

    failed = []

    def check(ok, what):
        print(("ok    " if ok else "FAIL  ") + what)
        if not ok:
            failed.append(what)

    check(same["state"] == "found" and same["confidence"] >= 0.9 and same["looks_alike"] >= 0.9, "same room is found and looks alike")
    check(wrong["match_by_depth"] >= 0.9, "wrong room: depth alone would accept it")
    check(wrong["state"] == "lost" and wrong["confidence"] < 0.1 and wrong["looks_alike"] < 0.1, "wrong room is lost once appearance counts")
    check(plain["looks_alike"] is None and plain["look_limit"] is None and plain["confidence"] == plain["match_by_depth"],
          "without features the score is the depth-only one")
    check(plain["confidence"] == wrong["match_by_depth"] == off["confidence"], "and equals what the appearance check reports as match_by_depth")
    check(all(r["position"] == same["position"] and r["heading_deg"] == same["heading_deg"] and r["map"] == same["map"]
              for r in (wrong, plain, off)), "position, heading and map do not depend on the appearance check")
    print("FAILED" if failed else "All checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
