#!/usr/bin/env python3
"""Contact sheet of watched spots on one PASLCD scene, checked against the labels, and
optionally the inputs for running the same scene through the live system.

Spots: every labelled changed region becomes a spot as in watch_eval.py; regions of the same
object in several query photos are merged to one spot (the first one met, in a shuffled order,
not the best one). Each gets a control box of the same size on a place with no labelled change.
watch.check then runs as the live system would: all 12 new frames, at the model's resolution.

One row per spot: the baseline crop, the new crop (from the original photos, so a person can
judge them; the check itself saw the 518-pixel frames), the labelled truth, the answer and
score, and a tick, a cross, or a dash when the spot was not seen. Nothing is left out. The
baseline crop is the baseline frame the check compared with (the one that sees the spot from
the direction most like the new frame's), which need not be the frame the spot was marked in.

    python watch_sheets.py --recon recall/out/base --data bench/data/PASLCD --scene Lounge --out watch/out [--live]
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import run_bench as rb  # noqa: E402
import watch  # noqa: E402
import watch_eval as we  # noqa: E402

TILE_W, TILE_H, TEXT_W = 230, 170, 400
GREEN, RED, GREY, YELLOW = (60, 200, 60), (50, 50, 230), (150, 150, 150), (0, 230, 255)  # BGR


def same_object(scene, geo, kept):
    """Is this spot's 3D centre inside a spot we already have, at about the same depth?"""
    if geo["centroid"] is None:
        return True
    for other in kept:
        u, v, z, inside = we.project(scene, geo["centroid"][None], other["frame"])
        if not inside[0]:
            continue
        x0, y0, x1, y1 = other["box"]
        d = other["points"] @ scene.r.E[other["frame"]][2, :3] + scene.r.E[other["frame"]][2, 3]
        if x0 <= u[0] <= x1 and y0 <= v[0] <= y1 and abs(z[0] - np.median(d)) < 0.15 * z[0]:
            return True
    return False


