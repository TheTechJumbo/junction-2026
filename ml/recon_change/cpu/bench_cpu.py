#!/usr/bin/env python3
"""How fast does VGGT run on a CPU with no GPU, and what do the fast settings cost?

One run = one setting. It loads the model on the CPU, reconstructs the baseline clip together
with each test clip, runs the change detector, and prints one JSON line with the timings, the
peak memory and what the detector found:

    python cpu/bench_cpu.py --baseline baseline.mp4 --clips after.mp4 after_box.mp4 \
        --frames 12+6 --long-side 294 --threads 4

Quality reference (GPU, 24+12 frames, long side 518): `after.mp4` gives no change and
`after_box.mp4` gives exactly one "appeared" cluster. A setting passes if it gives both.

`reconstruct_cpu` is a device-independent copy of `recon_change.reconstruct`. Two things differ
from the GPU path, and neither changes the maths:
  - Attention is computed a slice of query rows at a time (`--attn-chunk-mb`, default 256). It
    was meant to save memory. Measured, it saves none and is slower at long side 518, so use
    `--attn-chunk-mb 0` (the library's attention as it is). The default stays at 256 because
    that is what most rows in RESULTS.md were measured with.
  - The point-map head is skipped: the detector only reads depth and cameras (`--point-head`
    turns it back on to measure the saving).
"""
import argparse
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # recon_change.py is one folder up

import cv2
import numpy as np
import torch
import torch.nn.functional as F

import recon_change as rc

# What the GPU reference reports for each test clip: (appeared clusters, gone clusters).
EXPECTED = {"after": (0, 0), "after_box": (1, 0)}
ATTN_CHUNK_BYTES = 0  # set from --attn-chunk-mb; 0 leaves the library's attention untouched


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ----------------------------------------------------------------------------- machine
def cpu_info():
    model, flags = platform.processor() or "unknown", []
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                elif line.startswith("flags"):
                    flags = line.split(":", 1)[1].split()
                    break
    except OSError:
        pass
    visible = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    wanted = ("avx2", "avx512f", "avx512_vnni", "avx512_bf16", "amx_bf16")
    return {"model": model, "cores_visible": visible, "flags": [f for f in wanted if f in flags]}


