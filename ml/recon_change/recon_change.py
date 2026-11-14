#!/usr/bin/env python3
"""Feasibility test: rebuild a room in 3D from plain video and find what changed.

Give it a "before" and an "after" clip of the same room (glasses or phone). All frames go
through VGGT in one pass, so both clips land in one 3D coordinate frame. A surface counts as
changed when the other clip looked straight through the place where it is:

    appeared = surfaces in the after clip that the before clip saw through
    gone     = surfaces in the before clip that the after clip sees through

    python recon_change.py --before before.mp4 --after after.mp4 --out out

The before clip is split in two (even and odd frames). One half is the reference. The other
half is the control: nothing changed between the two halves, so anything reported there is a
false alarm. The thresholds come from how noisy the depth is inside the before clip and
between the two clips, whichever is worse.

The control cannot see errors that only show up between the two clips, so a clean control is
not proof. When the clips line up worse with each other than frames of one clip do, a tall object
lands in a slightly different place and looks like "gone here, appeared there". The look-alike
check removes that: a flagged surface is dropped when the other clip has a surface that looks the
same (DINOv2 patch features, which VGGT computes anyway) within reach of it.

Geometry cannot see flat or small things (a sheet of paper, a sticker, a spill, a pen). A second
kind of evidence covers those and is reported on its own, as "looks_different": places where the
after clip shows something that looks different from what every before frame showed there
(colour and patch features), by much more than the after clip differs from itself. It does not
change "appeared", "gone" or the verdict.
"""
import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
EDGE = 0.04  # neighbouring pixels whose depth differs by more than this fraction are an edge
# Pixels searched around a spot in the other clip before calling it empty. The two clips can be
# misaligned by more than frames of one clip are, which makes a tall object look moved. 15 clears
# that on the test videos; much larger windows hide small objects on floors seen at an angle.
WINDOW = 15
# Look-alike check. How far (as a fraction of the viewing distance) to search the other clip for
# the same-looking surface: REACH_PER_SIGMA times the depth noise the clips have between them
# beyond what one clip has inside itself, at most REACH_MAX. Well-aligned clips get a reach
# below one and a half image patches, which switches the check off, so nothing changes for them.
REACH_PER_SIGMA, REACH_MAX = 15.0, 0.15
FEAT_DIM = 256  # patch features are reduced to this many dimensions before they are stored
# "Looks different" check. Appearance is compared on a grid of CELL x CELL pixel cells (half an
# image patch). A cell is flagged when its score is above the limit: LOOK_MARGIN times the lowest
# limit at which the unchanged control reports nothing, and at least LOOK_Z robust standard
# deviations. Flagged cells must form a group of LOOK_MIN_VOXELS touching voxels, each seen in
# two frames.
CELL, LOOK_Z, LOOK_MARGIN, LOOK_MIN_VOXELS = 7, 8.0, 1.5, 8
LOOK_SHIFTS = ((0, 0), (-4, 0), (4, 0), (0, -4), (0, 4))  # pixels: the best of these is the colour match
GREEN, RED = (60, 220, 60), (60, 60, 235)  # BGR


@dataclass
class Recon:
    """What the reconstruction model gives us for S frames of H x W pixels."""

    depth: np.ndarray  # (S, H, W) distance along the camera's z axis, in scene units (not metres)
    conf: np.ndarray  # (S, H, W) the model's confidence in each depth value
    K: np.ndarray  # (S, 3, 3) camera intrinsics for the H x W image
    E: np.ndarray  # (S, 3, 4) world -> camera, OpenCV convention: x_cam = R @ x_world + t
    rgb: np.ndarray  # (S, H, W, 3) uint8
    feat: np.ndarray = None  # (S, H/14, W/14, C) what each image patch looks like; None = no look-alike check


