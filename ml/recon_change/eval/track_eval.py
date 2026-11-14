#!/usr/bin/env python3
"""Measure track.py against motion-capture truth, by replaying saved position fixes in time order.

Needs no GPU and no model: it reads what locate_walk_eval.py left behind (plan.json and
results.jsonl: 938 fixes on TUM RGB-D with their match scores, estimated and true poses and
timestamps) and the recordings' groundtruth.txt (the true camera pose 100 times a second), so the
answer can also be checked BETWEEN photos, while it is getting older.

    python eval/track_eval.py --walk DIR --truth DIR            # tables (markdown) on stdout
    python eval/track_eval.py --walk DIR --truth DIR --fit      # also print the fitted weights for track.py

--walk holds plan.json and results.jsonl; --truth holds <recording>/groundtruth.txt.

How truth is compared. Every fix reports a position on "the room map", but each fix builds that map
anew, so the map wobbles a little from fix to fix. One fixed transform per room (scale, rotation,
shift) takes the map to the motion-capture frame. It is fitted on the baseline cameras only, over
all fixes of the room, so it uses no truth about the wearer. Errors are in true metres. This is
stricter than LOCATE_WALK_RESULTS.md, which fits the transform anew for every fix: here the wobble
of the map counts as error, as it does for a display that keeps a pose from one fix to the next.

"Right" means position within 0.3 m AND heading within 15 degrees of the truth at that moment.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import track  # noqa: E402

RIGHT = (0.3, 15.0)
STRICT = (0.2, 10.0)
GRID_S = 0.5  # the display is looked at this often between photos
FEATURES = list(track.CAL)


def wrap(deg):
    return (np.asarray(deg) + 180.0) % 360.0 - 180.0


# ----------------------------------------------------------------------------- reading
def camera(E):
    return -E[:, :3].T @ E[:, 3], E[2, :3]


def axes(E, base):  # as locate.axes
    up = -np.mean([E[q][1, :3] for q in base], 0)
    up /= np.linalg.norm(up)
    north = E[base[0]][2, :3] - (E[base[0]][2, :3] @ up) * up
    north /= np.linalg.norm(north)
    return up, north, np.cross(north, up)


def similarity(src, dst):
    """Least-squares scale s, rotation R and shift t with dst ~ s * R @ src + t (Umeyama)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U @ Vt)) or 1.0
    R = U @ D @ Vt
    return float((S * np.diag(D)).sum() / ((a ** 2).sum() / len(src))), R, mu_d - float((S * np.diag(D)).sum() / ((a ** 2).sum() / len(src))) * R @ mu_s


def trajectory(path, s, R, t):
    """A recording's true poses on the room map: times, positions (map units), headings (degrees)."""
    rows = np.array([[float(v) for v in line.split()] for line in Path(path).read_text().splitlines()
                     if line.strip() and not line.startswith("#")])
    x, y, z, w = (rows[:, 4:8] / np.linalg.norm(rows[:, 4:8], axis=1, keepdims=True)).T
    forward = np.stack([2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)], 1) @ R
    return rows[:, 0], (rows[:, 1:4] - t) @ R / s, np.degrees(np.arctan2(forward[:, 0], forward[:, 1]))