def crop(photo, box, pad=0.6):
    """The box and some surroundings, cut from a photo and fitted into a tile, with the box drawn."""
    h, w = photo.shape[:2]
    u0, v0, u1, v1 = box
    side = max((u1 - u0) * w, (v1 - v0) * h) * (1 + 2 * pad)
    cx, cy = (u0 + u1) / 2 * w, (v0 + v1) / 2 * h
    half_w, half_h = side / 2 * TILE_W / TILE_H if TILE_W > TILE_H else side / 2, side / 2
    x0, y0 = int(max(0, cx - half_w)), int(max(0, cy - half_h))
    x1, y1 = int(min(w, cx + half_w)), int(min(h, cy + half_h))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return np.zeros((TILE_H, TILE_W, 3), np.uint8)
    part = cv2.cvtColor(np.ascontiguousarray(photo[y0:y1, x0:x1]), cv2.COLOR_RGB2BGR)
    f = min(TILE_W / part.shape[1], TILE_H / part.shape[0])
    part = cv2.resize(part, (max(1, int(part.shape[1] * f)), max(1, int(part.shape[0] * f))),
                      interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
    cv2.rectangle(part, (int((u0 * w - x0) * f), int((v0 * h - y0) * f)), (int((u1 * w - x0) * f), int((v1 * h - y0) * f)), YELLOW, 1)
    tile = np.zeros((TILE_H, TILE_W, 3), np.uint8)
    tile[:part.shape[0], :part.shape[1]] = part
    return tile


def mark(tile, kind):
    """A tick, a cross or a dash, drawn (the font has no such signs)."""
    x, y = tile.shape[1] - 46, 26
    if kind == "right":
        cv2.polylines(tile, [np.array([[x, y + 14], [x + 10, y + 26], [x + 30, y]], np.int32)], False, GREEN, 5, cv2.LINE_AA)
    elif kind == "wrong":
        cv2.line(tile, (x, y), (x + 26, y + 26), RED, 5, cv2.LINE_AA)
        cv2.line(tile, (x + 26, y), (x, y + 26), RED, 5, cv2.LINE_AA)
    else:
        cv2.line(tile, (x, y + 13), (x + 26, y + 13), GREY, 5, cv2.LINE_AA)


def text_tile(lines):
    tile = np.full((TILE_H, TEXT_W, 3), 28, np.uint8)
    for i, (line, colour) in enumerate(lines):
        cv2.putText(tile, line, (8, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
    return tile


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recon", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--instance", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--live", action="store_true", help="also write the frames and spots.json for a live run")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    out = Path(args.out)
    tag = f"{args.scene}_inst{args.instance}"
    rng = np.random.default_rng(args.seed)
    baseline, query, _, _ = rb.scene_pairs(args.data, args.scene, args.instance, 24, 12)
    recon, n_before, gts, names = we.load_recon(Path(args.recon) / f"{tag}_changed")
    base, after = list(range(n_before)), list(range(n_before, len(recon.depth)))
    photos = we.read_rgb(baseline + query)
    scene = watch.Scene(recon)

    # Spots: one per changed object, and a control box for each.
    regions = []
    for i, q in enumerate(after):
        n, labels, stats, _ = cv2.connectedComponentsWithStats(gts[i].astype(np.uint8), connectivity=8)
        regions += [(q, labels == k, int(stats[k, cv2.CC_STAT_AREA])) for k in range(1, n) if stats[k, cv2.CC_STAT_AREA] >= we.MIN_REGION]
    order = rng.permutation(len(regions))
    spots, kept, kept_all = [], [], []
    for idx in order:
        q, blob, area = regions[idx]
        spot = we.region_spot(scene, q, blob, base)
        if spot is None:
            continue
        geo = watch.spot_geometry(scene, {"name": "", **spot})
        if same_object(scene, geo, kept):
            continue
        kept.append(geo)
        kept_all.append(geo)
        spots.append({"name": "", **spot, "truth": "changed", "region_px": area})
        for _ in range(5):  # a control of the same size that is not a control we already have
            negative = we.matched_negative(scene, spot, q, gts, base, after, rng)
            if negative is None:
                break
            ngeo = watch.spot_geometry(scene, {"name": "", **negative})
            if not same_object(scene, ngeo, kept_all):
                kept_all.append(ngeo)
                spots.append({"name": "", **negative, "truth": "same", "region_px": area})
                break
    spots.sort(key=lambda s: (s["truth"] != "changed", -s["region_px"]))
    for i, s in enumerate(spots):
        s["name"] = f"spot {i + 1:02d}"
        s["box"] = [round(float(x), 4) for x in s["box"]]

    answers = watch.check(recon, base, after, spots)
    rows, tiles = [], []
    for s, a in zip(spots, answers):
        right = "not seen" if a["state"] == "not_seen" else "right" if a["state"] == s["truth"] else "wrong"
        rows.append({**s, **a, "verdict": right})
        shown = (a["frame_base"], a["box_base"]) if a.get("frame_base") is not None else (s["frame"], s["box"])
        left = crop(photos[shown[0]], shown[1])
        new = crop(photos[a["frame"]], a["box_new"]) if a["frame"] is not None else np.zeros((TILE_H, TILE_W, 3), np.uint8)
        for tile, label in ((left, f"baseline, frame {shown[0]}"), (new, f"new, frame {a['frame']}" if a["frame"] is not None else "no clear look")):
            cv2.putText(tile, label, (6, TILE_H - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(tile, label, (6, TILE_H - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        colour = {"right": GREEN, "wrong": RED, "not seen": GREY}[right]
        text = text_tile([(f"{s['name']}   box {int((s['box'][2] - s['box'][0]) * scene.W)} x {int((s['box'][3] - s['box'][1]) * scene.H)} px of 518 x 294", (230, 230, 230)),
                          (f"label: {'CHANGED' if s['truth'] == 'changed' else 'not changed'}", (230, 230, 230)),
                          (f"answer: {a['state']}" + (f"   score {a['score']:.2f} (limit 1)" if a["score"] is not None else ""), colour),
                          (f"why: {a['why']}", (200, 200, 200)),
                          (right, colour)])
        mark(text, right)
        tiles.append(np.hstack([left, new, text]))
    per_column = int(np.ceil(len(tiles) / 2))
    blank = np.zeros_like(tiles[0])
    columns = [np.vstack(tiles[c * per_column:(c + 1) * per_column] + [blank] * (per_column - len(tiles[c * per_column:(c + 1) * per_column])))
               for c in range(2)]
    sheet = np.hstack([columns[0], np.full((columns[0].shape[0], 6, 3), 90, np.uint8), columns[1]])
    counts = {k: sum(r["verdict"] == k for r in rows) for k in ("right", "wrong", "not seen")}
    head = np.full((34, sheet.shape[1], 3), 28, np.uint8)
    title = (f"{args.scene}: {len(rows)} watched spots ({sum(r['truth'] == 'changed' for r in rows)} labelled changed, the rest controls) | "
             f"right {counts['right']}, wrong {counts['wrong']}, not seen {counts['not seen']} | yellow = the watched box")
    cv2.putText(head, title, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA)
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "sheets" / f"{args.scene}.jpg"), np.vstack([head, sheet]), [cv2.IMWRITE_JPEG_QUALITY, 88])
    (out / "sheets" / f"{args.scene}.json").write_text(json.dumps(we.clean(rows), indent=1))
    print(f"{args.scene}: {title}")
    for kind in ("changed", "same"):
        part = [r for r in rows if r["truth"] == kind]
        print(f"  labelled {kind}: " + ", ".join(f"{k} {sum(r['verdict'] == k for r in part)}" for k in ("right", "wrong", "not seen")))

    if args.live:
        live = out / f"live_{args.scene}"
        live.mkdir(parents=True, exist_ok=True)
        for i, rgb in enumerate(recon.rgb):  # exactly the frames the model saw: 24 baseline, then 12 new
            cv2.imwrite(str(live / f"{i:03d}.jpg"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
        (live / "spots.json").write_text(json.dumps(
            {"scene": args.scene, "baseline_frames": [f"{i:03d}.jpg" for i in base], "new_frames": [f"{i:03d}.jpg" for i in after],
             "spots": [{"name": s["name"], "frame": s["frame"], "box": s["box"], "truth": s["truth"]} for s in spots]}, indent=1))
        (live / "expected.json").write_text(json.dumps(we.clean(
            [{k: r[k] for k in ("name", "truth", "state", "score", "frame", "box_new", "why", "verdict")} for r in rows]), indent=1))
        print(f"  live inputs in {live}")


if __name__ == "__main__":
    main()
