#!/usr/bin/env python3
"""Measure the day log's visits against motion-capture truth. CPU only, no model.

Reads what the walking test left (locate_walk_eval.py): plan.json (five places per room with true
positions; every query frame with its recording and time), results.jsonl (the saved position fix
of each query, with its real found / unsure / lost state) and each recording's groundtruth.txt.

Per recording: the true visits come from the true path and the true place positions; the saved
fixes are replayed in time order through day.build_visits, thinned to one fix every 1, 3 and 6 s.

    python day_eval.py --walk /scratch/$USER/junction-recon/walk --out /scratch/$USER/junction-recon/day

Three ways of reading a fix's distance to a place:
    shown   distance_m as locate.py returns it (today: the map's metres, too long)
    room    distance_m divided by the room's median metres ratio (one known distance per room)
    exact   the fix's camera, fitted to the truth, against the true place (the best any scale could do)
Truth always uses true metres: near means within ENTER, a true visit ends beyond ENTER + 0.25 m.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import day  # noqa: E402

TRUTH_SLACK_M = 0.25  # hysteresis of the true visits, so the truth itself does not flicker at the limit
TOL_S = 0.5           # an estimated and a true visit this close in time count as the same visit
PHASES = {1: (0.0, 0.5), 3: (0.0, 1.0, 2.0), 6: (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)}
BINS = ((1, 49), (50, 59), (60, 69), (70, 79), (80, 89), (90, 99))


def similarity(src, dst):
    """Least-squares scale s, rotation R and shift t with dst ~ s * R @ src + t (Umeyama)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U @ Vt)) or 1.0
    R = U @ D @ Vt
    s = float((S * np.diag(D)).sum() / ((a ** 2).sum() / len(src)))
    return s, R, mu_d - s * R @ mu_s


def true_path(folder):
    rows = [line.split() for line in (Path(folder) / "groundtruth.txt").read_text().splitlines() if line.strip() and not line.startswith("#")]
    data = np.array([[float(v) for v in r[:4]] for r in rows])
    return data[:, 0], data[:, 1:4]


def load(walk):
    """{(room, recording): {"fixes": [...], "t": times, "xy": true path, "places": {name: xy}}}"""
    walk = Path(walk)
    plan = json.loads((walk / "out" / "plan.json").read_text())
    streams = {}
    for line in (walk / "out" / "results.jsonl").read_text().splitlines():
        row = json.loads(line) if line.strip() else None
        if not row or row.get("kind") != "same" or row["key"][1] != 12 or row["n_new"] != 1 or "error" in row:
            continue
        room = plan["rooms"][row["room"]]
        q = room["queries"][row["query"]]
        E = np.array(row["E"])
        nb = row["n_baseline"]
        P = np.array([-e[:, :3].T @ e[:, 3] for e in E])  # camera centres in the model's frame (locate.camera)
        G = np.array([room["baseline"][i]["C"] for i in row["pick"]])
        s, R, t = similarity(P[:nb], G)
        here = s * R @ P[-1] + t
        places = {p["name"]: np.array(p["world"]) for p in room["places"]}
        fix = {"t": q["t"], "s": row["state"], "c": row["confidence"], "ratio": row["metres_per_unit"] / s,
               "shown": {w["name"]: w["distance_m"] for w in row["waypoints"]},
               "exact": {name: float(np.hypot(*(X - here)[:2])) for name, X in places.items()},
               "true": {name: float(np.hypot(*(X - np.array(q["C"]))[:2])) for name, X in places.items()},
               "pos_err": float(np.linalg.norm(here - np.array(q["C"])))}
        key = (row["room"], q["rec"])
        if key not in streams:
            times, xyz = true_path(walk / "data" / "tum" / q["rec"])
            streams[key] = {"fixes": [], "t": times, "xyz": xyz, "places": places}
        streams[key]["fixes"].append(fix)
    ratios = defaultdict(list)
    for (room, _), st in streams.items():
        st["fixes"].sort(key=lambda f: f["t"])
        ratios[room] += [f["ratio"] for f in st["fixes"] if f["s"] == "found"]
    ratio = {room: float(np.median(v)) for room, v in ratios.items()}
    for (room, _), st in streams.items():
        for f in st["fixes"]:
            f["room"] = {name: d / ratio[room] for name, d in f["shown"].items()}
    return streams, ratio


