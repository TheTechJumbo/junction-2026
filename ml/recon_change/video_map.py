#!/usr/bin/env python3
"""A room as a Gaussian splat from one ordinary video: record normally (the phone or the glasses, no app running), then
this goes through every frame afterwards. Heavy: run it on a GPU with room to spare (iridis/video_map.slurm).

    python video_map.py VIDEO OUT [--keyframes 96] [--change 0.9] [--steps 7000] [--no-train]
    python video_map.py FRAMES_FOLDER OUT ...    (a recording: frames NNNNNN_<ms>.jpg, relay POST /recording)

1. Every frame is read once: its sharpness and a small grey copy. A keyframe is the sharpest of the frames since the
   last keyframe, taken once the view has moved on enough (cosine similarity of the small copies under --change), so
   neighbouring keyframes overlap a lot. At most --keyframes, spread over the whole video.
2. VGGT puts all keyframes together in one pass (cameras and depth), each cropped to its middle square
   (worker.square_up: VGGT reads a tall picture far worse).
3. Points that two other keyframes confirm (same point, same depth within 3%; worker.agreeing) start the splat:
   OUT/untrained.splat, the training-free kind the room map shows.
4. With gsplat installed (iridis: gsplat-env), the splat is trained against the keyframes for --steps steps
   (L1 + D-SSIM, gsplat's default densify and prune): OUT/trained.splat, which looks like the pictures.

Coordinates are VGGT's: the first keyframe's camera at the origin, x right, y down, z ahead (viewer.html ?src=...).
OUT/keyframes/ holds the pictures used, OUT/summary.json the numbers.
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "live")]
import recon_change as rc  # noqa: E402
from worker import agreeing, pick_keyframes, square_up  # noqa: E402

SIDE = 518  # VGGT's input size


def keyframes(source, most, change):
    """[(frame number, picture)] (see worker.pick_keyframes) and how many frames there were. source: a video file, or a
    folder of frames (a recording, relay POST /recording) taken in name order."""
    source = Path(source)
    if source.is_dir():
        paths = sorted(source.glob("*.jpg"))
        frames = ((i, cv2.imread(str(f))) for i, f in enumerate(paths))
        return pick_keyframes(frames, most, change), len(paths)
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise SystemExit(f"Cannot read {source}")
    count = [0]

    def frames():
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield count[0], frame
            count[0] += 1

    picked = pick_keyframes(frames(), most, change)
    cap.release()
    return picked, count[0]


DEPTH_STEP = 4  # the depth of each keyframe is exported at a quarter of its size: 130 x 130 points for 518 x 518


def export_views(r, out, keep):
    """Each keyframe as a surface for the 3D page's playback: views.json (cameras, sizes) and depths.bin (float16,
    keyframe after keyframe, DEPTH_STEP times smaller than the picture, 0 where the depth is not trusted: depth edges and
    pixels no other keyframe confirms). The page lays the keyframe's own photo on its surface, so from where it was taken
    it is exactly as sharp as the photo; between keyframes it fades from one to the next."""
    S, H, W = r.depth.shape
    w, h = (W + DEPTH_STEP - 1) // DEPTH_STEP, (H + DEPTH_STEP - 1) // DEPTH_STEP
    depths = np.zeros((S, h, w), np.float16)
    for q in range(S):
        d = np.where(keep[q], r.depth[q], 0).astype(np.float32)
        depths[q] = cv2.resize(d, (w, h), interpolation=cv2.INTER_NEAREST)
    depths.tofile(out / "depths.bin")
    views = {"size": [W, H], "depth_size": [w, h], "K": np.round(r.K, 4).tolist(), "E": np.round(r.E, 6).tolist(),
             "files": [f"kf{q:03d}.jpg" for q in range(S)], "depths": "depths.bin", "median_depth": float(np.median(r.depth[keep])) if keep.any() else 1.0}
    (out / "views.json").write_text(json.dumps(views))
    return S


def ssim(a, b):
    """Structural similarity of two (1, 3, H, W) pictures, 11 px Gaussian window."""
    import torch
    import torch.nn.functional as F
    g = torch.exp(-((torch.arange(11, device=a.device) - 5.0) ** 2) / (2 * 1.5 ** 2))
    w = (g[:, None] * g[None, :])
    w = (w / w.sum()).expand(3, 1, 11, 11)
    mu_a, mu_b = F.conv2d(a, w, padding=5, groups=3), F.conv2d(b, w, padding=5, groups=3)
    va = F.conv2d(a * a, w, padding=5, groups=3) - mu_a ** 2
    vb = F.conv2d(b * b, w, padding=5, groups=3) - mu_b ** 2
    cov = F.conv2d(a * b, w, padding=5, groups=3) - mu_a * mu_b
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    return (((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))).mean()


def write_splat(path, pos, scale, rgb, alpha, quat):
    """The common .splat format (see splat.py): 32 bytes per Gaussian."""
    rec = np.zeros(len(pos), dtype=[("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    rec["pos"], rec["scale"] = pos, scale
    rec["rgba"] = np.concatenate([np.clip(rgb, 0, 255), np.clip(alpha, 0, 255)[:, None]], 1).astype(np.uint8)
    q = quat / (np.linalg.norm(quat, axis=1, keepdims=True) + 1e-12)
    rec["rot"] = np.clip(q * 128 + 128, 0, 255).astype(np.uint8)
    rec.tofile(path)
    return len(pos)


def start_points(r, keep, most=600_000):
    """The confirmed pixels as 3D points with colour and a size (the pixel's footprint at that depth)."""
    pts, cols, sizes = [], [], []
    S, H, W = r.depth.shape
    v, u = np.mgrid[0:H, 0:W]
    for q in range(S):
        m = keep[q]
        d = r.depth[q][m]
        K = r.K[q]
        cam = np.stack([(u[m] - K[0, 2]) / K[0, 0] * d, (v[m] - K[1, 2]) / K[1, 1] * d, d], 1)
        pts.append((cam - r.E[q][:, 3]) @ r.E[q][:, :3])
        cols.append(r.rgb[q][m])
        sizes.append(d / K[0, 0])
    pts, cols, sizes = np.concatenate(pts), np.concatenate(cols), np.concatenate(sizes)
    if len(pts) > most:
        pick = np.random.default_rng(0).choice(len(pts), most, replace=False)
        pts, cols, sizes = pts[pick], cols[pick], sizes[pick]
    return pts.astype(np.float32), cols.astype(np.float32), sizes.astype(np.float32)


def train(r, pts, cols, sizes, steps, out, log):
    """Optimise the splat against the keyframes with gsplat. Returns (Gaussians, seconds, PSNR on the keyframes)."""
    import torch
    from gsplat import rasterization
    from gsplat.strategy import DefaultStrategy

    dev = "cuda"
    S, H, W = r.depth.shape
    images = torch.tensor(r.rgb, device=dev, dtype=torch.float32) / 255.0  # (S, H, W, 3)
    viewmats = torch.eye(4, device=dev).repeat(S, 1, 1)
    viewmats[:, :3, :] = torch.tensor(r.E, device=dev, dtype=torch.float32)
    Ks = torch.tensor(r.K, device=dev, dtype=torch.float32)
    centres = -torch.einsum("sji,sj->si", viewmats[:, :3, :3], viewmats[:, :3, 3])
    scene_scale = float(torch.linalg.norm(centres - centres.mean(0), dim=1).max().clamp(min=float(np.median(r.depth)))) * 1.1
    n = len(pts)
    params = torch.nn.ParameterDict({
        "means": torch.nn.Parameter(torch.tensor(pts, device=dev)),
        "scales": torch.nn.Parameter(torch.log(torch.tensor(sizes, device=dev)).unsqueeze(1).repeat(1, 3)),
        "quats": torch.nn.Parameter(torch.tensor([[1.0, 0, 0, 0]], device=dev).repeat(n, 1)),
        "opacities": torch.nn.Parameter(torch.logit(torch.full((n,), 0.5, device=dev))),
        "sh0": torch.nn.Parameter(((torch.tensor(cols, device=dev) / 255.0 - 0.5) / 0.28209479).unsqueeze(1)),
    })
    rates = {"means": 1.6e-4 * scene_scale, "scales": 5e-3, "quats": 1e-3, "opacities": 5e-2, "sh0": 2.5e-3}
    optimizers = {k: torch.optim.Adam([{"params": params[k], "lr": rates[k], "name": k}], eps=1e-15) for k in params}
    strategy = DefaultStrategy(refine_start_iter=500, refine_stop_iter=int(steps * 0.7), reset_every=3000, refine_every=100,
                               verbose=False)
    strategy.check_sanity(params, optimizers)
    state = strategy.initialize_state(scene_scale=scene_scale)
    decay = 0.01 ** (1 / max(1, steps))  # the positions' learning rate falls to 1% over the run
    rng = np.random.default_rng(0)
    t0 = time.time()
    for step in range(steps):
        i = int(rng.integers(S))
        render, _, info = rasterization(params["means"], params["quats"], torch.exp(params["scales"]), torch.sigmoid(params["opacities"]),
                                        params["sh0"], viewmats[i:i + 1], Ks[i:i + 1], W, H, sh_degree=0, packed=False)
        strategy.step_pre_backward(params, optimizers, state, step, info)
        pred, true = render[0].permute(2, 0, 1)[None], images[i].permute(2, 0, 1)[None]
        loss = 0.8 * (pred - true).abs().mean() + 0.2 * (1 - ssim(pred, true))
        loss.backward()
        strategy.step_post_backward(params, optimizers, state, step, info, packed=False)
        for opt in optimizers.values():
            opt.step()
            opt.zero_grad(set_to_none=True)
        for group in optimizers["means"].param_groups:
            group["lr"] *= decay
        if step % 1000 == 0 or step == steps - 1:
            log(f"   step {step}: loss {float(loss):.4f}, {len(params['means'])} Gaussians, {time.time() - t0:.0f} s")
    with torch.no_grad():  # how close the trained splat comes to the keyframes
        def draw(view, K):
            render, _, _ = rasterization(params["means"], params["quats"], torch.exp(params["scales"]), torch.sigmoid(params["opacities"]),
                                         params["sh0"], view[None], K[None], W, H, sh_degree=0, packed=False)
            return render[0].clamp(0, 1)

        psnr = []
        for i in range(S):
            mse = float(((draw(viewmats[i], Ks[i]) - images[i]) ** 2).mean())
            psnr.append(10 * math.log10(1 / max(mse, 1e-10)))
        # For a person to judge: the splat drawn from three keyframes next to the photo, and from halfway between two
        # keyframes (no photo was taken there), in OUT/renders/.
        (out / "renders").mkdir(exist_ok=True)
        for i in sorted({0, S // 2, S - 1}):
            pair = torch.cat([draw(viewmats[i], Ks[i]), images[i]], 1).cpu().numpy()
            cv2.imwrite(str(out / "renders" / f"keyframe{i:03d}_splat_vs_photo.jpg"), cv2.cvtColor((pair * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
        if S > 1:
            a, b = S // 2 - 1 if S > 2 else 0, S // 2
            mid = viewmats[a].clone()
            mid[:3, 3] = (viewmats[a][:3, 3] + viewmats[b][:3, 3]) / 2
            u, _, vt = torch.linalg.svd(viewmats[a][:3, :3] + viewmats[b][:3, :3])  # the rotation halfway between
            mid[:3, :3] = u @ vt
            between = torch.cat([images[a], draw(mid, Ks[a]), images[b]], 1).cpu().numpy()
            cv2.imwrite(str(out / "renders" / f"between_{a:03d}_{b:03d}.jpg"), cv2.cvtColor((between * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
        rgb = (0.5 + 0.28209479 * params["sh0"][:, 0]).clamp(0, 1).cpu().numpy() * 255
        count = write_splat(out / "trained.splat", params["means"].cpu().numpy(), torch.exp(params["scales"]).cpu().numpy(), rgb,
                            torch.sigmoid(params["opacities"]).cpu().numpy() * 255, params["quats"].cpu().numpy())
    return count, round(time.time() - t0, 1), round(float(np.median(psnr)), 2)


def _quat_to_matrix(q):
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def _slerp(q0, q1, t):
    if q0 @ q1 < 0:
        q1 = -q1
    d = min(1.0, float(q0 @ q1))
    if d > 0.9995:
        q = q0 + t * (q1 - q0)
    else:
        a = math.acos(d)
        q = (math.sin((1 - t) * a) * q0 + math.sin(t * a) * q1) / math.sin(a)
    return q / np.linalg.norm(q)


def flythrough(out, between=6, fps=24, log=print):
    """The trained splat seen along the recorded path, as OUT/flythrough.mp4: from each keyframe's camera to the next
    in `between` steps (positions moved evenly, rotations turned evenly). Returns the number of frames written."""
    import splat as splat_file
    import torch
    from gsplat import rasterization

    out = Path(out)
    rec = np.fromfile(out / "trained.splat", dtype=[("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    cams = np.load(out / "cameras.npz")
    E, K = cams["E"], cams["K"]
    dev = "cuda"
    means, scales = torch.tensor(rec["pos"], device=dev), torch.tensor(rec["scale"], device=dev)
    quats = torch.tensor((rec["rot"].astype(np.float32) - 128) / 128, device=dev)
    quats = quats / quats.norm(dim=1, keepdim=True).clamp(min=1e-8)
    opacities = torch.tensor(rec["rgba"][:, 3].astype(np.float32) / 255, device=dev)
    colors = torch.tensor(rec["rgba"][:, :3].astype(np.float32) / 255, device=dev)
    side = int(round(2 * K[0][0, 2]))
    Kt = torch.tensor(K[0], device=dev, dtype=torch.float32)[None]
    rots = [splat_file.quaternion(e[:, :3]) for e in E]
    centres = [-e[:, :3].T @ e[:, 3] for e in E]
    poses = []
    for i in range(len(E) - 1):
        for s in range(between):
            t = s / between
            R = _quat_to_matrix(_slerp(rots[i], rots[i + 1], t))
            c = (1 - t) * centres[i] + t * centres[i + 1]
            poses.append((R, -R @ c))
    poses.append((E[-1][:, :3], E[-1][:, 3]))
    path = out / "flythrough.mp4"
    writer = None
    for code in ("avc1", "mp4v"):  # H.264 plays everywhere; OpenCV builds without it fall back to MPEG-4
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*code), fps, (side, side))
        if writer.isOpened():
            break
    with torch.no_grad():
        for R, tvec in poses:
            view = torch.eye(4, device=dev)
            view[:3, :3] = torch.tensor(R, device=dev, dtype=torch.float32)
            view[:3, 3] = torch.tensor(tvec, device=dev, dtype=torch.float32)
            render, _, _ = rasterization(means, quats, scales, opacities, colors, view[None], Kt, side, side, packed=False)
            frame = (render[0].clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)
            writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    writer.release()
    log(f"fly-through: {len(poses)} frames ({len(poses) / fps:.0f} s) along {len(E)} keyframes in {path}")
    return len(poses)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--flythrough-only", metavar="OUT", help="only draw OUT/flythrough.mp4 from OUT/trained.splat and cameras.npz")
    ap.add_argument("--keyframes", type=int, default=96, help="the most keyframes in the one VGGT pass (96 fit an L4's 24 GB)")
    ap.add_argument("--change", type=float, default=0.9, help="a new keyframe once the view is less alike than this")
    ap.add_argument("--steps", type=int, default=7000)
    ap.add_argument("--no-train", action="store_true")
    args = ap.parse_args()
    if args.flythrough_only:
        flythrough(args.flythrough_only)
        return
    if not args.video or not args.out:
        ap.error("give VIDEO OUT (or --flythrough-only OUT)")
    out = Path(args.out)
    (out / "keyframes").mkdir(parents=True, exist_ok=True)
    summary = {"video": Path(args.video).name}

    def log(text):
        print(text, flush=True)

    t0 = time.time()
    picked, total = keyframes(args.video, args.keyframes, args.change)
    frames = [cv2.resize(square_up(f), (SIDE, SIDE), interpolation=cv2.INTER_AREA) for _, f in picked]
    for k, f in enumerate(frames):
        cv2.imwrite(str(out / "keyframes" / f"{k:03d}.jpg"), f, [cv2.IMWRITE_JPEG_QUALITY, 92])
    summary.update(frames=total, keyframes=len(frames), keyframe_numbers=[n for n, _ in picked], select_seconds=round(time.time() - t0, 1))
    log(f"{total} frames read, {len(frames)} keyframes in {summary['select_seconds']} s")
    if len(frames) < 3:
        raise SystemExit("Too few keyframes: record longer, and move.")

    import torch
    model = rc.load_model(device="cuda")
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    r = rc.reconstruct(model, frames, long_side=SIDE)
    summary.update(vggt_seconds=round(time.time() - t0, 1), vggt_peak_gb=round(torch.cuda.max_memory_allocated() / 1e9, 2),
                   fov_deg=round(math.degrees(2 * math.atan(r.depth.shape[1] / 2 / float(r.K[0][1, 1]))), 1))  # vertical, keyframe 0
    del model
    torch.cuda.empty_cache()
    np.savez(out / "cameras.npz", K=r.K, E=r.E, keyframes=np.array([n for n, _ in picked]))  # world to camera, as VGGT gives them
    valid = rc.valid_mask(r, 35.0)
    agree = agreeing(r, valid)
    keep = valid & (agree >= 2)
    if keep.mean() < 0.03:
        keep = valid & (agree >= 1)
    pts, cols, sizes = start_points(r, keep)
    # the page's playback surfaces: depth edges out, but no need for two other keyframes to confirm a pixel of its own photo
    summary["views"] = export_views(r, out, valid & (agree >= 1) if (valid & (agree >= 1)).mean() > 0.05 else valid)
    quat = np.tile([1.0, 0, 0, 0], (len(pts), 1))
    summary["untrained_gaussians"] = write_splat(out / "untrained.splat", pts, np.repeat(sizes[:, None] * 0.75, 3, 1), cols,
                                                 np.full(len(pts), 255.0), quat)
    summary.update(kept_share=round(float(keep.mean()), 3), median_depth=round(float(np.median(r.depth[keep])), 3) if keep.any() else None)
    log(f"VGGT {summary['vggt_seconds']} s, {summary['vggt_peak_gb']} GB; {100 * keep.mean():.0f}% of pixels confirmed; "
        f"untrained splat {summary['untrained_gaussians']} Gaussians")
    if not args.no_train:
        try:
            summary["trained_gaussians"], summary["train_seconds"], summary["psnr_median"] = train(r, pts, cols, sizes, args.steps, out, log)
            log(f"trained splat: {summary['trained_gaussians']} Gaussians in {summary['train_seconds']} s, PSNR {summary['psnr_median']} dB")
            try:
                summary["flythrough_frames"] = flythrough(out, log=log)
            except Exception as err:  # noqa: BLE001  the splat itself is done
                log(f"no fly-through: {type(err).__name__}: {err}")
        except ImportError as err:
            summary["trained"] = f"not trained: {err} (gsplat is not installed here)"
            log(summary["trained"])
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    log("done")


if __name__ == "__main__":
    main()
