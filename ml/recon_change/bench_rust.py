#!/usr/bin/env python3
"""Time and memory of the change detection (analyse) with NumPy and with Rust (JUN-28).

Same input for every run: a synthetic room from test_synthetic.py, 24 before + 12 after frames,
saved once to a file. With the look-alike check on, the patch features have 256 numbers in
float16, like the real ones. Each configuration runs in a fresh process so that thread settings and
peak memory do not leak between runs. The model (VGGT) is not part of this: these numbers are
the detection only, on top of whatever the model pass costs.

    python bench_rust.py                     # 294x518 frames, 1 and 4 threads and all cores
    python bench_rust.py --sizes 294x392,294x518 --threads 4 --repeat 5

Three engines: NumPy only; "PR 20" (Rust depth_gap and votes only); and every Rust function.
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
    if features:
        from test_rust import wide_features

        r = wide_features(r)
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

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        # ps reports current RSS in KiB; Darwin ru_maxrss is already bytes.
        now = int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())], text=True)) * 1024
    else:
        peak *= 1024  # Linux ru_maxrss is KiB
        with open("/proc/self/statm") as f:
            now = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return peak, now


def child(path, repeat, lookalike):
    """Runs in the fresh process: load the scene, run analyse, print one JSON line."""
    import recon_change as rc
    import recon_rust

    if os.environ.get("RECON_FAST") != "0" and not recon_rust.enabled():
        raise RuntimeError("Rust benchmark requested but recon_fast is unavailable")
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


ENGINES = {"numpy": {"RECON_FAST": "0"},
           "pr20": {"RECON_FAST": "1", "RECON_FAST_OFF": "unproject,patch_median,nearest,lookalike_count"},
           "rust": {"RECON_FAST": "1", "RECON_FAST_OFF": ""}}


def run(path, engine, threads, repeat, lookalike):
    env = dict(os.environ, **ENGINES[engine])
    if threads:
        for k in ("RAYON_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env[k] = str(threads)
    out = subprocess.run([sys.executable, __file__, "--child", str(path), "--repeat", str(repeat)] + (["--lookalike"] if lookalike else []),
                         env=env, cwd=HERE, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sizes", default="294x518", help="frame sizes HxW, comma separated")
    p.add_argument("--threads", default="1,4,0", help="thread counts, 0 = all cores")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--child")
    p.add_argument("--lookalike", action="store_true", help="child only")
    a = p.parse_args()
    if a.child:
        return child(a.child, a.repeat, a.lookalike)

    print(f"{os.cpu_count()} logical CPUs. analyse() on 24 + 12 synthetic frames, median of {a.repeat} runs.")
    print("| frames | look-alike check | threads | NumPy | PR 20 | all Rust | speed-up | peak extra memory NumPy / PR 20 / all Rust | same result |")
    print("|---|---|---|---|---|---|---|---|---|")
    with tempfile.TemporaryDirectory() as tmp:
        for size in a.sizes.split(","):
            h, w = map(int, size.split("x"))
            for lookalike in (False, True):
                path = Path(tmp) / f"scene_{h}x{w}_{int(lookalike)}.npz"
                make_scene(path, h, w, lookalike)
                for t in map(int, a.threads.split(",")):
                    got = {e: run(path, e, t, a.repeat, lookalike) for e in ENGINES}
                    sec = {e: float(np.median(g["times"])) for e, g in got.items()}
                    same = all(got[e][k] == got["numpy"][k] for e in got for k in ("verdict", "appeared", "gone"))
                    label = t or "all ({})".format(got["rust"]["threads"])
                    mem = " / ".join(f"{got[e]['extra_mb']:.0f}" for e in ENGINES)
                    print(f"| {w}x{h} | {'on' if lookalike else 'off'} | {label} | {sec['numpy']:.2f} s | {sec['pr20']:.2f} s | "
                          f"{sec['rust']:.2f} s | {sec['numpy'] / sec['rust']:.1f}x | {mem} MB | {'yes' if same else 'NO'} |", flush=True)


if __name__ == "__main__":
    main()
