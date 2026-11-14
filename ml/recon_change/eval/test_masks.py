#!/usr/bin/env python3
"""Check run_bench.py's mask building without a GPU, on the synthetic room of test_synthetic.py.

A box moves from A to B. The exact ground truth for a query view is: the pixels where the box
now is (appeared) plus the pixels where it used to be (gone). The predicted masks must land on
both, which checks the projection of "gone" points into the query cameras.

    python test_masks.py
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import recon_change as rc  # noqa: E402
import run_bench  # noqa: E402
import test_synthetic as ts  # noqa: E402


def box_pixels(E, centre, size=0.3):
    """Pixels of one view that a box at centre covers (ignoring anything in front of it)."""
    _, _, colour = ts.render(E, centre, np.full(3, size), np.random.default_rng(0), 0.0)
    return (colour == (200, 120, 40)).all(-1)


def main():
    recon = ts.scene(ts.BOX_B)
    _, detail = rc.analyse(recon, 24, conf_pct=0, typical_m=2.5)
    pred = run_bench.predicted_masks(recon, detail, fill=5)
    tp = fp = fn = regions = hit = 0
    app_on_b = gone_on_a = app_px = gone_px = 0
    for q in detail["after"]:
        at_b, at_a = box_pixels(recon.E[q], ts.BOX_B), box_pixels(recon.E[q], ts.BOX_A)
        gt = at_b | at_a
        a, g = pred[q]
        p = a | g
        tp, fp, fn = tp + int((p & gt).sum()), fp + int((p & ~gt).sum()), fn + int((~p & gt).sum())
        app_on_b, app_px = app_on_b + int((a & at_b).sum()), app_px + int(a.sum())
        gone_on_a, gone_px = gone_on_a + int((g & at_a).sum()), gone_px + int(g.sum())
        n, h = run_bench.gt_regions(gt, p, 50, 0.10)
        regions, hit = regions + n, hit + h
    s = run_bench.scores(tp, fp, fn)
    print(f"precision {s['precision']:.3f} recall {s['recall']:.3f} F1 {s['f1']:.3f} IoU {s['iou']:.3f} | "
          f"regions hit {hit}/{regions}")
    print(f"appeared pixels on the box's new place: {app_on_b}/{app_px} | "
          f"gone pixels on the box's old place: {gone_on_a}/{gone_px}")
    checks = {
        "appeared mask sits on the new place": app_px > 0 and app_on_b / app_px > 0.9,
        # the projected points are dilated by a few pixels, so a rim falls just outside the box
        "gone mask sits on the old place": gone_px > 0 and gone_on_a / gone_px > 0.8,
        "every ground-truth region is hit": regions > 0 and hit == regions,
        "IoU above 0.5": s["iou"] > 0.5,
    }
    for what, ok in checks.items():
        print(("  ok   " if ok else "  FAIL ") + what)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
