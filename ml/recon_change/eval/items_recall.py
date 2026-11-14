#!/usr/bin/env python3
"""Ways to accept more item candidates, scored from saved boxes and saved second looks. No GPU:
it reads what eval/items_eval.py and eval/items_second_look.py wrote.

  python eval/items_recall.py --set <data>/set.json --context <data>/context.json \
         --raw out/eval/raw_tiles1.json --raw out/eval-indoor/raw_tiles1.json [--looks out/recall/second_look.json]

A rule says, per item: the phrases, the limit from which a box is accepted outright, and for a
weaker box (from `zoom_from` up) what a second look must say: the zoomed detector's score
(`zoom_limit`) and, if asked, the classifier's share for the item (`clip_limit`).

Every setting is chosen on the tuning half (test photos with an even id, and every second
item-free photo) and reported on the other half. Counting rules are those of items_eval.py
(a box is "on" a label by the looser rule there). "False" is an answer on an item-free photo.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import items  # noqa: E402
import items_eval as ev  # noqa: E402

NAMES = ev.NAMES
SIZES = [("under 32 px", 0, 32), ("32 to 64 px", 32, 64), ("over 64 px", 64, 1e9)]
GRID = [round(v, 3) for v in np.arange(0.2, 0.701, 0.025)]
# the classifier's texts that mean each item; all its other texts are look-alikes or scenery
CLIP_OF = {"keys": ["keys", "a bunch of keys", "a key", "a key ring"], "glasses": ["eyeglasses", "a pair of glasses", "spectacles"],
           "wallet": ["a wallet", "a leather wallet"], "phone": ["a mobile phone", "a smartphone", "a cell phone"],
           "remote control": ["a remote control", "a tv remote"]}


class Data:
    def __init__(self, args):
        self.records = json.loads(Path(args.set).read_text())
        self.context = json.loads(Path(args.context).read_text()) if args.context else {}
        self.raw = {}
        for path in args.raw:
            self.raw.update(json.loads(Path(path).read_text())["boxes"])
        self.arrays = {k: {p: (np.array(v)[:, :4], np.array(v)[:, 4]) for p, v in e["raw"].items()} for k, e in self.raw.items()}
        self.looks, self.texts, self.clip_scale = {}, [], {}
        if args.looks:
            saved = json.loads(Path(args.looks).read_text())
            self.looks, self.texts, self.clip_scale = saved["photos"], saved["texts"], saved.get("clip_scale", {})
        clean = sorted(k for k in self.raw if not k.startswith("lvis:"))
        self.clean = {"tune": clean[0::2], "report": clean[1::2], None: clean}

    def half(self, half):
        return [r for r in self.records if half in (None, r["half"])]


def rule(phrases="chosen", limit=0.5, zoom_from=None, zoom_limit=None, zoom_phrases=None, clip_limit=None, per_name=2):
    def per_item(value):
        return dict(value) if isinstance(value, dict) else {n: value for n in NAMES}
    return {"phrases": ev.PHRASES[phrases] if isinstance(phrases, str) else phrases, "limit": per_item(limit),
            "zoom_from": per_item(zoom_from), "zoom_limit": per_item(zoom_limit), "clip_limit": per_item(clip_limit),
            "zoom_phrases": zoom_phrases or (ev.PHRASES[phrases] if isinstance(phrases, str) else phrases), "per_name": per_name}


def zoom_score(look, name, phrases):
    """What the zoomed detector says about a candidate: its best box of the item's phrases that is on the candidate."""
    best = 0.0
    for phrase in phrases[name]:
        for *box, score in look.get("zoom", {}).get(phrase, []):
            if score > best and ev.on(box, look["box"])[1][0]:
                best = score
    return best


def clip_share(D, look, name, model=None):
    """The classifier's share for the item among all its texts (softmax), averaged over the two crops."""
    if not look.get("clip"):
        return None
    model = model or next(iter(look["clip"]))
    sims = np.array(look["clip"][model]) * D.clip_scale.get(model, 100.0)
    share = np.exp(sims - sims.max(1, keepdims=True))
    share /= share.sum(1, keepdims=True)
    mine = [D.texts.index(t) for t in CLIP_OF[name]]
    return float(share[:, mine].sum(1).mean())


