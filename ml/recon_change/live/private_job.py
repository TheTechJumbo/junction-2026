#!/usr/bin/env python3
"""The GPU side of a private training (live/private_train.py, PRIVACY.md): the GPU server is treated as untrusted. Started by
cluster/private_train.slurm in DATA = ROOT/private/NAME, which holds frames.zip.jv: anonymised, garbled frames, sealed.

    python live/private_job.py DATA [video_map.py options]

1. Opens a key inbox (vault.KeyInbox: a FIFO in /dev/shm on this node, named after the Slurm job) and waits for the
   laptop to push the vault key into it (vault.push_key, through srun). The key is only ever in this process's memory.
2. Opens frames.zip.jv in memory and writes the frames to /dev/shm/jpriv-<uid>-<job> (RAM on this node, mode 0700).
   They are garbled: this job never gets the garbling key and cannot turn them back into photos.
3. Runs video_map.py there (VGGT, the splat, its fly-through). Nothing it writes goes to scratch.
4. Seals every result into DATA/out/<file>.jv on scratch, then deletes the RAM folder and the inbox.
"""
import io
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vault  # noqa: E402

RESULTS = ["summary.json", "trained.splat", "untrained.splat", "cameras.npz", "flythrough.mp4", "views.json", "depths.bin",
           "labels.json", "birdseye.jpg", "birdseye.json"]
WAIT_KEY_S = 900


def log(text):
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    data, options = Path(argv[0]).resolve(), argv[1:]
    sealed = data / "frames.zip.jv"
    with open(sealed, "rb") as f:
        kid = vault.read_header(f)[0]
    job = os.environ.get("SLURM_JOB_ID") or f"pid{os.getpid()}"
    inbox = vault.KeyInbox(vault.inbox_path(job))
    work = Path("/dev/shm") / f"jpriv-{os.getuid()}-{job}"
    try:
        log(f"key inbox ready for job {job}; waiting for key {kid.hex()}")
        key = inbox.get(kid, wait=WAIT_KEY_S)
        if key is None:
            raise SystemExit(f"no key arrived in {WAIT_KEY_S} s")
        log("key received (in memory only)")
        work.mkdir(mode=0o700)
        frames = work / "frames"
        frames.mkdir()
        t0 = time.time()
        with zipfile.ZipFile(io.BytesIO(vault.unseal(key, sealed.read_bytes()))) as bundle:
            names = [n for n in bundle.namelist() if n.lower().endswith(".jpg") and "/" not in n and not n.startswith(".")]
            for n in names:
                (frames / n).write_bytes(bundle.read(n))
        log(f"{len(names)} garbled frames opened into RAM in {time.time() - t0:.1f} s")
        done = subprocess.run([sys.executable, "-u", str(HERE.parent / "video_map.py"), str(frames), str(work / "out"), *options],
                              cwd=str(HERE.parent))
        log(f"video_map.py ended with {done.returncode}")
        out = data / "out"
        out.mkdir(exist_ok=True)
        sealed_n = 0
        for name in RESULTS + [f"renders/{p.name}" for p in sorted((work / "out" / "renders").glob("*.jpg"))]:
            src = work / "out" / name
            if src.is_file():
                target = out / (name.replace("/", "__") + ".jv")
                vault.seal_file(key, src, target)
                sealed_n += 1
        log(f"{sealed_n} results sealed into {out}")
        return done.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)
        inbox.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
