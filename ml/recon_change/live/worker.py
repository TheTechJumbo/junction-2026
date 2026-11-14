#!/usr/bin/env python3
"""Vision worker. Runs inside a Slurm GPU job on Iridis and keeps VGGT loaded.

GPU nodes cannot be reached from outside, so requests arrive as folders on shared scratch:
the gateway (login node) writes queue/<job>/ and then a READY marker; this worker renames it
to RUNNING, does the work, writes result.json, and renames the marker to DONE or FAILED.

  baseline  keep up to 24 sharp frames of a clip as the zone's "how it should look"
  check     reconstruct the zone's baseline frames together with 12 frames of a new clip,
            and report what appeared and what is gone
  locate    reconstruct the zone's baseline frames together with up to 3 frames of what the
            wearer sees now, and report where they stand, which way they face, and the
            distance and direction to each named place (see locate.py). With RECON_ITEMS not
            empty it also reports small things seen in the newest frame ("keys", "glasses",
            ...) and where they are on the same map (see items.py)

A clip is either a video file or a zip of frames (000.jpg, 001.jpg, ...) that the relay
already picked, which is far smaller to send than a phone video.

Stop it cleanly with `touch STOP` in the folder above this one, or `scancel`.
"""
import json
import os
import re
import socket
import sys
import time
import traceback
import zipfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import items  # noqa: E402
import locate  # noqa: E402
import recon_change as rc  # noqa: E402
import splat  # noqa: E402

# Settings come from the environment, so this one script is both the GPU worker and the CPU worker.
DEVICE = os.environ.get("RECON_DEVICE") or None  # "cuda", "cpu", or unset to use a GPU when there is one
N_BASELINE, N_AFTER = (int(n) for n in os.environ.get("RECON_FRAMES", "24,12").split(","))
LONG_SIDE = int(os.environ.get("RECON_LONG_SIDE", "518"))  # model input size: smaller is faster and coarser
N_LOCATE = int(os.environ.get("RECON_LOCATE_FRAMES", "3"))  # frames of the wearer's view used for one position fix
# Baseline photos used for a position fix. With the appearance check, 12 reject every wrong room on
# the PASLCD benchmark and halve the time of 24 (eval/LOCATE_LOOKALIKE_RESULTS.md).
N_LOCATE_BASE = int(os.environ.get("RECON_LOCATE_BASELINE", min(N_BASELINE, 12)))
LANE = "-cpu" if DEVICE == "cpu" else ""  # the CPU worker has its own queue and heartbeat
QUEUE, BASELINES = ROOT / f"queue{LANE}", ROOT / "baselines"
ITEMS = items.wanted()  # RECON_ITEMS: small things to look for in every position fix; empty = off
ITEM_DETECTOR = {}  # {"model": the loaded detector} or {"error": why it could not be loaded}
INFO = {}  # what this worker is, filled in at start and attached to every result


def clip_frames(job, req, n):
    """Up to n frames from the job's clip: a video file, or a zip of frames picked by the relay."""
    clip = job / req["file"]
    try:
        if clip.suffix != ".zip":
            return rc.sample_frames(clip, n)
        folder = job / "frames"
        folder.mkdir(exist_ok=True)
        with zipfile.ZipFile(clip) as bundle:
            for name in bundle.namelist():
                if re.fullmatch(r"\d{1,4}\.jpg", name):  # nothing else is ever written to disk
                    (folder / name).write_bytes(bundle.read(name))
        return [f for f in rc.sample_frames(folder, n) if f is not None]
    except SystemExit:  # sample_frames is also a command-line helper and exits on unreadable input
        raise RuntimeError("Could not read any picture from that clip.") from None