def decide(D, key, r, why=None):
    """The detections a rule accepts in one photo, best first, one name per thing."""
    raw, got, todo = D.arrays[key], [], []
    looks = D.looks.get(key, {}).get("candidates", [])
    for name in NAMES:
        limit, start = r["limit"][name], r["zoom_from"][name]
        low = limit if start is None else min(limit, start)
        weak = 0
        for d in items.merge(raw, [name], low, phrases=lambda n: r["phrases"][n]):
            if d["score"] >= limit:
                got.append(d)
            elif weak < r["per_name"]:  # second looks are capped per item
                weak += 1
                todo.append(d)
    sure = list(got)  # a thing that already has a name gets no second look under another
    todo = [d for d in todo if not any(k["name"] != d["name"] and items.iou(np.array(d["box"]), np.array(k["box"]))[0][0] >= 0.6 for k in sure)]
    for d in sorted(todo, key=lambda d: -d["score"])[:r.get("most", 6)]:
        name = d["name"]
        if True:
            look = next((c for c in looks if c["name"] == name and items.iou(np.array(d["box"]), np.array(c["box"]))[0][0] >= 0.7), None)
            if look is None:
                continue
            seen = zoom_score(look, name, r["zoom_phrases"])
            if r["zoom_limit"][name] is not None and seen < r["zoom_limit"][name]:
                continue
            if r["clip_limit"][name] is not None and (clip_share(D, look, name) or 0) < r["clip_limit"][name]:
                continue
            got.append({**d, "score": round(max(seen, d["score"]), 3), "second_look": True})
    got.sort(key=lambda d: -d["score"])
    kept = []
    for d in got:
        if not any(k["name"] != d["name"] and items.iou(np.array(d["box"]), np.array(k["box"]))[0][0] >= 0.6 for k in kept):
            kept.append(d)
    return kept


def held(context, box_xywh):
    """Is a labelled box mostly inside a person's box (held, worn, in a pocket)? And is anybody in the photo?"""
    x, y, w, h = box_xywh
    people = [c for c in context if c[0] == "person"]
    inside = 0.0
    for _, px, py, pw, ph in people:
        inter = max(0, min(x + w, px + pw) - max(x, px)) * max(0, min(y + h, py + ph) - max(y, py))
        inside = max(inside, inter / max(w * h, 1e-6))
    return inside >= 0.3, bool(people)


def measure(D, r, half, names=NAMES):
    """Counts for a rule on one half. found[name][size] = [found, there]; lying = the same for labelled
    objects over 64 px that nobody holds (and in photos with nobody at all); answers and false answers."""
    m = {n: {"found": {s[0]: [0, 0] for s in SIZES}, "lying": [0, 0], "nobody": [0, 0], "right": 0, "wrong_absent": 0, "absent": 0,
             "wrong_other": 0, "silent": 0, "false": 0, "lying_photos": set(), "nobody_photos": set()} for n in names}
    for rec in D.half(half):
        key = f"lvis:{rec['file']}"
        if key not in D.arrays:
            continue
        W, H = rec["width"], rec["height"]
        got = decide(D, key, r)
        context = D.context.get(str(rec["id"]), [])
        for name, label in rec["items"].items():
            if name not in names:
                continue
            mine = [d for d in got if d["name"] == name]
            c = m[name]
            if not label["there"]:
                c["absent"] += 1
                c["wrong_absent"] += bool(mine)
                continue
            truth = np.array([[x / W, y / H, (x + w) / W, (y + h) / H] for x, y, w, h in label["boxes"]])
            for box, xywh in zip(truth, label["boxes"]):
                size = max(xywh[2], xywh[3]) * (960 / max(W, H))
                band = next(s[0] for s in SIZES if s[1] <= size < s[2])
                hit = int(any(ev.on(d["box"], box)[1][0] for d in mine))
                c["found"][band][0] += hit
                c["found"][band][1] += 1
                if size >= 64 and D.context:
                    in_hand, anybody = held(context, xywh)
                    if not in_hand:
                        c["lying"][0] += hit
                        c["lying"][1] += 1
                        c["lying_photos"].add(rec["id"])
                    if not anybody:
                        c["nobody"][0] += hit
                        c["nobody"][1] += 1
                        c["nobody_photos"].add(rec["id"])
            if not mine:
                c["silent"] += 1
            elif ev.on(mine[0]["box"], truth)[1].any():
                c["right"] += 1
            elif label["all_boxed"]:
                c["wrong_other"] += 1
    for key in D.clean[half]:
        for d in items.best_per_name(decide(D, key, r)):
            if d["name"] in names:
                m[d["name"]]["false"] += 1
    return m


