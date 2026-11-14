#!/usr/bin/env python3
"""Object map evaluation, step 2: everything that needs no model, from what eval/objects_dump.py saved.

Per scene:
  1. inventory from the 24 baseline photos            -> objects, contact sheets to count by eye
  2. update with the 12 held-out photos of the same,  -> every event is a false alarm
     unchanged room (the control)
  3. update with the 12 photos taken after the change -> events against the hand-drawn change masks
  4. where is X: every object of the inventory looked up in the held-out photos

A labelled changed region is one connected blob of a mask in one photo, at least 50 pixels at
518 x 294, as in eval/run_bench.py. It is "hit" when a reported event's place (for a move: either
end) lands in it, or within the object's own radius of it, in that photo. An event is "on a labelled
change" when its place lands on the mask in at least half of the after photos that have it in view.
The masks do not say what kind of change a region is, so "the right kind" is judged by eye from
the event sheets this script writes (before | after).

  python eval/objects_eval.py --dump dump --scene Lounge --out out
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import items  # noqa: E402
import locate  # noqa: E402
import objects  # noqa: E402
import recon_change as rc  # noqa: E402

T0 = 1_800_000_000.0
SECONDS = {}  # how long the logic took, per call
SIZES = [(50, 200, "small"), (200, 800, "medium"), (800, None, "large")]  # region sizes of eval/run_bench.py
WINDOW = (0.0, 0.0, 1.0, 1.0)  # the detector's picture and the model's frame show the same (16:9 both)


def load_boxes(path, phrases):
    z = np.load(path)
    return {p: (z["boxes"][z["phrase"] == i].astype(np.float64), z["scores"][z["phrase"] == i].astype(np.float64)) for i, p in enumerate(phrases)}


def detections(folder, stem, phrases, limit):
    return items.merge(load_boxes(folder / f"{stem}.npz", phrases), list(objects.VOCABULARY), limit, phrases=objects.phrases_for)


class Photos:
    """The photos at the size the detector saw them, read when first asked for."""

    def __init__(self, meta):
        self.meta, self.kept = meta, {}

    def __call__(self, stem):
        if stem not in self.kept:
            picture = cv2.imread(self.meta["photos"][stem])
            scale = self.meta["side"] / max(picture.shape[:2])
            self.kept[stem] = cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else picture
        return self.kept[stem]


def crop(picture, u, v, half, size=220, box=None, text=()):
    """A square piece of a picture around (u, v) in 0..1, with a ring at the point or a box, and lines of text."""
    H, W = picture.shape[:2]
    half = int(min(max(half, 50), min(H, W) // 2))
    x, y = int(np.clip(u * W - half, 0, W - 2 * half)), int(np.clip(v * H - half, 0, H - 2 * half))
    piece = picture[y:y + 2 * half, x:x + 2 * half].copy()
    scale = size / piece.shape[0]
    piece = cv2.resize(piece, (size, size), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    if box is not None:
        a = (int((box[0] * W - x) * scale), int((box[1] * H - y) * scale))
        b = (int((box[2] * W - x) * scale), int((box[3] * H - y) * scale))
        cv2.rectangle(piece, a, b, (255, 255, 255), 2)
        cv2.rectangle(piece, a, b, (200, 90, 255), 1)
    else:
        centre = (int((u * W - x) * scale), int((v * H - y) * scale))
        cv2.circle(piece, centre, 9, (255, 255, 255), 3)
        cv2.circle(piece, centre, 9, (0, 0, 255), 1)
    for k, line in enumerate(text):
        cv2.putText(piece, line, (4, 14 + 15 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(piece, line, (4, 14 + 15 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    return piece


def sheets(tiles, path, per_row=8, rows=5):
    """Write tiles as numbered sheets <path>_0.jpg, ...; returns the file names."""
    names = []
    if not tiles:
        return names
    blank = np.zeros_like(tiles[0])
    for n, start in enumerate(range(0, len(tiles), per_row * rows)):
        part = tiles[start:start + per_row * rows]
        part += [blank] * (-len(part) % per_row)
        sheet = np.vstack([np.hstack(part[i:i + per_row]) for i in range(0, len(part), per_row)])
        name = f"{path}_{n}.jpg"
        cv2.imwrite(name, sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
        names.append(name)
    return names


class Fix:
    """One saved position fix: the reconstruction pieces, the room map transform, the newest frame's index."""

    def __init__(self, folder, stem, info):
        z = np.load(folder / "fix" / f"{stem}.npz")
        K, E = z["K"], z["E"]
        self.q = len(K) - 1
        depth, valid = z["depth"], z["valid"]
        self.full = depth.ndim == 3  # every frame's depth was saved (needed for the baseline votes)
        if not self.full:
            d, v = np.zeros((len(K), *depth.shape), np.float32), np.zeros((len(K), *depth.shape), bool)
            d[self.q], v[self.q] = depth, valid
            depth, valid = d, v
        self.r = rc.Recon(depth=depth, conf=valid.astype(np.float32), K=K, E=E, rgb=None)
        self.valid = valid
        self.base = list(range(self.q))
        self.room = {"origin": z["origin"], "east": z["east"], "north": z["north"], "up": z["up"], "metres": float(z["metres"])}
        self.stem, self.info = stem, info
        self.near = {j: rc.nearest_in_window(depth[j][None], 7)[0] for j in self.base} if self.full else None

    def project(self, position):
        """A map position in this fix's newest frame: (u, v in 0..1, depth along the view, or None if behind)."""
        world = locate.from_map(self.room, position)[0]
        K, E = self.r.K[self.q], self.r.E[self.q]
        cam = E[:, :3] @ world + E[:, 3]
        if cam[2] <= 1e-6:
            return None
        H, W = self.r.depth[self.q].shape
        return (K[0, 0] * cam[0] / cam[2] + K[0, 2]) / (W - 1), (K[1, 1] * cam[1] / cam[2] + K[1, 2]) / (H - 1), float(cam[2])


