#!/usr/bin/env python3
"""Which evidence should the watched-spot score use? Looks at the CHOOSE scenes only.

For every kind of evidence (colour, structure, feature, depth) four forms are compared:
  plain   the difference inside the spot
  ring    minus the difference in the ring around the spot
  own     minus what baseline frames differ by on the same spot
  both    (spot - ring) minus (spot - ring) between baseline frames
Then weighted sums of a few of them are compared, each fitted on four of the five CHOOSE scenes
and scored on the fifth (so no number here is scored on the scenes it was fitted on, and the
REPORT scenes are not touched).

    python watch_explore.py --eval watch/out/eval
"""
import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import watch  # noqa: E402
import watch_report as wr  # noqa: E402

PAIRS = {"colour": ("c_spot", "c_ring"), "top": ("c_top", "c_ring"), "gcolour": ("a_spot", "a_ring"), "gtop": ("a_top", "a_ring"), "structure": ("z_spot", "z_ring"),
         "feature": ("f_spot", "f_ring"), "depth": ("g_spot", "g_ring")}


def val(d, k):
    return 0.0 if d is None or d.get(k) is None or not np.isfinite(d[k]) else d[k]


def variants(m, own):
    out = {}
    for kind, (a, b) in PAIRS.items():
        out[kind + ":plain"] = val(m, a)
        out[kind + ":ring"] = val(m, a) - val(m, b)
        out[kind + ":own"] = val(m, a) - val(own, a)
        out[kind + ":both"] = (val(m, a) - val(m, b)) - (val(own, a) - val(own, b))
        out[kind + ":clip"] = (val(m, a) - val(m, b)) - max(val(own, a) - val(own, b), 0.0)
        out[kind + ":ratio"] = val(m, a) / (val(m, b) + val(own, a) + 1e-3 + (2.0 if kind in ("colour", "top", "gcolour", "gtop") else 0.05))
    out["moved:plain"] = max(val(m, "g_far"), val(m, "g_near"))
    out["moved:own"] = out["moved:plain"] - max(val(own, "g_far"), val(own, "g_near"))
    return out


