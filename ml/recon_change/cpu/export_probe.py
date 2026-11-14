#!/usr/bin/env python3
"""Probe: can VGGT be exported to OpenVINO, and is that faster than PyTorch on the same CPU?

Needs its own environment (openvino is not in .venv-recon). It traces the part of the model the
detector uses for one fixed input size, runs it in OpenVINO in float32, runs the same frames in
PyTorch, and prints one JSON line with both timings and both detector outcomes:

    python cpu/export_probe.py --baseline baseline.mp4 --clip after_box.mp4 --frames 8+4 --long-side 518
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np
import torch

import bench_cpu as bc
import recon_change as rc


class DepthAndPose(torch.nn.Module):
    """VGGT cut down to what the detector reads: pose encoding, depth, depth confidence."""

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, images):
        pose, depth, conf = bc.forward(self.model, images, bf16=False, point_head=False)
        return pose, depth, conf


def to_recon(pose, depth, conf, rgb, hw):
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri

    E, K = pose_encoding_to_extri_intri(torch.as_tensor(pose).float(), hw)
    return rc.Recon(depth=np.asarray(depth, np.float32)[0, ..., 0], conf=np.asarray(conf, np.float32)[0],
                    K=K.numpy()[0], E=E.numpy()[0], rgb=rgb)


def outcome(recon, n_before):
    report, _ = rc.analyse(recon, n_before)
    return {"appeared": len(report["appeared"]), "gone": len(report["gone"]),
            "appeared_voxels": [c["voxels"] for c in report["appeared"]], "depth_noise": report["depth_noise_sigma"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--clip", required=True)
    ap.add_argument("--frames", default="8+4")
    ap.add_argument("--long-side", type=int, default=518)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--attn-chunk-mb", type=int, default=256)
    ap.add_argument("--out")
    args = ap.parse_args()
    torch.set_num_threads(args.threads)
    n_before, n_after = (int(v) for v in args.frames.split("+"))
    bc.ATTN_CHUNK_BYTES = args.attn_chunk_mb * 1024 * 1024
    if bc.ATTN_CHUNK_BYTES:
        from vggt.layers.attention import Attention
        Attention.forward = bc.chunked_attention_forward

    result = {"probe": "openvino", "settings": {"frames": args.frames, "long_side": args.long_side,
                                                 "attn_chunk_mb": args.attn_chunk_mb},
              "cores_used": args.threads, "cpu": bc.cpu_info(), "clip": Path(args.clip).stem}
    try:
        model, _ = bc.load_model_cpu()
        frames = rc.sample_frames(args.baseline, n_before) + rc.sample_frames(args.clip, n_after)
        H, W = rc.fit_size(*frames[0].shape[:2], long_side=args.long_side)
        rgb = np.stack([cv2.cvtColor(cv2.resize(f, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
                        for f in frames])
        images = torch.from_numpy(rgb).permute(0, 3, 1, 2).float().div(255)
        wrapper = DepthAndPose(model).eval()

        t0 = time.perf_counter()
        with torch.no_grad():
            pose, depth, conf = wrapper(images)
        result["torch_inference_s"] = round(time.perf_counter() - t0, 1)
        result["torch"] = outcome(to_recon(pose, depth.numpy(), conf.numpy(), rgb, (H, W)), n_before)
        bc.log(f"torch: {result['torch_inference_s']} s, {result['torch']}")

        import openvino as ov

        result["openvino_version"] = ov.__version__
        t0 = time.perf_counter()
        with torch.no_grad():
            ov_model = ov.convert_model(wrapper, example_input=images)
        result["convert_s"] = round(time.perf_counter() - t0, 1)
        bc.log(f"converted in {result['convert_s']} s, resident {bc.rss_gb()} GB")
        del wrapper, model
        t0 = time.perf_counter()
        compiled = ov.Core().compile_model(ov_model, "CPU", {
            "INFERENCE_NUM_THREADS": args.threads, "INFERENCE_PRECISION_HINT": "f32", "PERFORMANCE_HINT": "LATENCY"})
        result["compile_s"] = round(time.perf_counter() - t0, 1)
        times = []
        for _ in range(2):  # the first call includes one-off set-up
            t0 = time.perf_counter()
            out = compiled(images.numpy())
            times.append(round(time.perf_counter() - t0, 1))
        result["openvino_inference_s"] = times
        pose_o, depth_o, conf_o = (out[i] for i in range(3))
        result["openvino"] = outcome(to_recon(pose_o, depth_o, conf_o, rgb, (H, W)), n_before)
        result["max_abs_depth_diff"] = float(np.abs(np.asarray(depth_o) - depth.numpy()).max())
    except Exception as err:
        result["error"] = f"{type(err).__name__}: {str(err)[:600]}"
    result["peak_rss_gb"] = bc.rss_gb("VmHWM")
    line = json.dumps(result)
    print(line, flush=True)
    if args.out:
        with open(args.out, "a") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    main()
