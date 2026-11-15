#!/usr/bin/env python3
"""Do VGGT and gsplat still work when the frames are garbled with a key (live/garble.py, PRIVACY.md)? On a GPU (the cluster:
cluster/privacy_eval.slurm), on a recording that is already there; nothing new is recorded.

    python eval/privacy_eval.py FRAMES_OR_VIDEO OUT [--keyframes 64] [--fixes 10] [--steps 7000]
                                [--variants plain,tone,garbled,strong] [--train plain,garbled]

For every variant the same keyframes (picked from the plain frames, as video_map.py picks them) go through:
  plain    as recorded
  tone     tone curves, channel order and reversals only (Garbler with no coupling layers)
  garbled  the default Garbler (4 keyed coupling layers): what the trusted side would send
  strong   4 layers with stronger mixing (mix 0.8, frequencies 1.5)
  mild     the default without reversed channels and with the channels in order
Each garbled variant is sent as a JPEG (quality 95, 4:4:4), as the relay would send it, and read back.

1. VGGT on all keyframes in one pass. Against the plain pass: camera rotations between every pair of keyframes (degrees),
   camera centres after the best similarity transform (as a share of the path's size), depth after one scale (AbsRel and
   the share within 25%), and the share of pixels two other keyframes confirm (what starts the splat).
2. Position fixes as the worker makes them (locate.locate): 12 keyframes as the room's pictures, each of up to --fixes
   other keyframes as the wearer's view. found / unsure / lost per variant, and how far each fix is from the plain one.
3. Splats (gsplat, the training of video_map.py) on 7 of every 8 keyframes; every 8th is held out. A garbled splat's
   colours are decoded per Gaussian with the key, as the trusted side would decode trained.splat, then drawn and
   compared with the plain keyframes (PSNR, held-out and trained views).
OUT/summary.json has the numbers; OUT/renders/ a few pictures to look at (they stay with the frames, on the cluster).
Measurement only: the key is made here from --seed. In use, the key never leaves the trusted side (PRIVACY.md).
"""
import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "live")]
import locate  # noqa: E402
import recon_change as rc  # noqa: E402
import video_map  # noqa: E402
from garble import Garbler, encode_jpeg  # noqa: E402
from worker import agreeing, square_up  # noqa: E402

SIDE = video_map.SIDE
VARIANTS = {"plain": None, "tone": {"layers": 0}, "garbled": {}, "strong": {"mix": 0.8, "freq": 1.5},
            "mild": {"flips": False, "permute": False}}
N_BASE = 12  # the worker's N_LOCATE_BASE


def sent(g, frame):
    """What reaches the GPU after the trusted side garbled the frame and sent it as a JPEG."""
    return cv2.imdecode(np.frombuffer(encode_jpeg(g.garble_bgr(frame)), np.uint8), cv2.IMREAD_COLOR)


