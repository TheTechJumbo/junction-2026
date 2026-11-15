#!/usr/bin/env python3
"""Gaussian splats straight from a reconstruction, on a CPU, with no training.

Every trusted pixel of the new clip becomes one flat Gaussian disc: its centre is the pixel's
3D point, its size is the pixel's footprint at that depth, its colour is the pixel's colour, and
it faces the camera that saw it. That takes a fraction of a second in numpy. Training a splat
the usual way needs a GPU and minutes; this skips it and looks rougher up close.

What changed is coloured in: green for what appeared, red ghosts for what is gone.

The file is the common `.splat` format, 32 bytes per Gaussian: position (3 float32), scale
(3 float32), colour RGBA (4 uint8), rotation quaternion w, x, y, z (4 uint8, value*128+128).
Coordinates are VGGT's: the first camera at the origin, x right, y down, z forward.
"""
import numpy as np

import recon_change as rc


def quaternion(R):
    """Rotation matrix -> unit quaternion (w, x, y, z)."""
    w = np.sqrt(max(0.0, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2
    x = np.sqrt(max(0.0, 1 + R[0, 0] - R[1, 1] - R[2, 2])) / 2
    y = np.sqrt(max(0.0, 1 - R[0, 0] + R[1, 1] - R[2, 2])) / 2
    z = np.sqrt(max(0.0, 1 - R[0, 0] - R[1, 1] + R[2, 2])) / 2
    q = np.array([w, np.copysign(x, R[2, 1] - R[1, 2]), np.copysign(y, R[0, 2] - R[2, 0]), np.copysign(z, R[1, 0] - R[0, 1])])
    return q / np.linalg.norm(q)


def gaussians(r, detail, max_gaussians=200_000, max_frames=6):
    """Arrays (position, scale, rgba, quaternion) for the new clip, with the changes coloured."""
    frames = detail["after"]
    frames = [frames[i] for i in np.linspace(0, len(frames) - 1, min(max_frames, len(frames))).round().astype(int)]
    S, H, W = r.depth.shape
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    pos, scale, rgba, quat = [], [], [], []

    def add(points, depth, focal, colour, alpha, frame, spacing):
        size = depth / focal * spacing * 0.75  # a pixel's footprint at that depth
        pos.append(points.astype(np.float32))
        scale.append(np.stack([size, size, size * 0.3], 1).astype(np.float32))  # flat along the view direction
        rgba.append(np.concatenate([colour, np.full((len(points), 1), alpha)], 1).astype(np.uint8))
        quat.append(np.tile(quaternion(r.E[frame][:, :3].T), (len(points), 1)))  # disc faces its camera

    for q in frames:
        trusted = detail["valid"][q]
        stride = max(1, int(np.ceil(np.sqrt(trusted.sum() * len(frames) / max_gaussians))))
        pick = trusted & (u % stride == 0) & (v % stride == 0)
        colour = r.rgb[q].astype(np.float32)
        found = detail["appeared"]
        hit = np.zeros(H * W, bool)
        hit[found["pixels"][(found["member"] >= 0) & (found["frames"] == q)]] = True
        hit = hit.reshape(H, W)
        colour[hit] = 0.45 * colour[hit] + 0.55 * np.array([60, 220, 60])  # appeared: green
        add(rc.unproject(r.depth[q], r.K[q], r.E[q])[pick], r.depth[q][pick], r.K[q][0, 0], colour[pick], 235, q, stride)

    found = detail["gone"]  # what is gone only exists in the baseline frames: show it as red ghosts
    keep = np.flatnonzero(found["member"] >= 0)[::3]
    for q in np.unique(found["frames"][keep]):
        sel = keep[found["frames"][keep] == q]
        depth = r.depth[q].reshape(-1)[found["pixels"][sel]]
        add(found["points"][sel], depth, r.K[q][0, 0], np.tile([235.0, 60.0, 60.0], (len(sel), 1)), 150, q, 2)
    return np.concatenate(pos), np.concatenate(scale), np.concatenate(rgba), np.concatenate(quat)


def write_splat(r, detail, path, max_gaussians=200_000, marks=()):
    """Write the `.splat` file. Returns how many Gaussians it holds.
    marks: extra coloured balls, each (world position, radius, RGB colour), e.g. where the wearer is."""
    pos, scale, rgba, quat = gaussians(r, detail, max_gaussians)
    if marks:
        pos = np.concatenate([pos, np.array([m[0] for m in marks], np.float32)])
        scale = np.concatenate([scale, np.array([[m[1]] * 3 for m in marks], np.float32)])
        rgba = np.concatenate([rgba, np.array([[*m[2], 255] for m in marks], np.uint8)])
        quat = np.concatenate([quat, np.tile([1.0, 0.0, 0.0, 0.0], (len(marks), 1))])
    record = np.zeros(len(pos), dtype=[("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    record["pos"], record["scale"], record["rgba"] = pos, scale, rgba
    record["rot"] = np.clip(quat * 128 + 128, 0, 255).astype(np.uint8)
    with open(path, "wb") as f:
        f.write(record.tobytes())
    return len(pos)
