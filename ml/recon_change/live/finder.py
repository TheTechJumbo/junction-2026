#!/usr/bin/env python3
"""Find me X: the relay's memory of where things were put, for "find me the orange box", "where is the orange box",
"where did I put my orange box". Pure Python, no pictures: it works from what each position fix's item finder
(items.py on the GPU worker) reported.

    asked     object_in(question) -> "orange box"; the name goes on the zone's watch list, so every later fix looks
              for it (the relay's look_for), and the relay looks in its recent photos and the zone's pictures once.
    seen      observe(): every fix's boxes of a watched name, kept when they pass that name's checks (score, size from
              the fix's depth, colour when the name says one). AGREE_VIEWS fixes that agree within AGREE_M make a
              sighting: position, time and the nearest named place. A new sighting more than MOVED_M from the last one
              is a move ("moved from the snack table to our table at 10:42"). Both are returned as events for the day
              log and the event API.
    told      answer(): "The orange box is on our table, about 3 metres ahead on your left. I last saw it at 10:42."
              target(): where /navigate takes "take me there".

Vision is evidence, never ground truth: a sighting says where the glasses saw the thing, not that it is still there.
"""
import json
import math
import os
import re
import threading
import time
from datetime import datetime

# Per name: the item finder's score a box needs (OWLv2; the worker lists boxes from 0.25 as candidates), the range of
# its long side in metres (rough: the fix's metres), and the share of the middle of the box that must have the colour
# its name says. Measured for "orange box" in eval/FIND_RESULTS.md. A name without its own entry gets the defaults.
LIMITS = {"orange box": 0.3}
DEFAULT_LIMIT = 0.35
SIZES_M = {"orange box": (0.1, 0.6)}
DEFAULT_SIZE_M = (0.02, 2.0)
COLOUR_LEAST = 0.2
AGREE_M = 1.0      # two views this close agree (two fixes of one carton on the venue table: 0.74 m apart)
AGREE_VIEWS = 2    # views from different fixes that make a sighting
AGREE_S = 900      # agreeing views at most this far apart in time
MOVED_M = 1.5      # a sighting this far from the last one is a move (over the day log's ITEM_MOVED_M, 1 m)
VIEWS_KEPT = 30
LIVE_WITHIN_S, LIVE_FROM_M, LIVE_TURN_DEG = 20.0, 1.0, 35.0  # a live detection confirms a single view (confirm_live)
WANT_S = 12 * 3600  # an asked name stays on the watch list this long
MOST_WANTED = 6     # names on a zone's watch list (the worker takes 8 in all)
NEAR_M = 1.5        # the named place within this is "near"; within ON_M, "on" a table, "at" a booth, "by" a door
ON_M = 1.0
FRESH_FIX_S = 60    # "ahead on your left" only from a position fix this recent
OLD_S = 2 * 3600    # a sighting older than this is said to be a while ago
STALE_S = 60        # GET /find: a sighting older than this is "stale" (the caretaker's live outline wants seconds)
WATCH = [n.strip().lower() for n in os.environ.get("BEACON_FIND_WATCH", "orange box").split(",") if n.strip()]

WORKER_ITEMS = {"keys", "glasses", "wallet", "phone", "remote control"}  # tuned on the worker: only its accepted boxes count
ARTICLES = {"the", "my", "our", "a", "an", "your", "his", "her", "their", "some", "this", "that", "these", "those"}
PRONOUNS = {"it", "they", "them", "that", "this", "i", "me", "we", "you", "he", "she", "everything", "something",
            "anything", "there", "here", "home"}
TAIL = re.compile(r"\b(please|for me|now|again|right now|today|then|at|located|kept|gone|go|went|to|is|are|was|were|"
                  r"put|left|in this room|in here|around here|around)$")
