#!/usr/bin/env python3
"""Tables for phone_side_eval.py. Needs NumPy only.

    python phone_side_tables.py --walk DIR --out DIR [--variants vits vitb xfeat sift] [--chosen chosen.json]

--walk holds plan.json and results.jsonl of the walking test (the current method's fixes);
--out holds VARIANT.jsonl and VARIANT_info.json written by phone_side_eval.py --score.

Every limit is chosen on two rooms and tested on the third, for each choice of test room:

  1. The score of a fix is the number of matches that agree with its pose times the share of the
     picture they cover. For every setting of the grid (closest photos, pixels a match may be off,
     lowest similarity, descriptor size, sub-patch placing): "unsure" needs a higher score than
     any wrong-room query of the two training rooms reached, plus a quarter. "found" needs at
     least that, and the lowest multiple of it at which at most 1% of the training rooms' found
     fixes are off by more than 0.3 m or 15 degrees (--off-share 0: none may be off).
  2. The setting with the most found fixes on the two training rooms is taken.
  3. That setting and its limits are applied to the third room, and only that is reported.
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

OFF = (0.3, 15.0)  # a found fix is "off" beyond this many metres or degrees (as RIGHT in track_eval.py)
SETTING = ("k", "reproj", "min_sim", "ratio", "dim", "refine")
OFF_SHARE = 0.01  # the share of the training rooms' found fixes that may be off; set by --off-share
REACHES = (0.0,)  # limits on how far outside the baseline cameras a pose may be (0 = not checked); set by --reach


def similarity(src, dst):
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U @ Vt)) or 1.0
    R = U @ D @ Vt
    s = float((S * np.diag(D)).sum() / ((a ** 2).sum() / len(src)))
    return s, R, mu_d - s * R @ mu_s


def angle(a, b):
    return float(np.degrees(np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1))))


def three(values, digits=2):
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    return " / ".join(f"{f(values):.{digits}f}" for f in (np.median, lambda x: np.percentile(x, 90), np.max))


def share(k, n):
    return f"{k} of {n} ({100 * k / n:.0f}%)" if n else "-"


def current_method(walk, plan):
    """The walking test's own fixes (12 + 1): state, and position and heading error as
    locate_walk_tables.py measures them (a similarity transform fitted per fix)."""
    out = {}
    for line in (Path(walk) / "results.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["key"][1] != 12 or r["key"][2] != 1 or "error" in r:
            continue
        row = {"state": r["state"], "sharpness": r.get("sharpness")}
        if r["kind"] == "same":
            room = plan["rooms"][r["room"]]
            q = room["queries"][r["query"]]
            E = np.array(r["E"])
            P = np.array([-e[:, :3].T @ e[:, 3] for e in E])
            G = np.array([room["baseline"][i]["C"] for i in r["pick"]])
            s, R, t = similarity(P[:-1], G)
            true_fwd = np.array(q["R"])[:, 2]
            row.update(pos_err=float(np.linalg.norm(s * R @ P[-1] + t - np.array(q["C"]))), head_err=angle(R @ E[-1][2, :3], true_fwd),
                       view_gap=min(angle(true_fwd, np.array(room["baseline"][i]["R"])[:, 2]) for i in r["pick"]), turn=q["turn"])
        out[(r["kind"], r["room"], r["query_room"], r["query"])] = row
    return out


def off(r):
    return r.get("pos_err") is None or r["pos_err"] > OFF[0] or r["head_err"] > OFF[1]


def score(r):
    """As phone_side.decide: the matches that agree with the pose times the share of the picture they cover."""
    return r["inliers"] * r["spread"]


def choose_limits(rows, reaches=(0.0,)):
    """Limits for one setting from training rows. Returns (found, unsure, reach, found count, off count)."""
    best = None
    for reach in reaches:
        ok = [r for r in rows if not reach or (r.get("reach") is not None and r["reach"] <= reach)]
        wrong = [score(r) for r in ok if r["kind"] == "wrong"]
        same = [r for r in ok if r["kind"] == "same"]
        unsure = max(4.0, math.ceil(1.25 * (max(wrong, default=0.0) + 1)))
        for f in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0):
            found = math.ceil(unsure * f)
            got = [r for r in same if score(r) >= found]
            bad = sum(off(r) for r in got)
            if got and bad <= OFF_SHARE * len(got) and (best is None or (len(got), -bad) > (best[3], -best[4])):
                best = (found, unsure, reach, len(got), bad)
    return best or (1e9, 1e9, 0.0, 0, 0)


def state_of(r, limits):
    found, unsure, reach = limits[:3]
    if reach and (r.get("reach") is None or r["reach"] > reach):
        return "lost"
    return "found" if score(r) >= found else "unsure" if score(r) >= unsure else "lost"


def choose(rows_by_setting, train_rooms):
    """The setting and limits that find the most on the training rooms."""
    best = None
    for setting, rows in rows_by_setting.items():
        train = [r for r in rows if r["room"] in train_rooms]
        limits = choose_limits(train, REACHES)
        errs = [r["pos_err"] for r in train if r["kind"] == "same" and state_of(r, limits) == "found"]
        rank = (limits[3], -(float(np.median(errs)) if errs else 9.0))
        if best is None or rank > best[0]:
            best = (rank, setting, limits)
    return best[1], best[2]


def summary(rows):
    """Counts and errors of rows that already carry a state."""
    same, wrong = [r for r in rows if r["kind"] == "same"], [r for r in rows if r["kind"] == "wrong"]
    found = [r for r in same if r["state"] == "found"]
    return {"same": len(same), "found": len(found), "unsure": sum(r["state"] == "unsure" for r in same),
            "lost": sum(r["state"] == "lost" for r in same), "wrong": len(wrong),
            "wrong_accepted": sum(r["state"] != "lost" for r in wrong), "wrong_found": sum(r["state"] == "found" for r in wrong),
            "pos": [r["pos_err"] for r in found if r.get("pos_err") is not None], "head": [r["head_err"] for r in found if r.get("head_err") is not None],
            "off": sum(off(r) for r in found),
            "pose_right": sum((not off(r)) for r in same), "pose_right_strict": sum(r.get("pos_err") is not None and r["pos_err"] <= 0.2 and r["head_err"] <= 10 for r in same)}


def line(label, s):
    return (f"| {label} | {s['same']} | {share(s['found'], s['same'])} | {s['unsure']} | {s['lost']} | {s['wrong_accepted']} of {s['wrong']} | "
            f"{three(s['pos'])} | {three(s['head'], 1)} | {s['off']} | {share(s['pose_right'], s['same'])} |")


HEAD = ("| {} | Same-room queries | Found | Unsure | Lost | Wrong room accepted (found or unsure) | Position error of found fixes m: median / 90th / worst | "
        "Heading error deg: median / 90th / worst | Found but off by more than 0.3 m or 15 deg | Pose right whatever the state (0.3 m, 15 deg) |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--walk", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--variants", nargs="*", default=["vits", "vitb", "xfeat", "sift"])
    ap.add_argument("--reach", nargs="*", type=float, default=[0.0], help="reach limits to choose from (0 = not checked); needs rows with 'reach'")
    ap.add_argument("--off-share", type=float, default=0.01, help="share of the training rooms' found fixes that may be off")
    ap.add_argument("--bench-plan", help="write the plan file for phone_side_bench.slurm here")
    ap.add_argument("--chosen", help="write the chosen settings and limits here (JSON), for the benchmark and the replay")
    args = ap.parse_args()
    global REACHES, OFF_SHARE
    REACHES, OFF_SHARE = tuple(args.reach), args.off_share
    plan = json.loads((Path(args.walk) / "plan.json").read_text())
    rooms = list(plan["rooms"])
    now = current_method(args.walk, plan)
    L = []
    cur_rows = [{"kind": k[0], "room": k[1], **v} for k, v in now.items()]
    cur = {room: summary([r for r in cur_rows if r["room"] == room]) for room in rooms}
    cur["all"] = summary(cur_rows)

    chosen, tested = {}, {}
    for variant in args.variants:
        base, _, only_dim = variant.partition("@")  # "vits@64": the ViT-S rows with descriptors reduced to 64 numbers
        path = Path(args.out) / f"{base}.jsonl"
        if not path.exists():
            continue
        by = {12: defaultdict(list), 24: defaultdict(list)}
        for text in path.read_text().splitlines():
            r = json.loads(text)
            if r["dim"] == int(only_dim or 0):
                by[r["n"]][tuple(r[k] for k in SETTING)].append(r)
        for n in (12, 24):
            if not by[n]:
                continue
            held = []
            for test in rooms:
                setting, limits = choose(by[n], [r for r in rooms if r != test])
                rows = [dict(r, state=state_of(r, limits)) for r in by[n][setting] if r["room"] == test]
                held += rows
                chosen[f"{variant}/{n}/{test}"] = {"setting": dict(zip(SETTING, setting)), "found": limits[0], "unsure": limits[1], "reach": limits[2],
                                                  "train_found": limits[3], "train_off": limits[4]}
            setting, limits = choose(by[n], rooms)  # on all three rooms: what would ship; NOT a held-out number
            pooled = [dict(r, state=state_of(r, limits)) for r in by[n][setting]]
            chosen[f"{variant}/{n}/all"] = {"setting": dict(zip(SETTING, setting)), "found": limits[0], "unsure": limits[1], "reach": limits[2],
                                           "match_s_1core": float(np.median([r["match_s"] for r in pooled])), "pose_s_1core": float(np.median([r["pose_s"] for r in pooled])),
                                           "pose_s_1core_lost": float(np.median([r["pose_s"] for r in pooled if r["state"] == "lost"] or [0]))}
            tested[(variant, n)] = (held, pooled)

    L += ["### A. Found, and how right (12 baseline photos; every limit chosen on two rooms, tested on the third)", "", HEAD.format("Method")]
    L.append(line("Current method: VGGT over 12 photos + the frame (GPU)", cur["all"]))
    for (variant, n), (held, _) in tested.items():
        if n == 12:
            L.append(line(f"Phone side, {variant}", summary(held)))
    L += ["", "### B. Per room (12 baseline photos, held-out)", "", HEAD.format("Method, room")]
    for room in rooms:
        L.append(line(f"Current method, {room}", cur[room]))
        for (variant, n), (held, _) in tested.items():
            if n == 12:
                L.append(line(f"{variant}, {room}", summary([r for r in held if r["room"] == room])))
    L += ["", "### C. 24 baseline photos instead of 12 (held-out)", "", HEAD.format("Method")]
    for (variant, n), (held, _) in tested.items():
        if n == 24:
            L.append(line(f"Phone side, {variant}, 24 photos", summary(held)))
    L += ["", "### D. The settings and limits that were chosen", "",
          "| Variant | Photos | Test room | Closest photos used (0 = all) | Pixels a match may be off | Lowest similarity | Ratio | Descriptor size (0 = as it comes) | "
          "Sub-patch placing | Found needs a score of | Unsure needs | Reach limit (0 = none) | Found on the two training rooms (off) |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for key, c in chosen.items():
        variant, n, test = key.split("/")
        s = c["setting"]
        L.append(f"| {variant} | {n} | {'all three (ships; not held-out)' if test == 'all' else test} | {s['k']} | {s['reproj']:g} | {s['min_sim']:g} | {s['ratio']:g} | "
                 f"{s['dim']} | {'yes' if s['refine'] else 'no'} | {c['found']:g} | {c['unsure']:g} | {c['reach']:g} | "
                 f"{c.get('train_found', '-')} ({c.get('train_off', '-')}) |")

    # Where it breaks: by the angle to the closest baseline view, by blur, by turning speed.
    edges = {room: np.percentile([v["sharpness"] for k, v in now.items() if k[0] == "same" and k[1] == room], [33.3, 66.7]) for room in rooms}

    def band_of(key):
        v = now[key]
        lo, hi = edges[key[1]]
        return 0 if v["sharpness"] < lo else 1 if v["sharpness"] < hi else 2

    splits = [("By the angle between the query's view and the closest baseline view (degrees, from motion capture)", "view_gap",
               [("under 15", 0, 15), ("15 to 30", 15, 30), ("30 to 50", 30, 50), ("50 and more", 50, 1e9)]),
              ("By blur: thirds of the query frame's sharpness within its room (as the walking test)", "band",
               [("blurriest third", 0, 1), ("middle third", 1, 2), ("sharpest third", 2, 3)]),
              ("By how fast the camera was turning (degrees per second, from motion capture)", "turn",
               [("under 15", 0, 15), ("15 to 40", 15, 40), ("40 and more", 40, 1e9)])]
    shown = [(v, n) for (v, n) in tested if n == 12]
    L += ["", "### E. Where it breaks (12 baseline photos, held-out, all rooms pooled)"]
    for title, what, bands in splits:
        L += ["", title + ":", "", "| Band | Queries | Current method: found | " + " | ".join(f"{v}: found | {v}: position error m, median / 90th / worst" for v, _ in shown) + " |",
              "| --- | --- | --- | " + " | ".join("--- | ---" for _ in shown) + " |"]
        for label, lo, hi in bands:
            def inside(key):
                value = band_of(key) if what == "band" else now[key].get(what)
                return value is not None and lo <= value < hi
            keys = [k for k in now if k[0] == "same" and inside(k)]
            cells = []
            for v, n in shown:
                rows = [r for r in tested[(v, n)][0] if r["kind"] == "same" and inside((r["kind"], r["room"], r["query_room"], r["query"]))]
                found = [r for r in rows if r["state"] == "found"]
                cells.append(f"{share(len(found), len(rows))} | {three([r['pos_err'] for r in found])}")
            L.append(f"| {label} | {len(keys)} | {share(sum(now[k]['state'] == 'found' for k in keys), len(keys))} | " + " | ".join(cells) + " |")

    # Agreement with the current method, query by query.
    L += ["", "### F. The same queries, both methods (12 baseline photos, held-out)", "",
          "| Variant | Found by both | Only by the current method | Only by the phone side | By neither |", "| --- | --- | --- | --- | --- |"]
    for v, n in shown:
        c = defaultdict(int)
        for r in tested[(v, n)][0]:
            if r["kind"] == "same":
                c[(now[(r["kind"], r["room"], r["query_room"], r["query"])]["state"] == "found", r["state"] == "found")] += 1
        L.append(f"| {v} | {c[(True, True)]} | {c[(True, False)]} | {c[(False, True)]} | {c[(False, False)]} |")

    # Which room is this? The global descriptor alone.
    L += ["", "### G. Which room is this, from the global descriptor alone (12 baseline photos per room)", "",
          "| Variant | Query frames | Right room of the three | Held-out limit: same room accepted | Held-out limit: another room accepted |", "| --- | --- | --- | --- | --- |"]
    for variant in [v for v in args.variants if "@" not in v]:
        path = Path(args.out) / f"{variant}_info.json"
        if not path.exists():
            continue
        rec = [r for r in json.loads(path.read_text()).get("recognise", []) if r["n"] == 12]
        if not rec:
            L.append(f"| {variant} | - | no global descriptor | - | - |")
            continue
        right = sum(max(r["sims"], key=r["sims"].get) == r["room"] for r in rec)
        same_ok = same_n = wrong_ok = wrong_n = 0
        for test in rooms:  # the limit: just above the best a wrong-room frame reached on the other two rooms' photos
            limit = max(r["sims"][room] for r in rec for room in rooms if room != test and room != r["room"]) + 0.02
            same_ok += sum(r["sims"][test] >= limit for r in rec if r["room"] == test)
            same_n += sum(r["room"] == test for r in rec)
            wrong_ok += sum(r["sims"][test] >= limit for r in rec if r["room"] != test)
            wrong_n += sum(r["room"] != test for r in rec)
        L.append(f"| {variant} | {len(rec)} | {share(right, len(rec))} | {share(same_ok, same_n)} | {share(wrong_ok, wrong_n)} |")

    # Map size.
    L += ["", "### H. The map file per room", "", "| Variant | Photos | Descriptor size | Features per room | File MB per room (three rooms) | Baseline cameras against the truth, m (RMS) |",
          "| --- | --- | --- | --- | --- | --- |"]
    for variant in [v for v in args.variants if "@" not in v]:
        path = Path(args.out) / f"{variant}_info.json"
        if not path.exists():
            continue
        maps = json.loads(path.read_text())["maps"]
        for n in (12, 24):
            for dim in sorted({v["desc_dim"] for v in maps.values()}, reverse=True):
                got = [v for k, v in maps.items() if v["photos"] == n and v["desc_dim"] == dim]
                if got:
                    L.append(f"| {variant} | {n} | {dim} | {', '.join(str(v['features']) for v in got)} | {', '.join(f'{v['bytes'] / 1e6:.1f}' for v in got)} | "
                             f"{', '.join(f'{v['fit_rms_m']:.3f}' for v in got)} |")
    print("\n".join(L))
    if args.chosen:
        Path(args.chosen).write_text(json.dumps(chosen, indent=1))
    if args.bench_plan:  # one line per variant for phone_side_bench.slurm: the setting that would ship, 12 photos
        lines = [f"{key.split('/')[0].partition('@')[0]} {json.dumps(c['setting'])}" for key, c in chosen.items() if key.endswith("/12/all")]
        Path(args.bench_plan).write_text("".join(line + chr(10) for line in lines))


if __name__ == "__main__":
    main()
