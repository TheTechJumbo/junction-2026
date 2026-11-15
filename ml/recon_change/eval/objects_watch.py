#!/usr/bin/env python3
"""Object map evaluation, optional step: the watched-spot check (watch.py, branch jun-22-watch-spots)
asked "is this place as in the baseline?" for every inventory object in every held-out and after photo.

It needs the reconstruction's patch features, which eval/objects_dump.py did not keep, so the
position fixes are run again here: 12 baseline photos + 3 new photos per pass (the worker's
N_LOCATE frames), each new photo asked on its own. The inventory is rebuilt from the dump exactly
as eval/objects_eval.py does it, so the object ids are the same.

Saves <dump>/<scene>/watch.json: {"positions": {id: position}, "photos": {photo: {id: {"spot", "spot_score"}}}}.

  python eval/objects_watch.py --dump dump --scene Lounge [--device cpu] [--limit-inventory 0.2]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "eval"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--device", default=None)
    ap.add_argument("--limit-inventory", type=float, default=None)
    ap.add_argument("--fine", action="store_true")
    ap.add_argument("--per-pass", type=int, default=3)
    args = ap.parse_args()
    import torch
    import locate
    import objects
    import objects_dump
    import objects_eval
    import recon_change as rc
    import watch

    if args.device == "cpu":
        torch.set_num_threads(int(os.environ.get("SLURM_CPUS_PER_TASK", os.cpu_count() or 4)))
    folder = Path(args.dump) / args.scene
    meta = json.loads((folder / "meta.json").read_text())
    limit = objects.LIMIT_INVENTORY if args.limit_inventory is None else args.limit_inventory
    room_map = objects_eval.build_inventory(folder, meta, limit, args.fine)[0]
    print(f"{args.scene}: {sum(o['state'] == 'present' for o in room_map['objects'])} objects to watch", flush=True)
    model = rc.load_model(device=args.device)
    base_pictures = [objects_dump.read(meta["photos"][meta["baseline"][i]]) for i in meta["fix_base"]]
    base = list(range(len(base_pictures)))
    out = {"positions": {o["id"]: o["position"] for o in room_map["objects"]}, "limit_inventory": limit, "photos": {}}
    stems = meta["held"] + meta["after"]
    for start in range(0, len(stems), args.per_pass):
        part = stems[start:start + args.per_pass]
        t0 = time.time()
        r = rc.reconstruct(model, base_pictures + [objects_dump.read(meta["photos"][s]) for s in part])
        valid = rc.valid_mask(r, 25.0)
        room = locate.map_frame(r, base, valid)
        for k, stem in enumerate(part):
            out["photos"][stem] = objects.watched(room_map, r, base, len(base) + k, valid, room, watch)
            states = [v["spot"] for v in out["photos"][stem].values()]
            print(stem, {s: states.count(s) for s in ("same", "changed", "not_seen")}, flush=True)
        print(f"pass of {len(base)} + {len(part)} photos: {time.time() - t0:.1f} s", flush=True)
    (folder / "watch.json").write_text(json.dumps(out))
    print("done", flush=True)


if __name__ == "__main__":
    main()
