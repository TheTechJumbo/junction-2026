#!/usr/bin/env python3
"""Train a room's splat on the GPU server without the GPU server seeing the room (PRIVACY.md). The same call as remote_train.train, for
frames that are on this laptop (the trusted side):

    python live/private_train.py FRAMES NAME OUT [--host cluster] [--root DIR] [--side 768] [--steps 7000]

On the laptop, before anything leaves it:
  1. a session key K (32 random bytes), kept in OUT/private.key (hex, this laptop only) and nowhere else;
  2. every frame: faces and people blurred (anonymise.py), cut to its middle square and shrunk to --side pixels,
     colours garbled with derive(K, "garble") (garble.py), JPEG 4:4:4;
  3. the frames zipped and sealed with derive(K, "vault") (vault.py) and copied to ROOT/private/NAME/frames.zip.jv.
On the GPU server: cluster/private_train.slurm runs live/private_job.py, which waits for the vault key. Once the job runs, the key
goes into its memory through `srun --jobid --overlap` (vault.push_key); the job opens the frames in RAM, trains, and
seals its results. The job never gets the garbling key: its splat's colours are garbled too.
Back here: the sealed results are fetched and opened, the splats' colours decoded with the garbling key (trained.splat
is then an ordinary splat), the renders, bird's-eye view and fly-through un-garbled, and the keyframes written from this
laptop's own anonymised frames (kfNNN.jpg, for views.json), so no picture needs to come back from the GPU server.
OUT then looks like remote_train's: trained.splat, untrained.splat, summary.json, cameras.npz, views.json, depths.bin,
flythrough.webm, birdseye.jpg, renders/, kf000.jpg ...; OUT/private.json has the timings.
"""
import argparse
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import anonymise  # noqa: E402
import garble  # noqa: E402
import vault  # noqa: E402
from remote_train import DEFAULT_ROOT, _scp, _ssh, to_webm  # noqa: E402

BRIGHTNESS_LIMIT = 0.8  # garble.screened_key: a session key must leave less of the frames' brightness pattern than this
SIDE = 768  # the long side sent: video_map.py uses 518 for VGGT and the splat, a little more helps its labels
WAIT_S = 75 * 60


def prepare(path, g, side=SIDE, anonymised=True):
    """One frame as the trusted side sends it: (garbled JPEG bytes, the plain anonymised square, timings in ms)."""
    t0 = time.perf_counter()
    picture = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if picture is None:
        raise ValueError(f"{path.name}: not a picture")
    t1 = time.perf_counter()
    h, w = picture.shape[:2]
    if h > 1.05 * w:  # worker.square_up: a portrait frame's middle square (what VGGT and the splat use)
        picture = picture[(h - w) // 2:(h - w) // 2 + w]
    if max(picture.shape[:2]) > side:  # first: the detectors look at copies of 512 px at most anyway (anonymise.SIDES)
        s = side / max(picture.shape[:2])
        picture = cv2.resize(picture, (round(picture.shape[1] * s), round(picture.shape[0] * s)), interpolation=cv2.INTER_AREA)
    t2 = time.perf_counter()
    report = {"faces": 0, "people": 0}
    if anonymised:
        picture, report = anonymise.anonymise_image(picture)
    t3 = time.perf_counter()
    hidden = g.garble_bgr(picture)
    t4 = time.perf_counter()
    data = garble.encode_jpeg(hidden)
    t5 = time.perf_counter()
    ms = {"read": 1000 * (t1 - t0), "square": 1000 * (t2 - t1), "anonymise": 1000 * (t3 - t2), "garble": 1000 * (t4 - t3),
          "jpeg": 1000 * (t5 - t4)}
    return data, picture, ms, report


def decode_splat(path, g):
    """A .splat whose colours are garbled -> true colours (32-byte records, RGBA at bytes 24..27), in place."""
    rec = np.fromfile(path, dtype=np.uint8).reshape(-1, 32)
    for start in range(0, len(rec), 1 << 18):
        part = rec[start:start + (1 << 18)]
        part[:, 24:27] = np.clip(g.decode_colours(part[:, 24:27]) + 0.5, 0, 255).astype(np.uint8)
    rec.tofile(path)
    return len(rec)


def ungarble_video(path, g):
    cap = cv2.VideoCapture(str(path))
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    tmp = path.with_name("plain_" + path.name)
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), cap.get(cv2.CAP_PROP_FPS) or 24, size)
    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        writer.write(g.ungarble_bgr(frame))
        n += 1
    writer.release()
    cap.release()
    os.replace(tmp, path)
    return n


