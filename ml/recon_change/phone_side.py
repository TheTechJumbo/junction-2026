#!/usr/bin/env python3
"""Position fix in two halves: a map made once on a server, a small model per frame on the phone.

Today (locate.py) every fix runs VGGT over the baseline photos plus the wearer's frame. Here the
big model runs ONCE per room, over the baseline photos alone, and what it found is kept as a map:

    build_map()      server, once per room: per baseline photo one global descriptor (which photos
                     look like this view?) and local features with the 3D point behind each one
                     (from that photo's depth and camera). save_map() / load_map(): one .npz file.
    locate_frame()   phone, per frame: features of the new frame (a small model, see the
                     extractors), the closest baseline photos, matches, then the camera pose from
                     the 2D points in the frame and the 3D points of the map (PnP with RANSAC), and
                     found / unsure / lost from how many matches agree with that pose times how
                     much of the picture they cover.

Extractors (each turns a BGR picture into Features):

    DinoExtractor    DINOv2 ViT-S/14 or ViT-B/14: one feature per 14 px patch, the class token as
                     the global descriptor. "Just a ViT."
    XFeatExtractor   XFeat (0.66 M parameters, Apache-2.0): keypoints with 64-number descriptors.
    SiftExtractor    SIFT from OpenCV: no network at all, as a reference.

The pose comes back in the map's own 3D frame (VGGT's frame of the baseline pass, no real metres).
Measured in eval/PHONE_SIDE_RESULTS.md. No web code here, and the per-frame step needs only NumPy
and OpenCV besides the extractor's model.
"""
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

EDGE = 0.04  # as recon_change.EDGE: neighbouring pixels whose depth differs by more than this share are an edge
CELLS = 4  # spread: the picture is cut into CELLS x CELLS fields, and the share of fields holding an agreeing match is counted


@dataclass
class Features:
    """What an extractor sees in one picture."""

    size: tuple  # (height, width) of the picture
    xy: np.ndarray  # (N, 2) float32: where each feature is, in pixels of the picture (x, y)
    desc: np.ndarray  # (N, C) float32, each of length 1
    glob: np.ndarray = None  # (D,) float32 of length 1: the whole picture in one vector; None = the extractor has none
    grid: tuple = None  # (rows, columns) when the features are one per patch, row by row; None for keypoints
    reach: float = 1.0  # half the width of what one feature covers, in pixels of the picture


@dataclass
class RoomMap:
    """The map of one room, as the phone holds it."""

    kind: str  # which extractor made the features
    K: np.ndarray  # (3, 3) intrinsics of the baseline photos (their median), for a picture of `size`
    size: tuple  # (height, width) K refers to
    cams: np.ndarray  # (S, 3, 4) world -> camera of every baseline photo
    start: np.ndarray  # (S + 1,) features start[s] .. start[s + 1] belong to photo s
    xyz: np.ndarray  # (M, 3) float32: the 3D point behind each feature, in the map's frame
    desc: np.ndarray  # (M, C) float32 of length 1
    glob: np.ndarray = None  # (S, D) one global descriptor per photo, or None
    mean: np.ndarray = None  # (C0,) and
    basis: np.ndarray = None  # (C0, C): features were reduced with (f - mean) @ basis; None = stored as they came


@dataclass
class Limits:
    """The score of a fix is the number of matches that agree with the pose times the share of
    the picture's fields they cover (a few hundred agreeing matches in one corner are how a wrong
    room looks). found at a score of at least `found`, unsure at least `unsure`, else lost.
    reach (0 = not checked): the camera may be at most this many times the baseline cameras' own
    spread away from their middle; a pose far outside the filmed room is not believed.
    Chosen in eval/PHONE_SIDE_RESULTS.md, each on two rooms and tested on the third."""

    found: float = 30.0
    unsure: float = 15.0
    reach: float = 0.0


def unit(x, axis=-1):
    return x / (np.linalg.norm(x, axis=axis, keepdims=True) + 1e-9)


def fit_size(h, w, long_side, patch=14):
    """Model input size: long side as asked, both sides multiples of the patch (as recon_change.fit_size)."""
    scale = long_side / max(h, w)
    return max(patch, round(h * scale / patch) * patch), max(patch, round(w * scale / patch) * patch)


