#!/usr/bin/env python3
"""Turn the measurements of watch_eval.py into scores, limits and tables.

The weights of the score and the limits are chosen on the CHOOSE scenes and every table is then
printed for the REPORT scenes (and for all ten, marked as such).

    python watch_report.py --eval watch/out/eval

The score is watch.py's own (watch.score_of with its WEIGHTS and SCALE); watch_explore.py is
where the evidence and the weights were chosen.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import watch  # noqa: E402

CHOOSE = ["Cantina", "Lounge", "Lunch_room", "Garden", "Pots"]  # weights and limits are chosen here
REPORT = ["Printing_area", "Meeting_room", "Zen", "Playground", "Porch"]  # and reported here
BANDS = [("small, 50 to 199 px", 50, 200), ("medium, 200 to 799 px", 200, 800), ("large, 800 px and more", 800, 10 ** 9)]


def load(folder, instance):
    rows = []
    meta = []
    for path in sorted(Path(folder).glob(f"*_inst{instance}.json")):
        data = json.loads(path.read_text())
        rows += data["rows"]
        meta.append((data["scene"], data["regions_without_baseline_view"], data["positives_without_negative"]))
    return rows, meta


def single(row, key):
    """The measurement against the designated query photo at one resolution, or None if not seen."""
    m = (row.get("single") or {}).get(key)
    if not m or m.get("c_spot") is None or m["seen"] < watch.MIN_CLEAR:
        return None
    if watch.MAX_ANGLE is not None and (row["view"].get("angle") or 0.0) > watch.MAX_ANGLE:
        return None
    return m


def reason(row):
    if row.get("spot") is None:
        return "no baseline view"
    if not row.get("points"):
        return "no depth at the spot"
    if row["view"] is None:
        return "out of view"
    if row["view"]["why"] is None and watch.MAX_ANGLE is not None and (row["view"].get("angle") or 0.0) > watch.MAX_ANGLE:
        return "seen from another side"
    return row["view"]["why"] or "blocked"


def auc(pos, neg):
    """Chance that a changed spot scores above an unchanged one."""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    allv = np.concatenate([pos, neg])
    for v in np.unique(allv[np.isin(allv, allv[np.flatnonzero(np.diff(np.sort(allv)) == 0)])]):  # ties share a rank
        tie = allv == v
        ranks[tie] = ranks[tie].mean()
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def limit_at(neg, rate):
    """The score that this share of the unchanged spots exceed."""
    neg = np.sort(np.asarray(neg, float))
    return float(neg[min(len(neg) - 1, int(np.ceil(len(neg) * (1 - rate))))]) if len(neg) else float("inf")


def fit_weights(X, y, l2=1.0, steps=200):
    """Logistic regression by Newton steps. X is already scaled."""
    Xb = np.concatenate([X, np.ones((len(X), 1))], 1)
    w = np.zeros(Xb.shape[1])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-Xb @ w))
        grad = Xb.T @ (p - y) + l2 * np.r_[w[:-1], 0]
        hess = (Xb * (p * (1 - p))[:, None]).T @ Xb + l2 * np.diag(np.r_[np.ones(len(w) - 1), 0])
        w -= np.linalg.solve(hess, grad)
    return w[:-1]


def band_of(area):
    return next(i for i, (_, lo, hi) in enumerate(BANDS) if lo <= area < hi)


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"


def table(title, head, lines):
    print(f"\n### {title}\n")
    print("| " + " | ".join(head) + " |")
    print("| " + " | ".join("---" for _ in head) + " |")
    for line in lines:
        print("| " + " | ".join(str(c) for c in line) + " |")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--colour", nargs=2, metavar=("SPOT", "RING"), help="try other measurements as the colour evidence, e.g. c_top c_ring")
    ap.add_argument("--weights", nargs=2, type=float, metavar=("COLOUR", "FEATURE"), help="try other weights")
    args = ap.parse_args()
    if args.colour:
        watch.PAIRS["colour"] = tuple(args.colour)
    if args.weights:
        watch.WEIGHTS.update(colour=args.weights[0], feature=args.weights[1])
    rows, meta = load(args.eval, 1)
    print(f"{len(rows)} spots from {len(meta)} scenes (same lighting)")
    for scene, no_view, no_neg in meta:
        print(f"  {scene}: {no_view} regions without a baseline view, {no_neg} positives without a matched negative")

    def pick(rows, scenes=None, pair=None, label=None):
        return [r for r in rows if (scenes is None or r["scene"] in scenes) and (pair is None or r["pair"] == pair)
                and (label is None or r["label"] == label)]

    def score(row, key):
        m = single(row, key)
        return None if m is None else watch.score_of(m, row["own"].get(key))

    def negatives(scenes, key="x1", data=rows):
        """Scores of the seen unchanged spots: matched boxes of the changed pairs (independent
        paths) and every box of the unchanged pairs, pooled."""
        s = [score(r, key) for r in pick(data, scenes, None, 0) if r.get("spot")]
        return [x for x in s if x is not None]

    # Limits: on the CHOOSE scenes.
    neg = negatives(CHOOSE)
    limits = {"5%": limit_at(neg, 0.05), "1%": limit_at(neg, 0.01)}
    print(f"limits chosen on {len(neg)} unchanged spots of the choose scenes: {limits}")
    print(f"to put the 5% limit at 1.0, set SCALE = {watch.SCALE * limits['5%']:.4f} in watch.py (now {watch.SCALE})")

    # ------------------------------------------------------------------ headline
    def headline(name, scenes, key="x1", rows=rows, limits=limits):
        lines = []
        negs_all = [(r, score(r, key)) for r in pick(rows, scenes, "changed", 0) if r.get("spot")]
        for b, (band, lo, hi) in enumerate(BANDS + [("all sizes", 50, 10 ** 9)]):
            pos = [(r, score(r, key)) for r in pick(rows, scenes, "changed", 1) if lo <= r["region_area"] < hi]
            negs = [(r, s) for r, s in negs_all if lo <= r["region_area"] < hi]
            p_seen, n_seen = [s for _, s in pos if s is not None], [s for _, s in negs if s is not None]
            line = [band, len(pos)]
            for lim in limits.values():
                caught = sum(s > lim for s in p_seen)
                line += [pct(caught, len(pos)), f"{100 * caught / max(len(p_seen), 1):.0f}%",
                         pct(sum(s > lim for s in n_seen), len(n_seen))]
            line += [f"{auc(p_seen, n_seen):.2f}", pct(len(pos) - len(p_seen), len(pos)), pct(len(negs) - len(n_seen), len(negs))]
            for which in ("geometry", "with_looks"):
                have = [r for r, _ in pos if r.get("whole_room")]
                line.append(pct(sum(bool(r["whole_room"].get(which)) for r in have), len(have)))
            haven = [r for r, _ in negs if r.get("whole_room")]
            line.append(pct(sum(bool(r["whole_room"].get("with_looks")) for r in haven), len(haven)))
            lines.append(line)
        table(name, ["Region size at 518 x 294", "Regions", "Caught at the 5% limit (of all)", "(of seen)", "False alarms at it",
                     "Caught at the 1% limit (of all)", "(of seen)", "False alarms at it", "AUC", "Changed spots not seen",
                     "Unchanged spots not seen", "Whole-room detector: geometry", "geometry + looks different",
                     "Whole-room false alarms (with looks)"], lines)

    headline("REPORT scenes (limits chosen on the other five), model resolution", REPORT)
    headline("CHOOSE scenes (the limits were chosen here), model resolution", CHOOSE)
    headline("All ten scenes, model resolution", CHOOSE + REPORT)

    # why not seen
    for label, name in ((1, "changed"), (0, "unchanged")):
        sel = [r for r in pick(rows, None, "changed", label)]
        why = {}
        for r in sel:
            if score(r, "x1") is None:
                why[reason(r)] = why.get(reason(r), 0) + 1
        print(f"\nnot seen, {name} spots of the changed pairs ({len(sel)}):", why)

    # ------------------------------------------------------------------ false alarms by kind of unchanged spot
    lines = []
    for scenes, name in ((REPORT, "report scenes"), (CHOOSE, "choose scenes"), (CHOOSE + REPORT, "all ten")):
        for kind, sel in (("changed pair, matched box elsewhere (independent path)", pick(rows, scenes, "changed", 0)),
                          ("unchanged pair, box of a changed region (same capture)", [r for r in pick(rows, scenes, "unchanged") if r["box_of"] == "positive"]),
                          ("unchanged pair, matched box elsewhere (same capture)", [r for r in pick(rows, scenes, "unchanged") if r["box_of"] == "negative"])):
            s = [score(r, "x1") for r in sel if r.get("spot")]
            seen = [x for x in s if x is not None]
            lines.append([name, kind, len(s), pct(len(s) - len(seen), len(s))] + [pct(sum(x > lim for x in seen), len(seen)) for lim in limits.values()]
                         + [f"{np.median([r['texture'] for r in sel if r.get('spot')]):.1f}"])
    table("False alarms by kind of unchanged spot", ["Scenes", "Unchanged spot", "Spots", "Not seen", "False alarms at the 5% limit",
                                                    "at the 1% limit", "Texture in the box (median Lab spread)"], lines)

    # textured half of the negatives
    negs = [(r, score(r, "x1")) for r in pick(rows, REPORT, "changed", 0)]
    negs = [(r, s) for r, s in negs if s is not None]
    med = float(np.median([r["texture"] for r, _ in negs]))
    for name, part in (("more textured half", [s for r, s in negs if r["texture"] >= med]), ("plainer half", [s for r, s in negs if r["texture"] < med])):
        print(f"report scenes, unchanged spots, {name}: " + ", ".join(f"{k} limit {pct(sum(x > lim for x in part), len(part))}" for k, lim in limits.items()))
    posr = [r for r in pick(rows, REPORT, "changed", 1) if r.get("spot")]
    print(f"texture inside the box (Lab spread), report scenes: changed spots median {np.median([r['texture'] for r in posr]):.1f}, "
          f"unchanged spots median {med:.1f}")

    # ------------------------------------------------------------------ resolution
    lines = []
    for key in ("x1", "x2", "x4", "full"):
        # limits for this resolution, chosen the same way on the CHOOSE scenes
        neg = negatives(CHOOSE, key)
        lim5, lim1 = limit_at(neg, 0.05), limit_at(neg, 0.01)
        line = [key]
        for _, lo, hi in BANDS:
            pos = [r for r in pick(rows, REPORT, "changed", 1) if lo <= r["region_area"] < hi]
            p = [score(r, key) for r in pos]
            p_seen = [s for s in p if s is not None]
            n = [score(r, key) for r in pick(rows, REPORT, "changed", 0) if lo <= r["region_area"] < hi]
            n_seen = [s for s in n if s is not None]
            used = [single(r, key)["scale"] for r in pos if single(r, key)]
            line += [pct(sum(s > lim5 for s in p_seen), len(pos)), pct(sum(s > lim1 for s in p_seen), len(pos)),
                     pct(sum(s > lim5 for s in n_seen), len(n_seen)), f"{auc(p_seen, n_seen):.3f}", f"{np.median(used):.1f}" if used else "-"]
        lines.append(line)
    table("Resolution of the compared pictures (REPORT scenes; limits chosen per resolution on the other five)",
          ["Photos stored at"] + [f"{b.split(',')[0]}: {c}" for b, _, _ in BANDS for c in
                                  ("caught at 5%", "caught at 1%", "false alarms at 5%", "AUC", "median scale used")], lines)

    # ------------------------------------------------------------------ by pixel size in the new frame
    edges = [0, 8, 11, 14, 20, 28, 40, 1000]
    lines = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        for key in ("x1", "full"):
            neg = negatives(CHOOSE, key)
            lim5, lim1 = limit_at(neg, 0.05), limit_at(neg, 0.01)
            sel = [r for r in pick(rows, REPORT, "changed") if r.get("view") and r["view"]["box"]
                   and lo <= np.sqrt(r["view"]["area"]) < hi]
            p = [score(r, key) for r in sel if r["label"]]
            n = [score(r, key) for r in sel if not r["label"]]
            ps, ns = [s for s in p if s is not None], [s for s in n if s is not None]
            lines.append([f"{lo} to {hi - 1}" if hi < 1000 else f"{lo} and more", key, len(p), pct(len(p) - len(ps), len(p)),
                          pct(sum(s > lim5 for s in ps), len(ps)), pct(sum(s > lim5 for s in ns), len(ns)),
                          pct(sum(s > lim1 for s in ps), len(ps)), pct(sum(s > lim1 for s in ns), len(ns)), f"{auc(ps, ns):.2f}"])
    table("By how many pixels across the spot is in the new frame (square root of the landing box area; REPORT scenes; of the spots seen)",
          ["Pixels across at 518 x 294", "Photos", "Changed spots", "Not seen", "Caught at the 5% limit, of seen", "False alarms",
           "Caught at the 1% limit, of seen", "False alarms", "AUC"], lines)

    # ------------------------------------------------------------------ other lighting
    rows2, meta2 = load(args.eval, 2)
    if rows2:
        print(f"\n{len(rows2)} spots from {len(meta2)} scenes with a baseline in other lighting")
        indoor = [m[0] for m in meta2]
        lines = []
        for name, data, scenes in (("same lighting, the same five indoor scenes", rows, indoor), ("other lighting", rows2, indoor)):
            for pair in ("changed", "unchanged"):
                n = [score(r, "x1") for r in pick(data, scenes, pair, 0) if r.get("spot")]
                ns = [s for s in n if s is not None]
                p = [score(r, "x1") for r in pick(data, scenes, pair, 1)]
                ps = [s for s in p if s is not None]
                lines.append([name, pair, len(n), pct(len(n) - len(ns), len(n))] + [pct(sum(s > lim for s in ns), len(ns)) for lim in limits.values()]
                             + [pct(sum(s > lim for s in ps), len(p)) if p else "-" for lim in limits.values()] + [f"{auc(ps, ns):.2f}" if p else "-"])
        table("Lighting: false alarms on unchanged spots (limits from the same-lighting choose scenes)",
              ["Baseline", "Pair", "Unchanged spots", "Not seen", "False alarms at the 5% limit", "at the 1% limit",
               "Changes caught at 5%", "at 1%", "AUC"], lines)
        headline("Other lighting, five indoor scenes, by size", indoor, rows=rows2)
        headline("Same lighting, the same five indoor scenes, by size", indoor)

    # ------------------------------------------------------------------ the live check: best of 12 new frames
    def multi(row, key="x1"):
        looks = [m for m in row.get("multi") or [] if watch.MAX_ANGLE is None or (m["view"].get("angle") or 0.0) <= watch.MAX_ANGLE][:3]
        s = [watch.score_of(m[key], m.get("own_" + key)) for m in looks
             if m[key].get("c_spot") is not None and m[key]["seen"] >= watch.MIN_CLEAR]
        return float(np.median(s)) if s else None
    lines = []
    for scenes, name in ((REPORT, "report scenes"), (CHOOSE + REPORT, "all ten")):
        for b, (band, lo, hi) in enumerate(BANDS + [("all sizes", 50, 10 ** 9)]):
            p = [multi(r) for r in pick(rows, scenes, "changed", 1) if lo <= r["region_area"] < hi]
            n = [multi(r) for r in pick(rows, scenes, "changed", 0) if lo <= r["region_area"] < hi and r.get("spot")]
            ps, ns = [s for s in p if s is not None], [s for s in n if s is not None]
            lines.append([name, band, len(p)] + [x for lim in limits.values() for x in
                                                  (pct(sum(s > lim for s in ps), len(p)), pct(sum(s > lim for s in ns), len(ns)))]
                         + [f"{auc(ps, ns):.2f}", pct(len(p) - len(ps), len(p))])
        u = [multi(r) for r in pick(rows, scenes, "unchanged")]
        us = [s for s in u if s is not None]
        lines.append([name, "unchanged pairs", len(u)] + [x for lim in limits.values() for x in ("-", pct(sum(s > lim for s in us), len(us)))]
                     + ["-", pct(len(u) - len(us), len(u))])
    table("As a live check would run: all 12 new frames given, median score of the 3 best looks (same limits)",
          ["Scenes", "Region size", "Spots", "Caught at the 5% limit", "False alarms", "Caught at the 1% limit", "False alarms", "AUC", "Not seen"], lines)
    if rows2:
        lines = []
        for name, data in (("same lighting", rows), ("other lighting", rows2)):
            p = [multi(r) for r in pick(data, indoor, "changed", 1)]
            n = [multi(r) for r in pick(data, indoor, "changed", 0) if r.get("spot")]
            ps, ns = [s for s in p if s is not None], [s for s in n if s is not None]
            lines.append([name, len(p)] + [x for lim in limits.values() for x in
                                           (pct(sum(s > lim for s in ps), len(p)), pct(sum(s > lim for s in ns), len(ns)))]
                         + [pct(len(p) - len(ps), len(p))])
        table("Live mode and lighting: the five indoor scenes, all 12 new frames (same limits)",
              ["Baseline", "Changed spots", "Caught at the 5% limit", "False alarms", "Caught at the 1% limit", "False alarms", "Not seen"], lines)


if __name__ == "__main__":
    main()
