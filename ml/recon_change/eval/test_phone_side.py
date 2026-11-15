#!/usr/bin/env python3
"""Offline test of phone_side.py: the map made once and the per-frame position fix.

No cluster, no data, no model: a made-up room (a floor and a wall with marked spots, each spot
with its own random descriptor), made-up cameras with exact depth pictures, and a made-up
"extractor" that sees the spots. Needs NumPy and OpenCV only.

    python eval/test_phone_side.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import phone_side as ps  # noqa: E402

H, W = 240, 320
K = np.array([[260.0, 0, 160.0], [0, 260.0, 120.0], [0, 0, 1]])


def look_at(position, target):
    """World -> camera (3, 4) of a camera at `position` looking at `target`, world z up."""
    forward = np.asarray(target, float) - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    R = np.stack([right, down, forward])
    return np.concatenate([R, (-R @ position)[:, None]], 1)


def depth_picture(E):
    """Exact depth of the floor (z = 0) and the wall (y = 3) for one camera."""
    v, u = np.mgrid[0:H, 0:W]
    rays = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones((H, W))], -1) @ E[:, :3]  # in the world
    C = -E[:, :3].T @ E[:, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        floor = np.where(rays[..., 2] < -1e-6, -C[2] / rays[..., 2], np.inf)
        wall = np.where(rays[..., 1] > 1e-6, (3.0 - C[1]) / rays[..., 1], np.inf)
    return np.minimum(floor, wall).astype(np.float32)  # rays have camera z = 1, so the ray length IS the depth


def room(seed):
    rng = np.random.default_rng(seed)
    floor = np.stack([rng.uniform(-2, 2, 500), rng.uniform(0, 3, 500), np.zeros(500)], 1)
    wall = np.stack([rng.uniform(-2, 2, 400), np.full(400, 3.0), rng.uniform(0, 2, 400)], 1)
    spots = np.concatenate([floor, wall])
    return spots, ps.unit(rng.normal(size=(len(spots), 64))).astype(np.float32), ps.unit(rng.normal(size=16)).astype(np.float32)


def see(spots, desc, glob, E, rng, noise=0.3):
    """What the made-up extractor returns for a camera: the spots in view, a little off."""
    cam = spots @ E[:, :3].T + E[:, 3]
    xy = np.stack([K[0, 0] * cam[:, 0] / cam[:, 2] + K[0, 2], K[1, 1] * cam[:, 1] / cam[:, 2] + K[1, 2]], 1)
    ok = (cam[:, 2] > 0.2) & (xy[:, 0] > 3) & (xy[:, 0] < W - 4) & (xy[:, 1] > 3) & (xy[:, 1] < H - 4)
    return ps.Features((H, W), (xy[ok] + rng.normal(0, noise, (int(ok.sum()), 2))).astype(np.float32), desc[ok], glob)


def main():
    rng = np.random.default_rng(0)
    spots, desc, glob = room(1)
    cams = [look_at(np.array([x, -1.0, 1.5]), [x / 2, 2.5, 0.4]) for x in (-1.5, -0.5, 0.5, 1.5)]
    depth = np.stack([depth_picture(E) for E in cams])
    conf = np.ones_like(depth) + rng.uniform(0, 1, depth.shape).astype(np.float32)
    feats = [see(spots, desc, glob, E, rng, noise=0.0) for E in cams]
    m = ps.build_map(feats, depth, conf, np.stack([K] * 4), np.stack(cams), kind="made up")
    assert len(m.xyz) > 300 and m.start[-1] == len(m.xyz) == len(m.desc), (len(m.xyz), m.start)

    # The 3D point stored for a feature is the spot it shows (to within what a pixel of depth allows).
    all_desc = {d.tobytes(): s for d, s in zip(desc, spots)}
    off = [np.linalg.norm(all_desc[d.tobytes()] - x) for d, x in zip(m.desc, m.xyz)]
    assert np.median(off) < 0.02 and np.percentile(off, 90) < 0.08, (np.median(off), np.percentile(off, 90))

    # A new view of the same room: found, and the pose is right.
    E = look_at(np.array([0.2, -0.6, 1.4]), [0.3, 2.6, 0.5])
    out = ps.locate_frame(m, see(spots, desc, glob, E, rng), k=2, reproj=3.0)
    true_position = -E[:, :3].T @ E[:, 3]
    angle = np.degrees(np.arccos(np.clip(out["forward"] @ E[2, :3], -1, 1)))
    assert out["state"] == "found", out
    assert np.linalg.norm(out["position"] - true_position) < 0.05 and angle < 1.5, (out["position"], true_position, angle)
    assert out["spread"] >= 0.5 and out["inliers"] >= 100 and len(out["photos"]) == 2, out

    # With half of the matches wrong the pose still holds: the wrong ones do not agree with it.
    f = see(spots, desc, glob, E, rng)
    wrong = rng.choice(len(f.xy), len(f.xy) // 2, replace=False)
    f.xy[wrong] = rng.uniform([0, 0], [W, H], (len(wrong), 2)).astype(np.float32)
    out = ps.locate_frame(m, f, k=0, reproj=3.0)
    assert out["state"] == "found" and np.linalg.norm(out["position"] - true_position) < 0.05, out

    # Another room (other spots, other descriptors): lost.
    other_spots, other_desc, other_glob = room(2)
    # Random descriptors always have a closest partner, so a floor on the similarity is what keeps
    # chance matches out (without it a few hundred chance matches let some pose find 15 that agree).
    out = ps.locate_frame(m, see(other_spots, other_desc, other_glob, E, rng), k=0, reproj=3.0, min_sim=0.7)
    assert out["state"] == "lost" and out["score"] < 15 and out["matches"] < 30, out
    out = ps.locate_frame(m, see(spots, desc, glob, E, rng), k=0, reproj=3.0, min_sim=0.7)
    assert out["state"] == "found" and out["inliers"] >= 100, out

    # A frame with nothing in it: lost, no crash.
    out = ps.locate_frame(m, ps.Features((H, W), np.zeros((0, 2), np.float32), np.zeros((0, 64), np.float32), glob))
    assert out["state"] == "lost" and out["position"] is None, out

    # The limits.
    limits = ps.Limits(found=30, unsure=15)  # on agreeing matches x the share of the picture they cover
    assert [ps.decide(n, s, limits) for n, s in ((80, 0.5), (200, 0.1), (20, 0.9), (100, 0.1))] == ["found", "unsure", "unsure", "lost"]
    assert ps.decide(80, 0.5, ps.Limits(30, 15, reach=3.0), reach=5.0) == "lost"  # a pose far outside the filmed room
    assert out["reach"] is None and ps.reach_of(m, true_position) < 1.5
    assert ps.spread_of(np.array([[1.0, 1.0], [W - 1.0, H - 1.0]]), (H, W)) == 2 / 16

    # The map survives the file, and a map with reduced descriptors still gives the pose.
    with tempfile.TemporaryDirectory() as folder:
        small = ps.build_map(feats, depth, conf, np.stack([K] * 4), np.stack(cams), kind="made up", dim=12)
        assert small.desc.shape[1] == 12 and small.basis.shape == (64, 12)
        for name, room_map in (("full", m), ("small", small)):
            ps.save_map(Path(folder) / f"{name}.npz", room_map)
            again = ps.load_map(Path(folder) / f"{name}.npz")
            assert again.kind == "made up" and again.size == (H, W) and np.allclose(again.xyz, room_map.xyz)
            out = ps.locate_frame(again, see(spots, desc, glob, E, rng), k=2, reproj=3.0)
            assert out["state"] == "found" and np.linalg.norm(out["position"] - true_position) < 0.06, (name, out)
        sizes = {p.name: p.stat().st_size for p in Path(folder).iterdir()}
        assert sizes["small.npz"] < sizes["full.npz"], sizes

    # One feature per patch (as a ViT gives): matching between patch centres stays within one patch.
    rows, cols, patch = 6, 8, 40.0
    v, u = np.mgrid[0:rows, 0:cols]
    centres = np.stack([(u + 0.5) * patch, (v + 0.5) * patch], -1).reshape(-1, 2).astype(np.float32)
    grid_desc = ps.unit(rng.normal(size=(rows * cols, 32))).astype(np.float32)
    f = ps.Features((H, W), centres, grid_desc, None, (rows, cols), patch / 2)
    fake = ps.RoomMap("grid", K, (H, W), np.stack(cams[:1]), np.array([0, rows * cols]), rng.normal(size=(rows * cols, 3)).astype(np.float32),
                      grid_desc)
    xy, xyz, sim = ps.match(fake, f, [0], refine=True)
    assert len(xy) == rows * cols and np.abs(xy - centres).max() < patch and sim.min() > 0.99, (len(xy), np.abs(xy - centres).max())
    print("phone_side: all checks passed")


if __name__ == "__main__":
    main()
