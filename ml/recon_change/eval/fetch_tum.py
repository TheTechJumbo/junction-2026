#!/usr/bin/env python3
"""Download recordings of the TUM RGB-D benchmark for locate_walk_eval.py.

A person walks through a room holding a Kinect: 640x480 colour pictures at 30 a second, a depth
picture in real metres for each, and the camera's true position and direction from a
motion-capture system. Sturm et al., "A Benchmark for the Evaluation of RGB-D SLAM Systems",
IROS 2012. Licence: CC BY 4.0. No sign-up; the files are plain downloads linked from
https://cvg.cit.tum.de/data/datasets/rgbd-dataset/download (checked 2026-11-14).

    python fetch_tum.py --out data/tum                      # the recordings the test uses
    python fetch_tum.py --out data/tum freiburg1_desk       # just one

Each recording becomes <out>/<name>/ with rgb/, depth/, rgb.txt, depth.txt, groundtruth.txt.
The archive is deleted after unpacking. Nothing here belongs in git.
"""
import argparse
import shutil
import tarfile
import urllib.request
from pathlib import Path

BASE = "https://cvg.cit.tum.de/rgbd/dataset"
# name -> size of the .tgz in MB, as the server reported it on 2026-11-14
RECORDINGS = {
    "freiburg1_room": 746, "freiburg1_desk": 328, "freiburg1_desk2": 333, "freiburg1_360": 399, "freiburg1_xyz": 427,
    "freiburg2_desk": 1806, "freiburg2_xyz": 2100,
    "freiburg3_long_office_household": 1415,
}


def fetch(name, out):
    target = out / name
    if (target / "groundtruth.txt").exists():
        print(f"{name}: already there", flush=True)
        return
    url = f"{BASE}/{name.split('_')[0]}/rgbd_dataset_{name}.tgz"
    archive = out / f"{name}.tgz"
    print(f"{name}: downloading {url}", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "research-script"})
    with urllib.request.urlopen(request) as response, open(archive, "wb") as sink:
        shutil.copyfileobj(response, sink, 1 << 20)
    print(f"{name}: unpacking {archive.stat().st_size / 1e6:.0f} MB", flush=True)
    scratch = out / f"{name}.unpack"
    scratch.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar:  # a download is not trusted: plain files and folders below the target only
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                print(f"{name}: skipped {member.name}", flush=True)
                continue
            tar.extract(member, scratch)
    inner = scratch / f"rgbd_dataset_{name}"
    inner.rename(target)
    shutil.rmtree(scratch, ignore_errors=True)
    archive.unlink()
    print(f"{name}: done, {len(list((target / 'rgb').glob('*.png')))} colour pictures", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help=f"recordings to fetch (default: all of {', '.join(RECORDINGS)})")
    ap.add_argument("--out", default="data/tum")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in args.names or RECORDINGS:
        fetch(name, out)


if __name__ == "__main__":
    main()
