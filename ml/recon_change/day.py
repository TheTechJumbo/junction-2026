#!/usr/bin/env python3
"""Day log: what the wearer did through the day, made from what the relay already sees.

The relay hands over each thing as it happens:

    log = DayLog(folder, clock=home_time)
    log.fix(fix)                        every position fix (found, unsure or lost)
    log.event(event)                    a sensor or vision event in the team's schema
    log.question("Where are my keys?", topic="keys")
    log.reminders(reminder_list)        the reminders with their state: changes are recorded
    log.item("keys", near="pill box", zone="kitchen", position=[1.0, 2.0, 0.0])

and asks for the day back:

    log.timeline(day)                   one ordered list of entries
    log.summary(day, for_="wearer")     a few plain sentences ("carer" for the other reader)
    log.answer("When was I last in the kitchen?")    {"answer": ...} or None

Rules this file keeps:
- A lost or unsure fix carries no information. It never starts a visit, never extends one and
  never ends one. A stretch without a found fix is "no information", never "not there".
- Wording is observation only: what was seen and when. Never "you took your pills", never why.
- The log follows the glasses: "you were in the kitchen" means the glasses were.
- Nothing here stores a photo, a picture link or a map: names, times, distances and the words
  of a question only.

Storage: one file per day (YYYY-MM-DD.jsonl, one line per thing handed over) in the folder given.
Visits are worked out from those lines each time, so a restart loses nothing and a changed limit
re-reads the same day. Files older than KEEP_DAYS days are deleted.

Limits (measured in eval/DAY_RESULTS.md). Distances are the position fix's own `distance_m`, in
the map's metres. Without a known distance those are 24% to 54% too long, so ENTER_M = 1.5 is
1.0 to 1.2 true metres today and 1.5 true metres once the room has a scale.
"""
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ENTER_M = 1.5       # a found fix this close to a place starts a visit
LEAVE_M = 2.0       # a visit goes on while found fixes stay within this
OUT_FIXES = 2       # found fixes in a row beyond LEAVE_M that end a visit (one alone does not)
GAP_S = 60          # no found fix for this long: the visit is closed and the stretch is "no information"
WALK_S = 180        # near one place, then near another within this: a walk between them
JOIN_S = 20         # an event this close to a visit's first or last fix counts as during the visit
REPEAT_S = 30 * 60  # the same question again within this long is one entry with a count
ITEM_MOVED_M = 1.0  # an item seen this far from its last recorded spot is a new entry
KEEP_DAYS = 14
# Confidence, in percent, of a visit: how sure its fixes were, times three discounts.
RAW_SURE = 0.95     # a raw "found" fix with no stabiliser: 453 of 453 were within 0.2 m in the walking test
CONF = {"one_fix": 0.30,  # a visit resting on one fix loses this share; each further fix halves the loss
        "quiet": 0.40,    # lost when the longest stretch without a fix inside the visit reaches GAP_S
        "quiet_free_s": 15,  # a stretch this short costs nothing: two photos in a row without a fix are normal
        "edge": 0.25}     # lost when the closest fix was right at ENTER_M (nothing lost within 70% of it)
SPEAK_PCT = 60      # entries below this are left out of what is said to the wearer
JOIN_EDGE = 0.8     # an event joined to a visit only through the JOIN_S slack keeps this share

# Other words for a named place, used only when a place of that name is in the log.
PLACE_WORDS = {"pill box": ("pillbox", "pills", "medication", "medicine", "tablets"),
               "front door": ("door", "entrance"), "bathroom": ("toilet", "loo")}
PARTS = {"morning": (0, 12), "afternoon": (12, 18), "evening": (18, 24)}
TIMES = {2: "twice", 3: "three times", 4: "four times", 5: "five times", 6: "six times"}


# ----------------------------------------------------------------------------- visits
def confidence_pct(fixes, sure, quiet_s, gap_s=GAP_S, closest=None, enter=ENTER_M):
    """How sure a visit is, 1 to 99: the mean sureness of its fixes (0 to 1), less for few fixes, for a
    long stretch without a fix inside it, and for a place visit whose closest fix was at the limit."""
    support = 1 - CONF["one_fix"] * 0.5 ** (max(1, fixes) - 1)
    free = min(CONF["quiet_free_s"], gap_s / 2)
    steady = 1 - CONF["quiet"] * min(1.0, max(0.0, quiet_s - free) / (gap_s - free))
    edge = 1.0 if closest is None else 1 - CONF["edge"] * min(1.0, max(0.0, (closest / enter - 0.7) / 0.3))
    return int(round(max(1.0, min(99.0, 100 * sure * support * steady * edge))))