def regions_of(mask_path, H, W):
    m = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    gt = (cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST) > 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(gt, connectivity=8)
    return labels, {i: int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 50}


def size_class(area):
    return next(name for lo, hi, name in SIZES if area >= lo and (hi is None or area < hi))


def build_inventory(folder, meta, limit, fine=False):
    """The object map of a scene from its saved baseline reconstruction and boxes. Returns (map, r, valid, room)."""
    z = np.load(folder / "inv.npz")
    r = rc.Recon(depth=z["depth"], conf=z["conf"], K=z["K"], E=z["E"], rgb=z["rgb"])
    valid = rc.valid_mask(r, 25.0)
    room = locate.map_frame(r, meta["fix_base"], valid)
    found = [detections(folder / ("det2" if fine else "det"), stem, meta["phrases"], limit) for stem in meta["baseline"]]
    room_map = objects.inventory(found, r, base=meta["fix_base"], valid=valid, now=T0, room=room, picture_shape=tuple(meta["picture_shape"]), limit=limit)
    room_map["boxes"] = int(sum(len(f) for f in found))
    return room_map, r, valid, room


def run_updates(room_map, fixes, folder, phrases, args, picture_shape, record=None, verdicts=None):
    """Fold fixes into a copy of the map, in order. Returns (map, events reported on the way).
    verdicts: {photo: {object id: {"spot", "spot_score"}}} from eval/objects_watch.py, if it was run."""
    told = []
    for k, fix in enumerate(fixes):
        found = detections(folder / "det", fix.stem, phrases, args.limit_confirm)
        state = fix.info["state"]
        t0 = time.time()
        seen = objects.lift(found, fix.r, fix.q, fix.valid, WINDOW, fix.room, state,
                            base=fix.base if fix.full and not args.no_votes else None, near=fix.near)
        checked = [] if state == "lost" else objects.visibility(room_map, fix.r, fix.q, fix.valid, WINDOW, fix.room, picture_shape)
        for c in checked:
            c.update((verdicts or {}).get(fix.stem, {}).get(c["id"], {}))
        if record is not None:
            record.append((fix, seen, checked))
        t1 = time.time()
        room_map, new = objects.update(room_map, seen, {"state": state, "position": fix.info["position"], "heading_deg": fix.info["heading_deg"],
                                                        "checked": checked}, now=T0 + 600 + 5 * k, limit_confirm=args.limit_confirm,
                                       limit_new=args.limit_new, report_pct=args.report_pct, use_depth=not args.no_depth, use_spot=not args.no_spot)
        told += new
        SECONDS.setdefault("place_and_look", []).append(t1 - t0)  # objects.lift + objects.visibility (numpy, in the worker)
        SECONDS.setdefault("update", []).append(time.time() - t1)  # objects.update (plain Python, in the relay)
    return room_map, told