# ----------------------------------------------------------------------------- frames
def sharpness(img):
    """Variance of the Laplacian on a small grey copy: low means motion blur."""
    h, w = img.shape[:2]
    grey = cv2.cvtColor(cv2.resize(img, (320, max(1, 320 * h // w))), cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(grey, cv2.CV_64F).var()


def sample_frames(src, n):
    """n frames spread over a video (the sharpest of each stretch) or over a folder of images."""
    src = Path(src)
    if src.is_dir():
        files = sorted(p for p in src.iterdir() if p.suffix.lower() in IMG_EXT)
        if not files:
            raise SystemExit(f"No images in {src}")
        picks = sorted(set(np.linspace(0, len(files) - 1, min(n, len(files))).round().astype(int)))
        return [cv2.imread(str(files[i])) for i in picks]
    cap = cv2.VideoCapture(str(src))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        raise SystemExit(f"Cannot read {src} (is it a video file?)")
    n = min(n, total)
    stretch_of = {}  # candidate frame index -> which stretch of the video it belongs to
    for s in range(n):
        for f in np.linspace(s * total / n, (s + 1) * total / n, 6)[1:-1]:
            stretch_of[int(f)] = s
    best = {}  # stretch -> (sharpness, frame)
    index = 0
    while cap.grab():  # read in order: seeking is unreliable for phone video
        if index in stretch_of:
            ok, img = cap.retrieve()
            if ok:
                score = sharpness(img)
                if score > best.get(stretch_of[index], (-1.0, None))[0]:
                    best[stretch_of[index]] = (score, img)
        index += 1
    cap.release()
    return [best[s][1] for s in sorted(best)]


def fit_size(h, w, long_side=518, patch=14):
    """Model input size: long side 518 px, both sides multiples of the 14 px patch."""
    scale = long_side / max(h, w)
    return max(patch, round(h * scale / patch) * patch), max(patch, round(w * scale / patch) * patch)


# ----------------------------------------------------------------------------- model
def load_model(model_id="facebook/VGGT-1B", device=None):
    """Load VGGT. On a GPU this takes 13 to 17 s on Iridis, so a live service does it once.
    device: "cuda", "cpu", or None to use a GPU when there is one."""
    import torch
    from vggt.models.vggt import VGGT

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    t0 = time.time()
    model = VGGT.from_pretrained(model_id).to(device).eval()
    name = torch.cuda.get_device_name(0) if device == "cuda" else f"CPU, {torch.get_num_threads()} threads"
    print(f"VGGT loaded in {time.time() - t0:.0f} s on {name}", flush=True)
    return model


def reconstruct(model, frames_bgr, long_side=518):
    """Run VGGT on all frames at once: depth, confidence and a camera for every frame.
    Runs on whatever device the model is on. A smaller long_side is faster and coarser."""
    import torch
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri

    device = next(model.parameters()).device
    on_gpu = device.type == "cuda"
    H, W = fit_size(*frames_bgr[0].shape[:2], long_side=long_side)
    rgb = np.stack([
        cv2.cvtColor(cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB) for f in frames_bgr
    ])
    images = torch.from_numpy(rgb).permute(0, 3, 1, 2).float().div(255).to(device)  # (S, 3, H, W) in [0, 1]
    t0 = time.time()
    tokens = {}  # VGGT's first stage is a DINOv2 backbone: keep its patch tokens, they cost nothing extra
    hook = model.aggregator.patch_embed.register_forward_hook(
        lambda module, args, out: tokens.update(x=out["x_norm_patchtokens"] if isinstance(out, dict) else out))
    try:
        with torch.no_grad():
            if on_gpu:
                torch.cuda.reset_peak_memory_stats()
                dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
                with torch.autocast("cuda", dtype=dtype):
                    pred = model(images)
            else:
                pred = model(images)  # plain float32 on a CPU
            E, K = pose_encoding_to_extri_intri(pred["pose_enc"], images.shape[-2:])
    finally:
        hook.remove()
    if on_gpu:
        torch.cuda.synchronize()
    memory = f", peak GPU memory {torch.cuda.max_memory_allocated() / 1e9:.1f} GB" if on_gpu else ""
    print(f"VGGT: {len(rgb)} frames at {W}x{H} | inference {time.time() - t0:.1f} s{memory}", flush=True)

    def f32(t):
        return t.float().cpu().numpy()

    return Recon(depth=f32(pred["depth"])[0, ..., 0], conf=f32(pred["depth_conf"])[0], K=f32(K)[0], E=f32(E)[0], rgb=rgb,
                 feat=reduce_features(tokens["x"], H // 14, W // 14) if "x" in tokens else None)


def reduce_features(tokens, h, w, dim=FEAT_DIM):
    """Patch tokens (S, h*w, C) -> (S, h, w, dim) float16. Raw DINOv2 tokens share a large common
    part (every pair has cosine > 0.9); removing the mean and keeping the main directions of this
    scene makes "looks the same" and "looks different" far apart, and the result 4x smaller."""
    import torch

    x = tokens.float().reshape(-1, tokens.shape[-1])
    x = x - x.mean(0, keepdim=True)
    if dim < min(x.shape):
        x = x @ torch.pca_lowrank(x, q=dim, center=False, niter=4)[2]
    return x.reshape(len(tokens), h, w, -1).half().cpu().numpy()


def run_vggt(frames_bgr, model_id="facebook/VGGT-1B"):
    """One-off use: load the model, reconstruct, done."""
    return reconstruct(load_model(model_id), frames_bgr)


# ----------------------------------------------------------------------------- geometry
def unproject(depth, K, E):
    """Depth map -> world points (H, W, 3)."""
    H, W = depth.shape
    u, v = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    cam = np.stack([(u - K[0, 2]) / K[0, 0] * depth, (v - K[1, 2]) / K[1, 1] * depth, depth], -1)
    return (cam - E[:, 3]) @ E[:, :3]  # R^T (x_cam - t), written for row vectors


def valid_mask(r, conf_pct):
    """Pixels worth trusting: confident, and not on a depth edge (where depth is a blend)."""
    d = r.depth
    jx = np.abs(d[:, :, 1:] - d[:, :, :-1]) > EDGE * np.minimum(d[:, :, 1:], d[:, :, :-1])
    jy = np.abs(d[:, 1:] - d[:, :-1]) > EDGE * np.minimum(d[:, 1:], d[:, :-1])
    edge = np.zeros(d.shape, bool)
    edge[:, :, 1:] |= jx
    edge[:, :, :-1] |= jx
    edge[:, 1:] |= jy
    edge[:, :-1] |= jy
    kernel = np.ones((3, 3), np.uint8)
    edge = np.stack([cv2.dilate(e.astype(np.uint8), kernel) > 0 for e in edge])
    return (r.conf > np.percentile(r.conf, conf_pct)) & (d > 0) & ~edge


def nearest_in_window(depth, size=7):
    """Smallest depth in a size x size window around each pixel, for every frame. Thin things
    (chair legs, cables, lattices) cover few pixels and poses are never pixel-perfect, so "is
    this spot empty?" must ask whether anything nearby is in front, not just the one pixel."""
    kernel = np.ones((size, size), np.uint8)
    return np.stack([cv2.erode(np.where(d > 0, d, np.inf).astype(np.float32), kernel) for d in depth])


def depth_gap(points, E, K, depth, near, valid):
    """Project world points into one camera and compare with what that camera saw there.
    Returns two arrays, each a fraction of the point's depth (+ farther, - nearer), NaN where
    the camera has no trusted view of the point:
      gap       against the depth at that pixel
      gap_near  against the nearest depth in the window around it (see nearest_in_window)"""
    cam = points @ E[:, :3].T + E[:, 3]
    z = cam[:, 2]
    H, W = depth.shape
    with np.errstate(divide="ignore", invalid="ignore"):
        u = np.rint(K[0, 0] * cam[:, 0] / z + K[0, 2])
        v = np.rint(K[1, 1] * cam[:, 1] / z + K[1, 2])
    inside = (z > 1e-6) & (u >= 0) & (u < W) & (v >= 0) & (v < H)
    ui, vi = u[inside].astype(np.int64), v[inside].astype(np.int64)
    trusted = valid[vi, ui]
    out = []
    for seen in (depth, near):
        g = (seen[vi, ui] - z[inside]) / z[inside]
        g[~trusted] = np.nan
        full = np.full(len(points), np.nan, np.float32)
        full[inside] = g
        out.append(full)
    return out


def measure_noise(r, valid, near, group_a, group_b, per_frame=4000, seed=0):
    """Depth disagreement between two views of an unchanged scene: robust sigma of the gap."""
    rng = np.random.default_rng(seed)
    gaps = []
    for q in group_a:
        pts = unproject(r.depth[q], r.K[q], r.E[q])[valid[q]]
        if len(pts) > per_frame:
            pts = pts[rng.choice(len(pts), per_frame, replace=False)]
        for j in group_b:
            g, _ = depth_gap(pts, r.E[j], r.K[j], r.depth[j], near[j], valid[j])
            gaps.append(g[np.isfinite(g)])
    gaps = np.abs(np.concatenate(gaps)) if gaps else np.zeros(0)
    core = gaps[gaps < 0.25]  # ignore plain occlusions
    return float(1.4826 * np.median(core)) if len(core) else 0.05


def votes(r, valid, near, pts, frames, tol_agree, tol_free):
    """For each world point, how many of these frames saw the same surface (agree), how many
    looked straight through its place (free), and whether any had a trusted view (seen)."""
    agree = np.zeros(len(pts), np.int32)
    free = np.zeros(len(pts), np.int32)
    seen = np.zeros(len(pts), bool)
    for j in frames:
        gap, gap_near = depth_gap(pts, r.E[j], r.K[j], r.depth[j], near[j], valid[j])
        agree += np.abs(gap) < tol_agree
        free += gap_near > tol_free  # everything near that pixel is farther: the place is empty
        seen |= np.isfinite(gap)
    return agree, free, seen


def compare(r, valid, near_own, near_cross, query, ref, tol_own, tol_cross, look=None):
    """Flag pixels of the query frames whose surface the ref frames looked straight through.

    Two tests, both needed:
      1. The point is real. The model's depth is wrong at the edges of thin things, which
         leaves points floating in mid-air. Other frames of the point's own clip must agree
         with it, and none of them may look through it.
      2. The other clip saw empty space there: enough ref frames look through the point, and
         next to none saw the same surface.
    Each test has its own (agree, see-through) tolerances and its own search window, because
    frames of one clip line up with each other much better than the two clips line up.
    With look = (reach, min_sim), a third test drops flags that have a look-alike surface in
    the ref frames (see has_lookalike)."""
    need = 2 if len(ref) >= 3 else 1
    own_need = 2 if len(query) >= 4 else 1
    points, frames, pixels = [], [], []
    seen_n = agree_n = dropped = 0
    table = patch_table(r, valid, ref) if look else None
    for q in query:
        pix = np.flatnonzero(valid[q])
        pts = unproject(r.depth[q], r.K[q], r.E[q]).reshape(-1, 3)[pix]
        own_agree, own_free, own_seen = votes(r, valid, near_own, pts, [j for j in query if j != q], *tol_own)
        seen_n += int(own_seen.sum())
        agree_n += int((own_seen & (own_agree > 0)).sum())
        real = (own_agree >= own_need) & (own_free == 0)
        pix, pts = pix[real], pts[real]
        agree, free, _ = votes(r, valid, near_cross, pts, ref, *tol_cross)
        flag = (free >= need) & (6 * agree <= free)
        if look and flag.any():
            alike = has_lookalike(r, q, pix[flag], pts[flag], table, *look, need)
            dropped += int(alike.sum())
            flag[flag] = ~alike
        points.append(pts[flag])
        frames.append(np.full(int(flag.sum()), q))
        pixels.append(pix[flag])
    return {
        "points": np.concatenate(points), "frames": np.concatenate(frames), "pixels": np.concatenate(pixels),
        "consistency": agree_n / max(seen_n, 1),  # how often a clip's own frames agree with each other
        "dropped": dropped,  # flagged pixels removed by the look-alike check
    }


def patch_table(r, valid, frames):
    """One row per image patch of these frames: where its surface is in the world (the patch
    centre at the median trusted depth), what it looks like, and which frame it is from."""
    H, W = r.depth.shape[1:]
    h, w = r.feat.shape[1:3]
    ph, pw = H // h, W // w
    pos, feat, frame = [], [], []
    for j in frames:
        d = np.where(valid[j], r.depth[j], np.nan)[:h * ph, :w * pw]
        d = d.reshape(h, ph, w, pw).transpose(0, 2, 1, 3).reshape(h, w, ph * pw)
        ok = np.isfinite(d).sum(-1) >= ph * pw // 4
        v, u = np.nonzero(ok)
        z = np.nanmedian(d[ok], -1)
        K, E = r.K[j], r.E[j]
        cam = np.stack([((u + 0.5) * pw - 0.5 - K[0, 2]) / K[0, 0] * z, ((v + 0.5) * ph - 0.5 - K[1, 2]) / K[1, 1] * z, z], -1)
        pos.append((cam - E[:, 3]) @ E[:, :3])
        feat.append(r.feat[j][ok])
        frame.append(np.full(len(z), j))
    return np.concatenate(pos).astype(np.float32), np.concatenate(feat), np.concatenate(frame)


def distances(a, b):
    """Distance from every row of a to every row of b, without building all (A, B, 3) differences."""
    a, b = a.astype(np.float64), b.astype(np.float64)
    return np.sqrt(np.maximum((a * a).sum(1)[:, None] + (b * b).sum(1)[None] - 2.0 * a @ b.T, 0.0))


def same_place_similarity(r, a, b, seed=0, sample=1500):
    """How alike two views of one unchanged surface look: cosine similarity between patches of
    table a and the patch of table b that sits at the same place (within 1.5% of the distance).
    Returns the lower quartile, or None if the tables hardly overlap."""
    (pa, fa, qa), (pb, fb, _) = a, b
    if not len(pa) or not len(pb):
        return None
    pick = np.random.default_rng(seed).choice(len(pa), min(sample, len(pa)), replace=False)
    z = np.einsum("ij,ij->i", pa[pick], r.E[qa[pick], 2, :3]) + r.E[qa[pick], 2, 3]
    dist = distances(pa[pick], pb)
    nearest = dist.argmin(1)
    close = dist[np.arange(len(pick)), nearest] < 0.015 * z
    if close.sum() < 50:
        return None
    return float(np.percentile(np.einsum("ij,ij->i", fa[pick][close], fb[nearest[close]]), 25))


def has_lookalike(r, q, pix, pts, table, reach, min_sim, need):
    """For flagged pixels of frame q: does the other clip have a surface within reach (a fraction
    of the viewing distance) that looks the same, in at least `need` of its frames? If so the
    flag is the same object reconstructed in a slightly different place, not a change."""
    pos, feat, frame = table
    H, W = r.depth.shape[1:]
    h, w = r.feat.shape[1:3]
    patch = (pix // W) // (H // h) * w + (pix % W) // (W // w)
    ids, inv = np.unique(np.minimum(patch, h * w - 1), return_inverse=True)
    inv = inv.ravel()
    centre = np.stack([np.bincount(inv, pts[:, k], len(ids)) for k in range(3)], 1) / np.bincount(inv, minlength=len(ids))[:, None]
    z = centre @ r.E[q][2, :3] + r.E[q][2, 3]
    hit = (distances(centre, pos) < reach * z[:, None]) & (r.feat[q].reshape(h * w, -1)[ids] @ feat.T > min_sim)
    count = np.zeros(len(ids), np.int32)
    for j in np.unique(frame):
        count += hit[:, frame == j].any(1)
    return (count >= need)[inv]


def cluster(points, frames, voxel, min_voxels, min_frames):
    """Group flagged points into objects: touching voxels, each seen from at least min_frames frames.
    Returns (clusters, member) where member[i] is the cluster of point i, or -1."""
    member = -np.ones(len(points), np.int64)
    if not len(points):
        return [], member
    keys = np.floor(points / voxel).astype(np.int64)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.ravel()
    n_pts = np.bincount(inv, minlength=len(uniq))
    pairs = np.unique(np.stack([inv, frames], 1), axis=0)
    n_frames = np.bincount(pairs[:, 0], minlength=len(uniq))
    keep = (n_pts >= 3) & (n_frames >= min_frames)
    where = {tuple(k): i for i, k in enumerate(uniq.tolist()) if keep[i]}
    label = -np.ones(len(uniq), np.int64)
    offsets = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) if (a, b, c) != (0, 0, 0)]
    groups = []
    for key, start in where.items():
        if label[start] >= 0:
            continue
        label[start] = len(groups)
        stack, group = [key], [start]
        while stack:
            x, y, z = stack.pop()
            for a, b, c in offsets:
                nb = (x + a, y + b, z + c)
                i = where.get(nb)
                if i is not None and label[i] < 0:
                    label[i] = len(groups)
                    stack.append(nb)
                    group.append(i)
        groups.append(group)
    point_label = label[inv]
    clusters = []
    for g, group in enumerate(groups):
        if len(group) < min_voxels:
            continue
        sel = point_label == g
        p = points[sel]
        member[sel] = len(clusters)
        clusters.append({
            "voxels": len(group), "points": int(sel.sum()), "frames": int(len(np.unique(frames[sel]))),
            "center": p.mean(0).round(4).tolist(), "size": (p.max(0) - p.min(0)).round(4).tolist(),
        })
    # Biggest first. remap has one spare slot at the end so that member == -1 stays -1.
    order = np.array(sorted(range(len(clusters)), key=lambda i: -clusters[i]["voxels"]), dtype=np.int64)
    remap = np.full(len(clusters) + 1, -1, dtype=np.int64)
    remap[order] = np.arange(len(order))
    return [clusters[i] for i in order], remap[member]


# ----------------------------------------------------------------------------- appearance
def bilinear(img, x, y):
    """Values of img (h, w, C) at fractional positions x, y -> (N, C)."""
    h, w = img.shape[:2]
    x, y = np.clip(x, 0, w - 1), np.clip(y, 0, h - 1)
    x0 = np.minimum(np.floor(x).astype(np.int64), w - 2)
    y0 = np.minimum(np.floor(y).astype(np.int64), h - 2)
    fx, fy = (x - x0)[:, None].astype(np.float32), (y - y0)[:, None].astype(np.float32)
    return ((1 - fx) * (1 - fy) * img[y0, x0] + fx * (1 - fy) * img[y0, x0 + 1]
            + (1 - fx) * fy * img[y0 + 1, x0] + fx * fy * img[y0 + 1, x0 + 1])


def colour_maps(rgb):
    """Smoothed Lab colour of every frame. Lightness counts half: shadows change it the most."""
    lab = [cv2.GaussianBlur(cv2.cvtColor(im, cv2.COLOR_RGB2LAB).astype(np.float32), (0, 0), 2.0) for im in rgb]
    return np.stack(lab) * np.float32([0.5, 1.0, 1.0])


def cells_of(r, valid, q):
    """The CELL x CELL pixel cells of frame q that have enough trusted depth:
    cell index, centre pixel (u, v) and the world point of the surface (median depth)."""
    H, W = r.depth.shape[1:]
    gh, gw = H // CELL, W // CELL
    d = np.where(valid[q], r.depth[q], np.nan)[:gh * CELL, :gw * CELL]
    d = d.reshape(gh, CELL, gw, CELL).transpose(0, 2, 1, 3).reshape(gh * gw, CELL * CELL)
    cell = np.flatnonzero(np.isfinite(d).sum(1) >= CELL * CELL // 4)
    z = np.nanmedian(d[cell], 1)
    u, v = (cell % gw + 0.5) * CELL - 0.5, (cell // gw + 0.5) * CELL - 0.5
    K, E = r.K[q], r.E[q]
    cam = np.stack([(u - K[0, 2]) / K[0, 0] * z, (v - K[1, 2]) / K[1, 1] * z, z], -1)
    return cell, u, v, ((cam - E[:, 3]) @ E[:, :3]).astype(np.float32)


def best_match(r, lab, valid, pts, feat, colour, others, tol, beyond):
    """Compare surface points (with their own patch feature and colour) with what each of the
    frames `others` shows at that place. A frame counts when it has a trusted view of the same
    surface, or (beyond=True) of something behind it: then it looked through the place, and what
    it saw there is still what the place looked like. Something in front hides the place.
    Returns the best feature similarity, the smallest colour difference and the number of
    frames that counted, for each point."""
    H, W = r.depth.shape[1:]
    sim = np.full(len(pts), -1.0, np.float32)
    diff = np.full(len(pts), np.inf, np.float32)
    n = np.zeros(len(pts), np.int32)
    for j in others:
        K, E = r.K[j], r.E[j]
        cam = pts @ E[:, :3].T + E[:, 3]
        z = cam[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = K[0, 0] * cam[:, 0] / z + K[0, 2]
            v = K[1, 1] * cam[:, 1] / z + K[1, 2]
        inside = (z > 1e-6) & (u >= 0) & (u <= W - 1) & (v >= 0) & (v <= H - 1)
        ui = np.clip(np.rint(np.nan_to_num(u)), 0, W - 1).astype(np.int64)
        vi = np.clip(np.rint(np.nan_to_num(v)), 0, H - 1).astype(np.int64)
        with np.errstate(divide="ignore", invalid="ignore"):
            gap = (r.depth[j][vi, ui] - z) / z
        same = inside & valid[j][vi, ui] & (np.abs(gap) < tol)
        ok = same | (inside & valid[j][vi, ui] & (gap > 0)) if beyond else same
        idx = np.flatnonzero(ok)
        if not len(idx):
            continue
        seen = np.stack([bilinear(lab[j], u[idx] + du, v[idx] + dv) for du, dv in LOOK_SHIFTS])
        # Exposure and white balance drift between frames: remove the typical colour offset.
        on_same = same[idx]
        offset = np.median(colour[idx][on_same] - seen[0][on_same], 0) if on_same.sum() >= 50 else 0.0
        diff[idx] = np.minimum(diff[idx], np.linalg.norm(colour[idx][None] - seen - offset, axis=-1).min(0))
        if feat is not None:
            h, w = r.feat.shape[1:3]
            f = bilinear(r.feat[j], (u[idx] + 0.5) * w / W - 0.5, (v[idx] + 0.5) * h / H - 0.5)
            f /= np.linalg.norm(f, axis=1, keepdims=True) + 1e-6
            sim[idx] = np.maximum(sim[idx], np.einsum("ij,ij->i", feat[idx], f))
        n[idx] += 1
    return sim, diff, n


def appearance(r, lab, valid, query, ref, tol, beyond=True):
    """For every cell of the query frames: how much more it differs from the ref frames' view of
    its place than from the other query frames' view of it. A cell on an edge, a reflection or
    fine texture differs from every other view, its own clip included, so only the surplus counts.
    Returns frame, cell, world point, colour surplus and feature surplus of each comparable cell."""
    H, W = r.depth.shape[1:]
    need = 2 if len(ref) >= 3 else 1
    out = {k: [] for k in ("frame", "cell", "point", "colour", "feature")}
    for q in query:
        cell, u, v, pts = cells_of(r, valid, q)
        colour = bilinear(lab[q], u, v)
        feat = None
        if r.feat is not None:
            h, w = r.feat.shape[1:3]
            feat = bilinear(r.feat[q], (u + 0.5) * w / W - 0.5, (v + 0.5) * h / H - 0.5)
            feat /= np.linalg.norm(feat, axis=1, keepdims=True) + 1e-6
        s_own, c_own, n_own = best_match(r, lab, valid, pts, feat, colour, [j for j in query if j != q], tol, False)
        s_ref, c_ref, n_ref = best_match(r, lab, valid, pts, feat, colour, ref, tol, beyond)
        ok = (n_own >= 1) & (n_ref >= need)
        out["frame"].append(np.full(int(ok.sum()), q))
        out["cell"].append(cell[ok])
        out["point"].append(pts[ok])
        out["colour"].append((c_ref - c_own)[ok])
        out["feature"].append((s_own - s_ref)[ok] if feat is not None else np.zeros(int(ok.sum()), np.float32))
    return {k: np.concatenate(v) if len(v) else np.zeros(0) for k, v in out.items()}


def spread(x):
    """Median and robust standard deviation."""
    if not len(x):
        return 0.0, 0.0
    med = float(np.median(x))
    return med, float(1.4826 * np.median(np.abs(x - med)))


def look_scores(found, control):
    """One score per cell: colour surplus plus feature surplus, each in robust standard
    deviations. The scale is the wider of the two clips' own spread and the control's, with a
    floor, so a noisy scene or a change of lighting makes the check less sensitive, not wrong.
    Returns (scores of found, scores of control, scales)."""
    scales = {}
    score, score0 = np.zeros(len(found["frame"]), np.float32), np.zeros(len(control["frame"]), np.float32)
    for key, floor in (("colour", 1.0), ("feature", 0.02)):
        med, sd = spread(found[key])
        med0, sd0 = spread(control[key])
        scales[key] = max(sd, sd0, floor)
        score += (found[key] - med) / scales[key]
        score0 += (control[key] - med0) / scales[key]
    return score, score0, scales


def voxel_ids(points, size):
    """One integer per point: the voxel of that size it falls in."""
    k = np.floor(np.asarray(points, np.float64) / size).astype(np.int64) + (1 << 20)
    return (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]


def looks_clusters(r, valid, found, score, limit, voxel, min_voxels=LOOK_MIN_VOXELS, skip_points=None):
    """Cells over the limit -> their trusted pixels -> groups of touching voxels, like the
    geometry hits. Cells at a place the geometry already reports (skip_points) are left out, and
    a group next to such a place is marked, so one object is not announced twice unknowingly."""
    H, W = r.depth.shape[1:]
    gh, gw = H // CELL, W // CELL
    hit = score > limit
    known = np.unique(voxel_ids(skip_points, 3 * voxel)) if skip_points is not None and len(skip_points) else np.zeros(0, np.int64)
    if len(known):
        hit &= ~np.isin(voxel_ids(found["point"], 3 * voxel), known)
    points, frames, pixels = [], [], []
    for q in np.unique(found["frame"][hit]):
        grid = np.zeros(gh * gw, bool)
        grid[found["cell"][hit & (found["frame"] == q)]] = True
        mask = np.zeros((H, W), bool)
        mask[:gh * CELL, :gw * CELL] = np.repeat(np.repeat(grid.reshape(gh, gw), CELL, 0), CELL, 1)
        pix = np.flatnonzero(mask & valid[q])
        points.append(unproject(r.depth[q], r.K[q], r.E[q]).reshape(-1, 3)[pix])
        frames.append(np.full(len(pix), q))
        pixels.append(pix)
    out = {"points": np.concatenate(points) if points else np.zeros((0, 3), np.float32),
           "frames": np.concatenate(frames) if frames else np.zeros(0, np.int64),
           "pixels": np.concatenate(pixels) if pixels else np.zeros(0, np.int64)}
    min_frames = 2 if len(np.unique(found["frame"])) >= 3 else 1
    out["clusters"], out["member"] = cluster(out["points"], out["frames"], voxel, min_voxels, min_frames)
    if len(known):
        # neighbouring voxels of the geometry hits, one voxel of 3 x voxel around them
        steps = np.array([(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)], np.float64) * 3 * voxel
        around = np.unique(np.concatenate([voxel_ids(np.asarray(skip_points, np.float64) + s, 3 * voxel) for s in steps]))
    for i, c in enumerate(out["clusters"]):
        c["next_to_geometry_change"] = bool(len(known) and np.isin(voxel_ids(out["points"][out["member"] == i], 3 * voxel), around).any())
    return out


def calm_limit(r, valid, unchanged, score0, voxel, min_voxels=LOOK_MIN_VOXELS):
    """The lowest limit at which the control (the before clip against itself, where nothing
    changed) reports no group at all. Raising the limit only removes cells, so a bisection over
    the control's own scores finds it."""
    over = np.sort(score0[score0 > LOOK_Z])
    if not len(over) or not looks_clusters(r, valid, unchanged, score0, LOOK_Z, voxel, min_voxels)["clusters"]:
        return LOOK_Z
    lo, hi = 0, len(over) - 1  # with the highest score as the limit no cell is left
    while lo < hi:
        mid = (lo + hi) // 2
        if looks_clusters(r, valid, unchanged, score0, float(over[mid]), voxel, min_voxels)["clusters"]:
            lo = mid + 1
        else:
            hi = mid
    return float(over[lo])


def find_looks_different(r, valid, reference, control, after, tol, voxel, skip_points=None, beyond=True):
    """The "looks different" check: see the top of this file. r.feat must be unit length or None.
    Returns (hits like compare() gives them, a summary for the report, the per-cell evidence)."""
    lab = colour_maps(r.rgb)
    found = appearance(r, lab, valid, after, reference, tol, beyond)
    unchanged = appearance(r, lab, valid, control, reference, tol, beyond)
    if len(found["frame"]) < 200 or len(unchanged["frame"]) < 200:
        return None, {"active": False}, None
    score, score0, scales = look_scores(found, unchanged)
    calm = calm_limit(r, valid, unchanged, score0, voxel)
    limit = max(LOOK_Z, LOOK_MARGIN * calm)
    hits = looks_clusters(r, valid, found, score, limit, voxel, skip_points=skip_points)
    info = {"active": True, "cells_compared": int(len(score)), "cells_over_limit": int((score > limit).sum()),
            "limit": round(limit, 2), "control_calm_at": round(calm, 2),
            "colour_scale": round(scales["colour"], 2), "feature_scale": round(scales["feature"], 3)}
    return hits, info, {"found": found, "score": score, "unchanged": unchanged, "score_control": score0, "limit": limit}


# ----------------------------------------------------------------------------- analysis
def analyse(r, n_before, conf_pct=25.0, typical_m=2.0, min_voxels=8, window=WINDOW, cross=True, lookalike=True, reach=None,
            looks_different=True):
    """Find what appeared and what is gone between the first n_before frames and the rest.
    lookalike=False switches the look-alike check off; reach overrides how far it searches.
    looks_different=False switches off the separate report of places that look different."""
    S = len(r.depth)
    if r.feat is not None:  # unit length, so a dot product is a cosine similarity
        unit = r.feat.astype(np.float32)
        r = Recon(r.depth, r.conf, r.K, r.E, r.rgb, unit / (np.linalg.norm(unit, axis=-1, keepdims=True) + 1e-6))
    reference = list(range(0, n_before, 2))  # half of the before clip
    control = list(range(1, n_before, 2))  # the other half: same scene, nothing changed
    after = list(range(n_before, S))
    valid = valid_mask(r, conf_pct)
    near_own = nearest_in_window(r.depth)  # frames of one clip line up to within a few pixels
    near_cross = nearest_in_window(r.depth, window)  # the two clips can be tens of pixels apart
    scale = float(np.median(r.depth[valid]))  # typical viewing distance, in scene units
    voxel = 0.02 * scale
    sigma = measure_noise(r, valid, near_own, control, reference)  # depth noise inside the before clip
    sigma_cross = measure_noise(r, valid, near_own, after, reference) if cross else sigma  # and between the clips
    tol_own = (float(np.clip(3 * sigma, 0.02, 0.08)), float(np.clip(6 * sigma, 0.05, 0.20)))
    worst = max(sigma, sigma_cross)
    tol_agree, tol_free = float(np.clip(3 * worst, 0.02, 0.12)), float(np.clip(6 * worst, 0.05, 0.40))

    # Look-alike check: only between the clips, and only as far as they are misaligned.
    look = None
    if lookalike and r.feat is not None:
        if reach is None:
            reach = min(REACH_PER_SIGMA * float(np.sqrt(max(sigma_cross ** 2 - sigma ** 2, 0.0))), REACH_MAX)
        patch_reach = 1.5 * (r.depth.shape[2] // r.feat.shape[2]) / float(np.median(r.K[:, 0, 0]))  # 1.5 patches, as a fraction of distance
        if reach >= patch_reach:
            typical = same_place_similarity(r, patch_table(r, valid, after), patch_table(r, valid, reference))
            look = (float(reach), float(np.clip(0.7 if typical is None else typical, 0.6, 0.85)))

    def run(query, ref, look=None):
        found = compare(r, valid, near_own, near_cross, query, ref, tol_own, (tol_agree, tol_free), look)
        min_frames = 2 if len(query) >= 3 else 1
        found["clusters"], found["member"] = cluster(found["points"], found["frames"], voxel, min_voxels, min_frames)
        return found

    appeared, gone = run(after, reference, look), run(reference, after, look)
    false_a, false_b = run(control, reference), run(reference, control)

    # Second kind of evidence, reported on its own: the place looks different.
    looks, looks_info, looks_evidence = None, {"active": False}, None
    if looks_different and len(control) >= 2 and len(after) >= 2:
        known = np.concatenate([f["points"][f["member"] >= 0] for f in (appeared, gone)])
        looks, looks_info, looks_evidence = find_looks_different(r, valid, reference, control, after, tol_agree, voxel, known)

    to_cm = 100 * typical_m / scale  # scene units -> cm, if a typical viewing distance is typical_m
    for found in (appeared, gone, false_a, false_b) + ((looks,) if looks else ()):
        for c in found["clusters"]:
            c["size_cm_approx"] = [round(s * to_cm) for s in c["size"]]
    real = appeared["clusters"] + gone["clusters"]
    false = false_a["clusters"] + false_b["clusters"]
    biggest_real = max([c["voxels"] for c in real], default=0)
    biggest_false = max([c["voxels"] for c in false], default=0)
    consistency = false_a["consistency"]
    if consistency < 0.7:
        verdict = "UNSTABLE: the reconstruction disagrees with itself. Move slower, add light, overlap views more."
    elif not real:
        verdict = "NO CHANGE FOUND between the clips."
    elif biggest_real >= 3 * biggest_false:
        verdict = "PASS: changes stand clear of the false alarms."
    else:
        verdict = "UNCLEAR: changes found, but the unchanged control gives false alarms of similar size."
    report = {
        "verdict": verdict,
        "frames": {"reference": len(reference), "control": len(control), "after": len(after)},
        "self_consistency": round(consistency, 3),
        "depth_noise_sigma": round(sigma, 4),
        "depth_noise_between_clips": round(sigma_cross, 4),
        "smallest_detectable_step_cm_at_1m": round(100 * tol_free, 1),
        "thresholds": {"agree": round(tol_agree, 3), "see_through": round(tol_free, 3), "window_px": window,
                       "voxel_cm_approx": round(voxel * to_cm, 1)},
        "lookalike_check": {"active": look is not None, "reach": round(look[0], 3) if look else 0.0,
                            "min_similarity": round(look[1], 3) if look else None,
                            "pixels_dropped": appeared["dropped"] + gone["dropped"]},
        "appeared": appeared["clusters"], "gone": gone["clusters"], "false_alarms_in_control": false,
        # Not part of the verdict: places that look different although the geometry found no change there.
        "looks_different_check": looks_info, "looks_different": looks["clusters"] if looks else [],
    }
    detail = {"valid": valid, "appeared": appeared, "gone": gone, "reference": reference, "after": after,
              "control": control, "voxel": voxel, "tol_agree": tol_agree}
    if looks:
        detail["looks_different"], detail["looks_evidence"] = looks, looks_evidence
    return report, detail


# ----------------------------------------------------------------------------- outputs
def tint(rgb, pixels, colour):
    img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    mask = np.zeros(img.shape[:2], np.uint8)
    mask.reshape(-1)[pixels] = 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    img[mask > 0] = (0.45 * img[mask > 0] + 0.55 * np.array(colour)).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(img, contours, -1, colour, 2)
    return img


def contact_sheet(r, detail, path, per_row=3):
    """Top row: before frames with what is gone in red. Bottom row: after frames with what appeared in green."""
    rows = []
    for found, frames, colour, title in ((detail["gone"], detail["reference"], RED, "BEFORE (red = gone)"),
                                         (detail["appeared"], detail["after"], GREEN, "AFTER (green = appeared)")):
        hits = found["member"] >= 0
        count = {q: int((hits & (found["frames"] == q)).sum()) for q in frames}
        picks = sorted(frames, key=lambda q: -count[q])[:per_row]
        tiles = []
        for q in picks:
            tile = tint(r.rgb[q], found["pixels"][hits & (found["frames"] == q)], colour)
            cv2.putText(tile, f"{title}  frame {q}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(tile, f"{title}  frame {q}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            tiles.append(tile)
        while len(tiles) < per_row:
            tiles.append(np.zeros_like(tiles[0]))
        rows.append(np.hstack(tiles))
    cv2.imwrite(str(path), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])


def write_ply(r, detail, path, max_points=400_000, seed=0):
    """Point cloud of the after clip, with appeared points in green and gone points in red."""
    rng = np.random.default_rng(seed)
    pts, col = [], []
    for q in detail["after"]:
        m = detail["valid"][q]
        pts.append(unproject(r.depth[q], r.K[q], r.E[q])[m])
        col.append(r.rgb[q][m])
    pts, col = np.concatenate(pts), np.concatenate(col)
    if len(pts) > max_points:
        pick = rng.choice(len(pts), max_points, replace=False)
        pts, col = pts[pick], col[pick]
    for found, colour in ((detail["appeared"], GREEN[::-1]), (detail["gone"], RED[::-1])):
        p = found["points"][found["member"] >= 0]
        pts = np.concatenate([pts, p])
        col = np.concatenate([col, np.tile(np.array(colour, np.uint8), (len(p), 1))])
    vertex = np.zeros(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    vertex["x"], vertex["y"], vertex["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
    vertex["r"], vertex["g"], vertex["b"] = col[:, 0], col[:, 1], col[:, 2]
    header = ("ply\nformat binary_little_endian 1.0\n" f"element vertex {len(pts)}\n"
              "property float x\nproperty float y\nproperty float z\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    with open(path, "wb") as f:
        f.write(header.encode())
        f.write(vertex.tobytes())


def print_report(report):
    print("\n" + "=" * 78)
    print(report["verdict"])
    print(f"Self-consistency {report['self_consistency']:.0%} | depth noise {report['depth_noise_sigma']:.1%} | "
          f"smallest step it can see: about {report['smallest_detectable_step_cm_at_1m']} cm at 1 m")
    for name in ("appeared", "gone", "false_alarms_in_control", "looks_different"):
        print(f"{name.replace('_', ' ')}: {len(report.get(name, []))}")
        for c in report.get(name, [])[:6]:
            print(f"   {c['voxels']:4d} voxels, about {'x'.join(map(str, sorted(c['size_cm_approx'], reverse=True)))} cm, "
                  f"seen in {c['frames']} frames")
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", help="video file or folder of images: the room as it was")
    ap.add_argument("--after", help="video file or folder of images: the room now")
    ap.add_argument("--from-recon", help="recon.npz from an earlier --save-recon run: redo the change detection without a GPU")
    ap.add_argument("--out", default="out")
    ap.add_argument("--n-after", type=int, default=12, help="frames from the after clip; the before clip gets twice as many")
    ap.add_argument("--typical-distance-m", type=float, default=2.0, help="rough camera-to-scene distance, only used to print sizes in cm")
    ap.add_argument("--conf-pct", type=float, default=25.0, help="drop this percentage of least-confident pixels")
    ap.add_argument("--save-recon", action="store_true", help="also save depth and cameras to recon.npz")
    ap.add_argument("--no-lookalike", action="store_true", help="switch off the look-alike check (see the top of this file)")
    ap.add_argument("--no-looks-different", action="store_true", help="switch off the report of places that look different")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.from_recon:
        saved = np.load(args.from_recon)
        recon = Recon(*(saved[k] for k in ("depth", "conf", "K", "E", "rgb")), saved["feat"] if "feat" in saved else None)
        n_before = int(saved["n_before"])
    elif args.before and args.after:
        before = sample_frames(args.before, 2 * args.n_after)
        after = sample_frames(args.after, args.n_after)
        print(f"Frames: {len(before)} before, {len(after)} after")
        if len(before) < 6 or len(after) < 3:
            raise SystemExit("Need at least 6 before frames and 3 after frames. Record longer clips.")
        recon, n_before = run_vggt(before + after), len(before)
        if args.save_recon:
            np.savez_compressed(out / "recon.npz", n_before=n_before, **{k: v for k, v in recon.__dict__.items() if v is not None})
    else:
        raise SystemExit("Give --before and --after, or --from-recon.")

    t0 = time.time()
    report, detail = analyse(recon, n_before, args.conf_pct, args.typical_distance_m, lookalike=not args.no_lookalike,
                             looks_different=not args.no_looks_different)
    print(f"Change detection: {time.time() - t0:.1f} s")
    (out / "report.json").write_text(json.dumps(report, indent=2))
    contact_sheet(recon, detail, out / "changes.jpg")
    write_ply(recon, detail, out / "scene.ply")
    print_report(report)
    print(f"Wrote {out / 'changes.jpg'}, {out / 'scene.ply'} and {out / 'report.json'}")


if __name__ == "__main__":
    main()
