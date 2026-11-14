#!/usr/bin/env python3
"""Build the lost-items test set: ordinary photos (COCO) with hand-labelled keys, glasses,
wallets, phones and remotes (LVIS v1 validation labels). No sign-up needed for either.

  curl -O https://dl.fbaipublicfiles.com/LVIS/lvis_v1_val.json.zip && unzip lvis_v1_val.json.zip
  python eval/items_fetch.py --lvis lvis_v1_val.json --out <folder>

LVIS labels are not complete for every photo: each photo lists the categories that were checked
and found (with every instance boxed, unless marked "not exhaustive") and the categories that
were checked and are NOT there. For each item we take photos that have it and photos checked not
to have it; an item that a photo says nothing about is left out of the scoring for that photo.

Writes <out>/set.json and the photos to <out>/img/. Nothing here goes into git.
"""
import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# our item name -> the LVIS category that means it
CATEGORY = {"keys": "key", "glasses": "spectacles", "wallet": "wallet", "phone": "cellular_telephone",
            "remote control": "remote_control"}


def spread(ids, n):
    ids = sorted(ids)
    if len(ids) <= n:
        return ids
    return [ids[round(i * (len(ids) - 1) / (n - 1))] for i in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lvis", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--with-item", type=int, default=70, help="photos per item that have it")
    ap.add_argument("--without", type=int, default=50, help="photos per item checked not to have it")
    args = ap.parse_args()
    out = Path(args.out)
    (out / "img").mkdir(parents=True, exist_ok=True)
    lvis = json.loads(Path(args.lvis).read_text())
    cat_id = {c["name"]: c["id"] for c in lvis["categories"]}
    ours = {cat_id[c]: item for item, c in CATEGORY.items()}
    images = {im["id"]: im for im in lvis["images"]}
    boxes = {}  # (image, item) -> [[x, y, w, h], ...]
    for a in lvis["annotations"]:
        if a["category_id"] in ours:
            boxes.setdefault((a["image_id"], ours[a["category_id"]]), []).append([round(v, 1) for v in a["bbox"]])
    chosen = set()
    for cid, item in ours.items():
        have = {i for (i, name) in boxes if name == item}
        lack = {i for i, im in images.items() if cid in im["neg_category_ids"]}
        print(f"{item}: {len(have)} photos have it, {len(lack)} are checked not to have it", flush=True)
        chosen |= set(spread(have, args.with_item)) | set(spread(lack, args.without))
    records = []
    for i in sorted(chosen):
        im = images[i]
        url = im["coco_url"]  # plain http: the photo server has no certificate for its own name
        record = {"id": i, "file": url.rsplit("/", 1)[1], "url": url, "width": im["width"], "height": im["height"],
                  "half": "tune" if i % 2 == 0 else "report", "items": {}}
        for cid, item in ours.items():
            if (i, item) in boxes:
                record["items"][item] = {"there": True, "all_boxed": cid not in im["not_exhaustive_category_ids"],
                                         "boxes": boxes[(i, item)]}
            elif cid in im["neg_category_ids"]:
                record["items"][item] = {"there": False}
        records.append(record)

    def fetch(record):
        path = out / "img" / record["file"]
        if not path.exists():
            request = urllib.request.Request(record["url"], headers={"User-Agent": "python-urllib"})
            path.write_bytes(urllib.request.urlopen(request, timeout=60).read())

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(fetch, records))
    (out / "set.json").write_text(json.dumps(records))
    print(f"{len(records)} photos in {out}", flush=True)


if __name__ == "__main__":
    main()