def _patient(host, command, tries=6, log=print):
    """_ssh again after a pause when the login node drops the connection (it resets ones that come too fast)."""
    for attempt in range(tries):
        try:
            return _ssh(host, command)
        except (RuntimeError, subprocess.TimeoutExpired) as err:
            if attempt == tries - 1 or not any(w in str(err) for w in ("Connection", "kex_exchange", "timed out", "banner")):
                raise
            log(f"ssh: {str(err)[-80:]}; again in {10 * (attempt + 1)} s")
            time.sleep(10 * (attempt + 1))


def train(source, name, out, host=None, root=None, steps=7000, keyframes=96, log=print, side=SIDE, workers=4, labels=None,
          keep_remote=True, resume=None):
    """Returns {"state": "done" or "failed", "job", "seconds", "summary", "error", "timing"} like remote_train.train.
    resume=JOB: that job was submitted before (with OUT/private.key): only push the key into it and bring the results back."""
    t_start = time.time()
    host = host or os.environ.get("BEACON_TRAIN_HOST") or "cluster"
    root = _patient(host, f"cd {root or os.environ.get('BEACON_REMOTE_ROOT') or DEFAULT_ROOT} && pwd", log=log).strip()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    frames = sorted(Path(source).glob("*.jpg"))
    if len(frames) < 3:
        raise ValueError("fewer than 3 frames")
    key_file = out / "private.key"
    screen = {}
    if resume:
        master = bytes.fromhex(key_file.read_text().strip())
    else:  # a key whose colour code hides these frames' brightness pattern (garble.screened_key), judged on 4 of them
        t0 = time.time()
        samples = [cv2.imread(str(frames[int(i)])) for i in np.linspace(0, len(frames) - 1, 4).round()]
        master, kept, tries = garble.screened_key([s for s in samples if s is not None], derive=lambda k: vault.derive(k, "garble"),
                                                  limit=BRIGHTNESS_LIMIT, make=vault.new_key)
        screen = {"brightness_kept": round(kept, 2), "keys_tried": tries, "seconds": round(time.time() - t0, 1)}
        log(f"session key: {tries} drawn, brightness kept {kept:.2f} (limit {BRIGHTNESS_LIMIT})")
        key_file.write_text(master.hex())
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    g = garble.Garbler(vault.derive(master, "garble"))
    vkey = vault.derive(master, "vault")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        prepared = list(pool.map(lambda p: prepare(p, g, side), frames))
    took = time.time() - t0
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as z:
        for path, (data, _, _, _) in zip(frames, prepared):
            z.writestr(path.name, data)
    sealed = vault.seal(vkey, bundle.getvalue()) if not resume else b""
    per = {k: round(float(np.median([p[2][k] for p in prepared])), 1) for k in prepared[0][2]}
    timing = {"frames": len(frames), "key_screening": screen, "prepare_s": round(took, 1), "ms_per_frame_wall": round(1000 * took / len(frames), 1),
              "ms_per_frame_one_thread": per, "workers": workers, "sent_mb": round(len(sealed) / 1e6, 2),
              "faces": sum(p[3]["faces"] for p in prepared), "people": sum(p[3]["people"] for p in prepared)}
    log(f"{len(frames)} frames anonymised, garbled and sealed in {took:.1f} s ({timing['ms_per_frame_wall']} ms a frame); "
        f"{timing['sent_mb']} MB to send")
    data_dir = f"{root}/private/{name}"
    if resume:
        job = str(int(resume))
    else:
        _patient(host, f"rm -rf {data_dir} && mkdir -p {data_dir}/out", log=log)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "frames.zip.jv").write_bytes(sealed)
            t0 = time.time()
            _scp(host, ["frames.zip.jv"], f"{data_dir}/frames.zip.jv", cwd=tmp)
            timing["upload_s"] = round(time.time() - t0, 1)
        options = f"--steps {steps} --keyframes {keyframes}" + (f" --labels {shlex.quote(labels)}" if labels is not None else "")
        job = _patient(host, f"cd {root} && sbatch --parsable cluster/private_train.slurm {data_dir} {options}", log=log).strip()
        log(f"private training job {job} submitted")

    def state():
        return _patient(host, f"squeue -h -j {job} -o %T", log=log).strip()

    pushed = False
    while time.time() - t_start < WAIT_S:
        now = state()
        if not now:
            break
        if now == "RUNNING" and not pushed:
            try:
                vault.push_key(vkey, job, host=host)
                pushed = True
                timing["key_pushed_after_s"] = round(time.time() - t_start, 1)
                log(f"key pushed into job {job}'s memory")
            except (RuntimeError, subprocess.TimeoutExpired) as err:  # the job has not opened its inbox yet, or ssh dropped
                if "no key inbox" not in str(err):
                    log(f"key push: {str(err)[-120:]}")
        time.sleep(15 if not pushed else 30)
    got = bring_back(host, data_dir, out, vkey, g, [p[1] for p in prepared], log)
    if not keep_remote:
        _patient(host, f"rm -rf {data_dir}", log=log)
    timing["total_s"] = round(time.time() - t_start, 1)
    timing["decode_s"] = got.pop("decode_s", None)
    (out / "private.json").write_text(json.dumps(timing, indent=1))
    log(f"job {job} {got['state']} in {timing['total_s']:.0f} s")
    return {**got, "job": job, "seconds": round(time.time() - t_start), "timing": timing, "log": f"{host}:{root}/private-train-{job}.out"}