def total(m, what, band=None):
    if what == "found":
        return [sum(m[n]["found"][band][i] for n in m) for i in (0, 1)]
    if what in ("lying", "nobody"):
        return [sum(m[n][what][i] for n in m) for i in (0, 1)]
    return sum(m[n][what] for n in m)


def share(pair):
    return f"{pair[0]} of {pair[1]}" + (f" ({100 * pair[0] / pair[1]:.0f} %)" if pair[1] else "")


def table(D, m, half):
    lines = ["| item | found, under 32 px | found, 32 to 64 px | found, over 64 px | right answers | wrong: item not there | wrong: on something else | false on item-free photos |",
             "|---|---|---|---|---|---|---|---|"]
    n_clean = len(D.clean[half])
    for n in list(m) + ["all"]:
        one = {n: m[n]} if n != "all" else m
        lines.append(f"| {n if n != 'all' else '**all**'} | " + " | ".join(share(total(one, "found", s[0])) for s in SIZES)
                     + f" | {total(one, 'right')} | {total(one, 'wrong_absent')} of {total(one, 'absent')} | {total(one, 'wrong_other')} | {total(one, 'false')} of {n_clean} |")
    return "\n".join(lines)


def lying_table(m):
    lines = ["| item | over 64 px, nobody holds it | photos | over 64 px, nobody in the photo | photos |", "|---|---|---|---|---|"]
    for n in list(m) + ["all"]:
        one = {n: m[n]} if n != "all" else m
        lines.append(f"| {n if n != 'all' else '**all**'} | {share(total(one, 'lying'))} | {len(set().union(*[one[k]['lying_photos'] for k in one]))} "
                     f"| {share(total(one, 'nobody'))} | {len(set().union(*[one[k]['nobody_photos'] for k in one]))} |")
    return "\n".join(lines)


def errors(m, name):
    return m[name]["wrong_absent"] + m[name]["wrong_other"] + m[name]["false"]


ALLOWED = 0  # errors an item may make on the tuning half at its chosen limit (--allowed)


def lowest_clean(D, make, name, grid=GRID, margin=0.0, allowed=None):
    """The lowest value on the grid from which a rule makes no error for an item on the tuning half
    (wrong answers on test photos plus false answers on item-free photos), also at every higher value."""
    best, allowed = None, ALLOWED if allowed is None else allowed
    for value in sorted(grid, reverse=True):
        if errors(measure(D, make(value), "tune", [name]), name) > allowed:
            break
        best = value
    return None if best is None else round(best + margin, 3)


def line(D, label, r, half):
    m = measure(D, r, half)
    big = total(m, "found", "over 64 px")
    keys = m["keys"]["found"]["over 64 px"]
    return (f"| {label} | {share(total(m, 'found', 'under 32 px'))} | {share(total(m, 'found', '32 to 64 px'))} | {share(big)} | {share(keys)} "
            f"| {total(m, 'right')} | {total(m, 'wrong_absent') + total(m, 'wrong_other')} | {total(m, 'false')} of {len(D.clean[half])} |")