def load(walk, truth_dir):
    plan = json.loads((Path(walk) / "plan.json").read_text())
    rows = [json.loads(line) for line in (Path(walk) / "results.jsonl").read_text().splitlines() if line.strip()]
    rows = [r for r in rows if "error" not in r and r["key"][1] == 12 and r["key"][2] == 1]
    rooms = {}
    for room, spec in plan["rooms"].items():
        same = [r for r in rows if r["kind"] == "same" and r["room"] == room]
        M, G, per_fix = [], [], []
        for r in same:
            E, nb = np.array(r["E"]), r["n_baseline"]
            up, north, east = axes(E, list(range(nb)))
            P = np.array([camera(e)[0] for e in E])
            d = (P - P[0]) * r["metres_per_unit"]
            on_map = np.stack([d @ east, d @ north, d @ up], 1)
            truth = np.array([spec["baseline"][i]["C"] for i in r["pick"]])
            M.append(on_map[:nb])
            G.append(truth)
            q = spec["queries"][r["query"]]
            s1, R1, t1 = similarity(P[:nb], truth)  # the fit of LOCATE_WALK_RESULTS.md: anew for this fix
            fwd = R1 @ E[-1][2, :3]
            per_fix.append((float(np.linalg.norm(s1 * R1 @ P[-1] + t1 - np.array(q["C"]))),
                            float(np.degrees(np.arccos(np.clip(fwd @ np.array(q["R"])[:, 2], -1, 1))))))
        s, R, t = similarity(np.concatenate(M), np.concatenate(G))
        wobble = [float(np.sqrt((((s * m @ R.T + t) - g) ** 2).sum(1).mean())) for m, g in zip(M, G)]
        recs = defaultdict(list)
        for r, own in zip(same, per_fix):
            q = spec["queries"][r["query"]]
            f = np.array(q["R"])[:, 2] @ R
            recs[q["rec"]].append({
                "t": q["t"], "true_pos": (np.array(q["C"]) - t) @ R / s, "true_head": float(np.degrees(np.arctan2(f[0], f[1]))),
                "own_err": own, "speed": q["speed"], "turn": q["turn"],
                "fix": {"zone": room, "state": r["state"], "confidence": r["confidence"], "position": r["position"],
                        "heading_deg": r["heading_deg"], "waypoints": r["waypoints"]}})
        for fixes in recs.values():
            fixes.sort(key=lambda f: f["t"])
        wrong = [{"zone": room, "state": r["state"], "confidence": r["confidence"], "position": r["position"],
                  "heading_deg": r["heading_deg"], "waypoints": r["waypoints"]}
                 for r in rows if r["kind"] == "wrong" and r["room"] == room]
        rooms[room] = {"scale": s, "recs": dict(recs), "wrong": wrong, "wobble": wobble,
                       "traj": {rec: trajectory(Path(truth_dir) / rec / "groundtruth.txt", s, R, t) for rec in recs}}
    return rooms


def truth_at(traj, when):
    times, pos, head = traj
    i = int(np.clip(np.searchsorted(times, when), 1, len(times) - 1))
    i = i if abs(times[i] - when) < abs(times[i - 1] - when) else i - 1
    return pos[i], float(head[i]), abs(float(times[i]) - when)


# ----------------------------------------------------------------------------- replay
def stream(fixes, interval, phase):
    """One photo every `interval` seconds: at each tick the saved fix nearest in time, each fix used once."""
    out, used = [], set()
    times = np.array([f["t"] for f in fixes])
    tick = times[0] + phase
    while tick <= times[-1] + 1e-6:
        i = int(np.argmin(np.abs(times - tick)))
        if abs(times[i] - tick) <= min(0.5, interval / 2) and i not in used:
            used.add(i)
            out.append(fixes[i])
        tick += interval
    return out


def errors(position, heading, true_pos, true_head, scale):
    return scale * float(np.linalg.norm(np.asarray(position) - true_pos)), abs(float(wrap(heading - true_head)))