QUESTIONS = [  # (pattern on the plain question, group with the thing)
    r"^where (?:did|have|had|do) (?:i|we|you) (?:last )?(?:put|leave|left|place|placed|drop|dropped|see|seen|saw|set|keep|store)"
    r"(?: down)? (?P<x>.+)$",
    r"^where (?:can|could|do|would|will) (?:i|we) find (?P<x>.+)$",
    r"^(?:can|could|would|will) you (?:please )?(?:find|look for|search for|locate|spot)(?: me| for me)? (?P<x>.+)$",
    r"^(?:please )?(?:find|look for|search for|locate|spot)(?: me| for me)? (?P<x>.+)$",
    r"^where(?: is| s| are|re| was| were) (?P<x>.+)$",
    r"^(?:have|did) you seen? (?P<x>.+)$",
    r"^(?:i|we) (?:have |ve )?(?:lost|misplaced|cannot find|can not find|can t find|cant find|couldn t find) (?P<x>.+)$",
    r"^do you know where (?P<x>.+?) (?:is|are)$",
]
# Words that make "where is the X" a question about a place, not a thing: left to the way to a named place (or to the event
# API), unless X is already on the watch list or was seen.
PLACE_LIKE = {"room", "kitchen", "bathroom", "toilet", "loo", "wc", "garage", "bedroom", "hall", "hallway", "garden", "door",
              "exit", "entrance", "stairs", "lift", "elevator", "office", "lounge", "shop", "street", "station", "stage",
              "booth", "table", "fridge", "desk", "bed", "sofa", "couch", "corridor", "lobby", "reception", "car", "house",
              "home", "flat", "building", "floor", "venue", "area", "zone", "place"}
OUTLINE = ("image", "box2d", "dims", "fix_pose")  # kept from the newest view: the photo, the box in it, its size
SURFACES = ("table", "desk", "shelf", "counter", "bench", "worktop", "bed", "sofa", "couch", "chair", "stool", "cabinet")
STANDS = ("booth", "stand", "stall", "stage", "station")


