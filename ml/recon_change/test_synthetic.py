#!/usr/bin/env python3
"""Check the change detection without a GPU, on a synthetic room with exact (then noised) depth.

A floor, a back wall and one box. Between "before" and "after" the box moves from A to B, so
the detector should report one thing gone near A, one thing appeared near B, and no false
alarms in the control. A second scene with nothing moved must report no change.

    python test_synthetic.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

from recon_change import Recon, analyse, contact_sheet, write_ply

H, W, FOCAL = 294, 392, 330.0
BOX_A = np.array([-0.6, 1.2, 0.15])  # box centre before
BOX_B = np.array([0.7, 1.7, 0.15])  # box centre after
TARGET = np.array([0.0, 1.5, 0.2])  # every camera looks here
K = np.array([[FOCAL, 0, W / 2], [0, FOCAL, H / 2], [0, 0, 1]], np.float32)


def camera(pos):
    """World -> camera (OpenCV: x right, y down, z forward) for a camera at pos looking at TARGET. World z is up."""
    fwd = TARGET - pos
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd])
    return np.concatenate([R, (-R @ pos)[:, None]], 1).astype(np.float32)


def render(E, box_centre, box_size, rng, noise):
    """Exact z-depth of floor (z = 0), back wall (y = 3) and one axis-aligned box, plus noise."""
    R, t = E[:, :3], E[:, 3]
    origin = -R.T @ t
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    rays = np.stack([(u - K[0, 2]) / FOCAL, (v - K[1, 2]) / FOCAL, np.ones_like(u, float)], -1) @ R  # z-depth = ray parameter
    with np.errstate(divide="ignore", invalid="ignore"):
        floor = np.where(rays[..., 2] < 0, -origin[2] / rays[..., 2], np.inf)
        wall = np.where(rays[..., 1] > 0, (3.0 - origin[1]) / rays[..., 1], np.inf)
        lo = (box_centre - box_size / 2 - origin) / rays
        hi = (box_centre + box_size / 2 - origin) / rays
    near, far = np.minimum(lo, hi).max(-1), np.maximum(lo, hi).min(-1)
    box = np.where((near <= far) & (far > 0), near, np.inf)
    depth = np.minimum(np.minimum(floor, wall), box)
    hit = np.isfinite(depth)
    depth = np.where(hit, depth * (1 + noise * rng.standard_normal(depth.shape)), 0.0)
    colour = np.where(depth[..., None] == 0, 0, np.where((box <= np.minimum(floor, wall))[..., None], (200, 120, 40), (150, 150, 150)))
    return depth.astype(np.float32), hit, colour.astype(np.uint8)


def scene(box_after, box_size=0.3, noise=0.01, seed=1):
    rng = np.random.default_rng(seed)
    size = np.full(3, box_size)
    # 24 "before" views then 12 "after" views, on an arc in front of the scene at about head height
    angles = np.concatenate([np.linspace(-0.7, 0.7, 24), np.linspace(-0.65, 0.65, 12)])
    depth, conf, E, rgb = [], [], [], []
    for i, a in enumerate(angles):
        pos = np.array([2.2 * np.sin(a), 1.5 - 2.2 * np.cos(a), 1.5 + 0.1 * np.sin(3 * a)])
        e = camera(pos)
        d, hit, c = render(e, BOX_A if i < 24 else box_after, size, rng, noise)
        depth.append(d)
        conf.append(np.where(hit, 2.0 + rng.random(d.shape), 1.0))  # misses get the lowest confidence
        E.append(e)
        rgb.append(c)
    n = len(angles)
    return Recon(np.stack(depth), np.stack(conf).astype(np.float32), np.repeat(K[None], n, 0), np.stack(E), np.stack(rgb))


def near(clusters, where, tol):
    return [c for c in clusters if np.linalg.norm(np.array(c["center"]) - where) < tol]


def main():
    failures = []

    def check(ok, what):
        print(("  ok   " if ok else "  FAIL ") + what)
        if not ok:
            failures.append(what)

    print("Box moved from A to B (30 cm box, 1% depth noise):")
    recon = scene(BOX_B)
    report, detail = analyse(recon, 24, conf_pct=0, typical_m=2.5)
    print(f"  verdict: {report['verdict']}")
    print(f"  self-consistency {report['self_consistency']:.0%}, noise sigma {report['depth_noise_sigma']:.2%}, "
          f"see-through threshold {report['thresholds']['see_through']:.1%}")
    check(report["verdict"].startswith("PASS"), "verdict is PASS")
    check(len(near(report["appeared"], BOX_B, 0.25)) >= 1, "something appeared at B")
    check(len(near(report["gone"], BOX_A, 0.25)) >= 1, "something is gone at A")
    check(len(report["appeared"]) == 1 and len(report["gone"]) == 1, "exactly one appeared and one gone")
    check(len(report["false_alarms_in_control"]) == 0, "no false alarms in the control")
    check(report["self_consistency"] > 0.9, "reconstruction is self-consistent")
    with tempfile.TemporaryDirectory() as tmp:
        contact_sheet(recon, detail, Path(tmp) / "changes.jpg")
        write_ply(recon, detail, Path(tmp) / "scene.ply")
        check((Path(tmp) / "changes.jpg").stat().st_size > 10_000 and (Path(tmp) / "scene.ply").stat().st_size > 100_000,
              "overlay image and point cloud are written")

    print("Nothing moved:")
    report, _ = analyse(scene(BOX_A), 24, conf_pct=0, typical_m=2.5)
    print(f"  verdict: {report['verdict']}")
    check(report["verdict"].startswith("NO CHANGE"), "verdict is NO CHANGE")
    check(not report["appeared"] and not report["gone"], "nothing reported")

    print("Small box (6 cm) moved, for information only:")
    report, _ = analyse(scene(BOX_B, box_size=0.06), 24, conf_pct=0, typical_m=2.5)
    print(f"  verdict: {report['verdict']} (appeared {len(report['appeared'])}, gone {len(report['gone'])})")

    print("\nAll checks passed." if not failures else f"\n{len(failures)} check(s) failed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