def evidence(D, args):
    """What adding up weak sightings over several looks could buy, from the saved boxes.
    1. For every labelled object: the best score of a box on it. "Strong" is accepted today, "weak"
       (from `zoom_from` up to 0.5) is what voting could add, "unseen" is below that.
    2. In the item-free indoor scenes, where many photos show one room: in how many photos of a
       scene a false candidate of each name turns up. A look-alike that is there in many views
       would pass any vote."""
    low = args.zoom_from
    print(f"### best score on each labelled object (both halves), weak = {low} to 0.5\n")
    print("| item | size | objects | strong (0.5 and up) | weak | unseen |\n|---|---|---|---|---|---|")
    for name in NAMES:
        for band, lo, hi in SIZES:
            best = []
            for rec in D.records:
                label = rec["items"].get(name)
                raw = D.arrays.get(f"lvis:{rec['file']}")
                if not label or not label["there"] or raw is None:
                    continue
                W, H = rec["width"], rec["height"]
                got = items.merge(raw, [name], 0.05)
                for x, y, w, h in label["boxes"]:
                    if lo <= max(w, h) * (960 / max(W, H)) < hi:
                        box = [x / W, y / H, (x + w) / W, (y + h) / H]
                        best.append(max([d["score"] for d in got if ev.on(d["box"], box)[1][0]], default=0.0))
            best = np.array(best)
            if len(best):
                print(f"| {name} | {band} | {len(best)} | {(best >= 0.5).sum()} ({100 * (best >= 0.5).mean():.0f} %) "
                      f"| {((best >= low) & (best < 0.5)).sum()} ({100 * ((best >= low) & (best < 0.5)).mean():.0f} %) "
                      f"| {(best < low).sum()} ({100 * (best < low).mean():.0f} %) |")
    print(f"\n### false candidates from {low} up in the item-free photos, by room\n")
    print("| photos of | photos | " + " | ".join(f"with a false '{n}'" for n in NAMES) + " | with any |\n|---|---|" + "---|" * (len(NAMES) + 1))
    rooms = {}
    for key in D.clean[None]:
        group, file = key.split(":", 1)
        room = Path(file).parts[-4] if "PASLCD" in file else group
        got = {d["name"] for d in items.best_per_name(decide(D, key, rule(args.phrases, low)))}
        rooms.setdefault(room, []).append(got)
    for room, seen in sorted(rooms.items()):
        print(f"| {room} | {len(seen)} | " + " | ".join(str(sum(n in g for g in seen)) for n in NAMES) + f" | {sum(bool(g) for g in seen)} |")


BANDS = [0.25, 0.35, 0.5, 0.6, 1.01]  # score bands for the stated confidence
JUDGED = {"keys": 0.7, "glasses": 0.1, "wallet": 0.1, "phone": 0.35, "remote control": 0.5}  # the judge limits that are recommended


def outcomes(D, half):
    """Every answer from 0.25 up (the best box per item and photo) whose truth is known:
    (item, score, what the judge said or None, right?)."""
    out = []
    low = rule("chosen", BANDS[0])
    for rec in D.half(half):
        key = f"lvis:{rec['file']}"
        if key not in D.arrays:
            continue
        W, H = rec["width"], rec["height"]
        for d in items.best_per_name(decide(D, key, low)):
            label = rec["items"].get(d["name"])
            if label is None:
                continue
            if label["there"]:
                truth = np.array([[x / W, y / H, (x + w) / W, (y + h) / H] for x, y, w, h in label["boxes"]])
                right = bool(ev.on(d["box"], truth)[1].any())
                if not right and not label["all_boxed"]:
                    continue
            else:
                right = False
            out.append((key, d, right))
    for key in D.clean[half]:
        out += [(key, d, False) for d in items.best_per_name(decide(D, key, low))]
    rows = []
    for key, d, right in out:
        look = next((c for c in D.looks.get(key, {}).get("candidates", [])
                     if c["name"] == d["name"] and items.iou(np.array(d["box"]), np.array(c["box"]))[0][0] >= 0.7), None)
        share = clip_share(D, look, d["name"]) if look else None
        rows.append((d["name"], d["score"], None if share is None else share >= JUDGED[d["name"]], right))
    return rows


