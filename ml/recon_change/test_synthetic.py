#!/usr/bin/env python3
"""Check the change detection without a GPU, on a synthetic room with exact (then noised) depth.

A floor, a back wall and one box. Between "before" and "after" the box moves from A to B, so
the detector should report one thing gone near A, one thing appeared near B, and no false
alarms in the control. A second scene with nothing moved must report no change. A third scene
has the box rebuilt 20 cm off in the second clip, which is what misaligned clips do to a tall
object: the look-alike check must drop it, and must still let a box that moved far through.
A fourth scene has a flat sticker on the floor in the second clip: geometry cannot see it, the
"looks different" check must, and it must report nothing when nothing changed.

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
BOX_RGB, ROOM_RGB, STICKER_RGB = (200, 120, 40), (150, 150, 150), (40, 90, 200)
STICKER = np.array([0.3, 0.9, 0.0])  # centre of a flat 30 cm square on the floor


def camera(pos):
    """World -> camera (OpenCV: x right, y down, z forward) for a camera at pos looking at TARGET. World z is up."""
    fwd = TARGET - pos
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd])
    return np.concatenate([R, (-R @ pos)[:, None]], 1).astype(np.float32)


def render(E, box_centre, box_size, rng, noise, sticker=False):
    """Exact z-depth of floor (z = 0), back wall (y = 3) and one axis-aligned box, plus noise.
    sticker=True colours a flat square on the floor: no depth changes."""
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
    colour = np.where(depth[..., None] == 0, 0, np.where((box <= np.minimum(floor, wall))[..., None], BOX_RGB, ROOM_RGB))
    if sticker:
        with np.errstate(invalid="ignore"):
            on_floor = origin + rays * np.where(np.isfinite(floor), floor, 0.0)[..., None]
        inside = (floor < np.minimum(wall, box)) & (np.abs(on_floor[..., :2] - STICKER[:2]) < 0.15).all(-1)
        colour = np.where(inside[..., None], STICKER_RGB, colour)
    return depth.astype(np.float32), hit, colour.astype(np.uint8)


def patch_features(colour):
    """Stand-in for learned patch features: how much of each 14 px patch is box, room or nothing."""
    kinds = np.stack([(colour == BOX_RGB).all(-1), (colour == ROOM_RGB).all(-1), (colour == 0).all(-1)], -1)
    return kinds.astype(np.float32).reshape(H // 14, 14, W // 14, 14, 3).mean((1, 3))


def scene(box_after, box_size=0.3, noise=0.01, seed=1, features=False, sticker=False):
    rng = np.random.default_rng(seed)
    size = np.full(3, box_size)
    # 24 "before" views then 12 "after" views, on an arc in front of the scene at about head height
    angles = np.concatenate([np.linspace(-0.7, 0.7, 24), np.linspace(-0.65, 0.65, 12)])
    depth, conf, E, rgb = [], [], [], []
    for i, a in enumerate(angles):
        pos = np.array([2.2 * np.sin(a), 1.5 - 2.2 * np.cos(a), 1.5 + 0.1 * np.sin(3 * a)])
        e = camera(pos)
        d, hit, c = render(e, BOX_A if i < 24 else box_after, size, rng, noise, sticker and i >= 24)
        depth.append(d)
        conf.append(np.where(hit, 2.0 + rng.random(d.shape), 1.0))  # misses get the lowest confidence
        E.append(e)
        rgb.append(c)
    n = len(angles)
    return Recon(np.stack(depth), np.stack(conf).astype(np.float32), np.repeat(K[None], n, 0), np.stack(E), np.stack(rgb),
                 np.stack([patch_features(c) for c in rgb]) if features else None)


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
    check(report["looks_different_check"]["active"] and not report["looks_different"], "nothing looks different")

    print("Flat sticker (30 cm) on the floor in the second clip, nothing else changed:")
    report, _ = analyse(scene(BOX_A, sticker=True), 24, conf_pct=0, typical_m=2.5)
    print(f"  verdict: {report['verdict']}")
    print(f"  looks different: {[(c['voxels'], c['size_cm_approx']) for c in report['looks_different']]}")
    check(not report["appeared"] and not report["gone"], "geometry reports nothing")
    check(len(near(report["looks_different"], STICKER, 0.2)) >= 1, "the sticker is reported as looks_different")
    check(len(near(report["looks_different"], STICKER, 0.2)) == len(report["looks_different"]), "nothing else looks different")
    check(report["verdict"].startswith("NO CHANGE"), "the verdict still comes from geometry alone")
    report, _ = analyse(scene(BOX_A, sticker=True), 24, conf_pct=0, typical_m=2.5, looks_different=False)
    check(not report["looks_different"] and not report["looks_different_check"]["active"], "looks_different=False switches it off")

    print("Box rebuilt 20 cm off in the second clip (misaligned clips), look-alike check:")
    shifted = scene(BOX_A + np.array([0.2, 0.0, 0.0]), features=True)
    report, _ = analyse(shifted, 24, conf_pct=0, typical_m=2.5, lookalike=False)
    check(bool(report["appeared"]) and bool(report["gone"]), "without the check it is reported as gone and appeared")
    report, _ = analyse(shifted, 24, conf_pct=0, typical_m=2.5, reach=0.15)
    print(f"  look-alike check: {report['lookalike_check']}")
    check(not report["appeared"] and not report["gone"], "with the check nothing is reported")
    report, _ = analyse(shifted, 24, conf_pct=0, typical_m=2.5)
    check(not report["lookalike_check"]["active"], "the check stays off by itself when the clips line up (reach comes from the measured misalignment)")
    report, _ = analyse(scene(BOX_B, features=True), 24, conf_pct=0, typical_m=2.5, reach=0.15)
    check(len(near(report["appeared"], BOX_B, 0.25)) == 1 and len(near(report["gone"], BOX_A, 0.25)) == 1,
          "a box moved far (1.4 m) is still found with the check on")

    print("Small box (6 cm) moved, for information only:")
    report, _ = analyse(scene(BOX_B, box_size=0.06), 24, conf_pct=0, typical_m=2.5)
    print(f"  verdict: {report['verdict']} (appeared {len(report['appeared'])}, gone {len(report['gone'])})")

    print("\nAll checks passed." if not failures else f"\n{len(failures)} check(s) failed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