def umeyama(src, dst):
    """s, R, t with dst ~ s R src + t (least squares)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    U, D, Vt = np.linalg.svd(xd.T @ xs / len(src))
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    s = float(np.trace(np.diag(D) @ S) / max((xs ** 2).sum() / len(src), 1e-12))
    return s, R, mu_d - s * R @ mu_s


def centres(E):
    return np.stack([-e[:, :3].T @ e[:, 3] for e in E])


def angle(R):
    return math.degrees(math.acos(float(np.clip((np.trace(R) - 1) / 2, -1, 1))))


def compare(ref, r, ref_keep, keep):
    """How far pass r is from the plain pass ref: cameras and depth."""
    S = len(r.E)
    rot = [angle((ref.E[i][:, :3] @ ref.E[j][:, :3].T).T @ (r.E[i][:, :3] @ r.E[j][:, :3].T)) for i in range(S) for j in range(i + 1, S)]
    c_ref, c = centres(ref.E), centres(r.E)
    s, R, t = umeyama(c, c_ref)
    size = float(np.linalg.norm(c_ref - c_ref.mean(0), axis=1).max()) or 1.0
    ate = float(np.sqrt(((c @ R.T * s + t - c_ref) ** 2).sum(1).mean()))
    both = ref_keep & keep
    ratio = np.median(ref.depth[both] / np.maximum(r.depth[both], 1e-6)) if both.any() else 1.0
    d_ref, d = ref.depth[both], r.depth[both] * ratio
    focal = float(np.median(np.abs(r.K[:, 0, 0] - ref.K[:, 0, 0]) / ref.K[:, 0, 0]))
    return {"rotation_err_deg_median": round(float(np.median(rot)), 2), "rotation_err_deg_p90": round(float(np.percentile(rot, 90)), 2),
            "pairs_within_5deg": round(float(np.mean(np.array(rot) < 5)), 3),
            "centre_err_share_of_path": round(ate / size, 3), "path_size_units": round(size, 3),
            "depth_absrel": round(float(np.mean(np.abs(d - d_ref) / d_ref)), 3) if both.any() else None,
            "depth_within_25pct": round(float(np.mean(np.maximum(d / d_ref, d_ref / d) < 1.25)), 3) if both.any() else None,
            "focal_err": round(focal, 3), "pixels_compared": round(float(both.mean()), 3)}


def train_splat(r, idx, keep, steps, log):
    """video_map.train's optimisation on keyframes idx only. Returns the parameters (torch)."""
    import torch
    from gsplat import rasterization
    from gsplat.strategy import DefaultStrategy

    class Sub:  # the pass cut down to the training keyframes, for video_map.start_points
        depth, K, E, rgb = r.depth[idx], r.K[idx], r.E[idx], r.rgb[idx]

    pts, cols, sizes = video_map.start_points(Sub, keep[idx])
    dev = "cuda"
    S, H, W = Sub.depth.shape
    images = torch.tensor(Sub.rgb, device=dev, dtype=torch.float32) / 255.0
    viewmats = torch.eye(4, device=dev).repeat(S, 1, 1)
    viewmats[:, :3, :] = torch.tensor(Sub.E, device=dev, dtype=torch.float32)
    Ks = torch.tensor(Sub.K, device=dev, dtype=torch.float32)
    cams = -torch.einsum("sji,sj->si", viewmats[:, :3, :3], viewmats[:, :3, 3])
    scene_scale = float(torch.linalg.norm(cams - cams.mean(0), dim=1).max().clamp(min=float(np.median(Sub.depth)))) * 1.1
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
    strategy = DefaultStrategy(refine_start_iter=500, refine_stop_iter=int(steps * 0.7), reset_every=3000, refine_every=100, verbose=False)
    strategy.check_sanity(params, optimizers)
    state = strategy.initialize_state(scene_scale=scene_scale)
    decay = 0.01 ** (1 / max(1, steps))
    rng = np.random.default_rng(0)
    t0 = time.time()
    for step in range(steps):
        i = int(rng.integers(S))
        render, _, info = rasterization(params["means"], params["quats"], torch.exp(params["scales"]), torch.sigmoid(params["opacities"]),
                                        params["sh0"], viewmats[i:i + 1], Ks[i:i + 1], W, H, sh_degree=0, packed=False)
        strategy.step_pre_backward(params, optimizers, state, step, info)
        pred, true = render[0].permute(2, 0, 1)[None], images[i].permute(2, 0, 1)[None]
        loss = 0.8 * (pred - true).abs().mean() + 0.2 * (1 - video_map.ssim(pred, true))
        loss.backward()
        strategy.step_post_backward(params, optimizers, state, step, info, packed=False)
        for opt in optimizers.values():
            opt.step()
            opt.zero_grad(set_to_none=True)
        for group in optimizers["means"].param_groups:
            group["lr"] *= decay
        if step % 2000 == 0 or step == steps - 1:
            log(f"   step {step}: loss {float(loss):.4f}, {len(params['means'])} Gaussians, {time.time() - t0:.0f} s")
    return params, round(time.time() - t0, 1)