def table_of(rows, key="x1", max_angle=None):
    """label, scene, band, pair, variants for every seen spot."""
    out = []
    for r in rows:
        m = wr.single(r, key)
        if m is None or r["scene"] not in wr.CHOOSE:
            continue
        if max_angle is not None and (r["view"].get("angle") or 0) > max_angle:
            continue
        out.append((r["label"], r["scene"], wr.band_of(r["region_area"]), r["pair"], variants(m, r["own"].get(key)), r))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--key", default="x1")
    ap.add_argument("--max-angle", type=float)
    args = ap.parse_args()
    rows, _ = wr.load(args.eval, 1)
    data = table_of(rows, args.key, args.max_angle)
    names = list(data[0][4])
    y = np.array([d[0] for d in data])
    print(f"{len(data)} seen spots on the choose scenes: {int(y.sum())} changed, "
          f"{sum(1 for d in data if not d[0] and d[3] == 'changed')} unchanged in changed pairs, "
          f"{sum(1 for d in data if d[3] == 'unchanged')} in unchanged pairs")

    lines = []
    for name in names:
        x = np.array([d[4][name] for d in data])
        line = [name]
        for band in (0, 1, 2, None):
            sel = np.array([band is None or d[2] == band for d in data])
            line.append(f"{wr.auc(x[sel & (y == 1)], x[sel & (y == 0)]):.2f}")
        for pair in ("changed", "unchanged"):
            sel = np.array([d[0] == 1 or d[3] == pair for d in data])
            line.append(f"{wr.auc(x[sel & (y == 1)], x[sel & (y == 0)]):.2f}")
        lines.append(line)
    wr.table("AUC of each form of evidence alone (choose scenes)",
             ["Evidence", "small", "medium", "large", "all", "against changed-pair negatives", "against unchanged-pair boxes"], lines)

    # false alarms by viewing angle, with colour:ring as the score
    x = np.array([d[4]["top:ring"] + 60 * d[4]["feature:both"] for d in data])  # roughly the score, before any angle limit
    lim = wr.limit_at(x[y == 0], 0.05)
    angle = np.array([d[5]["view"].get("angle") or 0.0 for d in data])
    lines = []
    for lo, hi in ((0, 3), (3, 6), (6, 10), (10, 15), (15, 20), (20, 30), (30, 180)):
        sel = (angle >= lo) & (angle < hi)
        lines.append([f"{lo} to {hi}", int((sel & (y == 0)).sum()), wr.pct(int((x[sel & (y == 0)] > lim).sum()), int((sel & (y == 0)).sum())),
                      int((sel & (y == 1)).sum()), wr.pct(int((x[sel & (y == 1)] > lim).sum()), int((sel & (y == 1)).sum()))])
    wr.table("Colour and feature evidence by the angle between the baseline view and the new view (degrees)",
             ["Angle", "Unchanged", "False alarms", "Changed", "Caught"], lines)

    # weighted sums, fitted on four scenes and scored on the fifth
    formulas = [["gtop:ring", "feature:both"], ["gcolour:ring", "feature:both"], ["gtop:ring"], ["gtop:plain", "feature:both"], ["colour:ring"], ["colour:plain"], ["top:ring"], ["top:plain"], ["feature:both"],
                ["colour:ring", "feature:both"], ["top:ring", "feature:both"], ["top:plain", "feature:both"],
                ["top:ring", "feature:ring"], ["colour:ratio", "feature:both"], ["top:ring", "top:plain", "feature:both"],
                ["top:ring", "structure:both", "feature:both"], ["top:ring", "structure:ratio", "feature:both"],
                ["top:ring", "feature:both", "depth:ring"], ["top:ring", "feature:both", "moved:own"],
                ["colour:ring", "structure:both", "feature:both"], ["colour:both", "structure:both", "feature:both"],
                ["top:both", "feature:both"], ["top:own", "feature:both"]]
    lines = []
    scene = np.array([d[1] for d in data])
    band = np.array([d[2] for d in data])
    for formula in formulas:
        X = np.array([[d[4][n] for n in formula] for d in data])
        held = np.zeros(len(data))
        caught = np.zeros(3)
        total = np.zeros(3)
        alarms = n_neg = 0
        for s in wr.CHOOSE:
            fit, test = scene != s, scene == s
            spread = np.array([1.4826 * np.median(np.abs(X[fit & (y == 0)][:, i] - np.median(X[fit & (y == 0)][:, i]))) + 1e-9 for i in range(X.shape[1])])
            w = wr.fit_weights(X[fit] / spread, y[fit].astype(float)) / spread
            score = X @ w
            held[test] = score[test]
            lim = wr.limit_at(score[fit & (y == 0)], 0.05)
            for b in range(3):
                caught[b] += (score[test & (y == 1) & (band == b)] > lim).sum()
                total[b] += (test & (y == 1) & (band == b)).sum()
            alarms += (score[test & (y == 0)] > lim).sum()
            n_neg += (test & (y == 0)).sum()
        spread = np.array([1.4826 * np.median(np.abs(X[y == 0][:, i] - np.median(X[y == 0][:, i]))) + 1e-9 for i in range(X.shape[1])])
        w_all = wr.fit_weights(X / spread, y.astype(float))
        lines.append([" + ".join(formula)] + [wr.pct(int(caught[b]), int(total[b])) for b in range(3)]
                     + [wr.pct(int(caught.sum()), int(total.sum())), wr.pct(int(alarms), int(n_neg)),
                        " ".join(f"{v:.2f}" for v in w_all), " ".join(f"{v:.4g}" for v in w_all / spread)])
    wr.table("Weighted sums: fitted on four choose scenes, scored on the fifth, pooled (limit: 5% of the four scenes' unchanged spots)",
             ["Evidence used", "small caught", "medium caught", "large caught", "all caught", "false alarms",
              "weights per spread (all five)", "weights per unit (all five)"], lines)


if __name__ == "__main__":
    main()
