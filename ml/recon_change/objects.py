#!/usr/bin/env python3
"""The object map: what is in the room, where it is, and what was done with it.

Two things the wearer and a carer ask for:

1. "Where is my cup?" for the things in the room, without finding everything again in every
   picture. Things are labelled once (inventory), the label stays on the object's place on the room
   map (locate.map_frame: east, north, up in rough metres), and every position fix only updates it.
2. A diary of what was done. A person with dementia who lives alone is the only one who changes
   things at home, so "the keys were moved", "the cup is gone" is a record of what they did. The
   events here are always worded as what was seen of an object, never as a claim about the person.

How it works
  inventory   the open-vocabulary detector (items.py) looks at every baseline photo for a household
              vocabulary. One box in one photo is weak. Every box is put on the room map with the
              depth of the baseline reconstruction, and boxes of the same name that land at the
              same place from several photos are fused into one object (fuse). Agreement across
              photos is what makes an object count; the rest is kept as "unconfirmed".
  update      one position fix: the detections of the newest frame, placed on the same map, refresh
              the objects they land on. An object that should have been seen clearly (in view, not
              blocked, big enough in the picture: the facts items.check computes) and was not, from
              several standpoints, becomes "missing". The same name confirmed somewhere else while
              its old place is empty is one event, a move, not a loss and a new object.
  where_is    match the asked word to a label (plural, other words for it, the names the detector
              gave it) and return the object. No language model.

Everything in the map is plain numbers, strings, lists and dicts: json.dumps(map) works, and this
file needs only the standard library unless a function says otherwise (lift, visibility, observe,
inventory and marks need numpy and items.py; they run in the worker, next to the reconstruction).

Every object and every event carries a confidence_pct (0 to 100) that settles: it rises as looks
from different standpoints agree, falls with clear looks that do not find the object and with time,
and one look alone never flips it (see "Confidence" below). An event is only reported once its
confidence reaches REPORT_PCT; until then it waits in the map as "unconfirmed".

The limits below were set on one PASLCD scene and measured on others: eval/OBJECTS_RESULTS.md.
"""
import copy
import math
import re
import time

# What the inventory asks the detector for: label -> the phrases it is asked in (as items.PHRASES).
# How it was chosen, before any test photo was looked at: (1) the five things the product already
# looks for, (2) what a carer asks about for a person with dementia (medicine, glasses, keys, wallet,
# phone, walking stick, handbag), (3) movable indoor things from the category lists the detector's
# training data uses (COCO and the frequent LVIS names), room by room. Left out on purpose: the
# building (walls, doors, windows, floors, shelves), people and pets, and outdoor things.
# Two words for one thing are one label with two phrases ("cup": cup, mug), so that the same cup is
# not two objects; where_is() knows the other words.
VOCABULARY = {
    # the product's five
    "keys": ["keys", "a key"],
    "glasses": ["eyeglasses", "spectacles"],
    "wallet": ["a wallet"],
    "phone": ["a mobile phone", "a cell phone"],
    "remote control": ["a remote control"],
    # health and going out
    "pill box": ["a pill box", "a pill organizer"],
    "medicine bottle": ["a medicine bottle", "a pill bottle"],
    "watch": ["a wristwatch"],
    "walking stick": ["a walking stick", "a cane"],
    "handbag": ["a handbag", "a purse"],
    "bag": ["a bag", "a tote bag"],
    "backpack": ["a backpack"],
    "umbrella": ["an umbrella"],
    "hat": ["a hat", "a cap"],
    "shoes": ["a shoe", "a pair of shoes"],
    "slippers": ["slippers"],
    "jacket": ["a jacket", "a coat"],
    # reading, writing, electronics
    "book": ["a book"],
    "newspaper": ["a newspaper", "a magazine"],
    "notebook": ["a notebook", "a notepad"],
    "paper": ["a sheet of paper"],
    "pen": ["a pen", "a pencil"],
    "laptop": ["a laptop"],
    "tablet": ["a tablet computer"],
    "keyboard": ["a computer keyboard"],
    "mouse": ["a computer mouse"],
    "monitor": ["a computer monitor"],
    "television": ["a television"],
    "headphones": ["headphones"],
    "charger": ["a phone charger", "a power adapter"],
    "speaker": ["a loudspeaker"],
    "printer": ["a printer"],
    # kitchen and table
    "cup": ["a cup", "a mug"],
    "glass": ["a drinking glass"],
    "bottle": ["a bottle", "a water bottle"],
    "plate": ["a plate"],
    "bowl": ["a bowl"],
    "cutlery": ["a fork", "a spoon", "a knife"],
    "kettle": ["a kettle"],
    "teapot": ["a teapot"],
    "toaster": ["a toaster"],
    "microwave": ["a microwave oven"],
    "pan": ["a frying pan", "a cooking pot"],
    "jar": ["a jar"],
    "can": ["a tin can", "a drink can"],
    "carton": ["a milk carton", "a juice carton"],
    "food container": ["a food container", "a lunch box"],
    "flask": ["a thermos flask"],
    "tray": ["a tray"],
    "cutting board": ["a cutting board"],
    "towel": ["a towel"],
    "soap dispenser": ["a soap dispenser"],
    "tissue box": ["a tissue box"],
    "paper roll": ["a paper towel roll", "a toilet paper roll"],
    "fruit": ["a banana", "an apple", "an orange"],
    # around the home
    "bin": ["a trash can", "a waste bin"],
    "plant": ["a potted plant", "a houseplant"],
    "vase": ["a vase"],
    "lamp": ["a lamp"],
    "clock": ["a clock"],
    "candle": ["a candle"],
    "picture frame": ["a picture frame"],
    "cushion": ["a cushion", "a pillow"],
    "blanket": ["a blanket"],
    "box": ["a cardboard box", "a box"],
    "basket": ["a basket"],
    "toy": ["a toy", "a stuffed animal"],
    "ball": ["a ball"],
    "fan": ["an electric fan"],
    "scissors": ["scissors"],
    "stapler": ["a stapler"],
    "tape": ["a roll of tape"],
    "broom": ["a broom", "a mop"],
    "bucket": ["a bucket"],
    "spray bottle": ["a spray bottle"],
    "toothbrush": ["a toothbrush"],
    "hairbrush": ["a hairbrush", "a comb"],
    # furniture that gets moved
    "chair": ["a chair"],
    "stool": ["a stool"],
    "sofa": ["a sofa", "an armchair"],
    "table": ["a table"],
}
# Other words people use for a label, for where_is(). The detector is not asked these.
OTHER_WORDS = {
    "glasses": ["spectacles", "specs", "eyeglasses", "reading glasses"],
    "phone": ["mobile", "mobile phone", "cell phone", "cellphone", "smartphone", "iphone", "telephone"],
    "remote control": ["remote", "tv remote", "clicker", "zapper"],
    "pill box": ["pills", "pillbox", "tablets", "medication", "medicine", "pill organizer"],
    "medicine bottle": ["pill bottle", "medicine", "medication"],
    "watch": ["wristwatch"],
    "walking stick": ["cane", "stick"],
    "handbag": ["purse", "hand bag"],
    "bag": ["tote", "shopping bag"],
    "backpack": ["rucksack"],
    "hat": ["cap"],
    "shoes": ["shoe", "trainers", "sneakers", "boots"],
    "jacket": ["coat"],
    "newspaper": ["paper", "magazine"],
    "notebook": ["notepad", "diary"],
    "pen": ["pencil", "biro"],
    "laptop": ["computer", "notebook computer"],
    "tablet": ["ipad"],
    "television": ["tv", "telly"],
    "headphones": ["earphones", "headset"],
    "speaker": ["loudspeaker", "radio"],
    "cup": ["mug", "teacup", "coffee cup"],
    "glass": ["drinking glass", "tumbler"],
    "bottle": ["water bottle"],
    "cutlery": ["fork", "spoon", "knife"],
    "pan": ["pot", "frying pan", "saucepan"],
    "can": ["tin"],
    "food container": ["lunch box", "lunchbox", "tupperware"],
    "flask": ["thermos"],
    "cutting board": ["chopping board"],
    "tissue box": ["tissues"],
    "paper roll": ["kitchen roll", "toilet roll", "toilet paper", "paper towel"],
    "fruit": ["banana", "apple", "orange"],
    "bin": ["trash can", "waste bin", "rubbish bin", "dustbin", "wastebasket", "garbage can", "trash"],
    "plant": ["houseplant", "flower", "flowers", "pot plant"],
    "picture frame": ["photo", "photo frame", "picture"],
    "cushion": ["pillow"],
    "toy": ["teddy", "stuffed animal"],
    "broom": ["mop"],
    "hairbrush": ["comb", "brush"],
    "sofa": ["couch", "settee", "armchair"],
}