def set_baseline(job, req):
    frames = clip_frames(job, req, N_BASELINE)
    if len(frames) < 6:
        raise RuntimeError(f"The baseline clip gave only {len(frames)} frames. Record a longer one.")
    zone = BASELINES / req["zone"]
    zone.mkdir(parents=True, exist_ok=True)
    for old in zone.glob("*.jpg"):
        old.unlink()
    (zone / "waypoints.json").unlink(missing_ok=True)  # places were picked in the old pictures
    for i, frame in enumerate(frames):
        cv2.imwrite(str(zone / f"{i:02d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return {"frames": len(frames)}


def crop_like(frame, like):
    """Centre-crop a frame to another frame's shape. The model resizes every frame to the first
    one's size, so a portrait photo next to a landscape baseline would come out squashed."""
    h, w = frame.shape[:2]
    shape = like.shape[1] / like.shape[0]
    if w / h > shape:
        keep = max(1, round(h * shape))
        return frame[:, (w - keep) // 2:(w - keep) // 2 + keep]
    keep = max(1, round(w / shape))
    return frame[(h - keep) // 2:(h - keep) // 2 + keep]


def camera_position(E):
    return -E[:, :3].T @ E[:, 3]


def before_and_now(recon, detail):
    """One "before" and one "now" picture for a phone screen: the after frame showing most of
    what appeared (green) and the baseline frame showing most of what is gone (red). When only
    one side has something to show, the other is the view taken from the nearest spot."""
    def most(found, frames):
        hits = found["member"] >= 0
        count = {q: int((hits & (found["frames"] == q)).sum()) for q in frames}
        best = max(frames, key=lambda q: count[q])
        return best, count[best], found["pixels"][hits & (found["frames"] == best)]

    def nearest(frames, to):
        return min(frames, key=lambda q: float(np.linalg.norm(camera_position(recon.E[q]) - camera_position(recon.E[to]))))

    now, n_now, px_now = most(detail["appeared"], detail["after"])
    before, n_before, px_before = most(detail["gone"], detail["reference"])
    if n_now == 0 and n_before > 0:
        now, px_now = nearest(detail["after"], before), px_now[:0]
    elif n_before == 0:
        if n_now == 0:
            now = detail["after"][len(detail["after"]) // 2]
        before, px_before = nearest(detail["reference"], now), px_before[:0]
    return rc.tint(recon.rgb[before], px_before, rc.RED), rc.tint(recon.rgb[now], px_now, rc.GREEN)


def check(model, job, req):
    files = sorted((BASELINES / req["zone"]).glob("*.jpg"))
    if not files:
        raise RuntimeError(f"No baseline for '{req['zone']}' yet. Record the baseline clip first.")
    seconds = {}
    t0 = time.time()
    files = [files[i] for i in sorted(set(np.linspace(0, len(files) - 1, min(N_BASELINE, len(files))).round().astype(int)))]
    base = [cv2.imread(str(f)) for f in files]
    after = [crop_like(f, base[0]) for f in clip_frames(job, req, N_AFTER)]
    if len(after) < 3:
        raise RuntimeError(f"The clip gave only {len(after)} frames. Record a longer one.")
    seconds["frames"] = round(time.time() - t0, 2)
    t0 = time.time()
    recon = rc.reconstruct(model, base + after, long_side=LONG_SIDE)
    seconds["reconstruct"] = round(time.time() - t0, 2)
    t0 = time.time()
    report, detail = rc.analyse(recon, len(base))
    out = job / "out"
    out.mkdir(exist_ok=True)
    rc.contact_sheet(recon, detail, out / "changes.jpg")
    before, now = before_and_now(recon, detail)
    cv2.imwrite(str(out / "before.jpg"), before, [cv2.IMWRITE_JPEG_QUALITY, 90])
    cv2.imwrite(str(out / "now.jpg"), now, [cv2.IMWRITE_JPEG_QUALITY, 90])
    seconds["detect"] = round(time.time() - t0, 2)
    t0 = time.time()
    report["gaussians"] = splat.write_splat(recon, detail, out / "scene.splat")  # 3D view, built on the CPU
    seconds["splat"] = round(time.time() - t0, 2)
    report["seconds"] = seconds
    report["median_depth"] = round(float(np.median(recon.depth)), 4)
    report.update(INFO)
    return report


def where(model, job, req):
    """Where the wearer is in the zone's map, and the way to each named place."""
    zone = BASELINES / req["zone"]
    files = sorted(zone.glob("*.jpg"))
    if not files:
        raise RuntimeError(f"No baseline for '{req['zone']}' yet. Record the baseline clip first.")
    try:
        places = [p for p in json.loads((zone / "waypoints.json").read_text()) if p["frame"] < len(files)]
    except (OSError, ValueError):
        places = []
    seconds = {}
    t0 = time.time()
    spread = np.linspace(0, len(files) - 1, min(N_LOCATE_BASE, len(files))).round().astype(int)
    pick = sorted({int(i) for i in spread} | {p["frame"] for p in places})  # a place needs the picture it was picked in
    base = [cv2.imread(str(files[i])) for i in pick]
    seen = clip_frames(job, req, N_LOCATE)
    new = [crop_like(f, base[0]) for f in seen]
    if not new:
        raise RuntimeError("No picture of what the wearer sees.")
    seconds["frames"] = round(time.time() - t0, 2)
    t0 = time.time()
    recon = rc.reconstruct(model, base + new, long_side=LONG_SIDE)
    seconds["reconstruct"] = round(time.time() - t0, 2)
    t0 = time.time()
    slot = {f: i for i, f in enumerate(pick)}
    result, marks, valid = locate.locate(
        recon, list(range(len(base))), list(range(len(base), len(base) + len(new))),
        [{**p, "frame": slot[p["frame"]]} for p in places])
    out = job / "out"
    out.mkdir(exist_ok=True)
    cv2.imwrite(str(out / "view.jpg"), new[-1], [cv2.IMWRITE_JPEG_QUALITY, 88])
    seconds["locate"] = round(time.time() - t0, 2)
    result.update({"items": [], "items_looked_for": ITEMS})
    if ITEMS:  # lost items: found in the newest picture, placed with this fix's depth (items.py)
        t0 = time.time()
        try:
            if "model" not in ITEM_DETECTOR:
                raise RuntimeError(ITEM_DETECTOR.get("error", "not loaded"))
            found, item_marks = items.look(ITEM_DETECTOR["model"], ITEMS, job / req["file"], seen[-1], base[0], recon,
                                           list(range(len(base))), len(base) + len(new) - 1, valid, result["state"], out)
            result.update(found)
            marks += item_marks
        except Exception as error:  # the position fix must still go out
            result.update({"items": [], "items_note": f"Items were not looked for: {type(error).__name__}: {error}"[:300]})
            print(f"{job.name} items FAILED: {traceback.format_exc()}", flush=True)
        seconds["items"] = round(time.time() - t0, 2)
    if req.get("view"):  # the room as a splat, with the wearer (blue) and the places (yellow) marked
        t0 = time.time()
        nothing = {"points": np.zeros((0, 3)), "frames": np.zeros(0, int), "pixels": np.zeros(0, int), "member": np.zeros(0, int)}
        detail = {"after": list(range(len(base))), "valid": valid, "appeared": nothing, "gone": nothing}
        result["gaussians"] = splat.write_splat(recon, detail, out / "map.splat", marks=marks)
        seconds["splat"] = round(time.time() - t0, 2)
    result["seconds"] = seconds
    result["frames"] = {"baseline": len(base), "new": len(new)}
    result["median_depth"] = round(float(np.median(recon.depth)), 4)
    result.update(INFO)
    return result


def main():
    QUEUE.mkdir(exist_ok=True)
    BASELINES.mkdir(exist_ok=True)
    stop, alive = ROOT / f"STOP{LANE}", ROOT / f"worker{LANE}.alive"
    stop.unlink(missing_ok=True)
    import torch
    if DEVICE == "cpu":
        torch.set_num_threads(int(os.environ.get("SLURM_CPUS_PER_TASK", os.cpu_count() or 4)))
    model = rc.load_model(device=DEVICE)
    on_gpu = next(model.parameters()).is_cuda
    INFO.update({
        "device": torch.cuda.get_device_name(0) if on_gpu else f"CPU, {torch.get_num_threads()} threads",
        "on_gpu": on_gpu, "host": socket.gethostname().split(".")[0],
        "settings": {"baseline_frames": N_BASELINE, "new_frames": N_AFTER, "long_side": LONG_SIDE,
                     "locate_baseline_frames": N_LOCATE_BASE},
    })
    if ITEMS:
        try:
            ITEM_DETECTOR["model"] = items.load(device="cuda" if on_gpu else "cpu")
            INFO["settings"]["items"] = ITEM_DETECTOR["model"].settings(ITEMS)
        except Exception as error:  # the worker still serves checks and position fixes
            ITEM_DETECTOR["error"] = f"{type(error).__name__}: {error}"[:300]
            INFO["settings"]["items"] = {"model": items.MODEL_ID, "names": ITEMS, "error": ITEM_DETECTOR["error"]}
            print(f"Item detector NOT loaded: {traceback.format_exc()}", flush=True)
    (ROOT / f"worker{LANE}.json").write_text(json.dumps(INFO))
    print("worker ready", flush=True)
    while not stop.exists():
        alive.touch()  # heartbeat: the gateway reads this file's age, and reports "offline" when it goes stale
        for ready in sorted(QUEUE.glob("*/READY")):
            job = ready.parent
            ready.rename(job / "RUNNING")
            t0 = time.time()
            try:
                req = json.loads((job / "request.json").read_text())
                run = {"baseline": lambda: set_baseline(job, req), "locate": lambda: where(model, job, req)}
                result = run.get(req["op"], lambda: check(model, job, req))()
                (job / "result.json").write_text(json.dumps(result))
                (job / "RUNNING").rename(job / "DONE")
                print(f"{job.name} {req['op']} {req['zone']} done in {time.time() - t0:.1f} s", flush=True)
            except (Exception, SystemExit):  # one bad request must never stop the worker
                (job / "error.txt").write_text(traceback.format_exc())
                (job / "RUNNING").rename(job / "FAILED")
                print(f"{job.name} FAILED\n{traceback.format_exc()}", flush=True)
            alive.touch()
        time.sleep(0.25)
    alive.unlink(missing_ok=True)
    print("worker stopped", flush=True)


if __name__ == "__main__":
    main()
