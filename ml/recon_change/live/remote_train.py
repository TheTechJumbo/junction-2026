#!/usr/bin/env python3
"""Train a Gaussian splat of a recording (or a video) on the GPU machine the workers run on: Iridis now, a Verda
machine later. Uses video_map.py from the code deployed there with the workers (iridis/deploy_from_laptop.sh).

    python live/remote_train.py FRAMES_OR_VIDEO NAME OUT [--host iridis] [--root DIR] [--launch slurm|direct]

Where it runs, as settings (the relay passes its own):
  BEACON_TRAIN_HOST    SSH host alias (default iridis)
  BEACON_REMOTE_ROOT   the deployed folder the workers use: code, rooms, recordings/, and trained/NAME for this
                       (default /scratch/$USER/junction-recon)
  BEACON_TRAIN_LAUNCH  slurm: submit iridis/video_map.slurm (Iridis). direct: start video_map.py in the background on
                       the machine itself and watch its process (a plain GPU machine, such as one rented from Verda)
  BEACON_REMOTE_PYTHON the Python there (default .venv-recon/bin/python, relative to the root)

The frames are either already there (remote_frames: the relay in cluster mode keeps recordings in ROOT/recordings), or
sent from the laptop: a folder of frames (only those not there yet, one zip) or a video file. Results come back into
OUT: trained.splat, untrained.splat, summary.json, cameras.npz and renders/. On the cluster everything stays under
ROOT, outside any repository.
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

DEFAULT_ROOT = "/scratch/$USER/junction-recon"
BRING = ["summary.json", "trained.splat", "untrained.splat", "cameras.npz", "flythrough.mp4", "views.json", "depths.bin"]
WAIT_S = 75 * 60  # the job's own limit is an hour


def _run(cmd, cwd=None, timeout=300):
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:2])} failed: {(done.stderr or done.stdout).strip()[-300:]}")
    return done.stdout


def _ssh(host, command, timeout=120):
    return _run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, f"bash -lc \"{command}\""], timeout=timeout)


def _scp(host, sources, target, cwd, timeout=900):
    """Copy files given relative to cwd (no drive letters for scp to mistake for a host) to host:target."""
    return _run(["scp", "-q", "-o", "BatchMode=yes", *sources, f"{host}:{target}"], cwd=cwd, timeout=timeout)


def _fetch(host, source, cwd, timeout=600):
    return subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{host}:{source}", "."], cwd=cwd, capture_output=True,
                          timeout=timeout).returncode == 0


def to_webm(path):
    """The fly-through as WebM (VP8), which browsers play: the cluster's OpenCV has no H.264 encoder and writes MPEG-4
    Part 2, which they do not. Returns the new file, or None without OpenCV here."""
    try:
        import cv2
    except ImportError:
        return None
    cap = cv2.VideoCapture(str(path))
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    target = Path(path).with_suffix(".webm")
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"VP80"), cap.get(cv2.CAP_PROP_FPS) or 24, size)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        writer.write(frame)
    writer.release()
    cap.release()
    return target if target.exists() and target.stat().st_size else None


def train(source, name, out, host=None, root=None, launch=None, steps=7000, keyframes=96, log=print, remote_frames=None):
    """Returns {"state": "done" or "failed", "job", "seconds", "summary", "error"}."""
    t0 = time.time()
    host = host or os.environ.get("BEACON_TRAIN_HOST") or "iridis"
    launch = launch or os.environ.get("BEACON_TRAIN_LAUNCH") or "slurm"
    python = os.environ.get("BEACON_REMOTE_PYTHON") or ".venv-recon/bin/python"
    root = _ssh(host, f"cd {root or os.environ.get('BEACON_REMOTE_ROOT') or DEFAULT_ROOT} && pwd").strip()
    if "video_map.py" not in _ssh(host, f"ls {root}").split():
        raise RuntimeError(f"video_map.py is not deployed in {host}:{root}: run iridis/deploy_from_laptop.sh first")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    data = f"{root}/trained/{name}"
    _ssh(host, f"mkdir -p {data}/frames {data}/out")
    if remote_frames:
        given = remote_frames
    else:
        source = Path(source)
        if source.is_dir():  # a recording's frames: only the new ones go
            have = set(_ssh(host, f"ls {data}/frames").split())
            new = [f for f in sorted(source.glob("*.jpg")) if f.name not in have]
            if new:
                with tempfile.TemporaryDirectory() as tmp:
                    with zipfile.ZipFile(Path(tmp) / "new.zip", "w", zipfile.ZIP_STORED) as bundle:
                        for f in new:
                            bundle.write(f, f.name)
                    _scp(host, ["new.zip"], f"{data}/new.zip", cwd=tmp)
                _ssh(host, f"cd {data} && {root}/{python} -m zipfile -e new.zip frames/ && rm new.zip")
            log(f"{len(new)} new frames sent ({len(have) + len(new)} there)")
            given = f"{data}/frames"
        else:  # a video
            _scp(host, [source.name], f"{data}/video{source.suffix}", cwd=source.parent)
            given = f"{data}/video{source.suffix}"
            log(f"video sent ({source.stat().st_size >> 20} MB)")
    options = f"--steps {steps} --keyframes {keyframes}"
    if launch == "slurm":
        job = _ssh(host, f"cd {root} && sbatch --parsable iridis/video_map.slurm {given} {data}/out {options}").strip()
        running = lambda: bool(_ssh(host, f"squeue -h -j {job}").strip())  # noqa: E731
        job_log = f"{root}/video-map-{job}.out"
    else:  # direct: a plain GPU machine, no queue
        job = _ssh(host, f"cd {root} && (nohup {python} -u video_map.py {given} {data}/out {options} > {data}/out/train.log 2>&1 & echo \\$!)").strip()
        running = lambda: _ssh(host, f"kill -0 {job} 2>/dev/null && echo yes || true").strip() == "yes"  # noqa: E731
        job_log = f"{data}/out/train.log"
    log(f"{launch} job {job} started on {host}")
    while time.time() - t0 < WAIT_S and running():
        time.sleep(20)
    for f in BRING:
        _fetch(host, f"{data}/out/{f}", cwd=out)
    (out / "renders").mkdir(exist_ok=True)
    _fetch(host, f"{data}/out/renders/*.jpg", cwd=out / "renders")
    if (out / "flythrough.mp4").exists():
        to_webm(out / "flythrough.mp4")
    if (out / "views.json").exists():  # the keyframes' photos for the playback, next to views.json as kfNNN.jpg
        (out / "keyframes").mkdir(exist_ok=True)
        _fetch(host, f"{data}/out/keyframes/*.jpg", cwd=out / "keyframes")
        for f in sorted((out / "keyframes").glob("*.jpg")):
            f.replace(out / f"kf{f.name}")
        (out / "keyframes").rmdir()
    try:
        summary = json.loads((out / "summary.json").read_text())
    except (OSError, ValueError):
        summary = {}
    ok = (out / "trained.splat").exists()
    error = None if ok else (summary.get("trained") or f"no trained splat: see {host}:{job_log}")
    log(f"job {job} {'done' if ok else 'failed'} in {time.time() - t0:.0f} s")
    return {"state": "done" if ok else "failed", "job": job, "seconds": round(time.time() - t0), "summary": summary,
            "error": error, "log": f"{host}:{job_log}"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a folder of frames or a video file on this laptop")
    ap.add_argument("name")
    ap.add_argument("out")
    ap.add_argument("--host")
    ap.add_argument("--root")
    ap.add_argument("--launch", choices=("slurm", "direct"))
    ap.add_argument("--steps", type=int, default=7000)
    ap.add_argument("--keyframes", type=int, default=96)
    ap.add_argument("--remote-frames", help="the frames are already on the GPU machine, in this folder there (source is ignored)")
    args = ap.parse_args()
    print(json.dumps(train(args.source, args.name, args.out, args.host, args.root, args.launch, args.steps, args.keyframes,
                           remote_frames=args.remote_frames), indent=1))
