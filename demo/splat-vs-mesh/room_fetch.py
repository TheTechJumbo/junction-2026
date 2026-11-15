"""Fetch the public Mip-NeRF 360 "room" for ?scene=room: its Gaussian splat and some of the photos it was
trained from (Barron et al., CVPR 2022). Nothing here is ours, so none of it is committed (room/ is git-ignored).

    python room_fetch.py            # room/room.splat (51 MB) and room/photos/ (12 photos, 1280 px)
    python room_fetch.py --every 8  # more photos (the scene has 311)

Then build the block meshes and the example night from the splat:

    python room_convert.py room/room.splat room
    python night_demo.py room
"""
import argparse
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPLAT = "https://huggingface.co/cakewalk/splat-data/resolve/main/room.splat"
PHOTOS = "https://huggingface.co/api/datasets/nvs-bench/mipnerf360/tree/main/room/images"
PHOTO = "https://huggingface.co/datasets/nvs-bench/mipnerf360/resolve/main/{}"
CREDIT = "the photos the splat was made from · Mip-NeRF 360, Barron et al. 2022"


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "splat-demo"}), timeout=120) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(HERE / "room"))
    ap.add_argument("--every", type=int, default=26, help="take every Nth photo (default 26: 12 of 311)")
    ap.add_argument("--size", type=int, default=1280, help="longer side of the saved photos, px")
    args = ap.parse_args()
    out = Path(args.out)
    (out / "photos").mkdir(parents=True, exist_ok=True)
    splat = out / "room.splat"
    if not splat.exists():
        print("room.splat ...", flush=True)
        splat.write_bytes(get(SPLAT))
    try:
        import cv2
        import numpy as np
    except ImportError:
        cv2 = None
    files = [x["path"] for x in json.loads(get(PHOTOS)) if x["path"].lower().endswith(".jpg")][:: args.every]
    items = []
    for path in files:
        name = Path(path).stem.lower() + ".jpg"
        data = get(PHOTO.format(path))
        if cv2 is not None:  # smaller photos load quicker in the page
            im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            s = min(1.0, args.size / max(im.shape[:2]))
            im = cv2.resize(im, (round(im.shape[1] * s), round(im.shape[0] * s)), interpolation=cv2.INTER_AREA)
            data = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        (out / "photos" / name).write_bytes(data)
        items.append({"file": name, "caption": f"Mip-NeRF 360 photo {Path(path).stem}"})
        print(name, flush=True)
    json.dump({"photos": items, "credit": CREDIT, "source": "huggingface.co/datasets/nvs-bench/mipnerf360"},
              open(out / "photos" / "photos.json", "w"), indent=1)
    print(f"{len(items)} photos and room.splat in {out}")


if __name__ == "__main__":
    main()
