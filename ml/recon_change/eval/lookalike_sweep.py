#!/usr/bin/env python3
"""Choose the limit of locate.py's appearance check, on saved reconstructions (no GPU).

Input: the folder written by `locate_eval.py --dump`. For every pass the match score is worked out
for a grid of rules: patch reach 0 or 1, the limit as slack x a percentile of the baseline's own
similarity, and a few fixed limits for comparison. One line per pass goes to sweep.jsonl.

--summarise then picks the rule on the TUNE scenes only (the largest gap between the lowest
same-room score and the highest wrong-room score, worst baseline size) and shows that rule on the
REPORT scenes, which had no say in the choice. The split is by baseline scene and was fixed before
any score was seen; the known hard wrong-room cases (Lunch_room baseline) are on the report side.

    python lookalike_sweep.py --dump dump --out sweep --workers 16      # on CPU cores
    python lookalike_sweep.py --out sweep --summarise
"""
import argparse
import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TUNE = ["Cantina", "Printing_area", "Meeting_room", "Garden", "Zen"]
REPORT = ["Lounge", "Lunch_room", "Pots", "Playground", "Porch"]
PCTS, SLACKS, FIXED = (1, 5, 10, 25), (1.0, 0.9, 0.8, 0.7, 0.6, 0.5), (0.2, 0.3, 0.4, 0.5, 0.6, 0.7)
PCTS2, SLACKS2, FLOORS, CAPS = (2, 5, 10, 25, 50), (1.0, 0.9, 0.8, 0.7, 0.6), (0.4, 0.5), (0.6, 0.7)


def one(path):
    import locate
    import recon_change as rc

    saved = np.load(path)
    row = json.loads(str(saved["meta"]))
    r = rc.Recon(saved["depth"], saved["conf"], saved["K"], saved["E"], None, saved["feat"])
    n = row["n_baseline"]
    base = list(range(n))
    valid = rc.valid_mask(r, 25.0)
    near = rc.nearest_in_window(r.depth, 7)
    trusted = float(valid[base].mean())
    feat = locate.unit_features(r)
    probes = [base[i] for i in np.linspace(0, n - 1, min(4, n)).round().astype(int)]
    row["v"] = {}
    for reach in (0, 1):
        rng = np.random.default_rng(0)  # the same pixels as locate.locate samples
        own = [locate.views(r, valid, near, feat, q, [j for j in base if j != q], 0.06, 6000, rng, reach) for q in probes]
        now = locate.views(r, valid, near, feat, n, base, 0.06, 6000, rng, reach)

        def share(view, limit):
            agree, alike, seen = view
            return float(locate.matched(agree, alike, limit).mean()) * min(1.0, seen / max(trusted, 1e-6)) if len(agree) else 0.0

        def score(limit):
            usual = float(np.median([share(v, limit) for v in own])) or 1.0
            return round(min(1.0, share(now, limit) / usual), 3)

        sim_own = np.concatenate([alike[agree] for agree, alike, _ in own])
        sim_now = now[1][now[0]]
        if reach == 0:
            row["depth"] = score(None)
        row[f"own_r{reach}"] = [round(float(v), 3) for v in np.percentile(sim_own, [1, 5, 10, 25, 50])] if len(sim_own) else None
        row[f"now_r{reach}"] = [round(float(v), 3) for v in np.percentile(sim_now, [10, 25, 50, 75, 90])] if len(sim_now) else None
        for pct in PCTS:
            low = float(np.percentile(sim_own, pct)) if len(sim_own) else 0.0
            for slack in SLACKS:
                row["v"][f"r{reach}_p{pct}_s{slack}"] = score(slack * low)
        # The same, but from each pixel's second-best similarity among the frames that agree in depth
        # (the value the "at least two frames" rule tests), instead of from all pixel-frame pairs.
        second = lambda agree, alike: np.sort(np.where(agree, alike, -1.0), 1)[:, -2][agree.sum(1) >= 2]
        own2 = np.concatenate([second(agree, alike) for agree, alike, _ in own])
        now2 = second(now[0], now[1])
        row[f"own2_r{reach}"] = [round(float(v), 3) for v in np.percentile(own2, [1, 5, 10, 25, 50])] if len(own2) else None
        row[f"now2_r{reach}"] = [round(float(v), 3) for v in np.percentile(now2, [10, 50, 90, 95, 99])] if len(now2) else None
        for pct in PCTS2:
            low = float(np.percentile(own2, pct)) if len(own2) else 0.0
            for slack in SLACKS2:
                row["v"][f"r{reach}_t{pct}_s{slack}"] = score(slack * low)
                for floor in FLOORS if pct >= 25 else ():  # the same, but never below a fixed value
                    row["v"][f"r{reach}_t{pct}_s{slack}_f{floor}"] = score(max(floor, slack * low))
                    for cap in CAPS:  # ... and never above another
                        row["v"][f"r{reach}_t{pct}_s{slack}_f{floor}_c{cap}"] = score(min(cap, max(floor, slack * low)))
        for limit in FIXED:
            row["v"][f"r{reach}_fixed{limit}"] = score(limit)
    return row