def plain(text):
    """Lower case, letters and digits only, single spaces ("Where's" -> "where s")."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text).lower().replace("’", "'")).split())


def singular(word):
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def key(name):
    """How names are compared: plain, no articles, singular ("the Orange Boxes" -> "orange box")."""
    return " ".join(singular(w) for w in plain(name).split() if w not in ARTICLES)


def object_in(question):
    """The thing a "find / where is / where did I put" question asks for, as the wearer named it ("orange box"), or None
    (not such a question, or "where am I", "where is it")."""
    text = plain(question)
    for lead in ("hey meta ", "ok ", "okay ", "hey ", "please ", "excuse me "):
        if text.startswith(lead):
            text = text[len(lead):]
    for pattern in QUESTIONS:
        found = re.match(pattern, text)
        if not found:
            continue
        thing = found.group("x").strip()
        for _ in range(4):  # "the orange box please", "my keys now", "the orange box at"
            thing = TAIL.sub("", thing).strip()
        words = [w for w in thing.split() if w not in ARTICLES]
        if not words or all(w in PRONOUNS for w in words) or words[0] in ("i", "we", "you", "he", "she") or len(words) > 5:
            return None
        return " ".join(words)
    return None


def place_like(name):
    """Does a name sound like a place ("the garage", "exit door", "snack table") rather than a thing to find?"""
    return any(singular(w) in PLACE_LIKE or w in PLACE_LIKE for w in plain(name).split())


def match(asked, names):
    """The name among `names` that `asked` means, or None: the same key, else one name containing it (or it containing
    one), else the closest spelling."""
    import difflib
    want = key(asked)
    if not want:
        return None
    keyed = {key(n): n for n in names}
    if want in keyed:
        return keyed[want]
    squashed = {k.replace(" ", ""): n for k, n in keyed.items()}
    if want.replace(" ", "") in squashed:
        return squashed[want.replace(" ", "")]
    near = [n for k, n in keyed.items() if k and (re.search(rf"\b{re.escape(want)}\b", k) or re.search(rf"\b{re.escape(k)}\b", want))]
    if len(near) == 1:
        return near[0]
    close = difflib.get_close_matches(want, list(keyed), n=1, cutoff=0.8)
    return keyed[close[0]] if close else None


def the(place):
    """"our table" -> "our table", "snack table" -> "the snack table"."""
    return place if plain(place).split()[:1] and plain(place).split()[0] in ARTICLES else f"the {place}"


def at_place(place, distance):
    """"on our table", "at the IronShard booth", "by the exit door", or "near the ..." further off."""
    if distance > ON_M:
        return f"near {the(place)}"
    words = plain(place).split()
    if any(w in SURFACES for w in words):
        return f"on {the(place)}"
    if any(w in STANDS for w in words):
        return f"at {the(place)}"
    return f"by {the(place)}"


def nearest(position, places, skip=""):
    """(name, distance in metres) of the named place nearest to `position` (on the floor), within NEAR_M; else None."""
    best = None
    for p in places or []:
        if not p.get("name") or not p.get("position") or key(p["name"]) == key(skip):
            continue
        d = math.dist(p["position"][:2], position[:2])
        if d <= NEAR_M and (best is None or d < best[1]):
            best = (p["name"], d)
    return best


def side_words(bearing):
    b = abs(bearing)
    side = "right" if bearing > 0 else "left"
    if b <= 20:
        return "ahead"
    if b <= 60:
        return f"ahead on your {side}"
    if b <= 150:
        return f"on your {side}"
    return "behind you"


def metres(d):
    if d < 1.5:
        return f"about {d:.1f} metres".replace("about 1.0 metres", "about a metre")
    return f"about {round(d)} metres"


def clock(seconds, tz=None):
    return datetime.fromtimestamp(seconds, tz).strftime("%H:%M") if tz else datetime.fromtimestamp(seconds).strftime("%H:%M")


def median3(points):
    return [round(sorted(p[k] for p in points)[len(points) // 2], 3) for k in range(3)]


class Finder:
    """The watch lists and sightings of every zone, kept in one JSON file (no pictures)."""

    def __init__(self, path=None, clock_tz=None, now=time.time):
        self.path, self.tz, self.now = path, clock_tz, now
        self.lock = threading.Lock()
        try:
            state = json.loads(path.read_text()) if path else {}
        except (OSError, ValueError):
            state = {}
        self.wanted = state.get("wanted") or {}    # {zone: {name: asked at}}
        self.things = state.get("things") or {}    # {zone: {name: {"views": [...], "sighting": {...}, "moves": [...]}}}
        self.rejected = {}                         # {zone: {name: the last box not taken, and why}}: for GET /find

    def save(self):
        if not self.path:
            return
        try:
            self.path.write_text(json.dumps({"wanted": self.wanted, "things": self.things}))
        except OSError:
            pass

    # ------------------------------------------------------------------ the watch list
    def want(self, zone, name):
        """Put a name on the zone's watch list (the canonical name a sighting already has, if one matches). Returns
        (name, new): new is True when it was not being looked for."""
        with self.lock:
            known = list(self.things.get(zone, {})) + list(self.wanted.get(zone, {})) + WATCH
            name = match(name, known) or " ".join(plain(name).split())
            new = name not in self.watched(zone)
            mine = self.wanted.setdefault(zone, {})
            mine[name] = self.now()
            for old in sorted(mine, key=mine.get)[:-MOST_WANTED]:
                mine.pop(old)
            self.save()
            return name, new

    def forget(self, zone, name=None):
        with self.lock:
            if name is None:
                self.wanted.pop(zone, None)
                self.things.pop(zone, None)
            else:
                name = match(name, list(self.things.get(zone, {})) + list(self.wanted.get(zone, {}))) or name
                self.wanted.get(zone, {}).pop(name, None)
                self.things.get(zone, {}).pop(name, None)
            self.save()

    def watched(self, zone):
        """The names every fix in this zone looks for: WATCH, then the newest asked."""
        now = self.now()
        asked = sorted((n for n, t in (self.wanted.get(zone) or {}).items() if now - t <= WANT_S),
                       key=lambda n: -self.wanted[zone][n])
        return list(dict.fromkeys(WATCH + asked))[:MOST_WANTED + len(WATCH)]

    def handles(self, zone, name):
        return name in self.watched(zone) or name in (self.things.get(zone) or {})

    # ------------------------------------------------------------------ seeing
    def check(self, box):
        """(passed, why not): the checks one box of a watched name must pass."""
        name = box.get("name")
        if not box.get("position"):
            return False, "no position"
        if name in WORKER_ITEMS and not box.get("accepted"):
            return False, "the worker did not accept it (its own limits and second look)"
        limit = LIMITS.get(name, DEFAULT_LIMIT)
        if (box.get("score") or 0) < limit:
            return False, f"score {box.get('score')} < {limit}"
        low, high = SIZES_M.get(name, DEFAULT_SIZE_M)
        size = box.get("size_m")
        if size is not None and not low <= size <= high:
            return False, f"size {size} m not in {low}-{high} m"
        colour = box.get("colour")
        if colour is not None and colour < COLOUR_LEAST:
            return False, f"colour share {colour} < {COLOUR_LEAST}"
        return True, ""

    def check_live(self, box):
        """The checks a live box (worker op "detect": no depth, so no position or size) must pass: score and colour."""
        name = box.get("name")
        if name in WORKER_ITEMS and not box.get("accepted"):
            return False
        if (box.get("score") or 0) < LIMITS.get(name, DEFAULT_LIMIT):
            return False
        return box.get("colour") is None or box["colour"] >= COLOUR_LEAST

    def observe(self, zone, boxes, places, job, at=None, image=None, source="fix"):
        """One fix's boxes (its "items" and "items_candidates"; or the zone's pictures, source "pictures") -> events: a
        new sighting ("seen") or a move ("moved"), each {"kind", "zone", "name", "position", "at", "near", "near_m",
        "views", "score", "text"} (+ "from" for a move). `places`: the named places to describe a spot by."""
        at = self.now() if at is None else at
        events = []
        with self.lock:
            watched = set(self.watched(zone)) | set(self.things.get(zone) or {})
            best = {}
            for box in boxes or []:
                name = box.get("name")
                if name not in watched:
                    continue
                ok, why = self.check(box)
                if not ok:
                    worst = self.rejected.setdefault(zone, {}).get(name)
                    if not worst or (box.get("score") or 0) >= (worst.get("score") or 0) or self.now() - worst["at"] > 30:
                        self.rejected[zone][name] = {"score": box.get("score"), "size_m": box.get("size_m"),
                                                     "colour": box.get("colour"), "why": why, "at": self.now(), "job": job}
                    continue
                if name not in best or box["score"] > best[name]["score"]:
                    best[name] = box
            for name, box in best.items():
                thing = self.things.setdefault(zone, {}).setdefault(name, {"views": [], "sighting": None, "moves": []})
                view = {"position": [round(float(c), 3) for c in box["position"][:3]], "score": round(float(box["score"]), 3),
                        "at": round(at, 1), "job": job, "size_m": box.get("size_m"), "colour": box.get("colour"),
                        "source": source, **({"image": image} if image else {}),
                        **{k: box[k] for k in ("box2d", "dims", "fix_pose") if box.get(k) is not None}}
                thing["views"] = [v for v in thing["views"] if v["job"] != job][-VIEWS_KEPT + 1:] + [view]
                got = self.settle(zone, name, thing, view, places)
                if got:
                    events.append(got)
            if best:
                self.save()
        return events

    def confirm_live(self, zone, name, at, pose, job, places, image=None, box2d=None):
        """A live detection (worker op "detect": no depth) of `name` in a photo taken from where a single view not yet
        confirmed was taken (within LIVE_FROM_M and LIVE_TURN_DEG, LIVE_WITHIN_S later): it is there again, so that view
        is confirmed (the second agreeing view, at the same spot). pose: [east, north, heading] of the live photo's fix.
        Returns the event (a sighting or a move) or None."""
        with self.lock:
            thing = (self.things.get(zone) or {}).get(name)
            maybe = self.single_view(zone, name)
            if not thing or not maybe or not pose or not maybe.get("fix_pose") or not 0 < at - maybe["at"] <= LIVE_WITHIN_S:
                return None
            fp = maybe["fix_pose"]
            if math.dist(fp[:2], pose[:2]) > LIVE_FROM_M or abs((pose[2] - fp[2] + 180) % 360 - 180) > LIVE_TURN_DEG:
                return None
            view = {**maybe, "at": round(at, 1), "job": job, "source": "live",
                    **({"image": image} if image else {}), **({"box2d": box2d} if box2d else {})}
            thing["views"] = thing["views"][-VIEWS_KEPT + 1:] + [view]
            event = self.settle(zone, name, thing, view, places)
            self.save()
            return event

    def settle(self, zone, name, thing, view, places):
        """After a new view: is there a sighting (AGREE_VIEWS agreeing views, the newest among them), and is it the one
        already kept, a refinement of it, or a move? Returns an event or None."""
        old = thing.get("sighting")
        if old and math.dist(old["position"], view["position"]) <= AGREE_M and view["at"] >= old["at"]:
            old["at"], old["last_job"] = view["at"], view["job"]  # seen again where it was: the time moves on
            old.update({k: view[k] for k in OUTLINE if k in view})  # and the outline is the newest photo's
            return None
        group = [v for v in thing["views"] if abs(v["at"] - view["at"]) <= AGREE_S and
                 math.dist(v["position"], view["position"]) <= AGREE_M]
        if len({v["job"] for v in group}) < AGREE_VIEWS:
            return None
        if old and view["at"] < old["at"]:
            return None  # older pictures that disagree with a newer sighting change nothing
        position = median3([v["position"] for v in group])
        near = nearest(position, places, skip=name)
        sighting = {"position": position, "at": max(v["at"] for v in group), "first_at": min(v["at"] for v in group),
                    "confirmed_at": round(self.now(), 1), "views": len({v["job"] for v in group}),
                    "score": max(v["score"] for v in group), "near": near[0] if near else "",
                    "near_m": round(near[1], 2) if near else None, "source": view.get("source", "fix"),
                    "last_job": view["job"], **{k: view[k] for k in OUTLINE if k in view}}
        thing["sighting"] = sighting
        when = clock(sighting["at"], self.tz)
        where = at_place(near[0], near[1]) if near else "in a new spot"
        event = {"zone": zone, "name": name, "position": position, "at": sighting["at"], "near": sighting["near"],
                 "near_m": sighting["near_m"], "views": sighting["views"], "score": sighting["score"],
                 "source": sighting["source"]}
        if old is None:
            return {**event, "kind": "seen", "text": f"The {name} was seen {where} at {when}."}
        if math.dist(old["position"], position) <= MOVED_M:
            return None  # the same spot, placed again a little differently
        before = at_place(old["near"], old.get("near_m") or 0) if old.get("near") else "where it was"
        move = {"at": sighting["at"], "from": old["position"], "to": position, "from_near": old.get("near", ""),
                "to_near": sighting["near"], "from_at": old["at"], "metres": round(math.dist(old["position"], position), 2)}
        thing["moves"] = (thing.get("moves") or [])[-9:] + [move]
        frm = f"from {the(old['near'])}" if old.get("near") else "from where it was"
        to = f"to {the(near[0])}" if near else f"about {round(move['metres'], 1)} m away"
        return {**event, "kind": "moved", "from": old["position"], "from_near": old.get("near", ""),
                "text": f"The {name} moved {frm} {to} at {when}." if near or old.get("near") else
                        f"The {name} moved {where} at {when}, {to}.", "before_text": before}

    # ------------------------------------------------------------------ telling
    def sighting(self, zone, name):
        thing = (self.things.get(zone) or {}).get(name) or {}
        return thing.get("sighting")

    def single_view(self, zone, name):
        """The newest view of a name that no other view has confirmed yet (a maybe), or None."""
        thing = (self.things.get(zone) or {}).get(name) or {}
        views = thing.get("views") or []
        old = thing.get("sighting")
        newest = max(views, key=lambda v: v["at"], default=None)
        if newest is None or (old and newest["at"] <= old["at"]):
            return None
        return newest

    def target(self, zone, name):
        """{"name", "position", "kind": "object", "confirmed", "at"} for /navigate: the sighting, else the one view that
        is not confirmed yet; None when the name was never seen."""
        found = self.sighting(zone, name)
        if found:
            return {"name": name, "position": found["position"], "kind": "object", "confirmed": True, "at": found["at"]}
        maybe = self.single_view(zone, name)
        if maybe:
            return {"name": name, "position": maybe["position"], "kind": "object", "confirmed": False, "at": maybe["at"]}
        return None

    def targets(self, zone):
        return [t for t in (self.target(zone, n) for n in (self.things.get(zone) or {})) if t]

    def resolve(self, zone, asked):
        """The canonical name `asked` means in this zone (a thing seen or watched), or None."""
        return match(asked, list(self.things.get(zone) or {}) + self.watched(zone))

    def answer(self, zone, name, fix=None, places=None, looking=False):
        """The answer to "where is the <name>?": {"answer", "point", "target", "seen_at", "near", "confirmed"}.
        fix: the zone's newest position fix (for "ahead on your left"); places: the named places."""
        now = self.now()
        found = self.sighting(zone, name)
        maybe = self.single_view(zone, name)
        fresh = fix and fix.get("state") != "lost" and fix.get("position") and now - fix.get("at", 0) < FRESH_FIX_S
        reply = {"item": name, "point": None, "target": None, "seen_at": None, "near": "", "confirmed": False, "looking": looking}

        def way(position):
            if not fresh:
                return ""
            east, north = position[0] - fix["position"][0], position[1] - fix["position"][1]
            d = math.hypot(east, north)
            if d < 0.8:
                return "right by you"
            bearing = (math.degrees(math.atan2(east, north)) - (fix.get("heading_deg") or 0) + 180) % 360 - 180
            return f"{metres(d)} {side_words(bearing)}"

        if found:
            near = nearest(found["position"], places, skip=name) or ((found["near"], found["near_m"]) if found.get("near") else None)
            where = at_place(*near) if near else ""
            here = way(found["position"])
            parts = [p for p in (where, here) if p]
            first = f"The {name} is {', '.join(parts)}." if parts else f"I saw the {name}, but I cannot say where from here."
            when = clock(found["at"], self.tz)
            last = f"I last saw it at {when}" + (", a while ago." if now - found["at"] > OLD_S else ".")
            if found.get("source") == "pictures":
                last = f"I saw it there in the room's pictures from {when}."
            text = f"{first} {last}"
            moves = ((self.things.get(zone) or {}).get(name) or {}).get("moves") or []
            if moves and moves[-1]["at"] == found["at"] and moves[-1].get("from_near"):
                text += f" It was moved there from {the(moves[-1]['from_near'])}."
            if maybe and maybe["at"] > found["at"] and math.dist(maybe["position"], found["position"]) > MOVED_M:
                other = nearest(maybe["position"], places, skip=name)
                text += (f" I may also have seen it {at_place(*other)} at {clock(maybe['at'], self.tz)}; I am checking."
                         if other else f" I may have just seen it somewhere else; I am checking.")
            reply.update(answer=text + " Say take me there and I will guide you.", seen_at=found["at"], near=near[0] if near else "",
                         confirmed=True, point={"name": name, "position": found["position"], "image": found.get("image")}, target=name)
            return reply
        if maybe:
            near = nearest(maybe["position"], places, skip=name)
            where = at_place(*near) if near else "somewhere here"
            here = way(maybe["position"])
            text = (f"I may have seen the {name} {where}{', ' + here if here else ''} at {clock(maybe['at'], self.tz)}, but only once. "
                    "Look that way slowly so I can be sure. Say take me there and I will guide you.")
            reply.update(answer=text, seen_at=maybe["at"], near=near[0] if near else "", target=name,
                         point={"name": name, "position": maybe["position"], "image": maybe.get("image")})
            return reply
        if looking:
            reply["answer"] = (f"I have not seen the {name} yet. I am looking for it now, also in the photos I took earlier. "
                               "Look around slowly, then ask me again.")
        else:
            reply["answer"] = f"I have not seen the {name} yet. I am watching for it: look around slowly, then ask me again."
        return reply

    def reply(self, zone, name, fix=None, places=None, looking=False):
        """GET /find?what=: {found, looking, answer, place, position, at, moved_from, distance_m, bearing_deg, stale,
        confirmed, box2d, image, size_m}. box2d is 0..1 in `image`, the photo the detection ran on; size_m is
        [width, height, depth] in metres, rough (from the box, the distance and the camera's field of view)."""
        now = self.now()
        said = self.answer(zone, name, fix, places, looking=looking)
        thing = (self.things.get(zone) or {}).get(name) or {}
        found = self.sighting(zone, name)
        record = found or self.single_view(zone, name)
        out = {"what": name, "name": name, "found": record is not None, "confirmed": found is not None,
               "looking": record is None or looking, "answer": said["answer"], "place": said.get("near") or None,
               "position": None, "at": None, "moved_from": None, "distance_m": None, "bearing_deg": None, "stale": True,
               "box2d": None, "image": None, "size_m": None, "target": said.get("target")}
        if record is None:
            return out
        near = nearest(record["position"], places, skip=name)
        moves = thing.get("moves") or []
        out.update(position=record["position"], at=record["at"], place=near[0] if near else (record.get("near") or None),
                   stale=now - record["at"] > STALE_S or found is None, box2d=record.get("box2d"), image=record.get("image"),
                   size_m=record.get("dims"))
        if found and moves and moves[-1]["at"] == found["at"]:
            out["moved_from"] = moves[-1].get("from_near") or None
        if fix and fix.get("state") != "lost" and fix.get("position") and now - fix.get("at", 0) < FRESH_FIX_S:
            east, north = record["position"][0] - fix["position"][0], record["position"][1] - fix["position"][1]
            out["distance_m"] = round(math.hypot(east, north), 2)
            out["bearing_deg"] = int(round((math.degrees(math.atan2(east, north)) - (fix.get("heading_deg") or 0) + 180) % 360 - 180))
        return out

    def sentence(self, zone, name, distance, bearing, places=None):
        """/navigate's instruction for a thing: "The orange box is on the snack table, 3 metres ahead on your left"."""
        record = self.sighting(zone, name) or self.single_view(zone, name)
        near = nearest(record["position"], places, skip=name) if record else None
        here = "right by you" if distance < 0.8 else f"{metres(distance)} {side_words(bearing)}"
        return f"The {name} is {at_place(*near)}, {here}" if near else f"The {name} is {here}"

    def listing(self, zone):
        """GET /find: the watch list, each thing's sighting, its maybe, its moves and the last box not taken (and why)."""
        out = []
        for name in dict.fromkeys(self.watched(zone) + list(self.things.get(zone) or {})):
            thing = (self.things.get(zone) or {}).get(name) or {}
            s = thing.get("sighting")
            out.append({"name": name, "watched": name in self.watched(zone), "sighting": s,
                        "time": clock(s["at"], self.tz) if s else None, "maybe": self.single_view(zone, name),
                        "views": len(thing.get("views") or []), "moves": thing.get("moves") or [],
                        "rejected": (self.rejected.get(zone) or {}).get(name)})
        return out