# ----------------------------------------------------------------------------- limits
# Detector scores (OWLv2, as items.py gives them). items.py shows a sighting from 0.5 on; here many
# photos vote, so weaker boxes may take part (eval/OBJECTS_RESULTS.md says what that costs).
LIMIT_INVENTORY = 0.2  # a box in a baseline photo takes part in the inventory from this score on
LIMIT_CONFIRM = 0.2  # a box at a known object's place says "still there" from this score on
LIMIT_NEW = 0.3  # a box where nothing is known starts a possible new object from this score on
STRONG = 0.5  # the deployed limit of items.py: a box this strong is one full piece of evidence
# Same place? Two sightings are one thing when their centres are closer than
# max(SAME_M, SAME_SIZE x the larger of the two sizes). Positions from different passes of the
# 3D model differ by some tens of centimetres (measured: eval/OBJECTS_RESULTS.md).
SAME_M, SAME_SIZE = 0.3, 0.6
MOVED_M, MOVED_DEG = 0.3, 15  # a look counts as another one only from a different standpoint (as live/relay.py)
CLOSE_PX = 48.0  # the object's long side in the detector's picture from which a look counts as clear
BLEND = 0.25  # how far a new sighting pulls an object's stored position towards itself
NEAR_PLACE_M = 1.5  # a spot this close to a named place is described as "near" it (as live/relay.py)
COLOURS = {"present": (255, 90, 200), "moved": (255, 150, 40), "missing": (235, 60, 60), "unconfirmed": (170, 170, 170)}  # RGB

# Confidence. Every object carries a belief that it is really there: a sum of evidence, kept as
# log-odds ("belief"), shown as confidence_pct = 100 / (1 + exp(-belief)).
#   start      PRIOR: one box alone is probably nothing.
#   rises      each sighting from another photo or standpoint adds HIT x (score / STRONG), between
#              0.3 and 1.5 of HIT. In updates the second sighting in a row counts double: two looks
#              that agree are worth more than twice one look.
#   ceiling    agreement cannot make a weak name sure: a box the detector gives 0.3 in twenty photos
#              is the same doubtful name twenty times (seen on the tuning scene: pictures on a
#              television found as "laptop" in all 24 photos). So the belief never goes above what
#              the object's three best scores allow (CEILING: mean of the three best -> most it can be).
#   falls      a clear look without it (in view, not blocked, big enough) from another standpoint
#              takes MISS away, the second in a row twice MISS, from the third on three times: one
#              look never flips an object, three in a row do. A look where the depth still shows a
#              surface at the object's place counts SURFACE_STAYS of that (the detector missing a
#              thing that is there is the common case); one where the camera now sees clearly
#              farther counts SEES_FARTHER of it. When the watched-spot check (watch.py) has
#              looked at the place, its verdict is used instead of the depth (SPOT_SAME,
#              SPOT_CHANGED). In the inventory, each photo that had a clear look and no box takes
#              INVENTORY_MISS away.
#   time       the belief shrinks towards "do not know" (50 %) with a half-life of HALF_LIFE_S
#              without a look. Not measured: the test data has no clock.
#   bounded    between -BELIEF_MAX and BELIEF_MAX (5 % .. 95 %), so nothing is ever beyond recall.
# An object counts ("present") from SHOW_PCT on and is "missing" when it falls to GONE_PCT after at
# least two clear looks in a row without it. For a missing object confidence_pct is how sure the
# map is that it is NOT at its place. Events carry their own confidence_pct (see update) and are
# only reported from REPORT_PCT on; below that they wait in the map as "unconfirmed".
PRIOR, HIT, MISS, INVENTORY_MISS = -1.5, 1.2, 0.75, 0.1
CEILING = [(0.25, 0.30), (0.35, 0.45), (0.45, 0.65), (0.55, 0.85), (0.62, 0.95)]  # mean of the 3 best scores -> highest confidence
SURFACE_STAYS, SEES_FARTHER = 0.5, 1.3
# With the watched-spot check (watch.py, when the worker has it): the object's place compared with the
# baseline picture of it. "same" makes a miss by the detector count SPOT_SAME of a miss, "changed"
# SPOT_CHANGED, and a "changed" place counts as a look without the object even when the object is too
# small in the picture for the detector to be trusted.
SPOT_SAME, SPOT_CHANGED = 0.25, 1.3
BELIEF_MAX = 3.0
HALF_LIFE_S = 24 * 3600.0
SHOW_PCT, GONE_PCT, REPORT_PCT = 60, 25, 70
WAS_EMPTY, WAS_UNKNOWN, WAS_THERE = 0.95, 0.75, 0.4  # how far "the baseline shows this place empty / not seen / with a surface" supports "appeared"
EVENTS_KEPT = 300


def phrases_for(label):
    """The phrases the detector is asked for one label (pass as items.merge(..., phrases=phrases_for))."""
    return VOCABULARY.get(label, [label])


def all_phrases(labels=None):
    return sorted({p for label in (VOCABULARY if labels is None else labels) for p in phrases_for(label)})


def _round(values, digits=3):
    return [round(float(v), digits) for v in values]


def _reach(size_a, size_b=0.0):
    return max(SAME_M, SAME_SIZE * max(size_a or 0.0, size_b or 0.0))


def _p(belief):
    return 1.0 / (1.0 + math.exp(-belief))