def thin(fixes, every, phase):
    """One fix per tick of `every` seconds: the first fix inside each tick, none if the tick is empty."""
    if not every:
        return fixes
    out, t0 = [], fixes[0]["t"] + phase
    taken = set()
    for f in fixes:
        if f["t"] < t0:
            continue
        tick = int((f["t"] - t0) // every)
        if tick not in taken:
            taken.add(tick)
            out.append(f)
    return out


def truth_visits(st, enter):
    """True visits per place in the stretch the fixes cover, from the true path at 10 Hz."""
    lo, hi = st["fixes"][0]["t"], st["fixes"][-1]["t"]
    grid = np.arange(lo, hi + 1e-6, 0.1)
    xyz = np.stack([np.interp(grid, st["t"], st["xyz"][:, k]) for k in range(3)], 1)
    dist = {name: np.hypot(*(xyz[:, :2] - X[:2]).T) for name, X in st["places"].items()}
    samples = [{"t": float(t), "s": "found", "z": "r", "w": {name: float(d[i]) for name, d in dist.items()}} for i, t in enumerate(grid)]
    visits = day.build_visits(samples, enter=enter, leave=enter + TRUTH_SLACK_M, out_fixes=1, gap_s=1e9)["places"]
    return visits, grid, dist


def overlap(a, b):
    return a["start"] <= b["end"] + TOL_S and a["end"] >= b["start"] - TOL_S


def replay(streams, read, every, enter, leave, out_fixes, rooms=None, truth_enter=None):
    """Counts over all recordings (and all phases of the thinning) for one way of reading and one setting.
    truth_enter: what "near" truly means, when the limits given to the log are not meant as true metres."""
    log_enter, enter = enter, truth_enter or enter
    tot = defaultdict(float)
    errs, rels, visits_out = [], [], []
    for (room, rec), st in streams.items():
        if rooms and room not in rooms:
            continue
        true, grid, dist = truth_visits(st, enter)
        for phase in PHASES.get(every, (0.0,)):
            stream = [{"t": f["t"], "s": f["s"], "z": "r", "w": f[read]} for f in thin(st["fixes"], every, phase)]
            est = day.build_visits(stream, enter=log_enter, leave=leave, out_fixes=out_fixes, gap_s=day.GAP_S)["places"]
            tot["runs"] += 1
            tot["fixes"] += len(stream)
            tot["found_fixes"] += sum(f["s"] == "found" for f in stream)
            tot["true"] += len(true)
            tot["true_s"] += sum(u["end"] - u["start"] for u in true)
            tot["est"] += len(est)
            tot["est_s"] += sum(v["end"] - v["start"] for v in est)
            for u in true:
                hits = [v for v in est if v["place"] == u["place"] and overlap(v, u)]
                length = u["end"] - u["start"]
                long = every and length >= 2 * every
                tot["true_long"] += bool(long)
                if hits:
                    tot["found"] += 1
                    tot["found_long"] += bool(long)
                    tot["split"] += len(hits) > 1
                    seen = sum(v["end"] - v["start"] for v in hits)
                    errs.append(seen - length)
                    if length >= 1:
                        rels.append((seen - length) / length)
            for v in est:
                real = any(u["place"] == v["place"] and overlap(v, u) for u in true)
                inside = (grid >= v["start"] - 0.05) & (grid <= v["end"] + 0.05)
                d = dist[v["place"]][inside] if inside.any() else dist[v["place"]][[int(np.argmin(np.abs(grid - v["start"])))]]
                tot["invented"] += not real
                tot["invented_far"] += (not real) and float(d.min()) > enter + 0.5
                visits_out.append({"room": room, "rec": rec, "place": v["place"], "pct": v["confidence_pct"], "real": bool(real),
                                   "fixes": v["fixes"], "closest": v["closest"], "seconds": v["end"] - v["start"],
                                   "true_min": float(d.min()), "inside": float((d <= enter + TRUTH_SLACK_M).mean())})
    out = dict(tot)
    out.update(time_err_median_s=float(np.median(np.abs(errs))) if errs else None, time_bias_median_s=float(np.median(errs)) if errs else None,
               time_err_median_share=float(np.median(np.abs(rels))) if rels else None, visits=visits_out)
    return out


def pct(a, b):
    return "-" if not b else f"{100 * a / b:.0f}%"


def line(label, r):
    return (f"| {label} | {r['true']:.0f} | {r['found']:.0f} ({pct(r['found'], r['true'])}) | {pct(r['found_long'], r['true_long'])} of {r['true_long']:.0f} | "
            f"{r['split']:.0f} ({pct(r['split'], r['found'])}) | {r['est']:.0f} | {r['invented']:.0f} ({pct(r['invented'], r['est'])}) | {r['invented_far']:.0f} | "
            f"{pct(r['est_s'], r['true_s'])} | {'-' if r['time_err_median_s'] is None else format(r['time_err_median_s'], '.1f')} s"
            f" ({'-' if r['time_err_median_share'] is None else format(100 * r['time_err_median_share'], '.0f') + '%'}) |")


HEAD = ("| {} | True visits | Found | Found, of those lasting 2 photos or more | Split in two or more (of found) | Visits logged | Invented | Invented, wearer truly over 0.5 m "
        "outside | Time logged / true time | Error of time spent per found visit, median |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")


def calibration(visits):
    rows = []
    for lo, hi in BINS:
        group = [v for v in visits if lo <= v["pct"] <= hi]
        if group:
            rows.append(f"| {lo} to {hi}% | {len(group)} | {np.mean([v['pct'] for v in group]):.0f}% | {100 * np.mean([v['real'] for v in group]):.0f}% | "
                        f"{100 * np.mean([v['inside'] for v in group]):.0f}% |")
    return "| Stated confidence | Visits | Mean stated | Were real | Share of the logged time truly near |\n| --- | --- | --- | --- | --- |\n" + "\n".join(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--walk", required=True, help="the walking test's folder: out/plan.json, out/results.jsonl, data/tum/")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    streams, ratio = load(args.walk)
    rooms = sorted({room for room, _ in streams})
    md, results = [], {"ratio": ratio, "limits": {"enter": day.ENTER_M, "leave": day.LEAVE_M, "out_fixes": day.OUT_FIXES, "gap_s": day.GAP_S,
                                                   "conf": day.CONF, "raw_sure": day.RAW_SURE, "speak_pct": day.SPEAK_PCT}}

    md.append("## The streams\n\n| Room | Recording | Seconds covered | Fixes | Median seconds between fixes | Found | Longest stretch without a found fix |\n"
              "| --- | --- | --- | --- | --- | --- | --- |")
    for (room, rec), st in sorted(streams.items()):
        t = np.array([f["t"] for f in st["fixes"]])
        found = np.array([f["t"] for f in st["fixes"] if f["s"] == "found"])
        edges = np.concatenate([[t[0]], found, [t[-1]]])
        md.append(f"| {room} | {rec} | {t[-1] - t[0]:.0f} | {len(t)} | {np.median(np.diff(t)):.1f} | {pct(len(found), len(t))} | {np.diff(edges).max():.1f} s |")
    found_err = [f["pos_err"] for st in streams.values() for f in st["fixes"] if f["s"] == "found"]
    md.append(f"\nFound fixes: {len(found_err)}, position error median {np.median(found_err):.2f} m, worst {max(found_err):.2f} m. "
              f"Metres ratio per room (shown / true): " + ", ".join(f"{r} {ratio[r]:.2f}" for r in rooms) + ".")
    spread = {r: np.percentile([f["ratio"] for (room, _), st in streams.items() if room == r for f in st["fixes"] if f["s"] == "found"], [5, 95]) for r in rooms}
    md.append("Ratio from fix to fix, 5th to 95th percentile: " + ", ".join(f"{r} {a:.2f} to {b:.2f}" for r, (a, b) in spread.items()) + ".")

    E, L, O = day.ENTER_M, day.LEAVE_M, day.OUT_FIXES
    for read, title in (("room", "one known distance per room (`room`)"), ("shown", "today's metres, no scale (`shown`)"), ("exact", "best case (`exact`)")):
        md.append(f"\n## Visits with the shipped limits (enter {E} m, leave {L} m, {O} fixes out), {title}\n\n" + HEAD.format("Room, one fix every"))
        for every in (1, 3, 6):
            for group in rooms + [None]:
                r = replay(streams, read, every, E, L, O, [group] if group else None)
                results[f"{read}/{every}/{group or 'all'}"] = {k: v for k, v in r.items() if k != "visits"}
                md.append(line(f"{group or '**All rooms**'}, {every} s", r))
                if group is None:
                    results[f"{read}/{every}/visits"] = r["visits"]
    r = replay(streams, "room", 0, E, L, O)
    md.append(f"\nEvery saved fix (no thinning), `room`: " + line("all", r))

    wide = E + 0.5
    md.append(f"\n## Today's metres with the limits raised to {wide} m and {wide + L - E} m (BEACON_NEAR_M={wide}), against true visits at {E} m\n\n" + HEAD.format("One fix every"))
    for every in (1, 3, 6):
        r = replay(streams, "shown", every, wide, wide + L - E, O, truth_enter=E)
        results[f"shown_wide/{every}/all"] = {k: v for k, v in r.items() if k != "visits"}
        md.append(line(f"All rooms, {every} s", r))
        for group in rooms:
            md.append(line(f"{group}, {every} s", replay(streams, "shown", every, wide, wide + L - E, O, [group], truth_enter=E)))

    md.append("\n## Does the stated confidence match?\n")
    for read in ("room", "shown"):
        for every in (1, 3, 6):
            md.append(f"\n`{read}`, one fix every {every} s:\n\n" + calibration(results[f"{read}/{every}/visits"]))
    every_visit = [v for read in ("room", "shown") for every in (1, 3, 6) for v in results[f"{read}/{every}/visits"]]
    md.append("\nAll six replays together:\n\n" + calibration(every_visit))
    md.append("\nBy number of fixes in the visit (all six replays):\n\n| Fixes in the visit | Visits | Were real | Mean stated |\n| --- | --- | --- | --- |")
    for lo, hi in ((1, 1), (2, 2), (3, 4), (5, 9), (10, 999)):
        group = [v for v in every_visit if lo <= v["fixes"] <= hi]
        if group:
            md.append(f"| {lo if lo == hi else f'{lo} to {hi}' if hi < 999 else f'{lo} or more'} | {len(group)} | {100 * np.mean([v['real'] for v in group]):.0f}% | {np.mean([v['pct'] for v in group]):.0f}% |")
    md.append("\nBy how close the closest fix was, as a share of the enter limit (all six replays):\n\n| Closest / enter | Visits | Were real | Mean stated |\n| --- | --- | --- | --- |")
    for lo, hi in ((0, 0.7), (0.7, 0.85), (0.85, 1.01)):
        group = [v for v in every_visit if lo <= v["closest"] / E < hi]
        if group:
            md.append(f"| {lo:.2f} to {min(hi, 1):.2f} | {len(group)} | {100 * np.mean([v['real'] for v in group]):.0f}% | {np.mean([v['pct'] for v in group]):.0f}% |")

    md.append("\n## Other limits (all rooms)\n\n" + HEAD.format("Reading, cadence, enter / leave / fixes out"))
    for read in ("room", "shown"):
        for every in (1, 6):
            for enter in (1.0, 1.5, 2.0):
                for slack in (0.0, 0.25, 0.5, 1.0):
                    for out_fixes in (1, 2):
                        r = replay(streams, read, every, enter, enter + slack, out_fixes)
                        results[f"sweep/{read}/{every}/{enter}/{slack}/{out_fixes}"] = {k: v for k, v in r.items() if k != "visits"}
                        md.append(line(f"{read}, {every} s, {enter} / {enter + slack} / {out_fixes}", r))
    text = "\n".join(md) + "\n"
    (out / "day_eval.md").write_text(text)
    (out / "day_eval.json").write_text(json.dumps(results))
    print(text)


if __name__ == "__main__":
    main()