def summarise(rows, light_rounds, show=()):
    show = list(show)
    rows = [r for r in rows if r["kind"] != "same_other_light" or r["round"] < light_rounds]
    sizes = sorted({r["n_baseline"] for r in rows}, reverse=True)
    rules = ["depth"] + list(rows[0]["v"])

    def get(r, rule):
        return r["depth"] if rule == "depth" else r["v"][rule]

    def stats(rule, scenes, n):
        got = [r for r in rows if r["scene"] in scenes and r["n_baseline"] == n]
        same = [get(r, rule) for r in got if r["kind"] == "same"]
        light = [get(r, rule) for r in got if r["kind"] == "same_other_light"]
        wrong = [get(r, rule) for r in got if r["kind"] == "wrong"]
        return same, light, wrong

    def gap(rule, scenes):  # worst size: lowest same-room score (either lighting) minus highest wrong-room score
        out = []
        for n in sizes:
            same, light, wrong = stats(rule, scenes, n)
            if same and wrong:
                out.append(min(same + light) - max(wrong))
        return min(out) if out else float("nan")

    def line(rule, scenes, n):
        same, light, wrong = stats(rule, scenes, n)
        f = lambda v, t: sum(x >= t for x in v)
        return (f"| {rule} | {n} | {f(same, 0.5)}/{len(same)} | {sum(x < 0.3 for x in same)} | {min(same):.2f} | "
                f"{f(light, 0.5)}/{len(light)} | {min(light) if light else float('nan'):.2f} | "
                f"{f(wrong, 0.5)} | {f(wrong, 0.3) - f(wrong, 0.5)} | {len(wrong)} | {max(wrong):.2f} |")

    head = ["| Rule | Baseline photos | Same room found | lost | lowest | Other lighting found | lowest | Wrong room found | unsure | of | highest |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    ranked = sorted(rules, key=lambda rule: -gap(rule, TUNE))
    best = ranked[0]
    L = [f"Passes: {len(rows)}. Tune scenes: {', '.join(TUNE)}. Report scenes: {', '.join(REPORT)}.", "",
         "Gap = lowest same-room score (either lighting) minus highest wrong-room score, at the worst baseline size.", "",
         "| Rule | Gap on tune scenes | Gap on report scenes | Gap on all scenes |", "| --- | --- | --- | --- |"]
    L += [f"| {rule} | {gap(rule, TUNE):.2f} | {gap(rule, REPORT):.2f} | {gap(rule, TUNE + REPORT):.2f} |" for rule in ranked[:12] + ["depth"]]
    L += ["", "The rule families at their best on the tune scenes:", "", "| Family | Best rule | Gap on tune scenes | Gap on report scenes |", "| --- | --- | --- | --- |"]
    families = {"all pairs, percentile x slack": lambda k: "_p" in k, "second-best, percentile x slack": lambda k: "_t" in k and "_f0" not in k,
                "second-best with a floor": lambda k: "_f0" in k and "_c0" not in k,
                "second-best with a floor and a cap": lambda k: "_c0" in k, "fixed limit": lambda k: "fixed" in k}
    for name, belongs in families.items():
        top = next(rule for rule in ranked if rule != "depth" and belongs(rule))
        L.append(f"| {name} | {top} | {gap(top, TUNE):.2f} | {gap(top, REPORT):.2f} |")
    for label, scenes in (("tune", TUNE), ("report", REPORT), ("all", TUNE + REPORT)):
        L += ["", f"Chosen on the tune scenes: `{best}`. On the {label} scenes, against depth alone:", ""] + head
        L += [line(rule, scenes, n) for rule in ["depth", best] + show for n in sizes]
    return "\n".join(L) + "\n", best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump")
    ap.add_argument("--out", default="sweep")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--detector-dir", help="folder with locate.py and recon_change.py (default: next to this script, else its parent)")
    ap.add_argument("--light-rounds", type=int, default=99, help="use only this many other-lighting rounds in the summary")
    ap.add_argument("--summarise", action="store_true")
    ap.add_argument("--show", nargs="*", default=[], help="more rules to show next to the chosen one")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sink = out / "sweep.jsonl"
    if not args.summarise:
        detector_dir = Path(args.detector_dir) if args.detector_dir else HERE if (HERE / "locate.py").exists() else HERE.parent
        sys.path[:0] = [str(detector_dir)]
        files = sorted(p for p in Path(args.dump).glob("*.npz") if ".part" not in p.name)
        print(f"{len(files)} saved reconstructions", flush=True)
        with Pool(args.workers) as pool, open(sink, "w") as f:
            for i, row in enumerate(pool.imap_unordered(one, files, chunksize=2)):
                f.write(json.dumps(row) + "\n")
                if i % 100 == 0:
                    print(i, flush=True)
    rows = [json.loads(line) for line in sink.read_text().splitlines() if line.strip()]
    text, _ = summarise(rows, args.light_rounds, args.show)
    (out / "sweep.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
