#!/usr/bin/env python3
"""Seconds per frame and peak memory of the phone side on a few CPU cores.

One run = one variant on one core count, in its own process, so the peak memory is its own. It
loads the small model and one room's map file (written by phone_side_eval.py --score), then runs
the per-frame step on query frames of that room and prints one JSON line:

    extract_s    the model on one frame (resize, forward pass, features out)
    match_s      closest baseline photos + matching
    pose_s       PnP with RANSAC + polish
    peak_rss_gb  highest resident memory of the process (Python, PyTorch, model, map, one frame)

    python phone_side_bench.py --variant vits --threads 4 --room fr3_office --settings '{"k": 3, "reproj": 12}'

The cores must be pinned from outside (taskset), as phone_side_bench.slurm does. Reading and
decoding the PNG is not counted: a phone has the camera frame in memory.

A server core is not a phone: these numbers say how much work the step is, not how long a phone
takes. See eval/PHONE_SIDE_RESULTS.md.
"""
import argparse
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]


def rss_gb(key="VmRSS"):
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(key + ":"):
                    return round(int(line.split()[1]) / 1e6, 3)
    except OSError:
        pass
    return None


def cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--threads", type=int, required=True)
    ap.add_argument("--room", default="fr3_office")
    ap.add_argument("--photos", type=int, default=12)
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--settings", default="{}", help="JSON: k, reproj, min_sim, ratio, refine, dim")
    ap.add_argument("--data", default="walk/data/tum")
    ap.add_argument("--walk", default="walk/out")
    ap.add_argument("--work", default="phone")
    ap.add_argument("--base-hub", default="items/hf/hub")
    ap.add_argument("--out")
    args = ap.parse_args()
    cv2.setNumThreads(args.threads)
    rss_python = rss_gb()
    import phone_side as ps
    import phone_side_eval as pe

    result = {"variant": args.variant, "threads": args.threads, "room": args.room, "photos": args.photos, "host": socket.gethostname(),
              "cpu": cpu_model(), "cores_allowed": len(os.sched_getaffinity(0)), "rss_python_gb": rss_python}
    settings = {"k": 3, "reproj": 8.0, "min_sim": 0.0, "ratio": 1.0, "refine": False, "dim": 0, **json.loads(args.settings)}
    result["settings"] = settings
    t0 = time.perf_counter()
    if pe.family(args.variant) != "sift":
        import torch

        torch.set_num_threads(args.threads)
        result["torch_threads"] = torch.get_num_threads()
    extractor = pe.make_extractor(args.variant, args.work, args.base_hub)
    result["load_s"] = round(time.perf_counter() - t0, 2)
    result["rss_model_gb"] = rss_gb()
    # The model on disk: the weights file, and for a quantised model the file it would be saved as.
    model = getattr(extractor, "model", None)
    if model is not None:
        import torch

        net = getattr(model, "net", model)
        result["parameters_m"] = round(sum(p.numel() for p in net.parameters()) / 1e6, 2)
        with tempfile.TemporaryDirectory() as folder:
            torch.save(net.state_dict(), Path(folder) / "m.pt")
            result["model_mb"] = round((Path(folder) / "m.pt").stat().st_size / 1e6, 1)
    dim = settings.pop("dim")
    map_file = Path(args.work) / "maps" / f"{args.variant}_{args.room}_{args.photos}_d{dim}.npz"
    result["map_mb"] = round(map_file.stat().st_size / 1e6, 2)
    t0 = time.perf_counter()
    m = ps.load_map(map_file)
    result["map_load_s"] = round(time.perf_counter() - t0, 3)
    result["map_features"] = int(len(m.xyz))
    plan = json.loads((Path(args.walk) / "plan.json").read_text())
    queries = plan["rooms"][args.room]["queries"][:args.frames]
    rows = []
    for i, q in enumerate(queries):
        img = cv2.imread(str(Path(args.data) / q["rec"] / q["rgb"]))
        t0 = time.perf_counter()
        f = extractor.extract(img)
        t1 = time.perf_counter()
        out = ps.locate_frame(m, f, **settings)
        if i >= 3:  # the first frames warm up the model
            rows.append((t1 - t0, out["seconds"]["match"], out["seconds"]["pose"], out["inliers"], out["matches"]))
    a = np.array(rows)
    result.update(frames=len(rows), extract_s=round(float(np.median(a[:, 0])), 4), match_s=round(float(np.median(a[:, 1])), 4),
                  pose_s=round(float(np.median(a[:, 2])), 4), frame_s=round(float(np.median(a[:, :3].sum(1))), 4),
                  frame_s_p90=round(float(np.percentile(a[:, :3].sum(1), 90)), 4), pose_s_max=round(float(a[:, 2].max()), 4),
                  inliers_median=float(np.median(a[:, 3])), matches_median=float(np.median(a[:, 4])),
                  peak_rss_gb=rss_gb("VmHWM"), rss_end_gb=rss_gb())
    line = json.dumps(result)
    print(line, flush=True)
    if args.out:
        with open(args.out, "a") as sink:
            sink.write(line + "\n")


if __name__ == "__main__":
    main()
