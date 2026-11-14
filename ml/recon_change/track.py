#!/usr/bin/env python3
"""One steady position from a stream of position fixes, with a confidence percentage.

A position fix (locate.py) stands alone: nothing links it to the one before. This module keeps a
little state per room and turns the stream of fixes into one answer that does not jump and does
not flicker. Pure logic: no web code, no numpy, and the state is plain JSON, so the relay can keep
it across a restart.

    tracker = Tracker()
    answer = tracker.update(fix, now)   # a new fix; `now` is when its photo was taken (seconds)
    answer = tracker.current(now)       # between fixes: the same pose, older and less sure
    saved = tracker.dump()              # a JSON-ready dict; Tracker(saved) carries on from it

The rules, in the order they are applied to a new fix:

1. A fix that is "lost", has no position, or is for another room never moves the answer. It is a
   miss. LOST_AFTER misses in a row, or SILENCE_S without a usable fix, drop the answer ("lost").
2. A "found" fix that a person could have reached from the current answer (no faster than
   WALK_M_S and TURN_DEG_S, plus NOISE_M and NOISE_DEG of slack) is followed at once. If it differs
   from the answer by no more than the jitter of fixes (JITTER_M, JITTER_DEG) the two are averaged;
   a larger difference is real motion and is followed in full.
3. A "found" fix that no person could have reached does not move the answer. It waits. If the next
   fix agrees with it, the answer jumps there; if the next fix agrees with the old answer, it is
   forgotten.
4. An "unsure" fix counts only when it shows the same pose as the answer (within NOISE_M and
   NOISE_DEG). Then it keeps the answer alive and nudges it a little. Otherwise it is a miss.
5. After being lost, or at the very start, one good fix gives a pose but the state is "settling".
   It takes STEADY_AFTER fixes that agree to be "steady" again.

With no new photo nothing moves: the position stays and the confidence falls with time.

The confidence percentage is the chance that the answer is right now: position within 0.3 m and
heading within 15 degrees of the truth. It is a logistic model of things known at run time (the
match score, how many recent fixes agreed, misses, a waiting jump, the age, and how fast the
wearer was moving), fitted and checked against motion-capture truth in eval/TRACK_RESULTS.md.
fix_confidence_pct() is the same for one fix on its own.
"""
import json
import math

WALK_M_S, TURN_DEG_S = 1.2, 120.0  # faster than this between two fixes is not a person
NOISE_M, NOISE_DEG = 0.35, 20.0  # two fixes this close show the same pose, whatever the time between them
JITTER_M, JITTER_DEG = 0.15, 8.0  # differences this small are noise between fixes: averaged, not followed
LOST_AFTER = 3  # misses in a row before the answer is dropped
SILENCE_S = 30.0  # no usable fix for this long: dropped as well
STEADY_AFTER = 2  # fixes that agree before "settling" becomes "steady"
KEEP = 5  # how many recent fixes are remembered
SHOW_PCT = 50  # the display draws an arrow at or above this, and "Look around slowly" below

# Fitted by eval/track_eval.py (leave one room out; the numbers here are the fit on all three rooms).
# `pct = 100 / (1 + exp(-sum(weight * feature)))`, features as in features().
CAL = {"bias": 1.2, "score": 2.0, "agree": 1.0, "misses": -0.8, "waiting": -1.0, "settling": -0.5,
       "age_s": -0.12, "moved_m": -1.5, "turned_deg": -0.03}
FIX_CAL = {"bias": -3.0, "score": 8.0}


def wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


def _percent(logit):
    return int(round(100.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))))


def fix_confidence_pct(fix):
    """The chance that ONE fix, taken on its own, is right (within 0.3 m and 15 degrees), from its
    match score. 0 for a fix without a position."""
    if not fix or fix.get("position") is None:
        return 0
    return _percent(FIX_CAL["bias"] + FIX_CAL["score"] * float(fix.get("confidence") or 0.0))