def build_visits(fixes, enter=ENTER_M, leave=LEAVE_M, out_fixes=OUT_FIXES, gap_s=GAP_S):
    """Fixes in time order -> rooms, places and gaps.

    A fix is {"t": seconds, "s": "found" | "unsure" | "lost", "z": zone, "w": {place: distance}}, with
    "pct" when the stabiliser gave a confidence percentage. Only found fixes are used. Returns {"rooms", "places", "gaps", "last_found", "unusable_after"}:
    a room visit is {"zone", "start", "end", "fixes", "quiet_s", "confidence_pct"}, a place visit adds
    "place" and "closest", a gap is {"start", "end", "photos"} (photos that arrived but gave no position).
    Every start and end is the time of a found fix, so a visit is "seen from ... to ...".
    """
    rooms, places, gaps = [], [], []
    room, near = None, {}
    last_found, unusable = None, 0
    for f in fixes:
        if f.get("s") != "found":
            unusable += 1
            continue
        t, zone = f["t"], f.get("z") or ""
        sure = f["pct"] / 100.0 if "pct" in f else RAW_SURE
        if last_found is not None and t - last_found > gap_s:  # nothing known in between
            if room:
                rooms.append(room)
            places.extend(near.values())
            room, near = None, {}
            gaps.append({"start": last_found, "end": t, "photos": unusable})
        if room and room["zone"] != zone:
            rooms.append(room)
            room = None
        if room is None:
            room = {"zone": zone, "start": t, "end": t, "fixes": 0, "quiet_s": 0.0, "sure": 0.0}
        room["quiet_s"] = max(room["quiet_s"], t - room["end"])
        room["end"] = t
        room["fixes"] += 1
        room["sure"] += sure
        dist = {(zone, name): d for name, d in (f.get("w") or {}).items()}
        for key in set(near) | set(dist):
            d, visit = dist.get(key), near.get(key)
            if visit is None:
                if d is not None and d <= enter:
                    near[key] = {"zone": zone, "place": key[1], "start": t, "end": t, "fixes": 1,
                                 "closest": d, "quiet_s": 0.0, "far": 0, "sure": sure}
            elif d is not None and d <= leave:
                visit["quiet_s"] = max(visit["quiet_s"], t - visit["end"])
                visit.update(end=t, fixes=visit["fixes"] + 1, closest=min(visit["closest"], d), far=0, sure=visit["sure"] + sure)
            else:
                visit["far"] += 1
                if visit["far"] >= out_fixes:
                    places.append(near.pop(key))
        last_found, unusable = t, 0
    if room:
        rooms.append(room)
    places.extend(near.values())
    for visit in rooms + places:
        visit.pop("far", None)
        visit["confidence_pct"] = confidence_pct(visit["fixes"], visit.pop("sure") / visit["fixes"], visit["quiet_s"], gap_s,
                                                 visit.get("closest"), enter)
    places.sort(key=lambda v: (v["start"], v["place"]))
    return {"rooms": rooms, "places": places, "gaps": gaps, "last_found": last_found, "unusable_after": unusable}


def build_walks(visits, gaps, name, walk_s=WALK_S):
    """Seen at one place (or room), then at another within walk_s, with no gap in between."""
    walks = []
    for cur in visits:
        before = [v for v in visits if v["end"] <= cur["start"] and v is not cur]
        if not before:
            continue
        prev = max(before, key=lambda v: v["end"])
        if prev[name] == cur[name] or cur["start"] - prev["end"] > walk_s:
            continue
        if any(g["start"] < cur["start"] and g["end"] > prev["end"] for g in gaps):
            continue
        walks.append({"from": prev[name], "to": cur[name], "start": prev["end"], "end": cur["start"], "zone": cur.get("zone", ""),
                      "confidence_pct": min(prev["confidence_pct"], cur["confidence_pct"])})
    return walks


# ----------------------------------------------------------------------------- words
def _plain(text):
    return " " + re.sub(r"[^\w]+", " ", str(text).casefold()).strip() + " "


def _squash(text):
    return re.sub(r"[^a-z0-9]+", "", str(text).casefold())


def _nice(name):
    return re.sub(r"[_\-]+", " ", str(name)).strip()


def _list(parts):
    parts = [p for p in parts if p]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1] if parts else ""


def _times(n):
    return "once" if n == 1 else TIMES.get(n, f"{n} times")


def _duration(seconds):
    minutes = int(round(seconds / 60))
    if minutes < 1:
        return "under a minute"
    hours, minutes = divmod(minutes, 60)
    parts = ([f"{hours} hour{'s' if hours > 1 else ''}"] if hours else []) + \
            ([f"{minutes} minute{'s' if minutes > 1 else ''}"] if minutes else [])
    return "about " + " ".join(parts)