def replay(room, rec, data, interval, phase, mix=None, seed=0, tail=None):
    """Feed one stream through a fresh tracker. Returns (rows at the moment of each photo, rows on a
    time grid between photos). mix: None, or (every, burst, relabel): every `every`-th photo is
    replaced by `burst` photos of another room, relabelled "found" if `relabel`."""
    scale, traj = data["scale"], data["traj"][rec]
    fixes = stream(data["recs"][rec], interval, phase)
    if len(fixes) < 3:
        return [], []
    rng = np.random.default_rng(seed)
    events = []
    for k, f in enumerate(fixes):
        foreign = mix and k >= 3 and (k - 3) % mix[0] < mix[1]
        if foreign:
            w = dict(data["wrong"][int(rng.integers(len(data["wrong"])))])
            if mix[2]:
                w.update(state="found", confidence=0.6)
            events.append((f["t"], w, None))
        else:
            events.append((f["t"], f["fix"], f))
    tr = track.Tracker(zone=room)
    at_fix, grid = [], []
    raw = None  # what today's display holds: the newest fix
    end = events[-1][0] + (interval if tail is None else tail)
    n, when, prev_truth = 0, events[0][0], None
    while when <= end + 1e-6:
        while n < len(events) and events[n][0] <= when + 1e-6:
            t, fix, src = events[n]
            before = tr.dump()
            out = tr.update(fix, t)
            true_pos, true_head, _ = truth_at(traj, t)
            row = {"room": room, "rec": rec, "t": t, "foreign": src is None, "fix_state": fix["state"], "score": fix["confidence"],
                   "kind": tr.s["recent"][-1]["kind"], "state": out["state"], "features": tr.features(t),
                   "moved_pose": before["position"] is not None and out["position"] is not None and (
                       before["position"] != tr.s["position"] or before["heading_deg"] != tr.s["heading_deg"]),
                   "dropped": before["position"] is not None and out["position"] is None}
            if fix.get("position") is not None:
                row["raw_err"] = errors(fix["position"], fix["heading_deg"], true_pos, true_head, scale)
            if out["position"] is not None:
                row["err"] = errors(tr.s["position"], tr.s["heading_deg"], true_pos, true_head, scale)
            if prev_truth is not None:
                row["truth_moved"] = errors(prev_truth[0], prev_truth[1], true_pos, true_head, scale)
            if src is not None:
                row["own_err"] = src["own_err"]
            prev_truth = (true_pos, true_head)
            at_fix.append(row)
            raw = fix
            n += 1
        true_pos, true_head, gap = truth_at(traj, when)
        if gap < 0.05 and raw is not None:
            out = tr.current(when)
            g = {"room": room, "rec": rec, "t": when, "state": out["state"], "features": tr.features(when), "age": out["age_s"],
                 "warm": when > events[1][0],
                 "raw_arrow": raw["state"] != "lost" and raw.get("position") is not None, "has_pose": out["position"] is not None}
            if g["raw_arrow"]:
                g["raw_err"] = errors(raw["position"], raw["heading_deg"], true_pos, true_head, scale)
            if g["has_pose"]:
                g["err"] = errors(tr.s["position"], tr.s["heading_deg"], true_pos, true_head, scale)
                g["hold"] = tr.features(max(when, tr.s["at"] + tr.cycle()))  # what decides the arrow: see track.current
            grid.append(g)
        when += GRID_S
    return at_fix, grid


def phases(interval):
    return [0.0] if interval <= 1 else list(np.arange(0.0, interval, 1.0))


def replay_all(rooms, interval, **more):
    """Every recording of every room, at every phase. Returns {room: [(at_fix, grid) per stream]}."""
    out = {}
    for room, data in rooms.items():
        out[room] = [replay(room, rec, data, interval, phase, seed=i, **more)
                     for rec in data["recs"] for i, phase in enumerate(phases(interval))]
        out[room] = [run for run in out[room] if run[0]]
    return out


# ----------------------------------------------------------------------------- confidence
def right(err, limit=RIGHT):
    return err is not None and err[0] <= limit[0] and err[1] <= limit[1]


def fit_logistic(X, y, l2=1.0, steps=60):
    """Plain logistic regression by Newton steps, with a small pull towards zero on every weight but the first."""
    w = np.zeros(X.shape[1])
    pull = np.eye(X.shape[1]) * l2
    pull[0, 0] = 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        step = np.linalg.solve(X.T @ (X * (p * (1 - p))[:, None]) + pull + 1e-9 * np.eye(len(w)), X.T @ (y - p) - pull @ w)
        w += step
        if np.abs(step).max() < 1e-7:
            break
    return w


def predict(w, X):
    return 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))


def samples(runs_by_room, room):
    """(features, right?) for every moment on the grid where the tracker has a pose, in one room."""
    X, y, y_strict = [], [], []
    for _, grid in runs_by_room[room]:
        for g in grid:
            if g["has_pose"]:
                X.append([g["features"][k] for k in FEATURES])
                y.append(right(g["err"]))
                y_strict.append(right(g["err"], STRICT))
    return np.array(X, float), np.array(y, float), np.array(y_strict, float)


def fix_samples(rooms, room):
    """(match score, right?) for single fixes, judged on their own (the per-fix fit of
    LOCATE_WALK_RESULTS.md). Photos of another room are in it too, and are never right: a caller
    with one fix in hand cannot know which room the photo was taken in."""
    fixes = [f for fs in rooms[room]["recs"].values() for f in fs]
    foreign = rooms[room]["wrong"]
    X = np.array([[1.0, f["fix"]["confidence"]] for f in fixes] + [[1.0, w["confidence"]] for w in foreign])
    return (X, np.array([right(f["own_err"]) for f in fixes] + [False] * len(foreign), float),
            np.array([right(f["own_err"], STRICT) for f in fixes] + [False] * len(foreign), float))


