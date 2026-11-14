#!/usr/bin/env python3
"""Gateway. Runs on the Iridis login node, at the far end of the relay's one SSH connection.

It speaks a tiny protocol on stdin/stdout so that a single connection carries every request:
a JSON line, then `size` raw bytes. Requests and replies have the same shape.

  {"op": "status"}
  {"op": "baseline", "zone": "kitchen", "size": N} + N bytes of video
  {"op": "check",    "zone": "kitchen", "size": N} + N bytes of video

It only moves files: it drops the clip into queue/<job>/ for the GPU worker (see worker.py)
and waits for the result. Standard library only, so it runs on any Python 3.
"""
import io
import json
import re
import sys
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE, BASELINES = ROOT / "queue", ROOT / "baselines"
IN, OUT = sys.stdin.buffer, sys.stdout.buffer
WAIT_S = 180  # give up on a job after this long
STALE_S = 90  # the worker's heartbeat is older than this: treat it as not running


def reply(header, blob=b""):
    header["size"] = len(blob)
    OUT.write(json.dumps(header).encode() + b"\n" + blob)
    OUT.flush()


def lane(device):
    """The GPU worker and the CPU worker each have their own queue folder and heartbeat file."""
    return "-cpu" if device == "cpu" else ""


def worker_age(device="gpu"):
    """Seconds since that worker's last heartbeat (it touches worker.alive), or None if it never started.
    Uses the file's modification time: reading a timestamp out of the file can catch it half-written."""
    try:
        return round(max(0.0, time.time() - (ROOT / f"worker{lane(device)}.alive").stat().st_mtime), 1)
    except OSError:
        return None


def worker_state(device):
    age = worker_age(device)
    running = age is not None and age < STALE_S
    try:
        info = json.loads((ROOT / f"worker{lane(device)}.json").read_text()) if running else None
    except (OSError, ValueError):
        info = None
    return {"running": running, "age_s": age, "info": info}


def handle(req, blob):
    op = req.get("op")
    device = "cpu" if req.get("device") == "cpu" else "gpu"
    age = worker_age(device)
    if op == "status":
        zones = sorted(p.name for p in BASELINES.glob("*") if any(p.glob("*.jpg")))
        workers = {d: worker_state(d) for d in ("gpu", "cpu")}
        return {"ok": True, "worker_running": any(w["running"] for w in workers.values()), "workers": workers, "zones": zones}, b""
    if op not in ("baseline", "check") or not re.fullmatch(r"[a-z0-9_-]{1,32}", str(req.get("zone", ""))):
        return {"ok": False, "error": "bad request: op must be baseline or check, zone must be a-z 0-9 _ -"}, b""
    if not blob:
        return {"ok": False, "error": "empty clip"}, b""
    if age is None or age > STALE_S:
        script = "worker_cpu.slurm" if device == "cpu" else "worker.slurm"
        return {"ok": False, "error": f"the {device.upper()} worker is not running on Iridis (sbatch iridis/{script})"}, b""

    job = ROOT / f"queue{lane(device)}" / f"{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
    job.mkdir(parents=True)
    clip = "clip.zip" if blob[:2] == b"PK" else "clip.mp4"  # a zip of frames from the relay, or a video
    (job / clip).write_bytes(blob)
    (job / "request.json").write_text(json.dumps({"op": op, "zone": req["zone"], "file": clip}))
    (job / "READY").touch()  # last, so the worker never sees a half-written job

    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        if (job / "DONE").exists():
            images = io.BytesIO()  # every picture the worker produced, as one zip
            with zipfile.ZipFile(images, "w", zipfile.ZIP_STORED) as bundle:
                for made in sorted((job / "out").glob("*")):
                    if made.suffix in (".jpg", ".splat"):
                        bundle.write(made, made.name)
            return {"ok": True, "job": job.name, "result": json.loads((job / "result.json").read_text())}, images.getvalue()
        if (job / "FAILED").exists():
            lines = (job / "error.txt").read_text().strip().splitlines()
            return {"ok": False, "job": job.name, "error": lines[-1] if lines else "worker failed"}, b""
        time.sleep(0.2)
    return {"ok": False, "job": job.name, "error": f"no result after {WAIT_S} s"}, b""


def main():
    reply({"ok": True, "hello": "gateway"})
    while True:
        line = IN.readline()
        if not line:
            break  # the relay closed the connection
        try:
            req = json.loads(line)
            blob = IN.read(int(req.get("size", 0)))
            header, data = handle(req, blob)
        except Exception as err:  # never let one bad request kill the connection
            header, data = {"ok": False, "error": f"{type(err).__name__}: {err}"}, b""
        reply(header, data)


if __name__ == "__main__":
    main()