def confidence(D, args):
    """The stated confidence: of the answers with one kind of evidence (item, score band, what the judge
    said), the share that were right on the tuning half, pulled towards the share of all items with
    that evidence when an item has few (as if 4 more answers at the pooled rate had been seen).
    Checked against the held-out half."""
    tune, test = outcomes(D, "tune"), outcomes(D, "report")

    def band(score):
        return max(i for i, edge in enumerate(BANDS[:-1]) if score >= edge)

    def count(rows, name, b, judged):
        mine = [r for r in rows if (name is None or r[0] == name) and band(r[1]) == b and (judged == "any" or r[2] is judged)]
        return sum(r[3] for r in mine), len(mine)

    table, lines = {}, []
    for judged in ("any", True, False):
        for b in range(len(BANDS) - 1):
            pool_right, pool_n = count(tune, None, b, judged)
            pooled = (pool_right + 1) / (pool_n + 2)
            for name in NAMES + [None]:
                right, n = count(tune, name, b, judged)
                stated = int(round(100 * ((right + 4 * pooled) / (n + 4) if name else pooled)))
                table.setdefault(name or "other", {}).setdefault({"any": "any", True: "agreed", False: "disagreed"}[judged], []).append(stated)
                seen_right, seen_n = count(test, name, b, judged)
                lines.append(f"| {name or 'all items'} | {BANDS[b]} to {min(BANDS[b + 1], 1.0)} | {dict(any='not asked or either', **{'True': 'agreed', 'False': 'disagreed'})[str(judged)]} "
                             f"| {right} of {n} | {stated} % | {seen_right} of {seen_n}" + (f" ({100 * seen_right / seen_n:.0f} %)" if seen_n else "") + " |")
    print("| item | score | the judge | right on the tuning half | stated confidence | right on the held-out half |\n|---|---|---|---|---|---|")
    print("\n".join(lines))
    # one number for how well the stated confidence holds: answers on the held-out half, grouped by what was stated
    print("\n| stated confidence | answers on the held-out half | right | measured |\n|---|---|---|---|")
    groups = {}
    for name, score, judged, right in test:
        stated = table[name]["any" if judged is None else "agreed" if judged else "disagreed"][band(score)]
        g = groups.setdefault(min(stated // 20 * 20, 80), [0, 0])
        g[0] += right
        g[1] += 1
    for low in sorted(groups):
        right, n = groups[low]
        print(f"| {low} to {low + 20} % | {n} | {right} | {100 * right / n:.0f} % |")
    print("\nCONFIDENCE =", json.dumps({"bands": BANDS[:-1], "of": table}))


HEAD = ("| rule | found under 32 px | found 32 to 64 px | found over 64 px | keys over 64 px | right answers | wrong answers | false on item-free photos |\n"
        "|---|---|---|---|---|---|---|---|")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--context")
    ap.add_argument("--raw", action="append", required=True)
    ap.add_argument("--looks")
    ap.add_argument("--margin", type=float, default=0.025, help="added to every limit found on the tuning half")
    ap.add_argument("--phrases", default="chosen")
    ap.add_argument("--zoom-from", type=float, default=0.25)
    ap.add_argument("--rule", help="JSON: limit, zoom_from, zoom_limit, clip_limit per item, instead of choosing them")
    ap.add_argument("--allowed", type=int, default=0, help="errors each item may make on the tuning half at its limit")
    ap.add_argument("--evidence", action="store_true", help="only: how strongly present items score, and how false candidates repeat in a room")
    ap.add_argument("--short", action="store_true", help="only the comparison lines, not the tables per item")
    ap.add_argument("--confidence", action="store_true", help="only: the stated confidence per kind of evidence, and how it holds")
    args = ap.parse_args()
    global ALLOWED
    ALLOWED = args.allowed
    more = __import__("items_second_look").MORE  # wording tests: every phrase, or every phrase for keys only
    ev.PHRASES.update({"more": more, "keys-more": {**ev.PHRASES["chosen"], "keys": more["keys"]}})
    D = Data(args)
    if args.evidence:
        return evidence(D, args)
    if args.confidence:
        return confidence(D, args)
    print(f"{len(D.records)} test photos, {len(D.clean[None])} item-free photos ({len(D.clean['tune'])} tuning, {len(D.clean['report'])} held out), "
          f"{sum(len(v['candidates']) for v in D.looks.values())} saved second looks\n")
    rules = {"today: limit 0.5 for all": rule(args.phrases, 0.5)}

    # 1. a limit per item
    limits = {n: lowest_clean(D, lambda v, n=n: rule(args.phrases, {**{k: 0.5 for k in NAMES}, n: v}), n, margin=args.margin) or 0.5 for n in NAMES}
    limits = {n: min(v, 0.5) for n, v in limits.items()}  # never stricter than today
    print("limit per item, chosen on the tuning half:", limits)
    rules["a limit per item"] = rule(args.phrases, limits)

    if D.looks:
        # 2. second look with the same detector, for candidates below the item's limit
        zoom = {n: lowest_clean(D, lambda v, n=n: rule(args.phrases, limits, args.zoom_from, {**{k: 9 for k in NAMES}, n: v}), n, margin=args.margin) or 9
                for n in NAMES}
        print("zoomed-score limit per item:", zoom)
        rules["+ second look, same detector"] = rule(args.phrases, limits, args.zoom_from, zoom)
        wide = {n: lowest_clean(D, lambda v, n=n: rule(args.phrases, limits, args.zoom_from, {**{k: 9 for k in NAMES}, n: v},
                                                       zoom_phrases=__import__("items_second_look").MORE), n, margin=args.margin) or 9 for n in NAMES}
        print("zoomed-score limit per item, more phrases in the second look:", wide)
        rules["+ second look, more phrases"] = rule(args.phrases, limits, args.zoom_from, wide, zoom_phrases=__import__("items_second_look").MORE)
        # 3. the classifier instead of, and on top of, the zoomed detector
        shares = [round(v, 2) for v in np.arange(0.05, 0.96, 0.05)]
        clip = {n: lowest_clean(D, lambda v, n=n: rule(args.phrases, limits, args.zoom_from, None, clip_limit={**{k: 9 for k in NAMES}, n: v}), n,
                                grid=shares, margin=0.05) or 9 for n in NAMES}
        print("classifier share per item, classifier alone:", clip)
        rules["+ second look, classifier"] = rule(args.phrases, limits, args.zoom_from, None, clip_limit=clip)
        both = {}
        for n in NAMES:  # the zoomed limit that is clean once the classifier must also say at least 0.3
            both[n] = lowest_clean(D, lambda v, n=n: rule(args.phrases, limits, args.zoom_from, {**{k: 9 for k in NAMES}, n: v},
                                                          clip_limit={**{k: 9 for k in NAMES}, n: 0.3}), n, margin=args.margin) or 9
        print("zoomed-score limit per item when the classifier must also give 0.3:", both)
        rules["+ second look, detector and classifier"] = rule(args.phrases, limits, args.zoom_from, both, clip_limit=0.3)
    if args.rule:
        given = json.loads(args.rule)
        rules["the given rule"] = rule(given.get("phrases", args.phrases), given.get("limit", 0.5), given.get("zoom_from"), given.get("zoom_limit"),
                                       clip_limit=given.get("clip_limit"))

    for half in ("tune", "report"):
        print(f"\n### {'tuning' if half == 'tune' else 'held-out'} half\n\n{HEAD}")
        for label, r in rules.items():
            print(line(D, label, r, half))
    for label, r in ({} if args.short else rules).items():
        m = measure(D, r, "report")
        print(f"\n### held-out half: {label}\n\n{table(D, m, 'report')}")
        if D.context:
            print("\n" + lying_table(measure(D, r, None)) + "\n(both halves)")
    print("\nrules:", json.dumps({k: {x: v[x] for x in ("limit", "zoom_from", "zoom_limit", "clip_limit")} for k, v in rules.items()}))


if __name__ == "__main__":
    main()
