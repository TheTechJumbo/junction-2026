#!/usr/bin/env python3
"""Time and memory of the change detection (analyse) with NumPy and with Rust (JUN-28).

Same input for every run: a synthetic room from test_synthetic.py, 24 before + 12 after frames,
saved once to a file. Each configuration runs in a fresh process so that thread settings and
peak memory do not leak between runs. The model (VGGT) is not part of this: these numbers are
the detection only, on top of whatever the model pass costs.

    python bench_rust.py                     # 294x392 and 294x518 frames, 1 and 4 threads and all cores
    python bench_rust.py --sizes 294x518 --threads 4 --repeat 5
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def make_scene(path, h, w, features):
    import test_synthetic as ts

    ts.H, ts.W = h, w
    ts.K = np.array([[ts.FOCAL, 0, w / 2], [0, ts.FOCAL, h / 2], [0, 0, 1]], np.float32)
    r = ts.scene(ts.BOX_B, features=features)
    np.savez(path, depth=r.depth, conf=r.conf, K=r.K, E=r.E, rgb=r.rgb, **({"feat": r.feat} if features else {}))


def peak_rss():
    """Peak memory of this process so far, in bytes."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        c = Counters()
        c.cb = ctypes.sizeof(c)
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return c.PeakWorkingSetSize, c.WorkingSetSize
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # KiB on Linux
    with open("/proc/self/statm") as f:
        now = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return peak, now


def child(path, repeat, lookalike):
    """Runs in the fresh process: load the scene, run analyse, print one JSON line."""
    import recon_change as rc
    import recon_rust

    d = np.load(path)
    r = rc.Recon(d["depth"], d["conf"], d["K"], d["E"], d["rgb"], d["feat"] if "feat" in d else None)
    _, before = peak_rss()
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        report, _ = rc.analyse(r, 24, conf_pct=0, typical_m=2.5, **({"reach": 0.15} if lookalike else {}))
        times.append(time.perf_counter() - t0)
    peak, _ = peak_rss()
    threads = recon_rust._rs.threads() if recon_rust.enabled() else None
    print(json.dumps({"times": times, "extra_mb": (peak - before) / 1e6, "verdict": report["verdict"].split(":")[0],
                      "appeared": len(report["appeared"]), "gone": len(report["gone"]), "threads": threads}))


def run(path, engine, threads, repeat, lookalike):
    env = dict(os.environ, RECON_FAST="1" if engine == "rust" else "0")
    if threads:
        for k in ("RAYON_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env[k] = str(threads)
    out = subprocess.run([sys.executable, __file__, "--child", str(path), "--repeat", str(repeat)] + (["--lookalike"] if lookalike else []),
                         env=env, cwd=HERE, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sizes", default="294x392,294x518", help="frame sizes HxW, comma separated")
    p.add_argument("--threads", default="1,4,0", help="thread counts, 0 = all cores")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--child")
    p.add_argument("--lookalike", action="store_true", help="child only")
    a = p.parse_args()
    if a.child:
        return child(a.child, a.repeat, a.lookalike)

    print(f"{os.cpu_count()} logical CPUs. analyse() on 24 + 12 synthetic frames, median of {a.repeat} runs.")
    print("| frames | look-alike check | threads | NumPy | Rust | speed-up | extra memory NumPy | extra memory Rust | same result |")
    print("|---|---|---|---|---|---|---|---|---|")
    with tempfile.TemporaryDirectory() as tmp:
        for size in a.sizes.split(","):
            h, w = map(int, size.split("x"))
            for lookalike in (False, True):
                path = Path(tmp) / f"scene_{h}x{w}_{int(lookalike)}.npz"
                make_scene(path, h, w, lookalike)
                for t in map(int, a.threads.split(",")):
                    slow, fast = run(path, "numpy", t, a.repeat, lookalike), run(path, "rust", t, a.repeat, lookalike)
                    ts, tf = float(np.median(slow["times"])), float(np.median(fast["times"]))
                    same = all(slow[k] == fast[k] for k in ("verdict", "appeared", "gone"))
                    label = t or "all ({})".format(fast["threads"])
                    print(f"| {w}x{h} | {'on' if lookalike else 'off'} | {label} | {ts:.2f} s | {tf:.2f} s | "
                          f"{ts / tf:.1f}x | {slow['extra_mb']:.0f} MB | {fast['extra_mb']:.0f} MB | {'yes' if same else 'NO'} |", flush=True)


if __name__ == "__main__":
    main()