def event_points(event):
    return [(end, event[end]) for end in ("from", "to") if event.get(end)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit-inventory", type=float, default=objects.LIMIT_INVENTORY)
    ap.add_argument("--limit-confirm", type=float, default=objects.LIMIT_CONFIRM)
    ap.add_argument("--limit-new", type=float, default=objects.LIMIT_NEW)
    ap.add_argument("--report-pct", type=float, default=objects.REPORT_PCT)
    ap.add_argument("--no-depth", action="store_true", help="a clear miss counts the same whatever the depth at the place says")
    ap.add_argument("--no-votes", action="store_true", help="do not ask the baseline frames whether a new sighting's place was empty")
    ap.add_argument("--fine", action="store_true", help="inventory from the finer look (2 rows of tiles)")
    ap.add_argument("--no-spot", action="store_true", help="do not use the watched-spot verdicts even if eval/objects_watch.py saved them")
    ap.add_argument("--no-sheets", action="store_true")
    ap.add_argument("--labels", help="json with the by-eye verdicts, to tabulate against confidence")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    folder = Path(args.dump) / args.scene
    meta = json.loads((folder / "meta.json").read_text())
    out = Path(args.out) / (args.scene + args.tag)
    out.mkdir(parents=True, exist_ok=True)
    phrases, photos = meta["phrases"], Photos(meta)
    shape = tuple(meta["picture_shape"])
    summary = {"scene": args.scene, "settings": {k: v for k, v in vars(args).items() if k not in ("dump", "out", "labels")}}

    # ---- 1. inventory
    t0 = time.time()
    room_map, r, valid, room = build_inventory(folder, meta, args.limit_inventory, args.fine)
    summary["seconds_inventory_logic"] = round(time.time() - t0, 2)  # reading the saved boxes, placing, fusing, counting clear looks
    objs = room_map["objects"]
    shown = [o for o in objs if o["state"] == "present"]
    summary["inventory"] = {"boxes": room_map["boxes"], "placed": room_map["sightings"], "objects": len(shown),
                            "unconfirmed": len(objs) - len(shown), "labels": sorted({o["label"] for o in shown}),
                            "views_median": float(np.median([o["views"] for o in shown])) if shown else None}
    print(f"{args.scene}: {summary['inventory']['boxes']} boxes in 24 photos, {room_map['sightings']} placed -> {len(shown)} objects, "
          f"{len(objs) - len(shown)} unconfirmed")
    for o in sorted(shown, key=lambda o: (o["label"], o["position"])):
        print(f"  {o['id']:>5} {o['label']:<16} {o['confidence_pct']:>3}%  views {o['views']:>2}/{o['could_see']:<2} best {o['best_score']:.2f} "
              f"size {o['size_m']:.2f} m at {o['position']}  names {list(o['names'])}")
    (out / "map.json").write_text(json.dumps(room_map))
    if not args.no_sheets:
        tiles = []
        for o in sorted(objs, key=lambda o: -o["confidence_pct"]):
            if o["confidence_pct"] < 40:
                continue
            b = o["best_box"]
            picture = photos(meta["baseline"][o["best_view"]])
            half = 1.6 * max((b[2] - b[0]) * picture.shape[1], (b[3] - b[1]) * picture.shape[0])
            tiles.append(crop(picture, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, half, box=b,
                              text=(f"{o['id']} {o['label']}", f"{o['confidence_pct']}% v{o['views']}/{o['could_see']} s{o['best_score']:.2f}")))
        summary["inventory"]["sheets"] = sheets(tiles, str(out / "inventory"))

    # ---- 2 and 3. updates: the unchanged control, then the changed room
    fixes = {kind: [Fix(folder, f["photo"], f) for f in meta["fixes"] if f["kind"] == kind] for kind in ("held", "after")}
    full = all(f.full for f in fixes["held"] + fixes["after"])
    summary["fix_states"] = {kind: [f.info["state"] for f in fixes[kind]] for kind in fixes}
    record = []
    verdicts = None
    if (folder / "watch.json").exists() and not args.no_spot:  # keyed by object id: only valid for the same inventory
        saved = json.loads((folder / "watch.json").read_text())
        same = {o["id"]: o["position"] for o in objs} == saved["positions"]
        verdicts = saved["photos"] if same else None
        print("watched-spot verdicts:", "used" if same else "NOT used (made for another inventory)")
    summary["watched_spots"] = verdicts is not None
    if verdicts:
        counts = {}
        for photo in verdicts.values():
            for v in photo.values():
                counts[v["spot"]] = counts.get(v["spot"], 0) + 1
        summary["watched_spot_verdicts"] = counts
    held_map, held_told = run_updates(room_map, fixes["held"], folder, phrases, args, shape, record, verdicts)
    after_map, after_told = run_updates(room_map, fixes["after"], folder, phrases, args, shape, verdicts=verdicts)

    def brief(e):
        return {k: e.get(k) for k in ("id", "kind", "label", "status", "confidence_pct", "from", "to", "text", "object")}

    summary["control"] = {"reported": [brief(e) for e in objects.diary(held_map)],
                          "unconfirmed": [brief(e) for e in held_map["events"] if e["status"] == "unconfirmed"],
                          "states": {s: sum(o["state"] == s for o in held_map["objects"]) for s in ("present", "moved", "missing", "unconfirmed")}}
    print(f"control (12 held-out photos, nothing changed): {len(summary['control']['reported'])} events reported (all false), "
          f"{len(summary['control']['unconfirmed'])} held back as unconfirmed; baseline votes available: {full}")
    for e in summary["control"]["reported"]:
        print("   FALSE", e["id"], e["kind"], e["label"], f"{e['confidence_pct']}%")

    H, W = fixes["after"][0].r.depth.shape[1:]
    gt = {f.stem: regions_of(meta["masks"][f.stem], H, W) for f in fixes["after"]}
    reported = objects.diary(after_map)
    waiting = [e for e in after_map["events"] if e["status"] == "unconfirmed"]
    by_id = {o["id"]: o for o in after_map["objects"]}
    hit_regions = {}  # (photo, region) -> event ids

    def land(event, fix):
        """Where the event's ends land in one after photo: [(end, u, v, radius px, region ids touched, on the mask)]."""
        labels, areas = gt[fix.stem]
        size = (by_id.get(event["object"]) or {}).get("size_m") or 0.1
        rows = []
        for end, position in event_points(event):
            p = fix.project(position)
            if p is None or not (0.02 <= p[0] <= 0.98 and 0.02 <= p[1] <= 0.98):
                continue
            x, y = p[0] * (W - 1), p[1] * (H - 1)
            radius = float(np.clip(0.5 * size / fix.room["metres"] / p[2] * fix.r.K[fix.q][0, 0], 6, 25))
            yy, xx = np.ogrid[:H, :W]
            disc = (xx - x) ** 2 + (yy - y) ** 2 <= radius ** 2
            touched = [int(i) for i in np.unique(labels[disc]) if i in areas]
            rows.append((end, p[0], p[1], radius, touched, bool((labels[disc] > 0).any())))
        return rows

    def judge(event):
        seen = on = 0
        for fix in fixes["after"]:
            rows = land(event, fix)
            if rows:
                seen += 1
                on += any(row[5] for row in rows)
                if event["status"] == "reported":
                    for row in rows:
                        for region in row[4]:
                            hit_regions.setdefault((fix.stem, region), []).append(event["id"])
        return {"photos_in_view": seen, "photos_on_mask": on, "on_labelled_change": bool(seen and on >= 0.5 * seen)}

    scored = []
    for event in reported + waiting:
        scored.append({**brief(event), **judge(event)})
    summary["after"] = {"events": scored}
    by_size = {name: [0, 0] for _, _, name in SIZES}
    for fix in fixes["after"]:
        for region, area in gt[fix.stem][1].items():
            by_size[size_class(area)][1] += 1
            by_size[size_class(area)][0] += (fix.stem, region) in hit_regions
    summary["after"]["regions_hit"] = by_size
    told = [s for s in scored if s["status"] == "reported"]
    print(f"after (12 photos of the changed room): {len(told)} events reported, {sum(s['on_labelled_change'] for s in told)} on a labelled change, "
          f"{sum(not s['on_labelled_change'] for s in told)} not; {len(scored) - len(told)} held back, of which "
          f"{sum(s['on_labelled_change'] for s in scored if s['status'] != 'reported')} on a labelled change")
    for s in scored:
        print(f"   {s['status']:<11} {s['id']:>4} {s['kind']:<15} {s['label']:<14} {s['confidence_pct']:>3}%  on mask in {s['photos_on_mask']}/{s['photos_in_view']} photos "
              f"-> {'ON A CHANGE' if s['on_labelled_change'] else 'not on a change'}")
    print("   labelled regions hit by a reported event: " + ", ".join(f"{name} {a}/{b}" for name, (a, b) in by_size.items()))

    if not args.no_sheets:  # before | after for every event, to judge the kind by eye
        tiles = []
        for event in reported + waiting:
            obj = by_id.get(event["object"]) or {}
            for end, position in event_points(event):
                best = None
                for i, stem in enumerate(meta["baseline"]):  # the baseline photo that has the place nearest its middle
                    world = locate.from_map(room, position)[0]
                    cam = r.E[i][:, :3] @ world + r.E[i][:, 3]
                    if cam[2] <= 1e-6:
                        continue
                    hh, ww = r.depth[i].shape
                    u, v = (r.K[i][0, 0] * cam[0] / cam[2] + r.K[i][0, 2]) / (ww - 1), (r.K[i][1, 1] * cam[1] / cam[2] + r.K[i][1, 2]) / (hh - 1)
                    off = max(abs(u - 0.5), abs(v - 0.5))
                    if off < 0.45 and (best is None or cam[2] < best[0]):
                        best = (float(cam[2]), stem, u, v, float(r.K[i][0, 0]) / ww)
                now = None
                for fix in fixes["after"]:
                    p = fix.project(position)
                    if p and max(abs(p[0] - 0.5), abs(p[1] - 0.5)) < 0.45 and (now is None or p[2] < now[0]):
                        now = (p[2], fix.stem, p[0], p[1], float(fix.r.K[fix.q][0, 0]) / W, fix.room["metres"])
                size = obj.get("size_m") or 0.15
                for which, view, metres in (("before", best, room["metres"]), ("after", now, now[5] if now else 1.0)):
                    if view is None:
                        tiles.append(np.zeros((220, 220, 3), np.uint8))
                        continue
                    picture = photos(view[1])
                    half = 2.2 * size / metres / view[0] * view[4] * picture.shape[1]
                    tiles.append(crop(picture, view[2], view[3], half, text=(f"{event['id']} {event['kind'][7:]} {event['label']}",
                                                                              f"{end} | {which} | {event['confidence_pct']}% {event['status'][:6]}")))
        summary["after"]["sheets"] = sheets(tiles, str(out / "events"), per_row=6, rows=5)
        tiles = []
        for event in objects.diary(held_map):
            obj = next((o for o in held_map["objects"] if o["id"] == event["object"]), {})
            for end, position in event_points(event):
                for fix in fixes["held"][:12:4]:
                    p = fix.project(position)
                    if p and 0 < p[0] < 1 and 0 < p[1] < 1:
                        picture = photos(fix.stem)
                        half = 2.2 * (obj.get("size_m") or 0.15) / fix.room["metres"] / p[2] * float(fix.r.K[fix.q][0, 0]) / W * picture.shape[1]
                        tiles.append(crop(picture, p[0], p[1], half, text=(f"{event['id']} {event['kind'][7:]} {event['label']}", f"{end} | control | {event['confidence_pct']}%")))
        summary["control"]["sheets"] = sheets(tiles, str(out / "control"), per_row=6, rows=5)

    # ---- 4. where is X: the inventory's places looked up in the held-out photos
    known = {o["id"]: o for o in room_map["objects"] if o["state"] == "present"}
    looks = lands = 0
    apart, tiles, done = [], [], set()
    for fix, seen, checked in record:
        for c in checked:
            obj = known.get(c["id"])
            if obj is None or not (c["in_view"] and c["close_enough"] and not c["blocked"]):
                continue
            looks += 1
            u, v = c["pixel"]
            same = [d for d in seen if objects._called(obj, d["name"])]
            inside = [d for d in same if d["box"][0] - 0.25 * (d["box"][2] - d["box"][0]) <= u <= d["box"][2] + 0.25 * (d["box"][2] - d["box"][0])
                      and d["box"][1] - 0.25 * (d["box"][3] - d["box"][1]) <= v <= d["box"][3] + 0.25 * (d["box"][3] - d["box"][1])]
            lands += bool(inside)
            placed = [float(np.linalg.norm(np.array(d["position"]) - obj["position"])) for d in inside if d.get("position") is not None]
            if placed:
                apart.append(min(placed))
            if obj["id"] not in done and not args.no_sheets:
                done.add(obj["id"])
                picture = photos(fix.stem)
                half = 2.2 * c["size_px"] / 960 * picture.shape[1] / 2
                tiles.append(crop(picture, u, v, half, text=(f"{obj['id']} {obj['label']}", f"{obj['confidence_pct']}% in {fix.stem[-4:]}")))
    summary["where"] = {"clear_looks": looks, "a_box_of_that_name_at_the_place": lands, "objects_with_a_clear_look": len(done) if not args.no_sheets else None,
                        "metres_apart_median": round(float(np.median(apart)), 2) if apart else None,
                        "metres_apart_90pct": round(float(np.percentile(apart, 90)), 2) if apart else None, "pairs": len(apart)}
    print(f"where is X: {looks} clear looks at inventory objects from held-out photos; in {lands} the detector put a box of that name on the "
          f"re-projected place; stored place against the new sighting's place: median {summary['where']['metres_apart_median']} m, "
          f"90% within {summary['where']['metres_apart_90pct']} m ({len(apart)} pairs)")
    if not args.no_sheets:
        summary["where"]["sheets"] = sheets(tiles, str(out / "where"))

    # ---- by-eye verdicts against confidence
    if args.labels:
        verdicts = json.loads(Path(args.labels).read_text()).get(args.scene, {})
        bins = [(40, 60), (60, 75), (75, 90), (90, 101)]
        table = {}
        for o in objs:
            v = verdicts.get("objects", {}).get(o["id"])
            if v:
                key = next((f"{lo}-{hi - 1}" for lo, hi in bins if lo <= o["confidence_pct"] < hi), None)
                if key:
                    table.setdefault(key, {"right": 0, "duplicate": 0, "wrong": 0})[v] += 1
        summary["inventory"]["by_eye"] = table
        print("inventory by eye, per confidence:", json.dumps(table))
    summary["seconds_per_fix_logic"] = {k: round(float(np.median(v)), 4) for k, v in SECONDS.items()}
    print(f"logic alone: inventory {summary['seconds_inventory_logic']} s; per fix {summary['seconds_per_fix_logic']} s (median)")
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "after_map.json").write_text(json.dumps(after_map))
    (out / "held_map.json").write_text(json.dumps(held_map))


if __name__ == "__main__":
    main()