def draw(params, colours, E, K, H, W):
    """The splat with these per-Gaussian colours (N x 3 in [0, 1]) from camera E, K: (H, W, 3) in [0, 1]."""
    import torch
    from gsplat import rasterization
    view = torch.eye(4, device="cuda")
    view[:3, :] = torch.tensor(E, device="cuda", dtype=torch.float32)
    render, _, _ = rasterization(params["means"], params["quats"], torch.exp(params["scales"]), torch.sigmoid(params["opacities"]),
                                 colours, view[None], torch.tensor(K, device="cuda", dtype=torch.float32)[None], W, H, packed=False)
    return render[0].clamp(0, 1).cpu().numpy()


def psnr01(a, b):
    return 10 * math.log10(1 / max(float(np.mean((a - b) ** 2)), 1e-10))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("out")
    ap.add_argument("--keyframes", type=int, default=64)
    ap.add_argument("--change", type=float, default=0.9)
    ap.add_argument("--fixes", type=int, default=10)
    ap.add_argument("--steps", type=int, default=7000)
    ap.add_argument("--variants", default="plain,tone,garbled,mild")
    ap.add_argument("--train", default="plain,garbled,mild")
    ap.add_argument("--seed", default="privacy-eval")
    args = ap.parse_args()
    out = Path(args.out)
    (out / "renders").mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(args.seed.encode()).digest()  # measurement only
    names = [v for v in args.variants.split(",") if v]
    if names[0] != "plain":
        names = ["plain"] + [v for v in names if v != "plain"]
    summary = {"source": Path(args.source).name, "variants": names}

    def log(text):
        print(text, flush=True)

    picked, total = video_map.keyframes(args.source, args.keyframes, args.change)
    frames = [cv2.resize(square_up(f), (SIDE, SIDE), interpolation=cv2.INTER_AREA) for _, f in picked]
    summary.update(frames=total, keyframes=len(frames))
    log(f"{total} frames, {len(frames)} keyframes")
    garblers = {n: Garbler(key, **VARIANTS[n]) for n in names if VARIANTS[n] is not None}
    inputs = {n: frames if n == "plain" else [sent(garblers[n], f) for f in frames] for n in names}
    for n in names:
        if n != "plain":
            summary.setdefault("frame_psnr_vs_plain_db", {})[n] = round(float(np.median([video_map_psnr(a, b) for a, b in zip(inputs[n], frames)])), 2)
    for n in names:
        cv2.imwrite(str(out / "renders" / f"input_{n}.jpg"), np.concatenate([inputs[n][k] for k in (0, len(frames) // 2)], 1))

    import torch
    model = rc.load_model(device="cuda")
    passes, keeps = {}, {}
    for n in names:
        t0 = time.time()
        r = rc.reconstruct(model, inputs[n], long_side=SIDE)
        valid = rc.valid_mask(r, 35.0)
        keep = valid & (agreeing(r, valid) >= 2)
        passes[n], keeps[n] = r, keep
        summary.setdefault("vggt", {})[n] = {"seconds": round(time.time() - t0, 1), "kept_share": round(float(keep.mean()), 3),
                                             "conf_median": round(float(np.median(r.conf)), 2)}
        if n != "plain":
            summary["vggt"][n].update(compare(passes["plain"], r, keeps["plain"], keep))
        log(f"VGGT {n}: {summary['vggt'][n]}")

    # position fixes as the worker makes them
    S = len(frames)
    base = sorted({int(i) for i in np.linspace(0, S - 1, min(N_BASE, S)).round()})
    others = [q for q in range(S) if q not in base]
    queries = [others[int(i)] for i in np.linspace(0, len(others) - 1, min(args.fixes, len(others))).round()] if others else []
    fixes = {n: [] for n in names}
    for n in names:
        for q in queries:
            r = rc.reconstruct(model, [inputs[n][b] for b in base] + [inputs[n][q]], long_side=SIDE)
            try:
                res, _, _ = locate.locate(r, list(range(len(base))), [len(base)], typical_m=locate.TYPICAL_M)
                fixes[n].append({"q": q, "state": res["state"], "confidence": res["confidence"], "position": res["position"], "heading": res["heading_deg"]})
            except Exception as err:  # noqa: BLE001  a failed fix is a lost one
                fixes[n].append({"q": q, "state": "lost", "error": f"{type(err).__name__}: {err}"})
    for n in names:
        states = [f["state"] for f in fixes[n]]
        row = {s: states.count(s) for s in ("found", "unsure", "lost")}
        if n != "plain":
            gaps, turns = [], []
            for a, b in zip(fixes["plain"], fixes[n]):
                if a["state"] != "lost" and b["state"] != "lost" and "position" in a and "position" in b:
                    gaps.append(float(np.hypot(a["position"][0] - b["position"][0], a["position"][1] - b["position"][1])))
                    turns.append(abs((a["heading"] - b["heading"] + 180) % 360 - 180))
            row.update(position_gap_m_median=round(float(np.median(gaps)), 2) if gaps else None,
                       heading_gap_deg_median=round(float(np.median(turns)), 1) if turns else None, compared=len(gaps))
        summary.setdefault("fixes", {})[n] = row
        log(f"fixes {n}: {row}")
    summary["fix_detail"] = fixes
    del model
    torch.cuda.empty_cache()

    # splats, decoded with the key
    held = [q for q in range(S) if q % 8 == 4]
    train_idx = [q for q in range(S) if q not in held]
    plain_rgb = passes["plain"].rgb.astype(np.float32) / 255.0
    for n in [v for v in args.train.split(",") if v in passes]:
        r = passes[n]
        params, seconds = train_splat(r, train_idx, keeps[n], args.steps, log)
        with torch.no_grad():
            raw = (0.5 + 0.28209479 * params["sh0"][:, 0]).clamp(0, 1).cpu().numpy()
            u8 = np.clip(raw * 255, 0, 255).astype(np.uint8)  # what trained.splat would hold
            g = garblers.get(n)
            shown = (g.decode_colours(u8) if g else u8.astype(np.float64)) / 255.0
            as_sent = torch.tensor(u8.astype(np.float32) / 255.0, device="cuda")
            decoded = torch.tensor(shown.astype(np.float32), device="cuda")
            H, W = r.depth.shape[1:]
            row = {"train_seconds": seconds, "gaussians": int(len(u8))}
            for part, idx in (("held_out", held), ("trained", train_idx[::4])):
                own = [psnr01(draw(params, as_sent, r.E[q], r.K[q], H, W), r.rgb[q] / 255.0) for q in idx]
                true = [psnr01(draw(params, decoded, r.E[q], r.K[q], H, W), plain_rgb[q]) for q in idx]
                row[f"{part}_psnr_vs_plain_db"] = round(float(np.median(true)), 2)
                if g:
                    row[f"{part}_psnr_in_garbled_space_db"] = round(float(np.median(own)), 2)
            for q in held[:2]:
                pair = np.concatenate([plain_rgb[q], draw(params, decoded, r.E[q], r.K[q], H, W)], 1)
                cv2.imwrite(str(out / "renders" / f"splat_{n}_held{q:03d}.jpg"), cv2.cvtColor((pair * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
        summary.setdefault("splat", {})[n] = row
        log(f"splat {n}: {row}")
        del params
        torch.cuda.empty_cache()
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    log("done")


def video_map_psnr(a, b):
    return 10 * math.log10(255.0 ** 2 / max(float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)), 1e-10))


if __name__ == "__main__":
    main()