def rss_gb(key="VmRSS"):
    """Resident memory of this process in GB. key: VmRSS (now) or VmHWM (peak so far)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(key + ":"):
                    return round(int(line.split()[1]) * 1024 / 1e9, 2)
    except OSError:
        pass
    try:  # Windows and macOS, if psutil is there
        import psutil
        info = psutil.Process().memory_info()
        return round((getattr(info, "peak_wset", info.rss) if key == "VmHWM" else info.rss) / 1e9, 2)
    except ImportError:
        return None


def reset_peak_rss():
    """Start the peak-memory counter again, so inference is measured apart from model loading."""
    try:
        with open("/proc/self/clear_refs", "w") as f:
            f.write("5")
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------------- model
def chunked_attention_forward(self, x, pos=None):
    """vggt.layers.attention.Attention.forward, with the attention matrix built for a slice of
    query rows at a time. Each row's softmax only needs its own row, so the result is the same,
    and one slice is at most ATTN_CHUNK_BYTES instead of batch x heads x tokens x tokens."""
    B, N, C = x.shape
    qkv = self.qkv(x).reshape(B, N, 3, self.num_heads, self.head_dim).permute(2, 0, 3, 1, 4)
    q, k, v = qkv.unbind(0)
    q, k = self.q_norm(q), self.k_norm(k)
    if self.rope is not None:
        q = self.rope(q, pos)
        k = self.rope(k, pos)
    rows = max(1, int(ATTN_CHUNK_BYTES // (B * self.num_heads * N * q.element_size())))
    if rows >= N:
        x = F.scaled_dot_product_attention(q, k, v)
    else:
        x = torch.cat([F.scaled_dot_product_attention(q[:, :, s:s + rows], k, v) for s in range(0, N, rows)], dim=2)
    x = x.transpose(1, 2).reshape(B, N, C)
    return self.proj_drop(self.proj(x))


def load_model_cpu(model_id="facebook/VGGT-1B", point_head=False):
    """Load VGGT in float32 on the CPU. Returns (model, seconds)."""
    from vggt.models.vggt import VGGT

    t0 = time.perf_counter()
    model = VGGT.from_pretrained(model_id).eval()
    model.track_head = None  # never used here
    if not point_head:
        model.point_head = None
    return model, time.perf_counter() - t0


def prepare(model, precision, compile_model):
    """Apply the number-format trick. Returns (model, seconds it took)."""
    t0 = time.perf_counter()
    if precision == "int8":
        # Dynamic quantisation: weights of every nn.Linear stored as int8, activations stay float.
        model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
    if compile_model:
        model.aggregator = torch.compile(model.aggregator)  # compiles on the first call
    return model, time.perf_counter() - t0


def forward(model, images, bf16, point_head):
    """The part of VGGT.forward the detector needs: cameras and depth."""
    x = images[None]
    with torch.autocast("cpu", dtype=torch.bfloat16, enabled=bf16):
        tokens, start = model.aggregator(x)
    tokens = [None if t is None else t.float() for t in tokens]  # heads run in float32, as on the GPU
    with torch.autocast("cpu", enabled=False):
        pose = model.camera_head(tokens)[-1]
        depth, conf = model.depth_head(tokens, images=x, patch_start_idx=start)
        if point_head:
            model.point_head(tokens, images=x, patch_start_idx=start)
    return pose, depth, conf


def reconstruct_cpu(model, frames_bgr, long_side=518, bf16=False, grad="no_grad", point_head=False):
    """Device-independent copy of recon_change.reconstruct. Returns (Recon, seconds, (W, H))."""
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri

    H, W = rc.fit_size(*frames_bgr[0].shape[:2], long_side=long_side)
    rgb = np.stack([
        cv2.cvtColor(cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB) for f in frames_bgr
    ])
    images = torch.from_numpy(rgb).permute(0, 3, 1, 2).float().div(255)  # (S, 3, H, W) in [0, 1]
    t0 = time.perf_counter()
    with (torch.inference_mode() if grad == "inference_mode" else torch.no_grad()):
        pose, depth, conf = forward(model, images, bf16, point_head)
        E, K = pose_encoding_to_extri_intri(pose.float(), images.shape[-2:])
        seconds = time.perf_counter() - t0

        def f32(t):
            return t.float().cpu().numpy()

        recon = rc.Recon(depth=f32(depth)[0, ..., 0], conf=f32(conf)[0], K=f32(K)[0], E=f32(E)[0], rgb=rgb)
    return recon, seconds, (W, H)


# ----------------------------------------------------------------------------- run
def main():
    global ATTN_CHUNK_BYTES
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True, help="clip of the place as it should be")
    ap.add_argument("--clips", nargs="+", required=True, help="clips to check against the baseline")
    ap.add_argument("--frames", default="24+12", help="baseline + new frames, e.g. 12+6")
    ap.add_argument("--long-side", type=int, default=518, help="long side of the model input, a multiple of 14")
    ap.add_argument("--threads", type=int, default=os.cpu_count(), help="CPU cores to use")
    ap.add_argument("--precision", choices=["fp32", "bf16", "int8"], default="fp32")
    ap.add_argument("--grad", choices=["no_grad", "inference_mode"], default="no_grad")
    ap.add_argument("--compile", action="store_true", help="torch.compile the aggregator")
    ap.add_argument("--attn-chunk-mb", type=int, default=256,
                    help="memory for one slice of the attention matrix; 0 = library default (whole matrix)")
    ap.add_argument("--point-head", action="store_true", help="also run the point-map head, as VGGT.forward does")
    ap.add_argument("--scale-window", action="store_true",
                    help="shrink the detector's 15 px search window with the input size")
    ap.add_argument("--tag", default="", help="free text copied into the result")
    ap.add_argument("--out", help="also append the JSON line to this file")
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    cv2.setNumThreads(args.threads)
    n_before, n_after = (int(v) for v in args.frames.split("+"))
    ATTN_CHUNK_BYTES = args.attn_chunk_mb * 1024 * 1024
    if ATTN_CHUNK_BYTES:
        from vggt.layers.attention import Attention
        Attention.forward = chunked_attention_forward

    result = {
        "settings": {"frames": args.frames, "long_side": args.long_side, "precision": args.precision,
                     "grad": args.grad, "compile": args.compile, "attn_chunk_mb": args.attn_chunk_mb,
                     "point_head": args.point_head, "scale_window": args.scale_window},
        "tag": args.tag, "cores_used": torch.get_num_threads(), "cpu": cpu_info(), "host": socket.gethostname(),
        "torch": torch.__version__, "clips": {},
    }
    try:
        model, load_s = load_model_cpu(point_head=args.point_head)
        model, prepare_s = prepare(model, args.precision, args.compile)
        result.update(load_s=round(load_s, 1), prepare_s=round(prepare_s, 1),
                      rss_after_load_gb=rss_gb(), peak_rss_load_gb=rss_gb("VmHWM"))
        result["peak_reset"] = reset_peak_rss()
        log(f"model ready: load {load_s:.1f} s, prepare {prepare_s:.1f} s, resident {result['rss_after_load_gb']} GB")

        baseline = rc.sample_frames(args.baseline, n_before)
        window = rc.WINDOW
        if args.scale_window:
            window = max(3, round(rc.WINDOW * args.long_side / 518) // 2 * 2 + 1)
        for clip in args.clips:
            name = Path(clip).stem
            frames = baseline + rc.sample_frames(clip, n_after)
            recon, inference_s, (W, H) = reconstruct_cpu(
                model, frames, args.long_side, args.precision == "bf16", args.grad, args.point_head)
            t0 = time.perf_counter()
            report, _ = rc.analyse(recon, len(baseline), window=window)
            detect_s = time.perf_counter() - t0
            found = (len(report["appeared"]), len(report["gone"]))
            result["settings"]["input"] = f"{W}x{H}"
            result["clips"][name] = {
                "frames": len(frames), "inference_s": round(inference_s, 1), "detect_s": round(detect_s, 1),
                "appeared": found[0], "gone": found[1], "false_alarms": len(report["false_alarms_in_control"]),
                "verdict": report["verdict"].split(":")[0].split(" between")[0],
                "appeared_voxels": [c["voxels"] for c in report["appeared"]],
                "self_consistency": report["self_consistency"], "depth_noise": report["depth_noise_sigma"],
                "depth_noise_between_clips": report["depth_noise_between_clips"],
                "expected": EXPECTED.get(name), "ok": found == EXPECTED[name] if name in EXPECTED else None,
            }
            log(f"{name}: inference {inference_s:.1f} s, detect {detect_s:.1f} s, appeared {found[0]}, gone {found[1]}")
        result["peak_rss_gb"] = rss_gb("VmHWM")  # peak during inference + detection if peak_reset, else whole run
        oks = [c["ok"] for c in result["clips"].values()]
        result["inference_s"] = round(float(np.mean([c["inference_s"] for c in result["clips"].values()])), 1)
        result["detect_s"] = round(float(np.mean([c["detect_s"] for c in result["clips"].values()])), 1)
        # A pass needs both reference clips; a run with one clip can only fail or stay undecided.
        result["passes"] = False if False in oks else (True if set(EXPECTED) <= set(result["clips"]) else None)
    except Exception as err:  # out of memory, a trick the CPU cannot do, ...: still report the run
        result["error"] = f"{type(err).__name__}: {str(err)[:400]}"
        result["peak_rss_gb"] = rss_gb("VmHWM")
        result["passes"] = False
    line = json.dumps(result)
    print(line, flush=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "a") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    main()
