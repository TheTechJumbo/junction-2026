#!/usr/bin/env python3
"""Many quick fixes or one slow precise one? A replay of the phone-side fix along whole recordings.

TRACK_RESULTS.md found that a fix does not jump, it goes stale: the camera moves on while the
display still shows the old pose. This replays that for the phone side.

    --run VARIANT   (cluster, CPU) the phone-side fix on every 6th frame (5 per second) of every
                    recording the walking test took queries from, with the setting and limits that
                    were chosen WITHOUT the room in question (chosen.json of phone_side_tables.py).
                    Writes out/replay_VARIANT.jsonl and out/replay_truth.json.
    --tables        (anywhere, NumPy only) the display replayed: a fix is taken every T seconds, is
                    ready L seconds later, and the display holds the newest ready fix (an arrow
                    when it is "found") until the next. Every 0.1 s the held pose is compared with
                    where the camera truly is at that moment: right = within 0.3 m and 15 degrees.
                    Next to it a PERFECT slow fix (the true pose at the photo, always found) every
                    T seconds: the best any slow method could do.

    python phone_side_replay.py --run xfeat --chosen phone/out/chosen.json
    python phone_side_replay.py --tables --out DIR --rates rates.json

rates.json: {"VARIANT": seconds per frame on 4 cores} from phone_side_bench.py.
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
RIGHT = (0.3, 15.0)
STEP = 6  # every 6th frame of a 30 per second recording
GRID_S = 0.1


def run(args):
    import cv2

    import phone_side as ps
    import phone_side_eval as pe
    from locate_walk_eval import recording

    cv2.setNumThreads(1)
    plan = json.loads((Path(args.walk) / "plan.json").read_text())
    chosen = json.loads(Path(args.chosen).read_text())
    out = Path(args.work) / "out"
    truth, rows = {}, []
    for room, r in plan["rooms"].items():
        c = chosen[f"{args.run}/12/{room}"]
        setting = dict(c["setting"])
        m, (s, R, t), _ = pe.room_map(args.work, args.run, plan, room, 12, setting.pop("dim"))
        limits = ps.Limits(c["found"], c["unsure"], c["spread"])
        pick = pe.load_recon(args.work, room, 12)[4]
        base_t = np.array([r["baseline"][i]["t"] for i in pick])
        for rec in sorted({q["rec"] for q in r["queries"]}):
            frames = recording(Path(args.data) / rec)
            truth[rec] = {"room": room, "t": [f["t"] for f in frames], "C": [[float(v) for v in f["C"]] for f in frames],
                          "fwd": [[float(v) for v in f["R"][:, 2]] for f in frames]}
            for f in frames[::STEP]:
                feats = pe.load_features(pe.feature_file(args.work, args.run, f"{rec}/{f['rgb']}"))
                fix = ps.locate_frame(m, feats, limits=limits, **setting)
                row = {"variant": args.run, "room": room, "rec": rec, "t": f["t"], "state": fix["state"], "inliers": fix["inliers"],
                       "own_recording": rec == r["baseline_recording"],
                       "baseline_gap_s": float(np.abs(base_t - f["t"]).min()) if rec == r["baseline_recording"] else None}
                if fix["position"] is not None:
                    pos, fwd = s * R @ fix["position"] + t, R @ fix["forward"]
                    row.update(pos=[float(v) for v in pos], fwd=[float(v) for v in fwd], pos_err=float(np.linalg.norm(pos - f["C"])),
                               head_err=pe.angle(fwd, f["R"][:, 2]))
                rows.append(row)
            got = [x for x in rows if x["rec"] == rec]
            print(f"{args.run} {room} {rec}: {len(got)} frames, found {sum(x['state'] == 'found' for x in got)}", flush=True)
    (out / f"replay_{args.run}.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    (out / "replay_truth.json").write_text(json.dumps(truth))


def display(times, C, F, fixes, interval, latency, phase):
    """Replay one recording. fixes: time-sorted (t, found, pos, fwd) at the 5-per-second frames.
    Returns per grid moment: (arrow shown, right)."""
    start, end = fixes[0][0], fixes[-1][0]
    taken = []
    k = 0
    while start + phase + k * interval <= end:
        taken.append(min(fixes, key=lambda x: abs(x[0] - (start + phase + k * interval))))
        k += 1
    out = []
    for now in np.arange(start + phase, end, GRID_S):
        ready = [x for x in taken if x[0] + latency <= now]
        if not ready:
            continue  # nothing to show before the first fix is ready: not counted
        _, found, pos, fwd = ready[-1]
        if not found:
            out.append((False, False))
            continue
        i = int(np.argmin(np.abs(times - now)))
        angle = math.degrees(math.acos(float(np.clip(np.dot(fwd, F[i]) / (np.linalg.norm(fwd) + 1e-12), -1, 1))))
        out.append((True, bool(np.linalg.norm(np.asarray(pos) - C[i]) <= RIGHT[0] and angle <= RIGHT[1])))
    return out


def tables(args):
    out = Path(args.out)
    truth = json.loads((out / "replay_truth.json").read_text())
    rates = json.loads(Path(args.rates).read_text()) if args.rates else {}
    L = []
    T = {rec: (np.array(v["t"]), np.array(v["C"]), np.array(v["fwd"]), v["room"]) for rec, v in truth.items()}

    def stats(fixes_by_rec, interval, latency, recs):
        got = []
        for rec in recs:
            times, C, F, _ = T[rec]
            fixes = fixes_by_rec[rec]
            for phase in np.arange(0.0, interval - 1e-9, max(0.2, interval / 10)):
                got += display(times, C, F, fixes, interval, latency, float(phase))
        shown = [g for g in got if g[0]]
        return len(shown) / max(len(got), 1), sum(g[1] for g in shown) / max(len(shown), 1), sum(g[1] for g in shown) / max(len(got), 1)

    perfect = {rec: [(t, True, c, f) for t, c, f in zip(times[::STEP], C[::STEP], F[::STEP])] for rec, (times, C, F, _) in T.items()}
    variants = {}
    for path in sorted(out.glob("replay_*.jsonl")):
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        by = defaultdict(list)
        for r in rows:
            by[r["rec"]].append((r["t"], r["state"] == "found", r.get("pos"), r.get("fwd")))
        variants[path.stem[len("replay_"):]] = ({k: sorted(v, key=lambda x: x[0]) for k, v in by.items()}, rows)
    own = {rec for v in variants.values() for r in v[1] if r["own_recording"] for rec in [r["rec"]]}
    groups = [("other recordings of the room (fr1_office, fr2_desk)", sorted(set(T) - own)), ("the recording the baseline photos came from (all three rooms)", sorted(own))]

    L += ["### I. Every fifth of a second along whole recordings (held-out settings, 12 baseline photos)", "",
          "| Variant | Recordings | Frames | Found | Position error of found fixes m: median / 90th / worst | Heading error deg | Found but off by more than 0.3 m or 15 deg |",
          "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, (_, rows) in variants.items():
        for label, recs in groups:
            got = [r for r in rows if r["rec"] in recs]
            found = [r for r in got if r["state"] == "found"]
            q = lambda key, d: " / ".join(f"{f([r[key] for r in found]):.{d}f}" for f in (np.median, lambda x: np.percentile(x, 90), np.max)) if found else "-"  # noqa: E731
            L.append(f"| {name} | {label} | {len(got)} | {len(found)} ({100 * len(found) / max(len(got), 1):.0f}%) | {q('pos_err', 2)} | {q('head_err', 1)} | "
                     f"{sum(r['pos_err'] > RIGHT[0] or r['head_err'] > RIGHT[1] for r in found)} |")

    L += ["", "### J. The display between fixes", "",
          "| Fixes | One fix every | Ready after | Recordings | Arrow shown, share of time | Of that time, right (0.3 m, 15 deg) | Shown and right, share of all time |",
          "| --- | --- | --- | --- | --- | --- | --- |"]

    def add(label, fixes, interval, latency):
        for group, recs in groups:
            recs = [r for r in recs if r in fixes]
            if recs:
                shown, right, both = stats(fixes, interval, latency, recs)
                L.append(f"| {label} | {interval:g} s | {latency:g} s | {group} | {100 * shown:.0f}% | {100 * right:.0f}% | {100 * both:.0f}% |")

    for interval, latency in ((6.0, 0.0), (6.0, 4.0), (3.0, 0.0), (1.0, 0.0), (0.4, 0.0), (0.2, 0.0)):
        add("a perfect fix (true pose, always found)", perfect, interval, latency)
    for name, (fixes, _) in variants.items():
        frame_s = rates.get(name)
        tries = [(1.0, 0.0), (0.4, 0.0), (0.2, 0.0)]
        if frame_s:
            tries.append((max(0.2, math.ceil(frame_s / 0.2 - 1e-9) * 0.2), float(frame_s)))
        for interval, latency in tries:
            add(f"phone side, {name}" + (" (its measured time per frame on 4 cores)" if latency else ""), fixes, interval, latency)
    print("\n".join(L))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="variant")
    ap.add_argument("--tables", action="store_true")
    ap.add_argument("--chosen", default="phone/out/chosen.json")
    ap.add_argument("--rates")
    ap.add_argument("--data", default="walk/data/tum")
    ap.add_argument("--walk", default="walk/out")
    ap.add_argument("--work", default="phone")
    ap.add_argument("--out", default="phone/out")
    args = ap.parse_args()
    if args.run:
        run(args)
    if args.tables:
        tables(args)


if __name__ == "__main__":
    main()
