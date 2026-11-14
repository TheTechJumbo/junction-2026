#!/usr/bin/env python3
"""What else is in each lost-items test photo, from the COCO labels (which, unlike LVIS, box
every person and every one of 80 common things in every photo). Used to pick out the photos that
look like our case: the item lies somewhere, nobody holds or wears it, not much else around.

  curl -O http://images.cocodataset.org/annotations/annotations_trainval2017.zip
  unzip annotations_trainval2017.zip annotations/instances_train2017.json annotations/instances_val2017.json
  python eval/items_context.py --set <folder>/set.json --coco annotations --out <folder>/context.json

Writes, per photo id: every COCO box as [name, x, y, w, h]. No sign-up is needed for the labels.
"""
import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--coco", required=True, help="folder with instances_train2017.json and instances_val2017.json")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    wanted = {r["id"] for r in json.loads(Path(args.set).read_text())}
    context = {}
    for part in ("val2017", "train2017"):
        coco = json.loads((Path(args.coco) / f"instances_{part}.json").read_text())
        name = {c["id"]: c["name"] for c in coco["categories"]}
        seen = {im["id"] for im in coco["images"]} & wanted
        for i in seen:
            context.setdefault(str(i), [])
        for a in coco["annotations"]:
            if a["image_id"] in seen:
                context[str(a["image_id"])].append([name[a["category_id"]]] + [round(v, 1) for v in a["bbox"]])
        print(f"{part}: {len(seen)} of our photos", flush=True)
        del coco
    Path(args.out).write_text(json.dumps(context))
    print(f"{len(context)} of {len(wanted)} photos have COCO labels", flush=True)


if __name__ == "__main__":
    main()
