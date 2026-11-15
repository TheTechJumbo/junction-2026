#!/usr/bin/env python3
"""Check splat_refine.py without a model or GPU, on the synthetic room of test_synthetic.py.

Each "clip" is eight views of the floor, back wall and box from a slightly different arc, with 1%
depth noise. Cases:

  unchanged    the same room clip after clip: detail climbs to HIGH, then nothing is rebuilt
  moving       the box keeps moving: its cells drop to LOW, are movable and stay capped while
               the rest of the room still reaches HIGH; where it left is emptied
  settles      the box then stays put: after SETTLE_STEPS clips its cells refine again
  threshold    sim_changed above 1 calls every seen cell changed, below -1 none
  colour       a reconstruction without patch features falls back to colour histograms
  save/load    the map comes back identical and writes the same .splat bytes

    python test_splat_refine.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

import splat_refine as sr
from recon_change import Recon
from test_synthetic import BOX_A, BOX_B, K, camera, patch_features, render

BOX_C = np.array([0.0, 2.2, 0.15])
SIZE = np.full(3, 0.3)


def clip(box, seed, features=True, n=8):
    """One capture: n views on an arc in front of the room, jittered by seed."""
    rng = np.random.default_rng(seed)
    shift = rng.uniform(-0.08, 0.08)
    depth, conf, E, rgb = [], [], [], []
    for a in np.linspace(-0.6, 0.6, n) + shift:
        pos = np.array([2.2 * np.sin(a), 1.5 - 2.2 * np.cos(a), 1.5 + 0.1 * np.sin(3 * a)])
        e = camera(pos)
        d, hit, c = render(e, box, SIZE, rng, 0.01)
        depth.append(d)
        conf.append(np.where(hit, 2.0 + rng.random(d.shape), 1.0))
        E.append(e)
        rgb.append(c)
    return Recon(np.stack(depth), np.stack(conf).astype(np.float32), np.repeat(K[None], n, 0), np.stack(E), np.stack(rgb),
                 np.stack([patch_features(c) for c in rgb]) if features else None)


def near(m, where, radius=0.3):
    """Map cells whose centre is within radius of where."""
    return np.linalg.norm(sr.centres(m.keys, m.cell) - where, axis=1) < radius


def show(report):
    lv = " ".join(f"L{k}:{v['cells']}/{v['gaussians']}" for k, v in report["by_level"].items())
    print(f"    step {report['step']}: seen {report['seen']}, changed {report['changed']}, refined {report['refined']}, "
          f"skipped {report['skipped']}, new {report['new']} (arrived {report['arrived']}), emptied {report['emptied']}, "
          f"built {report['gaussians_built']}, cells/Gaussians per level {lv}")


def main():
    failures = []

    def check(ok, what):
        print(("  ok   " if ok else "  FAIL ") + what)
        if not ok:
            failures.append(what)

    print("Unchanged room, six clips:")
    m = sr.Map()
    reports = []
    for s in range(6):
        reports.append(sr.step(m, clip(BOX_A, s), conf_pct=0))
        show(reports[-1])
    first, last = reports[0], reports[-1]
    check(first["new"] == first["seen"] and first["gaussians_built"] > 0, "the first clip creates every cell, at LOW")
    check(all(r["changed"] <= 0.02 * r["seen"] for r in reports[1:]), "almost no cell is called changed (at most 2%)")
    check(reports[1]["refined"] > 0.8 * reports[1]["seen"], "the second clip refines most cells by one level")
    check(last["by_level"][sr.HIGH]["gaussians"] > 0.99 * last["gaussians"], "after six clips over 99% of Gaussians are at HIGH")
    check(last["by_level"][sr.HIGH]["cells"] > 0.8 * len(m.keys), "and over 80% of cells (the rest are edges seen now and then)")
    high_before = m.keys[m.level == sr.HIGH]
    check(last["skipped"] > 0.9 * last["seen"], "and the last clip skips over 90% of the cells it sees")
    check(last["gaussians_built"] < 0.1 * reports[1]["gaussians_built"], "so it builds under a tenth of what a refining clip builds")
    per_cell = [v["gaussians"] / max(v["cells"], 1) for v in reports[1]["by_level"].values() if v["cells"]]
    check(last["by_level"][sr.HIGH]["gaussians"] / last["by_level"][sr.HIGH]["cells"] > 4 * per_cell[0],
          "a HIGH cell holds several times the Gaussians of a LOW one")
    check(not m.movable.any(), "nothing is movable")

    print("The box keeps moving (A -> B -> C -> B):")
    for s, box in zip(range(6, 9), (BOX_B, BOX_C, BOX_B)):
        rep = sr.step(m, clip(box, s), conf_pct=0)
        show(rep)
    at_b, at_c, at_a = near(m, BOX_B), near(m, BOX_C), near(m, BOX_A)
    far = ~(near(m, BOX_A, 0.6) | near(m, BOX_B, 0.6) | near(m, BOX_C, 0.6)) & np.isin(m.keys, high_before)
    check(m.movable[at_b].mean() > 0.5 and m.movable[at_c].mean() > 0.5, "cells where the box went are movable")
    check((m.level[m.movable] <= sr.MOVABLE_CAP).all(), "movable cells are capped at LOW")
    check(m.movable[at_a].mean() > 0.5, "cells where the box was are movable (changed or emptied)")
    check(rep["emptied"] > 0, "where the box left, the cells are emptied")
    check((m.level[far] == sr.HIGH).mean() > 0.9 and not m.movable[far].any(), "the rest of the room stays at HIGH and static")
    check(rep["skipped"] > 0.8 * rep["seen"], "the still room costs nothing while the box moves")

    print(f"The box then stays at B for {sr.SETTLE_STEPS + 2} clips:")
    for s in range(9, 9 + sr.SETTLE_STEPS + 2):
        rep = sr.step(m, clip(BOX_B, s), conf_pct=0)
        show(rep)
    at_b = near(m, BOX_B)
    check(not m.movable[at_b].any(), "its cells are static again")
    check(m.level[at_b].mean() >= 1, "and refine past LOW")

    print("Cosine threshold:")
    m2 = sr.Map()
    r0 = clip(BOX_A, 0)
    sr.step(m2, r0, conf_pct=0)
    rep = sr.step(sr.Map.load(save(m2)), r0, conf_pct=0, sim_changed=1.01)
    check(rep["changed"] == rep["seen"] - rep["new"], "sim_changed above 1: every seen cell changed")
    rep = sr.step(sr.Map.load(save(m2)), clip(BOX_B, 1), conf_pct=0, sim_changed=-1.01)
    check(rep["changed"] == 0, "sim_changed below -1: no cell changed, even with the box moved")
    rep = sr.step(sr.Map.load(save(m2)), clip(BOX_B, 1), conf_pct=0)
    check(rep["changed"] > 0, "default threshold: moving the box changes cells")

    print("Colour histograms when there are no patch features:")
    m3 = sr.Map()
    for s in range(4):
        rep = sr.step(m3, clip(BOX_A, s, features=False), conf_pct=0)
    check(m3.feat.shape[1] == 64 and rep["changed"] <= 0.02 * rep["seen"], "unchanged room stays unchanged")
    rep = sr.step(m3, clip(BOX_C, 4, features=False), conf_pct=0)
    check(m3.movable[near(m3, BOX_A)].mean() > 0.5, "the moved box is found")

    print("Save and load:")
    with tempfile.TemporaryDirectory() as tmp:
        back = sr.Map.load(save(m, Path(tmp) / "map.npz"))
        same = all(np.array_equal(getattr(m, k), getattr(back, k))
                   for k in ("keys", "feat", "level", "still", "movable", "changes", "pixels", "free", "splats", "owner"))
        check(same and back.cell == m.cell and back.steps == m.steps, "every array comes back identical")
        n = sr.write_splat(m, Path(tmp) / "a.splat")
        sr.write_splat(back, Path(tmp) / "b.splat")
        a, b = (Path(tmp) / "a.splat").read_bytes(), (Path(tmp) / "b.splat").read_bytes()
        check(a == b and len(a) == 32 * n == 32 * len(m.splats), "and writes the same .splat (32 bytes per Gaussian)")
        check(sr.write_splat(m, Path(tmp) / "c.splat", show_levels=True) == n, "the level-tinted view has the same Gaussians")

    print("\nAll checks passed." if not failures else f"\n{len(failures)} check(s) failed.")
    sys.exit(1 if failures else 0)


def save(m, path=None):
    path = path or Path(tempfile.mkdtemp()) / "map.npz"
    m.save(path)
    return path


if __name__ == "__main__":
    main()
