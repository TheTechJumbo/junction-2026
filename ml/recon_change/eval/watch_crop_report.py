#!/usr/bin/env python3
"""Does a close-up feature of the spot beat the feature the model computes on the whole frame?

Joins the output of watch_crop_features.py with that of watch_eval.py (changed pairs, spots seen
within watch.MAX_ANGLE). Three scores are compared, each "colour + feature" with the weights
fitted on the CHOOSE scenes and the limits taken from the CHOOSE scenes' unchanged spots:
  frame      the feature from the whole 518-pixel frame (what watch.py uses)
  close x1   the feature from a close-up cut out of the 518-pixel frames
  close full the feature from a close-up cut out of the original photos
and all three with the colour evidence at the model's resolution, so only the feature differs.

    python watch_crop_report.py --eval watch/out/eval --crop watch/out/crop
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import watch  # noqa: E402
import watch_report as wr  # noqa: E402


def key_of(r):
    return (r["scene"], r["q"], r["label"], r["spot"]["frame"], tuple(round(x, 5) for x in r["spot"]["box"]))


def both(new, own):
    if not new or new.get("f_spot") is None:
        return None
    return watch.surplus(new, own, "f_spot", "f_ring", "both")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--crop", required=True)
    args = ap.parse_args()
    rows, _ = wr.load(args.eval, 1)
    rows = {key_of(r): r for r in rows if r["pair"] == "changed" and r.get("spot")}
    data = []
    for path in sorted(Path(args.crop).glob("*_inst1.json")):
        for c in json.loads(path.read_text()):
            r = rows.get(key_of(c))
            m = wr.single(r, "x1") if r else None
            if m is None:
                continue
            feats = {"frame": both(m, r["own"]["x1"]), "close x1": both(c.get("new_x1"), c.get("own_x1")),
                     "close full": both(c.get("new_full"), c.get("own_full"))}
            if any(v is None for v in feats.values()):
                continue
            colour = watch.surplus(m, r["own"]["x1"], *watch.PAIRS["colour"], watch.FORMS["colour"])
            data.append((r["scene"], r["label"], wr.band_of(r["region_area"]), colour, feats))
    scene = np.array([d[0] for d in data])
    y = np.array([d[1] for d in data])
    band = np.array([d[2] for d in data])
    choose, report = np.isin(scene, wr.CHOOSE), np.isin(scene, wr.REPORT)
    print(f"{len(data)} spots with all three features: {int(y.sum())} changed, {int((1 - y).sum())} unchanged")
    lines = []
    for name in ("frame", "close x1", "close full"):
        f = np.array([d[4][name] for d in data])
        for with_colour in (False, True):
            X = np.stack([np.array([d[3] for d in data]), f], 1) if with_colour else f[:, None]
            spread = np.array([1.4826 * np.median(np.abs(X[choose & (y == 0)][:, i] - np.median(X[choose & (y == 0)][:, i]))) + 1e-9
                               for i in range(X.shape[1])])
            w = wr.fit_weights(X[choose] / spread, y[choose].astype(float)) / spread
            score = X @ w
            lim5, lim1 = wr.limit_at(score[choose & (y == 0)], 0.05), wr.limit_at(score[choose & (y == 0)], 0.01)
            line = [name, "colour + feature" if with_colour else "feature alone"]
            for b in (0, 1, 2):
                pos, neg = report & (y == 1) & (band == b), report & (y == 0) & (band == b)
                line += [f"{wr.auc(score[pos], score[neg]):.3f}", wr.pct(int((score[pos] > lim5).sum()), int(pos.sum())),
                         wr.pct(int((score[pos] > lim1).sum()), int(pos.sum()))]
            neg = report & (y == 0)
            line += [wr.pct(int((score[neg] > lim5).sum()), int(neg.sum())), wr.pct(int((score[neg] > lim1).sum()), int(neg.sum()))]
            lines.append(line)
    wr.table("Close-up features against whole-frame features (REPORT scenes, spots seen; weights and limits from the CHOOSE scenes)",
             ["Feature from", "Score", "small AUC", "small caught at 5%", "at 1%", "medium AUC", "medium caught at 5%", "at 1%",
              "large AUC", "large caught at 5%", "at 1%", "false alarms at 5%", "at 1%"], lines)


if __name__ == "__main__":
    main()