def new_state(zone=None):
    return {"zone": zone, "state": "lost", "position": None, "heading_deg": None, "at": None, "seen_at": None,
            "score": 0.0, "run": 0, "misses": 0, "waiting": None, "recent": [], "speed_m_s": 0.0, "turn_deg_s": 0.0,
            "places": {}}


class Tracker:
    def __init__(self, state=None, zone=None):
        self.s = json.loads(json.dumps(state)) if state else new_state(zone)

    def dump(self):
        return json.loads(json.dumps(self.s))

    # ------------------------------------------------------------------ a new fix
    def update(self, fix, now):
        s = self.s
        if fix.get("zone") and s["zone"] and fix["zone"] != s["zone"]:
            return self.current(now)  # a fix for another room is not ours to hear about at all
        s["seen_at"] = now
        self._places(fix)
        score = float(fix.get("confidence") or 0.0)
        usable = fix.get("state") in ("found", "unsure") and fix.get("position") is not None and fix.get("heading_deg") is not None
        strong = usable and fix["state"] == "found"
        if not usable:
            return self._miss(now, "lost")
        pos, head = [float(v) for v in fix["position"]], float(fix["heading_deg"])
        if s["position"] is None:  # nothing to compare with: only a found fix may start an answer
            if not strong:
                return self._miss(now, "unsure")
            self._stand(pos, head, now, score, run=1)
            return self._note(now, "start")
        dt = max(0.0, now - s["at"])
        d, dh = math.dist(pos, s["position"]), abs(wrap(head - s["heading_deg"]))
        same = d <= NOISE_M and dh <= NOISE_DEG
        reachable = d <= NOISE_M + WALK_M_S * dt and dh <= NOISE_DEG + TURN_DEG_S * dt
        if strong and reachable:
            if dt > 0.3:  # how fast the wearer has been moving, for how quickly the answer goes stale
                s["speed_m_s"] = 0.5 * s["speed_m_s"] + 0.5 * d / dt
                s["turn_deg_s"] = 0.5 * s["turn_deg_s"] + 0.5 * dh / dt
            a, b = max(0.5, min(1.0, d / JITTER_M)), max(0.5, min(1.0, dh / JITTER_DEG))  # jitter is averaged, motion followed
            self._stand([p + a * (q - p) for p, q in zip(s["position"], pos)], s["heading_deg"] + b * wrap(head - s["heading_deg"]),
                        now, score, run=s["run"] + 1)
            return self._note(now, "agree", d, dh)
        if not strong and same:  # an unsure fix that shows the same pose: it counts, and nudges a little
            self._stand([p + 0.3 * (q - p) for p, q in zip(s["position"], pos)], s["heading_deg"] + 0.3 * wrap(head - s["heading_deg"]),
                        now, max(score, 0.0), run=s["run"] + 1)
            return self._note(now, "agree", d, dh)
        if not strong:
            return self._miss(now, "unsure")
        # A found fix that no person could have reached from the answer. It needs a second fix that agrees with it.
        w = s["waiting"]
        if w and math.dist(pos, w["position"]) <= NOISE_M + WALK_M_S * max(0.0, now - w["at"]) and \
                abs(wrap(head - w["heading_deg"])) <= NOISE_DEG + TURN_DEG_S * max(0.0, now - w["at"]):
            s["speed_m_s"] = s["turn_deg_s"] = 0.0
            self._stand(pos, head, now, score, run=2)
            return self._note(now, "jump", d, dh)
        s["waiting"] = {"position": pos, "heading_deg": head, "at": now}
        return self._miss(now, "elsewhere", keep_waiting=True)

    def _stand(self, pos, head, now, score, run):
        s = self.s
        s.update(position=[round(v, 3) for v in pos], heading_deg=round(wrap(head), 2), at=now, score=score, run=run,
                 misses=0, waiting=None, state="steady" if run >= STEADY_AFTER else "settling")

    def _miss(self, now, why, keep_waiting=False):
        s = self.s
        s["misses"] += 1
        if not keep_waiting and s["waiting"] and now - s["waiting"]["at"] > SILENCE_S:
            s["waiting"] = None
        if s["position"] is not None and s["misses"] >= LOST_AFTER:
            self._drop()
        return self._note(now, why)

    def _drop(self):
        w = self.s["waiting"]
        self.s.update(state="lost", position=None, heading_deg=None, at=None, run=0, speed_m_s=0.0, turn_deg_s=0.0)
        if w:  # what was waiting is the best guess left: the next fix that agrees with it makes it the answer
            self.s["waiting"] = w

    def _note(self, now, kind, d=None, dh=None):
        s = self.s
        s["recent"] = (s["recent"] + [{"at": now, "kind": kind, "d_m": None if d is None else round(d, 3),
                                       "d_deg": None if dh is None else round(dh, 1)}])[-KEEP:]
        return self.current(now)

    def _places(self, fix):
        """The room's named places, kept steady too: they do not move, so their positions are averaged."""
        if not isinstance(fix.get("waypoints"), list):
            return
        places, good = self.s["places"], fix.get("state") == "found"
        names = set()
        for w in fix["waypoints"]:
            if not w.get("name") or w.get("position") is None:
                continue
            names.add(w["name"])
            p = [float(v) for v in w["position"]]
            old = places.get(w["name"])
            if old is None:
                places[w["name"]] = p
            elif good:
                places[w["name"]] = [round(o + 0.3 * (q - o), 3) for o, q in zip(old, p)]
        for gone in [n for n in places if n not in names]:  # the carer removed it
            del places[gone]

    # ------------------------------------------------------------------ the answer
    def features(self, now):
        """What the confidence is made from. All of it is known at run time."""
        s = self.s
        age = max(0.0, now - s["at"]) if s["at"] is not None else 0.0
        recent = s["recent"]
        return {"bias": 1.0, "score": s["score"],
                "agree": sum(r["kind"] in ("agree", "start", "jump") for r in recent) / KEEP,
                "misses": float(s["misses"]), "waiting": 1.0 if s["waiting"] else 0.0,
                "settling": 1.0 if s["state"] == "settling" else 0.0, "age_s": min(age, 60.0),
                "moved_m": min(s["speed_m_s"] * age, 5.0), "turned_deg": min(s["turn_deg_s"] * age, 180.0)}

    def current(self, now):
        """The answer at time `now`. Never changes the pose: with no new photo it only gets older."""
        s = self.s
        age = None if s["at"] is None else round(max(0.0, now - s["at"]), 1)
        lost = s["position"] is None or now - s["at"] > SILENCE_S
        agree = sum(r["kind"] in ("agree", "start", "jump") for r in s["recent"])
        if lost:
            return {"state": "lost", "position": None, "heading_deg": None, "confidence_pct": 0, "show": False,
                    "agree": agree, "of": len(s["recent"]), "age_s": age, "waiting": bool(s["waiting"]),
                    "waypoints": [{"name": n, "distance_m": None, "bearing_deg": None, "position": [round(v, 2) for v in p]}
                                  for n, p in s["places"].items()]}
        f = self.features(now)
        pct = _percent(sum(CAL[k] * f[k] for k in CAL))
        x, y, heading = s["position"][0], s["position"][1], s["heading_deg"]
        ways = []
        for name, p in s["places"].items():
            east, north = p[0] - x, p[1] - y
            ways.append({"name": name, "distance_m": round(math.hypot(east, north), 1),
                         "bearing_deg": int(round(wrap(math.degrees(math.atan2(east, north)) - heading))),  # 0 ahead, + to the right
                         "position": [round(v, 2) for v in p]})
        return {"state": s["state"], "position": [round(v, 2) for v in s["position"]], "heading_deg": int(round(heading)),
                "confidence_pct": pct, "show": s["state"] == "steady" and pct >= SHOW_PCT,
                "agree": agree, "of": len(s["recent"]), "age_s": age, "waiting": bool(s["waiting"]), "waypoints": ways}