def _gain(score):
    return HIT * min(1.5, max(0.3, float(score) / STRONG))


def _bound(belief):
    return round(max(-BELIEF_MAX, min(BELIEF_MAX, belief)), 3)


def _ceiling(top_scores):
    """The highest belief the object's best boxes allow (see CEILING)."""
    if not top_scores:
        return BELIEF_MAX
    top = sum(top_scores) / len(top_scores)
    (x0, y0), (x1, y1) = CEILING[0], CEILING[-1]
    most = y0 if top <= x0 else y1
    for (xa, ya), (xb, yb) in zip(CEILING, CEILING[1:]):
        if xa <= top <= xb:
            most = ya + (yb - ya) * (top - xa) / (xb - xa)
    return math.log(most / (1 - most))


def confidence_pct(obj, now=None):
    """0..100: how sure the map is of what it says about this object. For "present", "moved" and
    "unconfirmed": that it is at its place. For "missing": that it is not. With `now`, the time
    since the last look is taken off (HALF_LIFE_S)."""
    p = _p(obj.get("belief", PRIOR) if now is None else _aged(obj, now))
    return int(round(100 * (1 - p if obj.get("state") == "missing" else p)))


def _aged(obj, now):
    """The belief after the time since the last look: halfway back to "do not know" every HALF_LIFE_S.
    A weak guess that never counted does not grow by waiting."""
    belief = obj.get("belief", PRIOR)
    if obj.get("state") == "unconfirmed" and belief < 0:
        return belief
    return belief * 0.5 ** (max(0.0, now - obj.get("belief_at", now)) / HALF_LIFE_S)


def _stamp(obj):
    obj["belief"] = _bound(min(obj["belief"], _ceiling(obj.get("top_scores"))))
    obj["confidence_pct"] = confidence_pct(obj)


def new_map():
    """An empty object map for one room."""
    return {"version": 1, "objects": [], "events": [], "next_id": 1, "next_event": 1, "updates": 0}


def _add(room_map, **fields):
    obj = {"id": f"o{room_map['next_id']}", **fields}
    room_map["next_id"] += 1
    room_map["objects"].append(obj)
    return obj