BINS = [(0, 10), (10, 30), (30, 50), (50, 70), (70, 85), (85, 95), (95, 101)]


def reliability(p, y):
    """Rows of (bin, how many, mean stated, share right) and the expected calibration error in points."""
    rows, gap = [], 0.0
    for low, high in BINS:
        inside = (p * 100 >= low) & (p * 100 < high)
        if inside.any():
            rows.append((f"{low} to {min(high, 100)}%", int(inside.sum()), 100 * p[inside].mean(), 100 * y[inside].mean()))
            gap += inside.mean() * abs(100 * p[inside].mean() - 100 * y[inside].mean())
    return rows, gap


# ----------------------------------------------------------------------------- tables
def three(values, digits=2):
    values = [v for v in values if v is not None]
    if not values:
        return "- | - | -"
    return " | ".join(f"{f(values):.{digits}f}" for f in (np.median, lambda x: np.percentile(x, 90), np.max))


def pct(k, n):
    return f"{100 * k / n:.0f}%" if n else "-"


def display_stats(runs, shown, err_key):
    """Over the time grid of every stream: share of time an arrow is shown, the share of that time it
    is right, and changes between arrow and no arrow per minute. shown(g) says whether an arrow is drawn.
    Counted from each stream's second photo on: the replays are short pieces cut from longer use, and
    the steady answer needs two photos to start, which would count as one change per piece."""
    time_all = time_arrow = time_right = time_strict = flips = 0
    for _, grid in runs:
        last = None
        for g in grid:
            arrow = bool(shown(g))
            if not g["warm"]:
                continue
            time_all += 1
            if arrow:
                time_arrow += 1
                time_right += right(g.get(err_key))
                time_strict += right(g.get(err_key), STRICT)
            if last is not None and arrow != last:
                flips += 1
            last = arrow
    minutes = time_all * GRID_S / 60
    return {"minutes": minutes, "arrow": time_arrow / max(time_all, 1), "right": time_right / max(time_arrow, 1),
            "strict": time_strict / max(time_arrow, 1), "flips": flips / max(minutes, 1e-9),
            "right_of_all": time_right / max(time_all, 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--walk", required=True, help="folder with plan.json and results.jsonl from locate_walk_eval.py")
    ap.add_argument("--truth", required=True, help="folder with <recording>/groundtruth.txt")
    ap.add_argument("--fit", action="store_true", help="print the weights fitted on all rooms, for track.py")
    ap.add_argument("--limits", type=int, nargs="*", default=[10, 20, 30, 40, 50, 60], help="display limits to compare")
    args = ap.parse_args()
    rooms = load(args.walk, args.truth)
    names = list(rooms)
    L = []

    L += ["### The data", "", "| Room | Recordings | Saved fixes (found / unsure / lost) | Median time between saved fixes, s | True metres per map metre | "
          "Wobble of the map between fixes, m (median / worst) | Photos of other rooms |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for room, d in rooms.items():
        fixes = [f for fs in d["recs"].values() for f in fs]
        gaps = np.concatenate([np.diff([f["t"] for f in fs]) for fs in d["recs"].values()])
        states = [f["fix"]["state"] for f in fixes]
        L.append(f"| {room} | {len(d['recs'])} | {len(fixes)} ({states.count('found')} / {states.count('unsure')} / {states.count('lost')}) | "
                 f"{np.median(gaps):.2f} | {d['scale']:.2f} | {np.median(d['wobble']):.3f} / {np.max(d['wobble']):.3f} | {len(d['wrong'])} |")

    # ---- the confidence: fit on two rooms, test on the third
    intervals = [1, 3, 6]
    runs = {iv: replay_all(rooms, iv) for iv in intervals}
    weights, stated = {}, {}
    for test in names:
        train = [r for r in names if r != test]
        X = np.concatenate([samples(runs[iv], r)[0] for iv in intervals for r in train])
        y = np.concatenate([samples(runs[iv], r)[1] for iv in intervals for r in train])
        weights[test] = fit_logistic(X, y)
    X_all = np.concatenate([samples(runs[iv], r)[0] for iv in intervals for r in names])
    y_all = np.concatenate([samples(runs[iv], r)[1] for iv in intervals for r in names])
    w_all = fit_logistic(X_all, y_all)

    def stated_pct(g, which="features"):  # the percentage a grid moment would show, from weights that never saw its room
        return 100 * float(predict(weights[g["room"]], np.array([g[which][k] for k in FEATURES], float)))

    def stamp(runs_by_room):
        for room in names:
            for i, (_, grid) in enumerate(runs_by_room[room]):
                for g in grid:
                    g["stream"] = (id(runs_by_room), room, i)
                    g["pct"] = stated_pct(g) if g["has_pose"] else 0.0
                    g["hold_pct"] = stated_pct(g, "hold") if g["has_pose"] else 0.0

    for iv in intervals:
        stamp(runs[iv])

    def arrow(limit, off=None):
        """The display rule of track.current, with weights that never saw the room: on at `limit`, off
        below `off`. It has a memory, so it must be asked about the moments of a stream in order."""
        off = limit * track.HIDE_PCT / track.SHOW_PCT if off is None else off
        memory = {"stream": None, "on": False}

        def shown(g):
            if memory["stream"] != g["stream"]:
                memory.update(stream=g["stream"], on=False)
            memory["on"] = g["state"] == "steady" and g["hold_pct"] >= (off if memory["on"] else limit)
            return memory["on"]
        return shown

    def arrow_by_moment(limit):  # the rule that was tried first: the percentage of this very moment
        return lambda g: g["state"] == "steady" and g["pct"] >= limit

    L += ["", "### Is the percentage honest? (fitted on two rooms, tested on the third)", ""]
    P, Y, Ys = [], [], []
    per_room = []
    for test in names:
        Xt = np.concatenate([samples(runs[iv], test)[0] for iv in intervals])
        yt = np.concatenate([samples(runs[iv], test)[1] for iv in intervals])
        ys = np.concatenate([samples(runs[iv], test)[2] for iv in intervals])
        p = predict(weights[test], Xt)
        P.append(p), Y.append(yt), Ys.append(ys)
        per_room.append((test, len(p), 100 * p.mean(), 100 * yt.mean(), reliability(p, yt)[1], float(np.mean((p - yt) ** 2))))
    P, Y, Ys = np.concatenate(P), np.concatenate(Y), np.concatenate(Ys)
    rows, gap = reliability(P, Y)
    L += ["| Stated confidence | Moments | Mean stated | Share right (0.3 m, 15 deg) | Share right (0.2 m, 10 deg) |", "| --- | --- | --- | --- | --- |"]
    for (label, n, said, was), (low, high) in zip(rows, [b for b in BINS if ((P * 100 >= b[0]) & (P * 100 < b[1])).any()]):
        inside = (P * 100 >= low) & (P * 100 < high)
        L.append(f"| {label} | {n} | {said:.0f}% | {was:.0f}% | {100 * Ys[inside].mean():.0f}% |")
    L += ["", f"Overall gap between stated and measured (expected calibration error, all three test rooms together): **{gap:.1f} points**. "
          f"Mean squared error (Brier): {np.mean((P - Y) ** 2):.3f}; always saying the average ({100 * Y.mean():.0f}%) would score {np.mean((Y.mean() - Y) ** 2):.3f}.", "",
          "| Test room (fitted on the other two) | Moments | Mean stated | Share right | Gap, points | Brier |", "| --- | --- | --- | --- | --- | --- |"]
    for test, n, said, was, g, brier in per_room:
        L.append(f"| {test} | {n} | {said:.0f}% | {was:.0f}% | {g:.1f} | {brier:.3f} |")

    # ---- one fix on its own
    L += ["", "### One fix on its own", ""]
    Pf, Yf, Yfs = [], [], []
    for test in names:
        train = [r for r in names if r != test]
        w = fit_logistic(np.concatenate([fix_samples(rooms, r)[0] for r in train]), np.concatenate([fix_samples(rooms, r)[1] for r in train]), l2=0.1)
        Xt, yt, ys = fix_samples(rooms, test)
        Pf.append(predict(w, Xt)), Yf.append(yt), Yfs.append(ys)
    Pf, Yf, Yfs = np.concatenate(Pf), np.concatenate(Yf), np.concatenate(Yfs)
    rows_f, gap_f = reliability(Pf, Yf)
    L += ["| Stated confidence | Fixes | Mean stated | Share right (0.3 m, 15 deg) | Share right (0.2 m, 10 deg) |", "| --- | --- | --- | --- | --- |"]
    for label, n, said, was in rows_f:
        low = int(label.split()[0])
        high = next(b[1] for b in BINS if b[0] == low)
        inside = (Pf * 100 >= low) & (Pf * 100 < high)
        L.append(f"| {label} | {n} | {said:.0f}% | {was:.0f}% | {100 * Yfs[inside].mean():.0f}% |")
    L += ["", f"Gap: **{gap_f:.1f} points** over {len(Pf)} fixes ({sum(len(d['wrong']) for d in rooms.values())} of them photos of another room)."]
    w_fix = fit_logistic(np.concatenate([fix_samples(rooms, r)[0] for r in names]), np.concatenate([fix_samples(rooms, r)[1] for r in names]), l2=0.1)

    # ---- before and after
    limit = track.SHOW_PCT
    L += ["", f"### Before and after (arrow shown at {limit}% and above)", "",
          "| One photo every | Display | Minutes replayed | Arrow shown, share of time | Of that time, right (0.3 m, 15 deg) | (0.2 m, 10 deg) | "
          "Right arrow, share of ALL time | Changes arrow / no arrow per minute |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for iv in intervals:
        every = [run for room in names for run in runs[iv][room]]
        a = display_stats(every, lambda g: g["raw_arrow"], "raw_err")
        b = display_stats(every, arrow(limit), "err")
        for label, s in (("today: the newest fix", a), ("steady answer", b)):
            L.append(f"| {iv} s | {label} | {s['minutes']:.0f} | {100 * s['arrow']:.0f}% | {100 * s['right']:.0f}% | {100 * s['strict']:.0f}% | "
                     f"{100 * s['right_of_all']:.0f}% | {s['flips']:.1f} |")

    L += ["", "The same per room:", "", "| Room | One photo every | Display | Arrow shown | Of that time, right | Changes per minute |", "| --- | --- | --- | --- | --- | --- |"]
    for room in names:
        for iv in intervals:
            a = display_stats(runs[iv][room], lambda g: g["raw_arrow"], "raw_err")
            b = display_stats(runs[iv][room], arrow(limit), "err")
            for label, s in (("today", a), ("steady", b)):
                L.append(f"| {room} | {iv} s | {label} | {100 * s['arrow']:.0f}% | {100 * s['right']:.0f}% | {s['flips']:.1f} |")

    L += ["", "### The limit for showing an arrow", "",
          "| One photo every | Rule | Limit | Arrow shown | Of that time, right | Right arrow, share of all time | Changes per minute |",
          "| --- | --- | --- | --- | --- | --- | --- |"]
    for iv in intervals:
        every = [run for room in names for run in runs[iv][room]]
        for label, rule in (("once per photo, on at the limit and off below half of it (what runs)", arrow),
                            ("once per photo, one limit", lambda lim: arrow(lim, lim)),
                            ("moment by moment (tried first, blinks)", arrow_by_moment)):
            for lim in args.limits:
                s = display_stats(every, rule(lim), "err")
                L.append(f"| {iv} s | {label} | {lim}% | {100 * s['arrow']:.0f}% | {100 * s['right']:.0f}% | {100 * s['right_of_all']:.0f}% | {s['flips']:.1f} |")

    L += ["", "### Error at the moment of a photo", "",
          "| One photo every | What | How many | Position error m: median / 90th / worst | Heading error deg: median / 90th / worst |", "| --- | --- | --- | --- | --- |"]
    for iv in intervals:
        rows_ = [r for room in names for at_fix, _ in runs[iv][room] for r in at_fix]
        raw_shown = [r["raw_err"] for r in rows_ if r["fix_state"] != "lost" and "raw_err" in r]
        raw_every = [r["raw_err"] for r in rows_ if "raw_err" in r]
        steady = [r["err"] for r in rows_ if r["state"] == "steady" and "err" in r]
        after_miss = [r["err"] for r in rows_ if r["state"] == "steady" and "err" in r and r["kind"] not in ("agree", "start", "jump")]
        for label, e in (("newest fix, when it shows an arrow (found or unsure)", raw_shown), ("newest fix, every one (also lost)", raw_every),
                         ("steady answer", steady), ("steady answer held through a miss", after_miss)):
            L.append(f"| {iv} s | {label} | {len(e)} | {three([x[0] for x in e])} | {three([x[1] for x in e], 1)} |")

    L += ["", "### Error between photos (the answer as it gets older)", "",
          "| One photo every | Display | Position error m: median / 90th / worst | Heading error deg: median / 90th / worst |", "| --- | --- | --- | --- |"]
    for iv in intervals:
        grid = [g for room in names for _, gr in runs[iv][room] for g in gr]
        a = [g["raw_err"] for g in grid if g["raw_arrow"]]
        rule = arrow(limit)
        b = [g["err"] for g in grid if rule(g)]
        L.append(f"| {iv} s | today, while an arrow is shown | {three([x[0] for x in a])} | {three([x[1] for x in a], 1)} |")
        L.append(f"| {iv} s | steady answer, while an arrow is shown | {three([x[0] for x in b])} | {three([x[1] for x in b], 1)} |")

    L += ["", "### Following real motion", "",
          "| One photo every | Found fixes after the wearer really moved (over 0.3 m or 15 deg since the photo before) | Answer on the new fix at once | "
          "Held back (waiting for a second fix) | Smoothing lag at those fixes: answer error minus fix error, m median / worst |", "| --- | --- | --- | --- | --- |"]
    for iv in intervals:
        rows_ = [r for room in names for at_fix, _ in runs[iv][room] for r in at_fix]
        moved = [r for r in rows_ if r["fix_state"] == "found" and "truth_moved" in r and not right(r["truth_moved"]) and "err" in r]
        at_once = [r for r in moved if r["kind"] in ("agree", "start", "jump")]
        held = [r for r in moved if r["kind"] == "elsewhere"]
        lag = [r["err"][0] - r["raw_err"][0] for r in at_once]
        L.append(f"| {iv} s | {len(moved)} | {len(at_once)} ({pct(len(at_once), len(moved))}) | {len(held)} | "
                 f"{np.median(lag) if lag else 0:.3f} / {np.max(lag) if lag else 0:.3f} |")
    held_all = [(iv, r) for iv in intervals for room in names for at_fix, _ in runs[iv][room] for r in at_fix if r["kind"] == "elsewhere"]
    L += ["", f"Found fixes held back in all replays: {len(held_all)}" + (": " + ", ".join(
        f"{iv} s {r['room']} fix error {r['raw_err'][0]:.2f} m / {r['raw_err'][1]:.0f} deg" for iv, r in held_all[:8]) if held_all else "") + "."]

    # ---- another room's photos in the stream
    L += ["", "### Photos of another room mixed in", "",
          "| One photo every | What is mixed in | Photos of another room | Moved the answer | Answer dropped right after one | Arrow shown | Of that time, right | Changes per minute |",
          "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for iv in intervals:
        for label, mix in (("nothing", None), ("every 4th photo", (4, 1, False)), ("3 in a row, every 9 photos", (9, 3, False)),
                           ("every 4th, wrongly called found (made up)", (4, 1, True)), ("3 in a row, wrongly called found (made up)", (9, 3, True))):
            mixed = replay_all(rooms, iv, mix=mix)
            foreign = [r for room in names for at_fix, _ in mixed[room] for r in at_fix if r["foreign"]]
            stamp(mixed)
            s = display_stats([run for room in names for run in mixed[room]], arrow(limit), "err")
            L.append(f"| {iv} s | {label} | {len(foreign)} | {sum(r['moved_pose'] for r in foreign)} | {sum(r['dropped'] for r in foreign)} | "
                     f"{100 * s['arrow']:.0f}% | {100 * s['right']:.0f}% | {s['flips']:.1f} |")

    print("\n".join(L))
    if args.fit:
        print("\nLeave-one-room-out weights:")
        for test in names:
            print(f"  without {test}: " + ", ".join(f"{k} {v:+.3f}" for k, v in zip(FEATURES, weights[test])))
        print("\nFor track.py (fitted on all three rooms):")
        print("CAL = {" + ", ".join(f'"{k}": {v:.3f}' for k, v in zip(FEATURES, w_all)) + "}")
        print("FIX_CAL = {" + f'"bias": {w_fix[0]:.3f}, "score": {w_fix[1]:.3f}' + "}")


if __name__ == "__main__":
    main()