# ----------------------------------------------------------------------------- extractors
class DinoExtractor:
    """DINOv2 through Hugging Face transformers. model_dir: a folder with config.json and
    model.safetensors (or a hub name). precision: "float32", "int8" (PyTorch dynamic quantisation
    of the linear layers) or "bf16" (bfloat16 autocast)."""

    def __init__(self, model_dir, long_side=518, precision="float32"):
        import torch
        from transformers import AutoModel

        self.torch = torch
        self.long_side, self.precision = long_side, precision
        self.model = AutoModel.from_pretrained(str(model_dir)).eval()
        self.patch = int(self.model.config.patch_size)
        if precision == "int8":
            self.model = torch.ao.quantization.quantize_dynamic(self.model, {torch.nn.Linear}, dtype=torch.qint8)
        self.mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        self.name = f"dino{self.model.config.hidden_size}_{long_side}" + ("" if precision == "float32" else f"_{precision}")

    def extract(self, bgr):
        torch = self.torch
        h, w = bgr.shape[:2]
        H, W = fit_size(h, w, self.long_side, self.patch)
        rgb = cv2.cvtColor(cv2.resize(bgr, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        x = ((torch.from_numpy(rgb).permute(2, 0, 1).float().div(255) - self.mean) / self.std)[None]
        with torch.inference_mode():
            if self.precision == "bf16":
                with torch.autocast("cpu", dtype=torch.bfloat16):
                    out = self.model(pixel_values=x).last_hidden_state[0].float()
            else:
                out = self.model(pixel_values=x).last_hidden_state[0]
        out = out.numpy()
        rows, cols = H // self.patch, W // self.patch
        v, u = np.mgrid[0:rows, 0:cols]
        xy = np.stack([(u + 0.5) * self.patch * w / W, (v + 0.5) * self.patch * h / H], -1).reshape(-1, 2).astype(np.float32)
        return Features((h, w), xy, unit(out[1:]).astype(np.float32), unit(out[0]).astype(np.float32), (rows, cols),
                        0.5 * self.patch * w / W)


class XFeatExtractor:
    """XFeat (github.com/verlab/accelerated_features, Apache-2.0). repo: the cloned repository,
    which holds the weights. Its global descriptor is only the mean of its keypoint descriptors."""

    def __init__(self, repo, top_k=2048, long_side=0):
        import torch

        self.torch = torch
        sys.path.insert(0, str(repo))
        from modules.xfeat import XFeat

        self.model = XFeat(weights=str(Path(repo) / "weights" / "xfeat.pt"), top_k=top_k)
        self.model.dev = torch.device("cpu")
        self.model.net.to("cpu")
        self.top_k, self.long_side = top_k, long_side
        self.name = f"xfeat{top_k}" + (f"_{long_side}" if long_side else "")

    def extract(self, bgr):
        h, w = bgr.shape[:2]
        img = bgr
        if self.long_side and self.long_side != max(h, w):
            s = self.long_side / max(h, w)
            img = cv2.resize(bgr, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        x = self.torch.from_numpy(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).permute(2, 0, 1)[None].float()
        out = self.model.detectAndCompute(x, top_k=self.top_k)[0]
        xy = out["keypoints"].numpy().astype(np.float32) * np.float32([w / img.shape[1], h / img.shape[0]])
        desc = out["descriptors"].numpy().astype(np.float32)
        return Features((h, w), xy, desc, unit(desc.mean(0)).astype(np.float32) if len(desc) else None)


class SiftExtractor:
    """SIFT keypoints from OpenCV with RootSIFT descriptors. No network and no global descriptor."""

    def __init__(self, top_k=2048):
        self.sift = cv2.SIFT_create(nfeatures=top_k)
        self.name = f"sift{top_k}"

    def extract(self, bgr):
        h, w = bgr.shape[:2]
        points, desc = self.sift.detectAndCompute(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), None)
        if desc is None:
            return Features((h, w), np.zeros((0, 2), np.float32), np.zeros((0, 128), np.float32))
        desc = np.sqrt(desc / (desc.sum(1, keepdims=True) + 1e-9))
        return Features((h, w), np.float32([p.pt for p in points]), unit(desc).astype(np.float32))


# ----------------------------------------------------------------------------- the map (server)
def valid_mask(depth, conf, conf_pct=25.0):
    """Pixels worth trusting: confident, and not on a depth edge (as recon_change.valid_mask)."""
    d = depth
    jx = np.abs(d[:, :, 1:] - d[:, :, :-1]) > EDGE * np.minimum(d[:, :, 1:], d[:, :, :-1])
    jy = np.abs(d[:, 1:] - d[:, :-1]) > EDGE * np.minimum(d[:, 1:], d[:, :-1])
    edge = np.zeros(d.shape, bool)
    edge[:, :, 1:] |= jx
    edge[:, :, :-1] |= jx
    edge[:, 1:] |= jy
    edge[:, :-1] |= jy
    kernel = np.ones((3, 3), np.uint8)
    edge = np.stack([cv2.dilate(e.astype(np.uint8), kernel) > 0 for e in edge])
    return (conf > np.percentile(conf, conf_pct)) & (d > 0) & ~edge


def lift(features, depth, valid, K, E, min_share=0.6):
    """The 3D point behind each feature of one baseline photo. depth, valid: (H, W) of the
    reconstruction; K (3, 3) and E (3, 4, world -> camera) its camera. A feature is kept when at
    least min_share of the pixels it covers are trusted; its depth is their median.
    Returns (indices of the kept features, their (n, 3) points in the map's frame)."""
    H, W = depth.shape
    h, w = features.size
    sx, sy = W / w, H / h
    r = max(1, int(round(features.reach * sx)))
    keep, cam = [], []
    for i, (x, y) in enumerate(features.xy):
        u, v = x * sx, y * sy  # the feature in pixels of the depth picture
        a, b, c, d = int(round(v)) - r, int(round(v)) + r + 1, int(round(u)) - r, int(round(u)) + r + 1
        if a < 0 or c < 0 or b > H or d > W:
            if r > 1:  # a patch at the picture's edge: use the part inside
                a, b, c, d = max(a, 0), min(b, H), max(c, 0), min(d, W)
            else:
                continue
        ok = valid[a:b, c:d]
        if ok.mean() < min_share:
            continue
        z = float(np.median(depth[a:b, c:d][ok]))
        keep.append(i)
        cam.append(((u - K[0, 2]) / K[0, 0] * z, (v - K[1, 2]) / K[1, 1] * z, z))
    cam = np.asarray(cam, np.float64).reshape(-1, 3)
    return np.asarray(keep, np.int64), ((cam - E[:, 3]) @ E[:, :3]).astype(np.float32)


def build_map(features, depth, conf, K, E, kind="", conf_pct=25.0, dim=0):
    """features: one Features per baseline photo (extractor.extract of each photo, in the order of
    the reconstruction). depth, conf (S, H, W), K (S, 3, 3), E (S, 3, 4): the reconstruction of the
    baseline photos (recon_change.reconstruct). dim: reduce the local features to this many numbers
    with the main directions of this room's own features (0 = keep them as they are)."""
    valid = valid_mask(depth, conf, conf_pct)
    xyz, desc, start = [], [], [0]
    for s, f in enumerate(features):
        keep, points = lift(f, depth[s], valid[s], K[s], E[s])
        xyz.append(points)
        desc.append(f.desc[keep])
        start.append(start[-1] + len(keep))
    xyz, desc = np.concatenate(xyz), np.concatenate(desc).astype(np.float32)
    mean = basis = None
    if dim and dim < desc.shape[1] and len(desc) > dim:
        mean = desc.mean(0)
        basis = np.linalg.svd(desc - mean, full_matrices=False)[2][:dim].T.astype(np.float32)
        desc = unit((desc - mean) @ basis).astype(np.float32)
    glob = None if any(f.glob is None for f in features) else np.stack([f.glob for f in features]).astype(np.float32)
    Kmap = np.median(np.asarray(K, np.float64), 0)
    return RoomMap(kind, Kmap, tuple(int(v) for v in depth.shape[1:]), np.asarray(E, np.float64), np.asarray(start, np.int64),
                   xyz, desc, glob, mean, basis)


def save_map(path, m):
    """One compressed file: descriptors as 16-bit floats, points as 32-bit floats."""
    extra = {k: v for k, v in (("glob", m.glob), ("mean", m.mean), ("basis", m.basis)) if v is not None}
    np.savez_compressed(path, kind=m.kind, K=m.K, size=np.asarray(m.size), cams=m.cams.astype(np.float32), start=m.start,
                        xyz=m.xyz.astype(np.float32), desc=m.desc.astype(np.float16),
                        **{k: v.astype(np.float16 if k == "glob" else np.float32) for k, v in extra.items()})


def load_map(path):
    z = np.load(path)
    get = lambda k: z[k].astype(np.float32) if k in z.files else None  # noqa: E731
    return RoomMap(str(z["kind"]), z["K"].astype(np.float64), tuple(int(v) for v in z["size"]), z["cams"].astype(np.float64),
                   z["start"], z["xyz"], unit(z["desc"].astype(np.float32)), get("glob"), get("mean"), get("basis"))


# ----------------------------------------------------------------------------- per frame (phone)
def nearest_photos(m, glob, k):
    """The k baseline photos whose global descriptor is closest to the frame's. All photos when k
    is 0, or when the frame or the map has no global descriptor. Returns (photos, best similarity)."""
    S = len(m.start) - 1
    if glob is None or m.glob is None:
        return list(range(S)), None
    sims = m.glob @ glob
    order = np.argsort(-sims)
    return [int(s) for s in (order if not k or k >= S else order[:k])], float(sims[order[0]])


def match(m, f, photos, min_sim=0.0, ratio=1.0, refine=False, temperature=0.03):
    """Match the frame's features with the features of each given baseline photo: a pair counts
    when each is the other's closest (mutual nearest neighbour), their cosine similarity is at
    least min_sim, and (ratio < 1) the closest is clearly closer than the second closest.
    refine: for one-per-patch features, place the 2D point between the patch and its neighbours,
    weighted by how alike each is to the map feature, instead of at the patch centre.
    Returns (xy (n, 2) in the frame's pixels, xyz (n, 3) in the map's frame, similarity (n,))."""
    q = f.desc
    if m.basis is not None:
        q = unit((q - m.mean) @ m.basis).astype(np.float32)
    out_xy, out_xyz, out_sim = [], [], []
    for s in photos:
        a, b = int(m.start[s]), int(m.start[s + 1])
        if b - a < 2 or len(q) < 2:
            continue
        sim = q @ m.desc[a:b].T
        to_map = sim.argmax(1)
        qi = np.flatnonzero(sim.argmax(0)[to_map] == np.arange(len(q)))
        mi = to_map[qi]
        best = sim[qi, mi]
        keep = best >= min_sim
        if ratio < 1.0 and len(qi):
            second = np.partition(sim[qi], -2, axis=1)[:, -2]
            keep &= np.sqrt(np.maximum(2 - 2 * best, 0)) <= ratio * np.sqrt(np.maximum(2 - 2 * second, 0))
        qi, mi, best = qi[keep], mi[keep], best[keep]
        xy = f.xy[qi]
        if refine and f.grid is not None and len(qi):
            rows, cols = f.grid
            r, c = qi // cols, qi % cols
            weight, total = np.zeros(len(qi), np.float32), np.zeros((len(qi), 2), np.float32)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    ok = (rr >= 0) & (rr < rows) & (cc >= 0) & (cc < cols)
                    n = np.where(ok, rr * cols + cc, qi)
                    wgt = np.where(ok, np.exp((sim[n, mi] - best) / temperature), 0.0).astype(np.float32)
                    weight += wgt
                    total += wgt[:, None] * f.xy[n]
            xy = total / weight[:, None]
        out_xy.append(xy)
        out_xyz.append(m.xyz[a:b][mi])
        out_sim.append(best)
    if not out_xy:
        return np.zeros((0, 2), np.float32), np.zeros((0, 3), np.float32), np.zeros(0, np.float32)
    return np.concatenate(out_xy), np.concatenate(out_xyz), np.concatenate(out_sim)


def reprojection(xyz, xy, K, R, t):
    """Pixels between each 2D point and where its 3D point lands with this camera; and its depth."""
    cam = xyz @ R.T + t
    z = cam[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u, v = K[0, 0] * cam[:, 0] / z + K[0, 2], K[1, 1] * cam[:, 1] / z + K[1, 2]
    err = np.hypot(u - xy[:, 0], v - xy[:, 1])
    return np.where(z > 1e-6, err, np.inf), z


def solve_pose(xyz, xy, K, reproj=8.0, iters=2000, seed=0):
    """The camera from 2D-3D pairs: RANSAC over three-point poses, then a least-squares polish on
    the pairs that agree. reproj: how many pixels a pair may be off and still agree.
    Returns (R, t, agreeing mask) with x_cam = R @ x_map + t, or None."""
    if len(xyz) < 6:
        return None
    xyz, xy, K = np.ascontiguousarray(xyz, np.float64), np.ascontiguousarray(xy, np.float64), np.asarray(K, np.float64)
    cv2.setRNGSeed(seed)
    try:
        ok, rvec, tvec, _ = cv2.solvePnPRansac(xyz, xy, K, None, iterationsCount=iters, reprojectionError=float(reproj),
                                               confidence=0.9999, flags=cv2.SOLVEPNP_AP3P)
    except cv2.error:
        return None
    if not ok:
        return None
    inliers = None
    for _ in range(2):  # polish on the agreeing pairs, count again, polish again
        R = cv2.Rodrigues(rvec)[0]
        err, _ = reprojection(xyz, xy, K, R, tvec.ravel())
        inliers = err < reproj
        if inliers.sum() < 6:
            return None
        rvec, tvec = cv2.solvePnPRefineLM(xyz[inliers], xy[inliers], K, None, rvec, tvec)
    R = cv2.Rodrigues(rvec)[0]
    err, _ = reprojection(xyz, xy, K, R, tvec.ravel())
    return R, tvec.ravel(), err < reproj


def spread_of(xy, size, cells=CELLS):
    """Share of the picture's cells x cells fields that hold at least one of these points."""
    if not len(xy):
        return 0.0
    h, w = size
    cu = np.clip((xy[:, 0] / w * cells).astype(int), 0, cells - 1)
    cv = np.clip((xy[:, 1] / h * cells).astype(int), 0, cells - 1)
    return len(set(zip(cu.tolist(), cv.tolist()))) / float(cells * cells)


def decide(inliers, spread, limits, reach=0.0):
    score = inliers * spread
    if limits.reach and reach > limits.reach:
        return "lost"
    return "found" if score >= limits.found else "unsure" if score >= limits.unsure else "lost"


def reach_of(m, position):
    """How far a camera is from the middle of the baseline cameras, in units of their own spread
    (the root-mean-square distance of the baseline cameras from their middle)."""
    centres = np.array([-E[:, :3].T @ E[:, 3] for E in m.cams])
    middle = centres.mean(0)
    return float(np.linalg.norm(position - middle) / (np.sqrt(((centres - middle) ** 2).sum(1).mean()) + 1e-9))


def frame_K(m, size):
    """The map's intrinsics for a picture of another size (the same camera, resized)."""
    K = m.K.copy()
    K[0] *= size[1] / m.size[1]
    K[1] *= size[0] / m.size[0]
    return K


def pose_frame(m, f, xy, xyz, K=None, reproj=8.0, limits=None, iters=2000):
    """The second half of a fix: the pose from the matches of match(), and how sure it is."""
    limits = limits or Limits()
    pose = solve_pose(xyz, xy, frame_K(m, f.size) if K is None else K, reproj, iters)
    out = {"state": "lost", "inliers": 0, "matches": int(len(xy)), "spread": 0.0, "score": 0.0, "reach": None, "position": None, "forward": None,
           "R": None, "t": None}
    if pose is not None:
        R, t, agree = pose
        out.update(inliers=int(agree.sum()), spread=spread_of(xy[agree], f.size), position=-R.T @ t, forward=R[2].copy(), R=R, t=t)
        out.update(score=out["inliers"] * out["spread"], reach=reach_of(m, out["position"]))
        out["state"] = decide(out["inliers"], out["spread"], limits, out["reach"])
    return out


def locate_frame(m, f, k=3, K=None, reproj=8.0, min_sim=0.0, ratio=1.0, refine=False, limits=None, iters=2000):
    """One position fix on the phone. m: the room's map; f: Features of the new frame.
    k: how many of the closest baseline photos to match against (0 = all). K: the frame's
    intrinsics (None = the map's, scaled to the frame: the same camera filmed the baseline).
    Returns a dict: state, inliers (matches that agree with the pose), matches, spread, score
    (inliers x spread), reach (see reach_of), position (the camera in the map's frame) and forward
    (its viewing direction), R and t, the photos used, best_sim (global similarity to the closest
    photo), seconds for matching and for the pose."""
    t0 = time.perf_counter()
    photos, best_sim = nearest_photos(m, f.glob, k)
    xy, xyz, _ = match(m, f, photos, min_sim, ratio, refine)
    t1 = time.perf_counter()
    out = pose_frame(m, f, xy, xyz, K, reproj, limits, iters)
    out.update(photos=photos, best_sim=best_sim, seconds={"match": t1 - t0, "pose": time.perf_counter() - t1})
    return out