class DayLog:
    def __init__(self, folder, clock=None, name=None, keep_days=KEEP_DAYS, now=time.time, enter=ENTER_M,
                 leave=LEAVE_M, out_fixes=OUT_FIXES, gap_s=GAP_S):
        """folder: where the day files live. clock: seconds since 1970 -> a datetime in the home's
        time zone (the relay's home_time). name: how the carer's sentences call the wearer."""
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda seconds: datetime.fromtimestamp(seconds).astimezone())
        self.name, self.keep_days, self.now = name, keep_days, now
        self.limits = {"enter": enter, "leave": leave, "out_fixes": out_fixes, "gap_s": gap_s}
        self.lock = threading.RLock()
        self.days = {}  # day -> its records, read from the file once
        self.prune()

    # ------------------------------------------------------------------------- storage
    def day_of(self, seconds):
        return self.clock(seconds).date().isoformat()

    def hhmm(self, seconds):
        return self.clock(seconds).strftime("%H:%M")

    def records(self, day):
        with self.lock:
            if day not in self.days:
                rows = []
                try:
                    for line in (self.folder / f"{day}.jsonl").read_text(encoding="utf-8").splitlines():
                        try:
                            row = json.loads(line)
                        except ValueError:  # a line cut short by a crash: skip it, keep the rest
                            continue
                        if isinstance(row, dict) and "t" in row and "k" in row:
                            rows.append(row)
                except OSError:
                    pass
                rows.sort(key=lambda r: r["t"])
                for old in sorted(self.days)[:-2]:  # keep at most three days in memory
                    del self.days[old]
                self.days[day] = rows
            return self.days[day]

    def add(self, record):
        """Keep one record: in memory and as one line at the end of its day's file."""
        with self.lock:
            day = self.day_of(record["t"])
            new_day = day not in self.days
            rows = self.records(day)
            if rows and record["t"] < rows[-1]["t"]:
                rows.append(record)
                rows.sort(key=lambda r: r["t"])
            else:
                rows.append(record)
            try:
                with open(self.folder / f"{day}.jsonl", "a", encoding="utf-8") as sink:
                    sink.write(json.dumps(record, separators=(",", ":")) + "\n")
            except OSError:
                pass
            if new_day:
                self.prune()
        return record

    def prune(self):
        """Delete day files older than keep_days."""
        oldest = (self.clock(self.now()).date() - timedelta(days=self.keep_days - 1)).isoformat()
        for path in self.folder.glob("*.jsonl"):
            if re.fullmatch(r"\d{4}-\d\d-\d\d", path.stem) and path.stem < oldest:
                try:
                    path.unlink()
                except OSError:
                    pass
                self.days.pop(path.stem, None)

    def stored_days(self):
        return sorted(p.stem for p in self.folder.glob("*.jsonl") if re.fullmatch(r"\d{4}-\d\d-\d\d", p.stem))

    # ------------------------------------------------------------------------- taking things in
    def fix(self, fix, at=None):
        """A position fix as the relay keeps it. When the relay added the stabiliser's answer under
        "stable" (track.py: "steady", "settling" or "lost", with a confidence_pct), that is what is
        logged: steady counts as found, settling as unsure. Without it the raw fix is used. Lost and
        unsure ones are stored too (they show that a photo arrived), but without a position."""
        t = float(at if at is not None else fix.get("at") or self.now())
        stable = fix.get("stable") if isinstance(fix.get("stable"), dict) else None
        use = stable or fix
        state = str(use.get("state") or "lost")
        if stable:
            state = {"steady": "found", "settling": "unsure"}.get(state, "lost")
        record = {"k": "fix", "t": round(t, 1), "s": state, "z": str(fix.get("zone") or "")}
        if stable:
            record["st"] = 1
        if state == "found":
            ways = {}
            for w in use.get("waypoints") or []:
                try:
                    if math.isfinite(float(w["distance_m"])):
                        ways[str(w["name"])] = round(float(w["distance_m"]), 2)
                except (KeyError, TypeError, ValueError):
                    continue
            record["w"] = ways
            try:
                record["p"] = [round(float(v), 2) for v in use["position"][:2]]
            except (KeyError, TypeError, ValueError):
                pass
            if stable and isinstance(stable.get("confidence_pct"), (int, float)):
                record["pct"] = int(round(max(0, min(100, stable["confidence_pct"]))))
            elif isinstance(fix.get("confidence"), (int, float)):
                record["c"] = round(float(fix["confidence"]), 2)
        return self.add(record)

    def event(self, event, at=None):
        """A sensor or vision event in the team's schema (id, type, source, ts, zone, object, confidence).
        An id already in that day's log is not stored twice. Returns the record, or None."""
        try:
            t = float(at) if at is not None else datetime.fromisoformat(str(event["ts"]).replace("Z", "+00:00")).timestamp()
            kind = str(event["type"])
        except (KeyError, TypeError, ValueError):
            return None
        record = {"k": "event", "t": round(t, 1), "type": kind, "source": str(event.get("source") or ""),
                  "z": str(event.get("zone") or ""), "object": event.get("object") or None}
        if event.get("id") is not None:
            record["id"] = str(event["id"])
        if isinstance(event.get("confidence"), (int, float)):
            record["c"] = round(float(event["confidence"]), 2)
        same = (lambda r: r.get("id") == record["id"]) if "id" in record else \
            (lambda r: (r["type"], r["t"], r.get("object")) == (record["type"], record["t"], record["object"]))
        with self.lock:
            if any(r["k"] == "event" and same(r) for r in self.records(self.day_of(t))):
                return None
            return self.add(record)

    def last_event_time(self, day=None):
        """The time of the newest stored event of a day (default today), or None: where to read on from."""
        rows = [r["t"] for r in self.records(day or self.day_of(self.now())) if r["k"] == "event"]
        return max(rows) if rows else None

    def question(self, text, at=None, topic=None):
        """A question the wearer asked. topic: the item it is about ("keys"), if the relay knows."""
        record = {"k": "ask", "t": round(float(at if at is not None else self.now()), 1), "q": str(text).strip()[:300]}
        if topic:
            record["topic"] = str(topic)
        return self.add(record)

    def reminders(self, states, at=None):
        """The relay's reminder list ({"id", "text", "state", "due_at", "place", "done_note"}). Each
        change to due, done or missed is recorded once."""
        t = float(at if at is not None else self.now())
        added = []
        with self.lock:
            known = {(r["id"], r.get("due"), r["state"]) for r in self.records(self.day_of(t)) if r["k"] == "reminder"}
            for r in states or []:
                state, due = r.get("state"), r.get("due_at")
                if state not in ("due", "done", "missed") or due is None or self.day_of(due) != self.day_of(t):
                    continue
                if (r["id"], due, state) in known:
                    continue
                record = {"k": "reminder", "t": round(float(due) if state == "due" else t, 1), "id": r["id"], "state": state,
                          "due": due, "text": str(r.get("text") or "")[:120], "place": str(r.get("place") or "")}
                if state == "done" and r.get("done_note"):
                    record["note"] = str(r["done_note"])[:200]
                added.append(self.add(record))
        return added

    def item(self, name, at=None, near="", zone="", position=None):
        """An item the glasses saw (keys, wallet). Recorded the first time each day, and again only
        when it is seen somewhere else. Returns the record, or None when nothing new."""
        t = float(at if at is not None else self.now())
        try:
            spot = [round(float(v), 2) for v in position[:2]] if position else None
        except (TypeError, ValueError):
            spot = None
        with self.lock:
            last = next((r for r in reversed(self.records(self.day_of(t))) if r["k"] == "item" and r["name"] == name and r["z"] == zone), None)
            if last:
                moved = math.dist(last["p"], spot) if last.get("p") and spot else None
                if (moved is None and last.get("near", "") == (near or "")) or (moved is not None and moved < ITEM_MOVED_M):
                    return None
            record = {"k": "item", "t": round(t, 1), "name": str(name), "near": str(near or ""), "z": str(zone or "")}
            if spot:
                record["p"] = spot
            return self.add(record)

    # ------------------------------------------------------------------------- the day, in entries
    def voice(self, for_):
        if for_ == "wearer":
            return {"sub": "you", "Sub": "You", "were": "were", "i_dont": "I do not know"}
        name = self.name or "the wearer"
        return {"sub": name, "Sub": name[0].upper() + name[1:], "were": "was", "i_dont": "It is not known"}

    def span(self, start, end):
        a, b = self.hhmm(start), self.hhmm(end)
        return f"at {a}" if a == b else f"from {a} to {b}"

    def thing(self, name, places):
        """How to call an event's object: the place name it matches ("pillbox" -> "pill box"), else its own."""
        return next((p for p in places if _squash(p) == _squash(name)), "pill box" if _squash(name) == "pillbox" else _nice(name))

    def event_words(self, record, places):
        kind, obj = record["type"], record.get("object")
        zone = f" in the {_nice(record['z'])}" if record.get("z") else ""
        at = self.hhmm(record["t"])
        thing = self.thing(obj, places) if obj else None
        if kind == "pillbox_open":
            return f"The {self.thing(obj or 'pillbox', places)} was opened at {at}", obj or "pillbox"
        if kind in ("door_open", "door_open_night", "door_closed"):
            return f"The {thing or 'door'} was {'closed' if kind == 'door_closed' else 'opened'} at {at}", obj or "door"
        if kind == "object_moved":
            return (f"The camera saw the {thing} in a different spot at {at}" if thing else
                    f"The camera saw that something had moved{zone} at {at}"), obj
        if kind == "object_gone":
            return (f"The camera did not see the {thing} in its usual spot at {at}" if thing else
                    f"The camera saw that something was gone{zone} at {at}"), obj
        if kind == "object_appeared":
            return (f"The camera saw the {thing} in a new spot at {at}" if thing else
                    f"The camera saw something new{zone} at {at}"), obj
        source = "The camera" if record.get("source") == "vision" else "A sensor"
        return f"{source} reported \"{_nice(kind)}\"{f' ({thing})' if thing else ''} at {at}", obj

    def timeline(self, day=None, for_="carer", now=None):
        """The day as one ordered list. Each entry: start, end (seconds), from, to (HH:MM), kind (room,
        place, walk, gap, event, question, reminder, item), place, zone, text (one plain sentence)
        and based_on (what the sentence rests on)."""
        now = self.now() if now is None else now
        day = day or self.day_of(now)
        with self.lock:
            rows = list(self.records(day))
        v = self.voice(for_)
        today = day == self.day_of(now)
        seen = build_visits([r for r in rows if r["k"] == "fix"], **self.limits)
        gap_s = self.limits["gap_s"]
        places = sorted({p["place"] for p in seen["places"]} | {name for r in rows if r["k"] == "fix" for name in r.get("w") or {}})
        out = []

        def entry(kind, start, end, text, based_on, pct=None, **more):
            # confidence_pct: None where no percentage is honest (a gap, an item sighting)
            out.append({"kind": kind, "start": start, "end": end, "from": self.hhmm(start), "to": self.hhmm(end),
                        "place": more.pop("place", ""), "zone": more.pop("zone", ""), "text": text, "based_on": based_on,
                        "confidence_pct": pct, **more})

        def basis(visit, extra=""):
            n = visit["fixes"]
            text = f"{n} position fix{'es' if n != 1 else ''} that said \"found\"{extra}"
            if visit["quiet_s"] > 30:
                text += f"; longest stretch without one: {_duration(visit['quiet_s']).replace('about ', '')}"
            return text

        for room in seen["rooms"]:
            ongoing = today and now - room["end"] <= gap_s
            entry("room", room["start"], room["end"], f"{v['Sub']} {v['were']} in the {_nice(room['zone'])} {self.span(room['start'], room['end'])}.",
                  basis(room), room["confidence_pct"], zone=room["zone"], ongoing=ongoing, fixes=room["fixes"])
        for visit in seen["places"]:
            ongoing = today and now - visit["end"] <= gap_s
            entry("place", visit["start"], visit["end"], f"{v['Sub']} {v['were']} near the {visit['place']} {self.span(visit['start'], visit['end'])}.",
                  basis(visit, f", the closest {visit['closest']:.1f} m from the {visit['place']} (map metres)"),
                  visit["confidence_pct"], place=visit["place"], zone=visit["zone"], ongoing=ongoing, fixes=visit["fixes"])
        for walk in build_walks(seen["places"], seen["gaps"], "place"):
            entry("walk", walk["start"], walk["end"],
                  f"{v['Sub']} went from the {walk['from']} to the {walk['to']} {self.between(walk['start'], walk['end'])}.",
                  f"seen near the {walk['from']} at {self.hhmm(walk['start'])}, then near the {walk['to']} at {self.hhmm(walk['end'])}",
                  walk["confidence_pct"], place=walk["to"], zone=walk["zone"], origin=walk["from"])
        for walk in build_walks(seen["rooms"], seen["gaps"], "zone"):
            entry("walk", walk["start"], walk["end"],
                  f"{v['Sub']} went from the {_nice(walk['from'])} to the {_nice(walk['to'])} {self.between(walk['start'], walk['end'])}.",
                  f"seen in the {_nice(walk['from'])} at {self.hhmm(walk['start'])}, then in the {_nice(walk['to'])} at {self.hhmm(walk['end'])}",
                  walk["confidence_pct"], zone=walk["to"], origin=walk["from"])

        def photos(n):
            return "no photos arrived" if not n else f"{n} photo{'s' if n != 1 else ''} arrived, none gave a position"

        for gap in seen["gaps"]:
            entry("gap", gap["start"], gap["end"], f"No information {self.span(gap['start'], gap['end'])}.", photos(gap["photos"]))
        if today and seen["last_found"] is not None and now - seen["last_found"] > gap_s:
            entry("gap", seen["last_found"], now, f"No information since {self.hhmm(seen['last_found'])}.", photos(seen["unusable_after"]), ongoing=True)
        if seen["last_found"] is None and any(r["k"] == "fix" for r in rows):
            first = next(r["t"] for r in rows if r["k"] == "fix")
            last = now if today else max(r["t"] for r in rows if r["k"] == "fix")
            entry("gap", first, last, f"No information {self.span(first, last)}.", photos(seen["unusable_after"]))

        def where_at(t, thing=None):
            """The visit a moment falls in: (kind, visit). A place the event is about wins, then
            another place, then the room. None when no found fix is near enough in time."""
            inside = lambda x: x["start"] - JOIN_S <= t <= x["end"] + JOIN_S  # noqa: E731
            near = [x for x in seen["places"] if inside(x)]
            same = [x for x in near if thing and _squash(x["place"]) == _squash(thing)]
            if same:
                return "same", same[0]
            if near:
                return "place", min(near, key=lambda x: x["closest"])
            room = next((x for x in seen["rooms"] if inside(x)), None)
            return ("room", room) if room else (None, None)

        for r in rows:
            if r["k"] != "event":
                continue
            words, thing = self.event_words(r, places)
            how, visit = where_at(r["t"], thing)
            if how == "same":
                tail = f" while {v['sub']} {v['were']} next to it."
            elif how == "place":
                tail = f" while {v['sub']} {v['were']} near the {visit['place']}."
            elif how == "room":
                tail = f" while {v['sub']} {v['were']} in the {_nice(visit['zone'])}."
            else:  # say that it is not known, never guess
                tail = f". {v['i_dont']} where {v['sub']} {v['were']} at that time."
            based = f"{r.get('source') or 'sensor'} event \"{r['type']}\"" + (f", confidence {r['c']}" if "c" in r and r.get("source") == "vision" else "")
            based += "; position: " + ("no found fix within %d s" % JOIN_S if how is None else f"a visit seen {self.span(visit['start'], visit['end'])}")
            # The event itself (a sensor is taken at its word, the camera at its own confidence), times the visit it is joined to.
            pct = 99.0 * (min(1.0, r["c"]) if "c" in r and r.get("source") == "vision" else 1.0)
            if visit:
                pct *= visit["confidence_pct"] / 100.0 * (1.0 if visit["start"] <= r["t"] <= visit["end"] else JOIN_EDGE)
            entry("event", r["t"], r["t"], words + tail, based, int(round(max(1.0, pct))), place=visit.get("place", "") if visit and how != "room" else "",
                  zone=(visit or {}).get("zone") or r.get("z", ""), type=r["type"], object=thing, joined=how or "unknown")

        groups, newest = [], {}  # the same question again within REPEAT_S joins its group, whatever was asked in between
        for r in rows:
            if r["k"] != "ask":
                continue
            key = r.get("topic") or _plain(r["q"])
            group = newest.get(key)
            if group and r["t"] - group["end"] <= REPEAT_S:
                group.update(end=r["t"], n=group["n"] + 1)
            else:
                newest[key] = {"key": key, "topic": r.get("topic"), "q": r["q"], "start": r["t"], "end": r["t"], "n": 1}
                groups.append(newest[key])
        for g in groups:
            what = f"where the {g['topic']} {'are' if g['topic'].endswith('s') else 'is'}" if g["topic"] else f"\"{g['q']}\""
            when = f"at {self.hhmm(g['start'])}" if g["n"] == 1 else f"{_times(g['n'])} {self.between(g['start'], g['end'])}"
            how, visit = where_at(g["start"])
            entry("question", g["start"], g["end"], f"{v['Sub']} asked {what} {when}.",
                  f"{g['n']} question{'s' if g['n'] != 1 else ''} asked through the glasses", 99,
                  place=visit["place"] if how in ("same", "place") else "", zone=visit["zone"] if visit else "", count=g["n"], topic=g["topic"] or "")

        by_reminder = {}
        for r in rows:
            if r["k"] == "reminder":
                by_reminder.setdefault((r["id"], r.get("due")), {})[r["state"]] = r
        for (_, due), states in by_reminder.items():
            any_state = next(iter(states.values()))
            text = f"The reminder \"{any_state['text']}\" was due at {self.hhmm(due)}"
            end = due
            if "done" in states:
                end = states["done"]["t"]
                text += f" and ended at {self.hhmm(end)}. {states['done'].get('note') or 'It was marked as done by hand.'}"
                state = "done"
            elif "missed" in states:
                end = states["missed"]["t"]
                text += f" and had not been marked as done by {self.hhmm(end)}."
                state = "missed"
            else:
                text += " and has not been marked as done."
                state = "due"
            entry("reminder", due, max(due, end), text, "the relay's reminder list", 99, place=any_state.get("place", ""), state=state)

        last_spot = {}
        for r in rows:
            if r["k"] != "item":
                continue
            name, were = r["name"], "were" if r["name"].endswith("s") else "was"
            near = f" near the {r['near']}" if r.get("near") else f" in the {_nice(r['z'])}" if r.get("z") else ""
            before = last_spot.get((r["z"], name))
            if before:
                moved = f", about {math.dist(before['p'], r['p']):.0f} m from where {'they' if were == 'were' else 'it'} {were} seen at {self.hhmm(before['t'])}" \
                    if before.get("p") and r.get("p") else ""
                text = f"The {name} {were} seen in a different spot{near} at {self.hhmm(r['t'])}{moved}."
            else:
                text = f"The {name} {were} seen{near} at {self.hhmm(r['t'])}."
            last_spot[(r["z"], name)] = r
            entry("item", r["t"], r["t"], text, "the item finder on a position fix", place=r.get("near", ""), zone=r.get("z", ""),
                  item=name, moved=bool(before))

        order = {"gap": 0, "room": 1, "place": 2, "walk": 3}
        out.sort(key=lambda e: (e["start"], order.get(e["kind"], 4), e["end"]))
        return out

    def between(self, start, end):
        a, b = self.hhmm(start), self.hhmm(end)
        return f"at {a}" if a == b else f"between {a} and {b}"

    # ------------------------------------------------------------------------- the day, in sentences
    def label(self, day, part, now):
        if day == self.day_of(now):
            return f"this {part}" if part else "today"
        if day == self.day_of(now - 86400):
            return f"yesterday {part}" if part else "yesterday"
        return f"on {day}" + (f" in the {part}" if part else "")

    def window(self, day, part):
        """Seconds at which a part of a day starts and ends."""
        wanted = datetime.fromisoformat(day + "T12:00:00")
        guess = wanted.timestamp()  # noon on this machine's clock; the home may be in another time zone
        noon = guess + (wanted - self.clock(guess).replace(tzinfo=None)).total_seconds()
        first, last = PARTS[part] if part else (0, 24)
        return noon + (first - 12) * 3600, noon + (last - 12) * 3600

    def sentences(self, day=None, for_="wearer", part=None, now=None):
        """The summary as a list of sentences. Fixed templates, no language model."""
        now = self.now() if now is None else now
        day = day or self.day_of(now)
        lo, hi = self.window(day, part)
        entries = [e for e in self.timeline(day, for_, now) if e["end"] >= lo and e["start"] < hi]
        v, carer = self.voice(for_), for_ != "wearer"
        label = self.label(day, part, now)
        if not entries:
            return [f"I have nothing recorded for {label}." if not carer else f"Nothing is recorded for {label}."]
        unsure = [e for e in entries if e["confidence_pct"] is not None and e["confidence_pct"] < SPEAK_PCT]
        if not carer:  # the wearer is told only what the log is sure enough of; the carer's page shows all of it
            entries = [e for e in entries if e not in unsure]
        kinds = lambda kind: [e for e in entries if e["kind"] == kind]  # noqa: E731
        out = []
        rooms, places, gaps = kinds("room"), kinds("place"), kinds("gap")

        def spans(group):
            return _list([self.span(e["start"], e["end"]) for e in group])

        if carer and rooms:
            known = sum(e["end"] - e["start"] for e in rooms)
            out.append(f"Position was known for {_duration(known)} {self.between(rooms[0]['start'], rooms[-1]['end'])}, "
                       f"from {sum(e['fixes'] for e in rooms)} position fixes.")
        for zone in dict.fromkeys(e["zone"] for e in rooms):
            group = [e for e in rooms if e["zone"] == zone]
            if len(group) <= 2:
                out.append(f"{v['Sub']} {v['were']} in the {_nice(zone)} {spans(group)}.")
            else:
                out.append(f"{v['Sub']} {v['were']} in the {_nice(zone)} {_times(len(group))} {self.between(group[0]['start'], group[-1]['end'])}, "
                           f"for {_duration(sum(e['end'] - e['start'] for e in group))} in all.")
        parts = []
        for place in dict.fromkeys(e["place"] for e in places):
            group = [e for e in places if e["place"] == place]
            parts.append(f"near the {place} {spans(group)}" if len(group) <= 2 else
                         f"near the {place} {_times(len(group))}, last {self.span(group[-1]['start'], group[-1]['end'])}")
        if parts:
            out.append(f"{v['Sub']} {v['were']} {_list(parts)}.")
        if not rooms and not places and (kinds("gap") or not out and not carer and unsure):
            out.append(f"{'I' if not carer else 'The glasses'} could not tell where {v['sub']} {v['were']} {label}.")
        events = kinds("event")
        out += [e["text"] for e in events[:6 if carer else 3]]
        if len(events) > (6 if carer else 3):
            out.append(f"{len(events) - (6 if carer else 3)} more sensor or camera events are in the timeline.")
        if carer:  # how often a question was repeated is for the carer, not read back to the wearer
            questions = kinds("question")
            repeated = [e for e in questions if e["count"] > 1 or e["topic"]]
            out += [e["text"] for e in repeated[:4]]
            others = sum(e["count"] for e in questions if e not in repeated[:4])
            if others:
                out.append(f"{others} other question{'s were' if others != 1 else ' was'} asked.")
        for e in kinds("reminder"):
            if carer or e["state"] != "missed":  # a missed reminder is the carer's to follow up
                out.append(e["text"])
        items = kinds("item")
        for name in dict.fromkeys(e["item"] for e in items):
            group = [e for e in items if e["item"] == name]
            out += [e["text"] for e in group[-3:]] if carer else [group[-1]["text"]]
        if gaps and (rooms or places):
            longest = max(gaps, key=lambda e: e["end"] - e["start"])
            when = f"since {longest['from']}" if longest.get("ongoing") else self.span(longest["start"], longest["end"])
            if len(gaps) == 1:
                out.append(f"{'The glasses' if carer else 'I'} could not tell where {v['sub']} {v['were']} {when}.")
            else:
                out.append(f"There were {len(gaps)} stretches when {'the glasses' if carer else 'I'} could not tell where "
                           f"{v['sub']} {v['were']}, the longest {when}.")
        if carer and unsure:
            out.append(f"{len(unsure)} of these entries {'is' if len(unsure) == 1 else 'are'} uncertain (under {SPEAK_PCT}%): "
                       f"the timeline shows which.")
        return out

    def summary(self, day=None, for_="wearer", part=None, now=None):
        return " ".join(self.sentences(day, for_, part, now))

    # ------------------------------------------------------------------------- answers
    def names(self, day):
        """The places and rooms a question can be about: every name in the fixes of that day, today and yesterday."""
        places, zones = {}, {}
        for d in {day, self.day_of(self.now()), self.day_of(self.now() - 86400)}:
            for r in self.records(d):
                if r["k"] == "fix":
                    if r.get("z"):
                        zones[_nice(r["z"]).casefold()] = r["z"]
                    for name in r.get("w") or {}:
                        places[name.casefold()] = name
        return places, zones

    def answer(self, question, now=None):
        """Answer a question about the day in words, or None when the question is not about the day.

        "What did I do today?" (also this morning, this afternoon, this evening, yesterday),
        "When was I last in the kitchen?", "When did I last go to the front door?",
        "Did I go to the pill box today?". Returns {"answer", "about", "place", "entries"}.
        """
        now = self.now() if now is None else now
        words = _plain(question)
        day = self.day_of(now - 86400) if " yesterday " in words else self.day_of(now)
        part = next((p for p in PARTS if f" {p} " in words), "evening" if " tonight " in words else None)
        whole = (r" (what (did|have) i (do|done)|what have i been doing|what did i get up to|how (was|has|is) my (day|morning|afternoon|evening)( been)?"
                 r"|what happened (today|yesterday|this (morning|afternoon|evening))|tell me about (my day|today|yesterday|this (morning|afternoon|evening))"
                 r"|where (was i|have i been) (today|yesterday|this (morning|afternoon|evening))) ")
        if re.search(whole, words):
            return {"answer": self.summary(day, "wearer", part, now), "about": "day", "place": "", "entries": []}
        # Only being somewhere or going somewhere: "When did I take my pills?" is not a question for this log.
        verb = r"(go|went|gone|walk|walked|visit|visited|been|be|in|at|near|by|next to)"
        last = re.search(rf" when (was|did|have) i (last |most recently )?{verb} ", words)
        went = re.search(rf" (did|have|was) i (ever |already |just )?{verb} ", words)
        if not last and not went:
            return None
        places, zones = self.names(day)
        found = [(places[n], "place") for n in sorted(places, key=len, reverse=True) if f" {n} " in words]
        found += [(zones[n], "room") for n in sorted(zones, key=len, reverse=True) if f" {n} " in words]
        found += [(places[name], "place") for name, others in PLACE_WORDS.items() if name in places and any(f" {o} " in words for o in others)]
        if not found:
            return None
        name, kind = found[0]
        lo, hi = self.window(day, part)
        entries = [e for e in self.timeline(day, "wearer", now) if e["end"] >= lo and e["start"] < hi]
        visits = [e for e in entries if e["kind"] == kind and (e["place"] if kind == "place" else e["zone"]) == name]
        gaps = [e for e in entries if e["kind"] == "gap"]
        where = f"near the {name}" if kind == "place" else f"in the {_nice(name)}"
        label = self.label(day, part, now)
        doubtful = [e for e in visits if e["confidence_pct"] < SPEAK_PCT]
        visits = [e for e in visits if e not in doubtful]
        reply = {"about": kind, "place": name, "entries": (visits or doubtful)[-3:],
                 "confidence_pct": (visits or doubtful)[-1]["confidence_pct"] if visits or doubtful else None}
        if not visits and doubtful:  # seen, but not surely enough to say so flatly
            when = self.span(doubtful[-1]["start"], doubtful[-1]["end"])
            return {**reply, "answer": f"I may have seen you {where} {when}" + ("" if day == self.day_of(now) else " yesterday") + ", but I am not sure."}
        if not visits:
            unknown = (" I could not tell where you were for part of that time." if gaps else
                       "" if any(e["kind"] == "room" for e in entries) else " I have no position recorded for that time.")
            before = ""
            if last and not part and day == self.day_of(now):  # "when was I last ...": nothing today, so look at yesterday
                earlier = [e for e in self.timeline(self.day_of(now - 86400), "wearer", now) if e["kind"] == kind and
                           (e["place"] if kind == "place" else e["zone"]) == name and e["confidence_pct"] >= SPEAK_PCT]
                if earlier:
                    before = f" Yesterday you were last {where} {self.span(earlier[-1]['start'], earlier[-1]['end'])}."
            return {**reply, "answer": f"I have not seen you {where} {label}.{unknown}{before}"}
        newest = visits[-1]
        if last:
            if newest.get("ongoing"):
                text = f"You are {where} now." + (f" You have been there since {newest['from']}." if newest["from"] != self.hhmm(now) else "")
            else:
                text = f"You were last {where} {self.span(newest['start'], newest['end'])}" + ("." if day == self.day_of(now) else " yesterday.")
            return {**reply, "answer": text}
        if len(visits) <= 3:
            text = f"Yes. You were {where} {_list([self.span(e['start'], e['end']) for e in visits])} {label}."
        else:
            text = f"Yes. You were {where} {_times(len(visits))} {label}, last {self.span(newest['start'], newest['end'])}."
        return {**reply, "answer": text}

    def report(self, day=None, now=None):
        """Everything the carer's page needs for one day."""
        now = self.now() if now is None else now
        day = day or self.day_of(now)
        lo, hi = self.window(day, None)
        return {"day": day, "today": day == self.day_of(now), "start": lo, "end": hi, "now": now, "days": self.stored_days(),
                "timeline": self.timeline(day, "carer", now), "summary": {"wearer": self.sentences(day, "wearer", None, now),
                                                                         "carer": self.sentences(day, "carer", None, now)},
                "limits": {**self.limits, "walk_s": WALK_S, "join_s": JOIN_S, "speak_pct": SPEAK_PCT}}