def bring_back(host, data_dir, out, vkey, g, plain_squares, log=print):
    """Fetch DATA/out/*.jv, open them, decode colours; the keyframes from this laptop's own frames."""
    out = Path(out)
    with tempfile.TemporaryDirectory() as tmp:
        for attempt in range(4):  # the login node drops connections that come too fast
            if subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{host}:{data_dir}/out/*.jv", "."], cwd=tmp, capture_output=True,
                              timeout=900).returncode == 0:
                break
            time.sleep(15 * (attempt + 1))
        t0 = time.time()
        for sealed in sorted(Path(tmp).glob("*.jv")):
            name = sealed.name[:-3].replace("__", "/")
            target = out / name
            target.parent.mkdir(parents=True, exist_ok=True)
            vault.unseal_file(vkey, sealed, target)
    for f in ("trained.splat", "untrained.splat"):
        if (out / f).exists():
            log(f"{f}: {decode_splat(out / f, g)} Gaussians decoded")
    for f in [out / "birdseye.jpg", *sorted((out / "renders").glob("*.jpg"))] if (out / "renders").exists() else [out / "birdseye.jpg"]:
        if f.exists():
            cv2.imwrite(str(f), g.ungarble_bgr(cv2.imread(str(f))), [cv2.IMWRITE_JPEG_QUALITY, 92])
    if (out / "flythrough.mp4").exists():
        ungarble_video(out / "flythrough.mp4", g)
        to_webm(out / "flythrough.mp4")
    try:
        summary = json.loads((out / "summary.json").read_text())
    except (OSError, ValueError):
        summary = {}
    for q, n in enumerate(summary.get("keyframe_numbers", [])):  # the playback's photos, from the laptop's own frames
        if 0 <= n < len(plain_squares):
            cv2.imwrite(str(out / f"kf{q:03d}.jpg"), cv2.resize(plain_squares[n], (518, 518), interpolation=cv2.INTER_AREA),
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
    ok = (out / "trained.splat").exists()
    return {"state": "done" if ok else "failed", "summary": summary, "decode_s": round(time.time() - t0, 1),
            "error": None if ok else (summary.get("trained") or "no trained splat")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames")
    ap.add_argument("name", help="a test name first (never a real zone until it works), e.g. privtest-hall")
    ap.add_argument("out")
    ap.add_argument("--host")
    ap.add_argument("--root")
    ap.add_argument("--side", type=int, default=SIDE)
    ap.add_argument("--steps", type=int, default=7000)
    ap.add_argument("--keyframes", type=int, default=96)
    ap.add_argument("--labels", default=None, help="video_map.py --labels (default: its own list)")
    ap.add_argument("--resume", metavar="JOB", help="a job submitted before with this OUT: push its key, bring its results back")
    a = ap.parse_args()
    result = train(a.frames, a.name, a.out, host=a.host, root=a.root, steps=a.steps, keyframes=a.keyframes, side=a.side, labels=a.labels,
                   resume=a.resume)
    print(json.dumps({k: v for k, v in result.items() if k != "summary"}, indent=1))
    return 0 if result["state"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