# ----------------------------------------------------------------------------- inventory
def fuse(sightings, now=None, limit=None):
    """Sightings from many photos of one unchanged room -> the object map.
    sightings   [{"name", "score", "position": [east, north, up], "size_m", "view": which photo,
                  optional "box", "crop"}]; those without a position are ignored
    One physical thing seen in several photos becomes one object: sightings of one name that land
    within reach of each other (see SAME_M) and come from different photos are one cluster; two
    boxes of one name in the same photo are two things and are never put together. Clusters of
    different names at the same place with the same size are one object, named by the stronger
    evidence, the other name kept in "names". Every object gets a belief from its photos (see
    "Confidence" above); from SHOW_PCT on it is "present", below it stays in the map as
    "unconfirmed": not shown, not reported, but a later sighting there is no surprise."""
    now = time.time() if now is None else now
    limit = LIMIT_INVENTORY if limit is None else limit
    usable = [s for s in sightings if s.get("position") is not None and s["score"] >= limit]
    usable.sort(key=lambda s: -s["score"])
    clusters = []

    def refresh(c):
        members = c["members"]
        c["position"] = [sorted(m["position"][k] for m in members)[len(members) // 2] for k in range(3)]  # the median: one bad depth does not pull it
        c["size_m"] = sorted(m.get("size_m") or 0.1 for m in members)[len(members) // 2]
        c["views"] = {m["view"] for m in members}

    for s in usable:  # strongest first: the clear boxes found the clusters, the weak ones join them
        best, best_d = None, None
        for c in clusters:
            if c["label"] != s["name"] or s["view"] in c["views"]:
                continue
            d = math.dist(c["position"], s["position"])
            if d <= _reach(c["size_m"], s.get("size_m")) and (best is None or d < best_d):
                best, best_d = c, d
        if best is None:
            best = {"label": s["name"], "members": []}
            clusters.append(best)
        best["members"].append(s)
        refresh(best)

    def join(same_label, share, inside=False):
        """Put together clusters at the same place that share no photo. inside: also when they do
        share photos, if the centre of one lies well inside the other (the detector boxed a part
        and the whole, or gave one thing two names with different boxes)."""
        done = False
        while not done:
            done = True
            for i, a in enumerate(clusters):
                for b in clusters[i + 1:]:
                    if (a["label"] == b["label"]) != same_label or (a["views"] & b["views"] and not inside):
                        continue
                    small, big = sorted([a["size_m"], b["size_m"]])
                    if not same_label and big > 2.0 * max(small, 1e-3):
                        continue  # a cup on a table is not the table
                    within = share * (big if same_label else small) if inside else share * _reach(big if same_label else small)
                    if math.dist(a["position"], b["position"]) <= within:
                        a["members"] += b["members"]
                        refresh(a)
                        clusters.remove(b)
                        done = False
                        break
                if not done:
                    break

    join(True, 0.6)  # the order of the boxes can split one thing in two
    join(False, 0.6)  # "cup" in one photo, "glass" in the next: one thing, two names
    join(True, 0.5, inside=True)  # a box of the whole sofa and a box of its left half
    join(False, 0.5, inside=True)  # "tablet" on the screen and "laptop" on the whole device

    room_map = new_map()
    for c in sorted(clusters, key=lambda c: -sum(m["score"] for m in c["members"])):
        names = {}
        for m in c["members"]:
            names[m["name"]] = names.get(m["name"], 0.0) + m["score"]
        label = max(names, key=names.get)
        best = max(c["members"], key=lambda m: m["score"])
        per_view = {}
        for m in c["members"]:  # one vote per photo: its best box of this thing
            per_view[m["view"]] = max(per_view.get(m["view"], 0.0), float(m["score"]))
        obj = _add(room_map, label=label, names={k: round(v, 2) for k, v in sorted(names.items(), key=lambda kv: -kv[1])},
                   position=_round(c["position"]), size_m=round(float(c["size_m"]), 3), views=len(c["views"]),
                   evidence=round(sum(m["score"] for m in c["members"]), 2), best_score=round(float(best["score"]), 2),
                   crop=best.get("crop"), best_view=best["view"], best_box=best.get("box"), first_seen=now, last_seen=now,
                   source="inventory", belief=PRIOR + sum(_gain(v) for v in per_view.values()), belief_at=now,
                   top_scores=[round(v, 3) for v in sorted(per_view.values(), reverse=True)[:3]], hits_in_row=0, missed=0, seen_from=[])
        _grade(obj)
    return room_map


def _grade(obj):
    """Set an inventory object's confidence and whether it counts."""
    obj["state"] = "present"
    _stamp(obj)
    obj["state"] = "present" if obj["confidence_pct"] >= SHOW_PCT else "unconfirmed"


# ----------------------------------------------------------------------------- update
def _standpoint(fix):
    position = fix.get("position") or [0.0, 0.0]
    return [round(float(position[0]), 2), round(float(position[1]), 2), float(fix.get("heading_deg") or 0.0)]


def _elsewhere(seen_from, here):
    """Is `here` another standpoint than every one in seen_from? Looks from one standpoint repeat the same mistake."""
    return all(math.dist(s[:2], here[:2]) >= MOVED_M or abs((here[2] - s[2] + 180) % 360 - 180) >= MOVED_DEG for s in seen_from)


def _called(obj, name):
    return name == obj["label"] or name in obj.get("names", {})


def _event(room_map, kind, obj, now, **more):
    event = {"id": f"e{room_map['next_event']}", "kind": kind, "object": obj["id"], "label": obj["label"], "time": now,
             "status": "unconfirmed", "confidence_pct": 0, **more}
    room_map["next_event"] += 1
    event["text"] = describe(event)
    room_map.setdefault("events", []).append(event)
    return event


def _event_confidence(event, obj):
    """An event's confidence from the beliefs behind it, 0..100.
    gone      (how sure the object was real before it was missed) x (how sure its place is empty now)
    appeared  (how sure the new object is real) x (how far the baseline supports that the place was
              empty before: WAS_EMPTY, WAS_UNKNOWN or WAS_THERE); for an object that is back: the first alone
    moved     the smaller of: the "gone" confidence of the old place, and how sure the new place is"""
    p = _p(obj.get("belief", PRIOR))
    if event["kind"] == "object_gone":
        return int(round(100 * event.get("was_sure", 0.5) * (1 - p)))
    if event["kind"] == "object_moved":
        return int(round(100 * min(event.get("old_place", 0.5), p)))
    return int(round(100 * p * (1.0 if event.get("returned") else obj.get("fresh", WAS_UNKNOWN))))


def update(room_map, detections, fix, now=None, limit_confirm=None, limit_new=None, report_pct=None, use_depth=True, use_spot=True):
    """Fold one position fix into the map. Returns (new map, events newly reported by this fix);
    the map given is not changed.
    detections  what the detector found in the newest frame, on the room map (lift, or the worker's
                "objects_seen"): [{"name", "score", "box", "position" or None, "size_m", optional
                "crop", optional "baseline": {"agree", "free"}}]
    fix         {"state": "found" | "unsure" | "lost", "position": [east, north, ...], "heading_deg",
                 "checked": [{"id", "in_view", "blocked", "close_enough", "pixel", "depth_gap_m",
                 "distance_m", optional "spot": "same" | "changed" | "not_seen"}]}: where the wearer
                stood, and for each object of the map whether this frame had a clear look at its
                place (visibility, or the worker's "objects_checked"; "spot" is the watched-spot
                verdict when watch.py is there). Objects not in "checked" are taken as not looked at.
    Events: object_moved ("from", "to"), object_gone ("from"), object_appeared ("to"), each with
    "evidence", "confidence_pct" and "text". Every event is kept in map["events"] with a "status":
    "unconfirmed" while its confidence is below report_pct (REPORT_PCT), "reported" from then on,
    "withdrawn" when later looks took it back before it was reported, "replaced" when it turned out
    to be half of a move. Only reported events are returned and belong in the diary (diary()).
    A lost fix changes nothing."""
    now = time.time() if now is None else now
    limit_confirm = LIMIT_CONFIRM if limit_confirm is None else limit_confirm
    limit_new = LIMIT_NEW if limit_new is None else limit_new
    report_pct = REPORT_PCT if report_pct is None else report_pct
    room_map = copy.deepcopy(room_map)
    room_map.setdefault("events", [])
    if not fix or fix.get("state") == "lost":
        return room_map, []
    room_map["updates"] = room_map.get("updates", 0) + 1
    here = _standpoint(fix)
    objects = room_map["objects"]
    for obj in objects:  # time since the last look: the belief shrinks towards "do not know"
        obj["belief"] = _aged(obj, now)
        obj["belief_at"] = now
    seen = [d for d in detections if d["score"] >= limit_confirm]
    placed = [d for d in seen if d.get("position") is not None]
    looks = {c.get("id"): c for c in fix.get("checked") or []}

    # 1. Which detection is which known object: nearest first, each detection and object once.
    pairs = []
    for i, d in enumerate(placed):
        for obj in objects:
            if _called(obj, d["name"]):
                gap = math.dist(obj["position"], d["position"])
                if gap <= _reach(obj.get("size_m"), d.get("size_m")):
                    pairs.append((gap, i, obj["id"]))
    matched, used = {}, set()
    for gap, i, oid in sorted(pairs):
        if oid in matched or i in used:
            continue
        matched[oid] = (placed[i], gap)
        used.add(i)
    # A box of the right name that could not be placed (no trusted depth) but sits on the object's pixel: still there.
    for obj in objects:
        pixel = (looks.get(obj["id"]) or {}).get("pixel")
        if obj["id"] in matched or not pixel:
            continue
        for d in seen:
            if d.get("position") is None and d.get("box") and _called(obj, d["name"]):
                u0, v0, u1, v1 = d["box"]
                grow_u, grow_v = 0.25 * (u1 - u0), 0.25 * (v1 - v0)
                if u0 - grow_u <= pixel[0] <= u1 + grow_u and v0 - grow_v <= pixel[1] <= v1 + grow_v:
                    matched[obj["id"]] = (d, None)
                    break

    # 2. Something of another name at an object's place: not a sighting, but the place is not empty either.
    occupied = set()
    for i, d in enumerate(placed):
        if i in used:
            continue
        for obj in objects:
            if obj["id"] in matched:
                continue
            small, big = sorted([obj.get("size_m") or 0.1, d.get("size_m") or 0.1])
            if big <= 2.0 * small and math.dist(obj["position"], d["position"]) <= 0.6 * _reach(small):
                occupied.add(obj["id"])
                if d["name"] not in obj.setdefault("names", {}) and d["score"] >= limit_new:
                    obj["names"][d["name"]] = round(float(d["score"]), 2)  # the other name it was called
                used.add(i)
                break

    shown, vanished = [], []  # objects that crossed SHOW_PCT upwards / fell to "missing" in this fix
    for obj in objects:
        was = obj["state"]
        if obj["id"] in matched:
            d, gap = matched[obj["id"]]
            if gap is not None:
                obj["position"] = _round(p + BLEND * (q - p) for p, q in zip(obj["position"], d["position"]))
            obj.update(last_seen=now, missed=0, best_score=max(obj.get("best_score", 0.0), round(float(d["score"]), 2)))
            obj.pop("missed_from", None)
            if d.get("crop"):
                obj["crop_now"] = d["crop"]
            if _elsewhere(obj.get("seen_from", []), here):  # the same standpoint again adds nothing
                obj["seen_from"] = (obj.get("seen_from", []) + [here])[-6:]
                obj["hits_in_row"] = obj.get("hits_in_row", 0) + 1
                obj["top_scores"] = sorted(obj.get("top_scores", []) + [round(float(d["score"]), 3)], reverse=True)[:3]
                obj["belief"] += _gain(d["score"]) * min(obj["hits_in_row"], 2)
                obj["evidence"] = round(obj.get("evidence", 0.0) + float(d["score"]), 2)
                obj["views"] = obj.get("views", 0) + 1
                _note_baseline(obj, d)
            _stamp(obj)
            if was in ("unconfirmed", "missing") and _p(obj["belief"]) * 100 >= SHOW_PCT:
                shown.append((obj, was, d, gap))
            continue
        if obj["id"] in occupied or was == "unconfirmed":
            _stamp(obj)
            continue
        look = looks.get(obj["id"]) or {}
        # the watched-spot verdict compares with the baseline picture, so it only speaks for a thing still at its inventory place
        spot = look.get("spot") if use_spot and obj.get("source") == "inventory" and not obj.get("moved_from") else None
        clear = look.get("in_view") and look.get("close_enough") and not look.get("blocked")
        if clear or spot == "changed":
            if _elsewhere([obj["missed_from"]] if obj.get("missed_from") else [], here):
                if not obj.get("missed"):
                    obj["sure_before"] = round(_p(obj["belief"]), 3)  # how sure it was real before this run of misses
                obj.update(missed=obj.get("missed", 0) + 1, missed_from=here, missed_at=now, hits_in_row=0)
                weight, says = 1.0, "no other evidence"
                gap = look.get("depth_gap_m")
                if spot in ("same", "changed"):
                    weight, says = (SPOT_SAME, "the place looks as in the baseline") if spot == "same" else (SPOT_CHANGED, "the place looks different")
                elif use_depth and gap is not None:
                    tolerance = max(0.05, 0.04 * float(look.get("distance_m") or 0.0))
                    weight, says = (SEES_FARTHER, "sees farther") if gap > tolerance else (SURFACE_STAYS, "a surface is still there")
                obj["belief"] -= MISS * min(obj["missed"], 3) * weight
                obj["missed_looks"] = (obj.get("missed_looks", []) + [{"time": now, "from": here, "size_px": look.get("size_px"), "depth_gap_m": gap,
                                                                        "spot": spot, "spot_score": look.get("spot_score"), "says": says}])[-6:]
                _stamp(obj)
                if was != "missing" and obj["missed"] >= 2 and obj["confidence_pct"] <= GONE_PCT:
                    vanished.append(obj)
        _stamp(obj)

    # 3. Detections that belong to nothing known: possible new objects, one standpoint so far.
    for i, d in enumerate(placed):
        if i in used or d["score"] < limit_new:
            continue
        obj = _add(room_map, label=d["name"], names={d["name"]: round(float(d["score"]), 2)}, position=_round(d["position"]),
                   size_m=round(float(d.get("size_m") or 0.1), 3), views=1, evidence=round(float(d["score"]), 2),
                   best_score=round(float(d["score"]), 2), crop=d.get("crop"), first_seen=now, last_seen=now, state="unconfirmed",
                   source="update", belief=PRIOR + _gain(d["score"]), belief_at=now, hits_in_row=1, missed=0, seen_from=[here],
                   top_scores=[round(float(d["score"]), 3)])
        _note_baseline(obj, d)
        _stamp(obj)

    # 4. Gone, appeared, or both at once: a move.
    def same_thing(a, b):
        return a["id"] != b["id"] and (_called(a, b["label"]) or _called(b, a["label"]))

    def event_of(obj, key):
        return next((e for e in room_map["events"] if e["id"] == obj.get(key)), None)

    def move(old, new, replaced):
        """`old` is not at its place, `new` is the same name confirmed elsewhere: one object, moved."""
        was, looks_away = list(old["position"]), old.get("missed", 0)
        old_place = old.get("sure_before", _p(old["belief"])) * (1 - _p(old["belief"]))
        old.update(position=new["position"], size_m=new["size_m"], state="moved", moved_from=was, moved_at=now, last_seen=new["last_seen"],
                   missed=0, seen_from=new.get("seen_from", []), crop_now=new.get("crop_now") or new.get("crop"), belief=new["belief"],
                   top_scores=new.get("top_scores", []),
                   hits_in_row=new.get("hits_in_row", 0))
        for key in ("missed_from", "gone_event", "appeared_event"):
            old.pop(key, None)
        objects.remove(new)
        _stamp(old)
        for event in replaced:
            if event:
                event["status"] = "replaced"
        event = _event(room_map, "object_moved", old, now, to=old["position"], old_place=round(old_place, 3),
                       supersedes=[e["id"] for e in replaced if e], **{"from": was}, evidence={
                           "clear_looks_at_old_place_without_it": looks_away, "sightings_at_new_place": new.get("views", 1),
                           "best_score_at_new_place": new.get("best_score"), "metres_moved": round(math.dist(was, old["position"]), 2)})
        old["moved_event"] = event["id"]

    for obj in vanished:
        if obj not in objects:
            continue
        new = [o for o in objects if same_thing(obj, o) and o.get("source") == "update" and not o.get("moved_from")
               and (o["state"] == "present" or any(o is s[0] for s in shown))]
        if new:
            target = min(new, key=lambda o: math.dist(o["position"], obj["position"]))
            shown = [s for s in shown if s[0] is not target]
            move(obj, target, [event_of(target, "appeared_event")])
            continue
        obj["state"] = "missing"
        mine = event_of(obj, "appeared_event")
        if mine and mine["status"] == "unconfirmed":  # it was never reported as new: say nothing at all
            mine["status"] = "withdrawn"
            _stamp(obj)
            continue
        event = _event(room_map, "object_gone", obj, now, was_sure=obj.get("sure_before", 0.5), **{"from": obj["position"]}, evidence={
            "clear_looks_without_it": obj["missed"], "looks": obj.get("missed_looks", [])[-3:], "photos_or_standpoints_that_saw_it": obj.get("views"),
            "best_score": obj.get("best_score"), "last_seen": obj.get("last_seen")})
        obj["gone_event"] = event["id"]
        _stamp(obj)
    for obj, was, d, gap in shown:
        if obj not in objects:
            continue
        if was == "missing":  # it is back where it was
            obj["state"] = "moved" if obj.get("moved_from") else "present"
            mine = event_of(obj, "gone_event")
            obj.pop("gone_event", None)
            if mine and mine["status"] == "unconfirmed":
                mine["status"] = "withdrawn"  # "gone" was never reported, so "back" would be news about nothing
            else:
                event = _event(room_map, "object_appeared", obj, now, to=obj["position"], returned=True,
                               evidence={"score": round(float(d["score"]), 2), "metres_from_old_place": None if gap is None else round(gap, 2)})
                obj["back_event"] = event["id"]
        elif obj.get("source") != "update":
            obj["state"] = "present"  # the inventory had it weakly: it was there all along, no event
        else:
            old = [o for o in objects if same_thing(obj, o) and o["state"] == "missing"]
            if old:
                target = min(old, key=lambda o: math.dist(o["position"], obj["position"]))
                move(target, obj, [event_of(target, "gone_event")])
                continue
            obj["state"] = "present"
            event = _event(room_map, "object_appeared", obj, now, to=obj["position"],
                           evidence={"standpoints": obj.get("views", 1), "best_score": obj.get("best_score"),
                                     "baseline_saw_it_empty": obj.get("was_empty", 0), "baseline_saw_a_surface": obj.get("was_there", 0)})
            obj["appeared_event"] = event["id"]
        _stamp(obj)

    # 5. Every open event's confidence follows the beliefs behind it; report the ones that got sure enough.
    by_id = {o["id"]: o for o in objects}
    reported = []
    for event in room_map["events"]:
        obj = by_id.get(event["object"])
        if obj is None or event["status"] not in ("unconfirmed", "reported"):
            continue
        key = {"object_gone": "gone_event", "object_moved": "moved_event"}.get(event["kind"], "back_event" if event.get("returned") else "appeared_event")
        if obj.get(key) != event["id"] or (event["kind"] != "object_gone" and obj["state"] == "missing"):
            continue  # an older event of this object: its numbers are history
        event["confidence_pct"] = _event_confidence(event, obj)
        if event["kind"] == "object_appeared" and not event.get("returned") and any(
                same_thing(obj, o) and o.get("missed") and o["state"] in ("present", "moved") for o in objects):
            continue  # a thing of that name was just not found at its place: this may be its new place, so wait for that to settle
        if event["status"] == "unconfirmed" and event["confidence_pct"] >= report_pct:
            event.update(status="reported", reported_at=now)
            reported.append(event)
    room_map["events"] = room_map["events"][-EVENTS_KEPT:]
    return room_map, [copy.deepcopy(e) for e in reported]


def _note_baseline(obj, d):
    """What the baseline photos of this fix show at a new sighting's place (lift's "baseline")."""
    votes = d.get("baseline")
    if votes:
        if votes.get("free", 0) >= 1 and not votes.get("agree", 0):
            obj["was_empty"] = obj.get("was_empty", 0) + 1
        elif votes.get("agree", 0) >= 2 and not votes.get("free", 0):
            obj["was_there"] = obj.get("was_there", 0) + 1
    empty, there = obj.get("was_empty", 0), obj.get("was_there", 0)
    obj["fresh"] = WAS_EMPTY if empty > there else WAS_THERE if there > empty else WAS_UNKNOWN


def diary(room_map, unconfirmed=False):
    """The events worth telling, oldest first: the reported ones (and, on request, the ones still
    waiting as "unconfirmed"). Withdrawn and replaced events are never told."""
    keep = ("reported", "unconfirmed") if unconfirmed else ("reported",)
    return [e for e in room_map.get("events", []) if e["status"] in keep]


def events(before, after, now=None):
    """What differs between two states of one room's map, as events: for a diary over a longer time
    ("since this morning") than the events update() reports fix by fix. Each carries the object's
    confidence_pct and is "reported" from REPORT_PCT on, "unconfirmed" below."""
    now = time.time() if now is None else now
    old = {o["id"]: o for o in before["objects"]}
    scratch = {"next_event": 1, "events": []}
    for obj in after["objects"]:
        was = old.get(obj["id"])
        counted = was is not None and was["state"] in ("present", "moved")
        event = None
        if obj["state"] == "missing" and counted:
            event = _event(scratch, "object_gone", obj, obj.get("missed_at", now), evidence={"clear_looks_without_it": obj.get("missed", 0)},
                           **{"from": was["position"]})
        elif obj["state"] in ("present", "moved") and counted and math.dist(was["position"], obj["position"]) > _reach(obj.get("size_m")):
            event = _event(scratch, "object_moved", obj, obj.get("moved_at", now), to=obj["position"],
                           evidence={"metres_moved": round(math.dist(was["position"], obj["position"]), 2)}, **{"from": was["position"]})
        elif obj["state"] in ("present", "moved") and (was["state"] == "missing" if was is not None else obj.get("source") == "update"):
            event = _event(scratch, "object_appeared", obj, obj.get("last_seen", now), to=obj["position"], returned=was is not None,
                           evidence={"standpoints": obj.get("views", 1)})
        if event:
            event["confidence_pct"] = confidence_pct(obj)
            event["status"] = "reported" if event["confidence_pct"] >= REPORT_PCT else "unconfirmed"
    return scratch["events"]


# ----------------------------------------------------------------------------- words
def _near(position, places):
    best = min(places or [], default=None, key=lambda p: math.dist(p["position"][:2], position[:2]))
    return best["name"] if best and math.dist(best["position"][:2], position[:2]) <= NEAR_PLACE_M else None


def _are(label):
    return ("are", "were", "them", "their") if label.endswith("s") and not label.endswith("ss") else ("is", "was", "it", "its")


def describe(event, places=None, clock=None):
    """One event as a sentence about the object, never about the person: "The keys were moved from
    near the shelf to near the table at 11:05."
    places: [{"name", "position"}] on the same map (a fix's "waypoints"), to say where in words.
    clock: function(seconds) -> "11:05"; without it no time is said."""
    label = event["label"]
    are, were, them, their = _are(label)
    at = f" at {clock(event['time'])}" if clock else ""

    def near(position):
        name = _near(position, places) if position else None
        return f"near the {name}" if name else None

    start, end = near(event.get("from")), near(event.get("to"))
    if event["kind"] == "object_moved":
        if start and end and start != end:
            return f"The {label} {were} moved from {start} to {end}{at}."
        if end:
            return f"The {label} {were} moved, and {were} then seen {end}{at}."
        return f"The {label} {were} moved{at}."
    if event["kind"] == "object_gone":
        return f"The {label} {were} no longer {start}{at}." if start else f"The {label} {were} no longer in {their} place{at}."
    if event.get("returned"):
        return f"The {label} {were} back in {their} place{at}."
    return f"Something new was seen{' ' + end if end else ''}{at}: {label}."


def _singular(word):
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 2 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _plain(text):
    words = re.sub(r"[^\w]+", " ", str(text).casefold()).split()
    return " ".join([w for w in words if w not in ("a", "an", "the", "my", "of")] or words)


def _lexicon(extra_labels=()):
    """Said form -> the labels it can mean. A label's own name and detector phrases come before other words for it."""
    labels = list(VOCABULARY) + [name for name in extra_labels if name not in VOCABULARY]
    table = {}
    for words_of in (lambda label: [label] + phrases_for(label), lambda label: OTHER_WORDS.get(label, [])):
        for label in labels:
            for word in words_of(label):
                form = _plain(word)
                if form and label not in table.setdefault(form, []):
                    table[form].append(label)
    return table


def where_is(room_map, question, fix=None, places=None, clock=None, now=None):
    """Answer "where is my mug?" from the map. Deterministic: the question's words are matched to
    labels (as said, then with plurals taken off; other words for the thing; the names the detector
    called an object). The longest match wins ("remote control" before "remote", "pill box" before "box").
    Returns {"known": the asked word is a thing the map can hold, "label", "found": an object of it
    is somewhere, "state", "position", "confidence_pct", "object", "others": more objects of that
    name, "distance_m", "bearing_deg" (from a usable fix: 0 ahead, + to the right), "near", "answer"}.
    Not in the map is said plainly; an "unconfirmed" object is never given as an answer."""
    words = re.sub(r"[^\w]+", " ", question.casefold()).split()
    table = _lexicon([o["label"] for o in room_map["objects"]] + [n for o in room_map["objects"] for n in o.get("names", {})])
    said = " " + " ".join(words) + " "
    hits = [(form, labels) for form, labels in table.items() if f" {form} " in said]
    if not hits:  # "cups", "phones", "remotes"
        said = " " + " ".join(_singular(w) for w in words) + " "
        hits = [(form, labels) for form, labels in table.items() if f" {' '.join(_singular(w) for w in form.split())} " in said]
    reply = {"known": bool(hits), "question": question, "label": None, "found": False, "state": None, "position": None,
             "confidence_pct": None, "object": None, "others": [], "distance_m": None, "bearing_deg": None, "near": None}
    if not hits:
        reply["answer"] = "I do not know that thing, so I have not been watching for it."
        return reply
    form, labels = max(hits, key=lambda h: len(h[0]))
    order = {"present": 0, "moved": 0, "missing": 1}
    found = {}
    for rank, label in enumerate(labels):
        for obj in room_map["objects"]:
            if obj["state"] in order and obj["id"] not in found and (obj["label"] == label or label in obj.get("names", {})):
                found[obj["id"]] = ((order[obj["state"]], 0 if obj["label"] == label else 1, rank, -obj.get("belief", 0.0), -obj.get("last_seen", 0)), obj)
    reply["label"] = labels[0]
    if not found:
        reply["answer"] = f"I have not seen your {labels[0]} in this room."
        return reply
    ranked = [obj for _, obj in sorted(found.values(), key=lambda f: f[0])]
    obj = ranked[0]
    label = obj["label"] if obj["label"] in labels else labels[0]
    reply.update(label=label, object=obj, state=obj["state"], position=obj["position"], found=obj["state"] != "missing",
                 confidence_pct=confidence_pct(obj, now), others=ranked[1:], near=_near(obj["position"], places))
    are, were, them, their = _are(label)
    seen = f" at {clock(obj['last_seen'])}" if clock and obj.get("last_seen") else ""
    spot = f" near the {reply['near']}" if reply["near"] else ""
    if obj["state"] == "missing":
        gone = f" at {clock(obj['missed_at'])}" if clock and obj.get("missed_at") else ""
        reply["answer"] = (f"Your {label} {were} last seen{spot}{seen}, but {'they were' if them == 'them' else 'it was'} not there when I "
                           f"looked{gone}. I will keep watching for {them}.")
        return reply
    text = f"Your {label} {were} moved and {were} last seen{spot}{seen}" if obj["state"] == "moved" else f"Your {label} {were} last seen{spot}{seen}"
    if fix and fix.get("state") != "lost" and fix.get("position"):
        east, north = obj["position"][0] - fix["position"][0], obj["position"][1] - fix["position"][1]
        bearing = (math.degrees(math.atan2(east, north)) - (fix.get("heading_deg") or 0) + 180) % 360 - 180
        distance = math.hypot(east, north)
        reply.update(distance_m=round(distance, 1), bearing_deg=int(round(bearing)))
        side = "ahead" if abs(bearing) <= 20 else "behind you" if abs(bearing) >= 150 else "to your right" if bearing > 0 else "to your left"
        text += ", right by you" if distance < 0.8 else f", about {distance:.1f} metres {side}"
    reply["answer"] = text + "."
    return reply


def listing(room_map, states=("present", "moved", "missing"), now=None):
    """The objects worth showing, surest first, each with a fresh confidence_pct: for a dashboard or the 3D view's labels."""
    rows = [{**o, "confidence_pct": confidence_pct(o, now)} for o in room_map["objects"] if o["state"] in states]
    return sorted(rows, key=lambda o: -o["confidence_pct"])


# ----------------------------------------------------------------------------- in the worker (numpy, items.py)
def lift(found, r, q, valid, window, room, state="found", base=None, near=None):
    """Detections of frame q of a reconstruction -> the same, on the room map and with a size.
    found: [{"name", "score", "box"}] as items.Detector.find / items.merge give them. Placement is
    items.place (same depth rules, same notes); "size_m" is the box's long side at that depth.
    With base (the baseline frames of the pass) each placed detection also gets "baseline":
    {"agree": how many baseline frames show a surface at that very point, "free": how many look
    past it to something farther, "seen": how many have a trusted view of it}. A thing that was
    not there when the baseline was taken has "free" views and no "agree" ones."""
    import numpy as np

    import items
    import locate
    import recon_change as rc

    placed, _ = items.place(found, r, None, q, valid, window, state, room=room)
    H, W = r.depth[q].shape
    x0, y0, x1, y1 = window
    K, E = r.K[q], r.E[q]
    if base is not None and near is None:
        near = {j: rc.nearest_in_window(r.depth[j][None], 7)[0] for j in base}
    for item in placed:
        item["size_m"] = None
        if item["position"] is None:
            continue
        world = locate.from_map(room, item["position"])[0]
        depth = float((E[:, :3] @ world + E[:, 3])[2])
        u0, v0, u1, v1 = item["box"]
        wide = (u1 - u0) / (x1 - x0) * (W - 1) / float(K[0, 0])
        high = (v1 - v0) / (y1 - y0) * (H - 1) / float(K[1, 1])
        item["size_m"] = round(float(max(wide, high) * depth * room["metres"]), 3)
        if base is not None:
            agree = free = seen = 0
            thick = min(wide, high) * depth  # the thing's short side in the pass's units: it cannot hide a gap larger than itself
            for j in base:
                gap, gap_near = rc.depth_gap(world[None].astype(np.float32), r.E[j], r.K[j], r.depth[j], near[j], valid[j])
                if not np.isfinite(gap[0]):
                    continue
                z = float((r.E[j][:, :3] @ world + r.E[j][:, 3])[2])
                tolerance = float(np.clip(0.5 * thick / max(z, 1e-6), 0.02, 0.06))
                seen += 1
                agree += int(abs(float(gap[0])) < tolerance)
                free += int(float(gap_near[0]) > 2 * tolerance)
            item["baseline"] = {"agree": agree, "free": free, "seen": seen}
    return placed


def visibility(room_map, r, q, valid, window, room, picture_shape, input_side=960, close_px=None):
    """For every object of the map: did frame q have a clear look at its place? The facts are
    items.check's (in view, blocked by something nearer, distance, the depth seen there against the
    object's), asked for the object's stored position; "close_enough" uses the object's own
    measured size instead of a size per name.
    Returns [{"id", "in_view", "blocked", "close_enough", "size_px", "pixel", "distance_m", "depth_gap_m"}]."""
    import items

    close_px = CLOSE_PX if close_px is None else close_px
    objs = [o for o in room_map["objects"] if o.get("position")]
    out = []
    for start in range(0, len(objs), 50):  # items.check looks at 50 spots at most
        part = objs[start:start + 50]
        asked = [{"name": f"object {o['id']}", "position": o["position"]} for o in part]
        facts, _ = items.check(asked, [a["name"] for a in asked], [], r, q, valid, window, room, picture_shape, input_side)
        by_name = {f["name"]: f for f in facts}
        for o, a in zip(part, asked):
            f = by_name.get(a["name"])
            if f is None:
                continue
            size_px = None if f["size_px"] is None else int(round(f["size_px"] * (o.get("size_m") or items.OTHER_SIZE_M) / items.OTHER_SIZE_M))
            out.append({"id": o["id"], "in_view": f["in_view"], "blocked": f["blocked"], "pixel": f["pixel"], "distance_m": f["distance_m"],
                        "depth_gap_m": f["depth_gap_m"], "size_px": size_px, "close_enough": bool(size_px is not None and size_px >= close_px)})
    return out


def spots(room_map, r, base, valid, room, min_px=6.0):
    """The map's inventory objects as watched spots for watch.check: for each one that is still at
    its inventory place, a box around that place in the baseline frame of this pass that sees it
    largest. [{"name": the object's id, "frame", "box": [u0, v0, u1, v1] in 0..1}]."""
    H, W = r.depth.shape[1:]
    wanted = {o["id"] for o in room_map["objects"] if o.get("source") == "inventory" and not o.get("moved_from") and o["state"] in ("present", "missing")}
    best = {}
    for j in base:
        for f in visibility(room_map, r, j, valid, (0.0, 0.0, 1.0, 1.0), room, (H, W), input_side=max(H, W), close_px=0):
            if f["id"] in wanted and f["in_view"] and not f["blocked"] and f["size_px"] and f["size_px"] > best.get(f["id"], (0,))[0]:
                best[f["id"]] = (f["size_px"], j, f["pixel"])
    out = []
    for oid, (size_px, j, (u, v)) in best.items():
        half = max(min_px, 0.5 * size_px)
        out.append({"name": oid, "frame": int(j), "box": [max(0.0, u - half / W), max(0.0, v - half / H), min(1.0, u + half / W), min(1.0, v + half / H)]})
    return out


def watched(room_map, r, base, q, valid, room, watcher):
    """The watched-spot verdict (watch.py, passed in as `watcher`) for each inventory object's place
    in frame q: {object id: {"spot": "same" | "changed" | "not_seen", "spot_score"}}. Needs the
    reconstruction's patch features (Recon.feat); without them, or without the module, nothing."""
    if watcher is None or r.feat is None:
        return {}
    asked = spots(room_map, r, base, valid, room)
    return {a["name"]: {"spot": a["state"], "spot_score": a["score"]} for a in watcher.check(r, list(base), [q], asked)} if asked else {}


def observe(found, room_map, r, base, q, valid, window, state, picture_shape, room=None, input_side=960, watcher=None):
    """Everything update() needs from one position fix, computed where the reconstruction is (the
    worker): {"objects_seen": detections on the map, "objects_checked": the look at each known
    object's place}. found: every box of the newest frame down to LIMIT_CONFIRM.
    watcher: the watch module (branch jun-22-watch-spots), if the worker has it: each checked
    object then also carries the watched-spot verdict for its place ("spot", "spot_score")."""
    import locate

    room = locate.map_frame(r, base, valid) if room is None else room
    seen = lift(found, r, q, valid, window, room, state, base=base)
    checked = [] if state == "lost" or not room_map else visibility(room_map, r, q, valid, window, room, picture_shape, input_side)
    if checked and watcher is not None:
        verdicts = watched(room_map, r, base, q, valid, room, watcher)
        for c in checked:
            c.update(verdicts.get(c["id"], {}))
    return {"objects_seen": seen, "objects_checked": checked}


def inventory(found_per_photo, r, base=None, valid=None, now=None, window=(0.0, 0.0, 1.0, 1.0), picture_shape=None, room=None,
              frames=None, crops=None, limit=None):
    """The object map of a room from its baseline photos.
    found_per_photo  for each baseline photo, the detector's boxes of the household vocabulary
                     ([{"name", "score", "box"}], down to LIMIT_INVENTORY or lower)
    r                the reconstruction of those photos (recon_change.reconstruct), frame i = photo i
                     (or frames[i] if given)
    base             the frames that define the room map (locate.map_frame); use the ones the
                     position fix uses, so that both put the room on the same map
    crops            optional function(photo index, detection) -> a crop's file name, kept on the object
    Returns the map (fuse). On each object "could_see": how many photos had a clear look at its
    place; every such photo without a box of it lowers its confidence a little (INVENTORY_MISS)."""
    import locate
    import recon_change as rc

    frames = list(range(len(found_per_photo))) if frames is None else list(frames)
    base = frames if base is None else list(base)
    valid = rc.valid_mask(r, 25.0) if valid is None else valid
    room = locate.map_frame(r, base, valid) if room is None else room
    picture_shape = picture_shape or r.rgb[frames[0]].shape
    sightings = []
    for i, (q, found) in enumerate(zip(frames, found_per_photo)):
        for d in lift(found, r, q, valid, window, room):
            d["view"] = i
            if crops is not None and d["position"] is not None:
                d["crop"] = crops(i, d)
            sightings.append(d)
    room_map = fuse(sightings, now=now, limit=limit)
    clear = {}
    for q in frames:
        for f in visibility(room_map, r, q, valid, window, room, picture_shape):
            if f["in_view"] and f["close_enough"] and not f["blocked"]:
                clear[f["id"]] = clear.get(f["id"], 0) + 1
    for obj in room_map["objects"]:
        obj["could_see"] = clear.get(obj["id"], 0)
        obj["belief"] -= INVENTORY_MISS * max(0, obj["could_see"] - obj["views"])
        _grade(obj)
    room_map["sightings"] = len([s for s in sightings if s["position"] is not None])
    return room_map


def marks(room_map, room, states=("present", "moved", "missing")):
    """The objects as balls for the 3D view: [(world position, radius, RGB)], the format
    splat.write_splat(..., marks=...) takes. Pink: there. Orange: moved. Red: missing (where it was).
    room: the map transform of the pass the view is made from (locate.map_frame)."""
    import locate

    out = []
    for obj in listing(room_map, states):
        world = locate.from_map(room, obj["position"])[0]
        out.append((world, max(0.04, 0.35 * (obj.get("size_m") or 0.1)) / room["metres"], COLOURS[obj["state"]]))
    return out


def labels(room_map, room=None, states=("present", "moved", "missing")):
    """The text to draw next to each ball: [{"id", "label", "state", "confidence_pct", "position"
    (map), "world" (the pass's 3D frame, when `room` is given), "colour"}]. JSON-ready."""
    import locate

    out = []
    for obj in listing(room_map, states):
        row = {"id": obj["id"], "label": obj["label"], "state": obj["state"], "confidence_pct": obj["confidence_pct"],
               "position": obj["position"], "colour": list(COLOURS[obj["state"]])}
        if room is not None:
            row["world"] = [round(float(v), 4) for v in locate.from_map(room, obj["position"])[0]]
        out.append(row)
    return out
