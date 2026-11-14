#!/usr/bin/env python3
"""Download the camera poses that come with PASLCD, for locate_eval.py. Data only.

Each scene instance has a COLMAP model (sparse/0/images.bin, about 25 MB) that holds one camera
pose for every photo of both captures, in one shared frame. COLMAP has no real-world scale, so
these poses are not in metres. Only the pose of each photo is kept: one small JSON per scene,
{photo name: [qw, qx, qy, qz, tx, ty, tz]} with x_cam = R(q) @ x_world + t.

    python fetch_poses.py --out poses            # run on a machine with internet
"""
import argparse
import concurrent.futures as cf
import json
import struct
import urllib.parse
import urllib.request
from pathlib import Path

RAW = "https://huggingface.co/datasets/ChamudithaJay/PASLCD/resolve/main/"
SCENES = ["Cantina", "Lounge", "Printing_area", "Lunch_room", "Meeting_room", "Garden", "Pots", "Zen", "Playground", "Porch"]


def parse_images_bin(buf):
    """COLMAP images.bin -> {photo name: [qw, qx, qy, qz, tx, ty, tz]}."""
    count = struct.unpack_from("<Q", buf, 0)[0]
    at, out = 8, {}
    for _ in range(count):
        _, qw, qx, qy, qz, tx, ty, tz, _ = struct.unpack_from("<I7dI", buf, at)
        at += 64
        end = buf.index(b"\0", at)
        name = buf[at:end].decode()
        points = struct.unpack_from("<Q", buf, end + 1)[0]
        at = end + 9 + 24 * points  # skip the 2D points: x, y, 3D point id
        out[name] = [qw, qx, qy, qz, tx, ty, tz]
    return out


def fetch(scene, instance, out):
    dest = out / f"{scene}_inst{instance}.json"
    if dest.exists():
        return scene, len(json.loads(dest.read_text()))
    url = RAW + urllib.parse.quote(f"{scene}/Instance_{instance}/sparse/0/images.bin")
    with urllib.request.urlopen(url, timeout=300) as r:
        poses = parse_images_bin(r.read())
    dest.write_text(json.dumps(poses))
    return scene, len(poses)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="poses")
    ap.add_argument("--scenes", nargs="*", default=SCENES)
    ap.add_argument("--instance", type=int, default=1)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(5) as pool:
        for scene, n in pool.map(lambda s: fetch(s, args.instance, out), args.scenes):
            print(f"{scene}: poses for {n} photos", flush=True)


if __name__ == "__main__":
    main()
