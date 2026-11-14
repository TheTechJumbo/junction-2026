#!/usr/bin/env python3
"""The new state rule of locate.py on PASLCD (found: two baseline frames agree; a view that only
passes when one baseline frame is enough is unsure instead of lost). No GPU.

Two ways in:

    --two DIR --one DIR   results.jsonl of locate_eval.py --from-dump run with need=2 and with
                          need=1 (the walking test saved both): the rule is applied to the pair
    --v2 DIR              results.jsonl of locate_eval.py --from-dump run with version 2 of
                          locate.py, which applies the rule itself (rows carry match_one_frame)

    python locate_v2_paslcd.py --two walk/paslcd/out_need2 --one walk/paslcd/out_need1
"""
import argparse
import json
from collections import Counter
from pathlib import Path

FOUND, UNSURE = 0.5, 0.3


def rows_of(folder):
    rows = [json.loads(line) for line in (Path(folder) / "results.jsonl").read_text().splitlines() if line.strip()]
    return {(r["kind"], r["n_baseline"], r["scene"], r["query_scene"], r["query"], r["round"]): r for r in rows if "error" not in r}


def state(two, one):
    return "found" if two >= FOUND else "unsure" if two >= UNSURE or one >= FOUND else "lost"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--two")
    ap.add_argument("--one")
    ap.add_argument("--v2")
    args = ap.parse_args()
    if args.v2:
        got = rows_of(args.v2)
        pairs = {k: (r["match"], r["match_one_frame"] or 0.0, r["state"], r) for k, r in got.items()}
    else:
        two, one = rows_of(args.two), rows_of(args.one)
        pairs = {k: (two[k]["match"], one[k]["match"], None, two[k]) for k in two if k in one}
    print(f"{len(pairs)} passes\n")
    print("| Photos | Baseline photos | Passes | Found before | Found after | Unsure before | Unsure after | Lost before | Lost after | "
          "Highest two-frame score | Highest one-frame score |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for kind, label in (("same", "same room, same light"), ("same_other_light", "same room, other light"), ("wrong", "wrong room")):
        for n in (8, 12, 24, None):
            g = [v for k, v in pairs.items() if k[0] == kind and (n is None or k[1] == n)]
            if not g:
                continue
            before = Counter("found" if t >= FOUND else "unsure" if t >= UNSURE else "lost" for t, _, _, _ in g)
            after = Counter(state(t, o) for t, o, _, _ in g)
            same = sum(s is None or s == state(t, o) for t, o, s, _ in g)
            print(f"| {label} | {n or 'all'} | {len(g)} | {before['found']} | {after['found']} | {before['unsure']} | {after['unsure']} | "
                  f"{before['lost']} | {after['lost']} | {max(t for t, _, _, _ in g):.2f} | {max(o for _, o, _, _ in g):.2f} |"
                  + ("" if same == len(g) else f" locate() disagrees with the rule in {len(g) - same}"))
    rescued = [(k, v) for k, v in pairs.items() if v[0] < UNSURE and v[1] >= FOUND]
    print(f"\nLost before, unsure after: {len(rescued)}. " + "; ".join(
        f"{k[0]} n={k[1]} {k[2]} <- {k[3]}/{k[4]}: two frames {v[0]:.2f}, one frame {v[1]:.2f}"
        + (f", position error {100 * v[3]['pose']['position_error_share']:.1f}% of the scene, heading {v[3]['pose']['heading_error_deg']:.1f} deg"
           if v[3].get("pose") else "") for k, v in rescued))
    if args.v2:
        rows = [v[3] for v in pairs.values()]
        up = Counter(r.get("up_from") for r in rows)
        print(f"\nUp taken from a surface in {up['surface']} of {len(rows)} passes, from the cameras in {up['cameras']}. "
              f"locate() seconds, median: {sorted(r['locate_s'] for r in rows)[len(rows) // 2]:.2f}.")
        hints = [r for r in rows if r.get("hint")]
        print(f"Hints: {len(hints)} ({Counter(r['kind'] for r in hints).most_common()}).")


if __name__ == "__main__":
    main()
