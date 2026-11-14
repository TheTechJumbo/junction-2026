#!/usr/bin/env python3
"""Offline test of day.py: visits, gaps, joins, answers, wording and the file on disk.

    python eval/test_day.py

No cluster, no relay, no network. The stream is synthetic: a morning in a kitchen with a pill
box 3 m from a front door, written by hand so every expected sentence is known.
"""
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import day  # noqa: E402

DAY = "2026-11-14"
UTC = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc)  # noqa: E731  the "home" of this test is on UTC
# Words the log must never use: it reports what was seen, not what it means or why.
BANNED = re.compile(r"\b(took|taken|swallow\w*|ate|forgot\w*|because|should|must have|probably|maybe|confus\w*|wander\w*|"
                    r"did not go|were not|was not there|never went|not there)\b", re.I)


def at(clock, date=DAY):
    """'08:14' or '08:14:30' on the test day -> seconds since 1970."""
    return datetime.fromisoformat(f"{date}T{clock}{':00' if len(clock) == 5 else ''}+00:00").timestamp()


def fix(state, pill=None, door=None, zone="kitchen"):
    ways = [{"name": n, "distance_m": d, "bearing_deg": 0, "position": [0, 0, 0]} for n, d in (("pill box", pill), ("front door", door)) if d is not None]
    # What the relay keeps for a fix also has pictures and a map: none of that may reach the log.
    return {"state": state, "confidence": 0.9, "zone": zone, "position": [1.0, 2.0, 0.0], "heading_deg": 10, "waypoints": ways,
            "images": {"view": "/evidence/job1/view.jpg"}, "map": {"points": [[0, 0, 0]]}, "splat": "/evidence/job1/room.splat"}


def walk(log, start, end, pill, door, every=6, state="found", zone="kitchen"):
    t = at(start)
    while t <= at(end):
        log.fix(fix(state, pill, door, zone), at=t)
        t += every


def fill(log):
    """The test day. Kitchen 08:10 to 08:40, pill box 08:14 to 08:16, front door 08:17 to 08:19,
    nothing from 08:40 to 10:00, kitchen again 10:00 to 10:20."""
    walk(log, "08:10", "08:13:54", 4.0, 5.0)
    walk(log, "08:14", "08:16", 0.8, 3.2)
    walk(log, "08:16:06", "08:16:54", 2.6, 2.4)
    walk(log, "08:17", "08:19", 3.4, 0.9)
    walk(log, "08:19:06", "08:40", 4.0, 5.0)
    walk(log, "10:00", "10:20", 4.0, 5.0)
    log.event({"id": "e1", "type": "pillbox_open", "source": "sensor", "ts": f"{DAY}T08:15:00+00:00", "zone": "kitchen", "object": "pillbox", "confidence": 1})
    log.event({"id": "e2", "type": "door_open", "source": "sensor", "ts": f"{DAY}T09:10:00+00:00", "zone": "entrance", "object": "front_door", "confidence": 1})
    log.event({"id": "e3", "type": "object_moved", "source": "vision", "ts": f"{DAY}T10:05:00+00:00", "zone": "kitchen", "object": "keys", "confidence": 0.6})
    for clock in ("10:00", "10:09", "10:20"):
        log.question("Where are my keys?", at=at(clock), topic="keys")
    log.question("Did I take my pills?", at=at("10:12"))
    log.item("keys", at=at("08:14"), near="pill box", zone="kitchen", position=[1.0, 2.0, 0.0])
    log.item("keys", at=at("08:15"), near="pill box", zone="kitchen", position=[1.1, 2.0, 0.0])  # the same spot: no new entry
    log.item("keys", at=at("10:06"), near="front door", zone="kitchen", position=[1.0, 5.0, 0.0])
    pills = {"id": "r1", "text": "Time to take your pills", "place": "pill box", "due_at": at("08:00")}
    water = {"id": "r2", "text": "Drink some water", "place": "", "due_at": at("09:00")}
    log.reminders([{**pills, "state": "due"}], at=at("08:00:05"))
    log.reminders([{**pills, "state": "due"}], at=at("08:05"))  # nothing changed: recorded once
    log.reminders([{**pills, "state": "done", "done_note": "The pill box was opened at 08:15."}, {**water, "state": "later"}], at=at("08:15:10"))
    log.reminders([{**water, "state": "due"}], at=at("09:00:05"))
    log.reminders([{**water, "state": "missed"}], at=at("11:00:05"))


def main():
    folder = Path(tempfile.mkdtemp(prefix="day-test-"))
    clock = {"now": at("11:30")}
    log = day.DayLog(folder, clock=UTC, now=lambda: clock["now"])
    assert log.timeline() == [] and log.summary() == "I have nothing recorded for today.", log.summary()
    assert log.answer("When was I last in the kitchen?") is None  # no place of that name is known yet
    fill(log)
    entries = log.timeline()
    kinds = lambda kind, e=None: [x for x in (entries if e is None else e) if x["kind"] == kind]  # noqa: E731
    texts = [e["text"] for e in entries]

    # Visits from the stream.
    rooms, places = kinds("room"), kinds("place")
    assert [(e["zone"], e["from"], e["to"]) for e in rooms] == [("kitchen", "08:10", "08:40"), ("kitchen", "10:00", "10:20")], rooms
    assert [(e["place"], e["from"], e["to"]) for e in places] == [("pill box", "08:14", "08:16"), ("front door", "08:17", "08:19")], places
    assert "The wearer was near the pill box from 08:14 to 08:16." in texts and "The wearer was in the kitchen from 08:10 to 08:40." in texts
    assert places[0]["fixes"] == 21 and "21 position fixes" in places[0]["based_on"] and "closest 0.8 m" in places[0]["based_on"], places[0]
    walks = kinds("walk")
    assert len(walks) == 1 and walks[0]["text"] == "The wearer went from the pill box to the front door between 08:16 and 08:17.", walks
    # A gap is "no information": the kitchen visit ends at its last found fix, nothing is said about the gap.
    gaps = kinds("gap")
    assert [(e["from"], e["to"], e["text"]) for e in gaps] == [("08:40", "10:00", "No information from 08:40 to 10:00."),
                                                              ("10:20", "11:30", "No information since 10:20.")], gaps
    assert gaps[0]["based_on"] == "no photos arrived"
    for e in entries:
        assert set(e) >= {"kind", "start", "end", "from", "to", "place", "zone", "text", "based_on"} and e["start"] <= e["end"], e
    assert entries == sorted(entries, key=lambda e: e["start"]), "the timeline is in time order"

    # Joins with where the wearer was.
    events = kinds("event")
    assert events[0]["text"] == "The pill box was opened at 08:15 while the wearer was next to it.", events[0]
    assert events[1]["text"] == "The front door was opened at 09:10. It is not known where the wearer was at that time.", events[1]
    assert events[2]["text"] == "The camera saw the keys in a different spot at 10:05 while the wearer was in the kitchen.", events[2]
    assert events[0]["joined"] == "same" and events[1]["joined"] == "unknown" and "confidence 0.6" in events[2]["based_on"]
    questions = kinds("question")
    assert [e["text"] for e in questions] == ["The wearer asked where the keys are three times between 10:00 and 10:20.",
                                              "The wearer asked \"Did I take my pills?\" at 10:12."], questions
    assert questions[0]["count"] == 3 and questions[0]["zone"] == "kitchen"
    reminders = kinds("reminder")
    assert reminders[0]["text"] == "The reminder \"Time to take your pills\" was due at 08:00 and ended at 08:15. The pill box was opened at 08:15.", reminders
    assert reminders[1]["text"] == "The reminder \"Drink some water\" was due at 09:00 and had not been marked as done by 11:00." and reminders[1]["state"] == "missed"
    items = kinds("item")
    assert [e["text"] for e in items] == ["The keys were seen near the pill box at 08:14.",
                                          "The keys were seen in a different spot near the front door at 10:06, about 3 m from where they were seen at 08:14."], items
    wearer_view = log.timeline(for_="wearer")
    assert "The pill box was opened at 08:15 while you were next to it." in [e["text"] for e in wearer_view]
    assert "The front door was opened at 09:10. I do not know where you were at that time." in [e["text"] for e in wearer_view]

    # The two summaries.
    wearer, carer = log.summary(for_="wearer"), log.summary(for_="carer")
    assert wearer.startswith("You were in the kitchen from 08:10 to 08:40 and from 10:00 to 10:20. "
                             "You were near the pill box from 08:14 to 08:16 and near the front door from 08:17 to 08:19. "
                             "The pill box was opened at 08:15 while you were next to it."), wearer
    assert "three times" not in wearer and "Drink some water" not in wearer, "repeats and missed reminders are for the carer"
    assert "There were 2 stretches when I could not tell where you were, the longest from 08:40 to 10:00." in wearer, wearer
    assert carer.startswith("Position was known for about 50 minutes between 08:10 and 10:20, from 502 position fixes."), carer
    assert "The wearer asked where the keys are three times between 10:00 and 10:20." in carer and "1 other question was asked." in carer, carer
    assert "had not been marked as done by 11:00" in carer
    assert log.summary(part="afternoon") == "I have nothing recorded for this afternoon."
    named = day.DayLog(folder, clock=UTC, now=lambda: clock["now"], name="Anna")
    assert "Anna was near the pill box from 08:14 to 08:16." in [e["text"] for e in named.timeline()]

    # Every answer the wearer can ask for.
    say = lambda q: (log.answer(q) or {}).get("answer")  # noqa: E731
    assert say("What did I do today?") == wearer
    morning = say("What did I do this morning?")
    assert morning.startswith("You were in the kitchen from 08:10 to 08:40 and from 10:00 to 10:20.") and "from 08:40 to 10:00" in morning, morning
    assert say("How was my day?") == wearer
    assert say("When was I last in the kitchen?") == "You were last in the kitchen from 10:00 to 10:20."
    assert say("When did I last go to the front door?") == "You were last near the front door from 08:17 to 08:19."
    assert say("Did I go to the pill box today?") == "Yes. You were near the pill box from 08:14 to 08:16 today."
    assert say("Have I been to the kitchen this afternoon?") == "I have not seen you in the kitchen this afternoon. I have no position recorded for that time."
    assert say("What did I do yesterday?") == "I have nothing recorded for yesterday."
    for other in ("Did I take my pills?", "When did I take my pills?", "Where is the pill box?", "Where are my keys?", "Did I lock the door?",
                  "When was I last in the garage?", "What time is it?", "Did I open the pill box today?"):
        assert log.answer(other) is None, other  # not about where the wearer was: left to the relay and the event API
    clock["now"] = at("10:20:30")  # asked while still in the kitchen
    assert say("When was I last in the kitchen?") == "You are in the kitchen now. You have been there since 10:00."
    clock["now"] = at("11:30")

    # No visit from lost or unsure fixes, even ones that claim to be next to a place.
    doubt = day.DayLog(Path(tempfile.mkdtemp(prefix="day-test-")), clock=UTC, now=lambda: clock["now"])
    walk(doubt, "09:00", "09:05", 0.5, 0.5, state="lost")
    walk(doubt, "09:05:06", "09:10", 0.5, 0.5, state="unsure")
    seen = doubt.timeline()
    assert [e["kind"] for e in seen] == ["gap"] and seen[0]["based_on"] == "101 photos arrived, none gave a position", seen
    assert doubt.summary() == "I could not tell where you were today." and doubt.answer("Did I go to the pill box today?") is None
    assert doubt.summary(for_="carer") == "The glasses could not tell where the wearer was today."
    # One found fix, then lost ones that look near: the visit is that one fix and no longer.
    doubt.fix(fix("found", 0.6, 4.0), at=at("09:20"))
    walk(doubt, "09:20:06", "09:22", 0.6, 4.0, state="lost")
    visit = [e for e in doubt.timeline() if e["kind"] == "place"]
    assert len(visit) == 1 and visit[0]["text"] == "The wearer was near the pill box at 09:20." and visit[0]["fixes"] == 1, visit
    assert doubt.answer("Did I go to the front door today?")["answer"] == "I have not seen you near the front door today. I could not tell where you were for part of that time."

    # Slack: one fix beyond LEAVE_M does not split a visit, a fix between ENTER_M and LEAVE_M keeps it, two beyond end it.
    def visits(distances, **limits):
        fixes = [{"t": 6.0 * i, "s": s, "z": "k", "w": {"p": d}} for i, (s, d) in enumerate(distances)]
        return day.build_visits(fixes, **limits)["places"]

    F = lambda *ds: [("found", d) for d in ds]  # noqa: E731
    assert [(v["start"], v["end"]) for v in visits(F(1.0, 1.2, 2.6, 1.1, 1.0))] == [(0.0, 24.0)]
    assert [(v["start"], v["end"]) for v in visits(F(1.0, 1.9, 1.8, 1.0))] == [(0.0, 18.0)]
    assert [(v["start"], v["end"]) for v in visits(F(1.0, 1.2, 2.6, 2.7, 1.0))] == [(0.0, 6.0), (24.0, 24.0)]
    assert visits(F(1.8, 1.9, 1.7)) == [], "between ENTER_M and LEAVE_M does not start a visit"
    assert [(v["start"], v["end"]) for v in visits(F(1.0) + [("lost", 9.0)] * 5 + F(1.0))] == [(0.0, 36.0)], "lost fixes do not end a visit"
    assert [(v["start"], v["end"]) for v in visits(F(1.0) + [("lost", 0.1)] * 12 + F(1.0))] == [(0.0, 0.0), (78.0, 78.0)], "a long silence does"
    assert [(v["start"], v["end"]) for v in visits(F(1.0, 2.6, 1.0), out_fixes=1)] == [(0.0, 0.0), (12.0, 12.0)]
    rooms2 = day.build_visits([{"t": 0, "s": "found", "z": "kitchen"}, {"t": 6, "s": "found", "z": "hall"}, {"t": 12, "s": "found", "z": "hall"}])["rooms"]
    assert [(r["zone"], r["start"], r["end"]) for r in rooms2] == [("kitchen", 0, 0), ("hall", 6, 12)]

    # The wording rule, on everything the log can say about this day.
    said = texts + [e["text"] for e in wearer_view] + [wearer, carer, morning] + [e["text"] for e in doubt.timeline()] + \
        [say(q) for q in ("When was I last in the kitchen?", "Did I go to the pill box today?", "Have I been to the kitchen this afternoon?")]
    for text in said:
        assert not BANNED.search(text), f"not an observation: {text}"
        assert text[0].isupper() and text.rstrip()[-1] == ".", text

    # The file: one per day, survives a restart, holds no pictures.
    files = sorted(p.name for p in folder.iterdir())
    assert files == [f"{DAY}.jsonl"], files
    raw = (folder / files[0]).read_text(encoding="utf-8")
    assert not re.search(r"jpg|png|splat|evidence|image|points", raw), "pictures or a map reached the day file"
    assert all(set(json.loads(line)) <= {"k", "t", "s", "z", "w", "p", "type", "source", "object", "id", "c", "q", "topic", "state", "due",
                                         "text", "place", "note", "name", "near"} for line in raw.splitlines())
    again = day.DayLog(folder, clock=UTC, now=lambda: clock["now"])
    assert again.timeline() == entries and again.summary(for_="carer") == carer, "the day changed across a restart"
    assert again.event({"id": "e1", "type": "pillbox_open", "source": "sensor", "ts": f"{DAY}T08:15:00+00:00"}) is None, "an event id is stored once"
    assert again.reminders([{"id": "r2", "text": "Drink some water", "place": "", "due_at": at("09:00"), "state": "missed"}]) == []
    assert again.last_event_time() == at("10:05")
    with open(folder / files[0], "a", encoding="utf-8") as sink:
        sink.write('{"k":"fix","t":17')  # a line cut short by a crash
    assert day.DayLog(folder, clock=UTC, now=lambda: clock["now"]).timeline() == entries
    report = again.report()
    assert json.loads(json.dumps(report)) == report and report["day"] == DAY and report["days"] == [DAY]
    assert report["end"] - report["start"] == 86400 and report["start"] == at("00:00")

    # Days: yesterday can be asked for, and only keep_days files are kept.
    clock["now"] = at("09:00", "2026-11-15")
    assert again.answer("What did I do yesterday?")["answer"].startswith("You were in the kitchen from 08:10 to 08:40 and from 10:00 to 10:20.")
    assert again.answer("When was I last in the kitchen?")["answer"] == ("I have not seen you in the kitchen today. I have no position "
                                                                         "recorded for that time. Yesterday you were last in the kitchen from 10:00 to 10:20.")

    # The stabiliser's answer ("stable", from track.py) is used when the relay adds it, the raw fix when it does not.
    steady = day.DayLog(Path(tempfile.mkdtemp(prefix="day-test-")), clock=UTC, now=lambda: clock["now"])
    way = lambda d: [{"name": "pill box", "distance_m": d, "bearing_deg": 0, "position": [0, 0, 0]}]  # noqa: E731
    raw = fix("found", 0.5, 4.0)
    steady.fix({**raw, "stable": {"state": "settling", "position": [1, 2, 0], "heading_deg": 0, "confidence_pct": 40, "waypoints": way(0.5)}}, at=at("12:00"))
    assert steady.timeline(now=at("12:00")) and all(e["kind"] == "gap" for e in steady.timeline(now=at("12:00"))), "settling starts no visit"
    for i, pct in enumerate((90, 80, 70)):
        steady.fix({**raw, "stable": {"state": "steady", "position": [1, 2, 0], "heading_deg": 0, "confidence_pct": pct, "waypoints": way(0.9)}},
                   at=at("12:00") + 6 * (i + 1))
    steady.fix({**fix("lost"), "stable": {"state": "steady", "position": [1, 2, 0], "heading_deg": 0, "confidence_pct": 55, "waypoints": way(0.9)}},
               at=at("12:00") + 24)  # the raw fix missed, the stabiliser still holds the pose, less sure
    steady.fix({**raw, "stable": {"state": "lost", "position": None, "heading_deg": None, "confidence_pct": 0}}, at=at("12:00") + 30)
    visit = [e for e in steady.timeline(now=at("12:01")) if e["kind"] == "place"]
    assert len(visit) == 1 and visit[0]["fixes"] == 4 and "closest 0.9 m" in visit[0]["based_on"], visit
    assert visit[0]["confidence_pct"] == day.confidence_pct(4, (0.9 + 0.8 + 0.7 + 0.55) / 4, 6, closest=0.9) < 74, visit[0]["confidence_pct"]

    # Confidence: more fixes raise it, a hole inside the visit and a closest fix right at the limit lower it.
    one, many = day.confidence_pct(1, 0.95, 0, closest=0.6), day.confidence_pct(20, 0.95, 6, closest=0.6)
    assert one < day.confidence_pct(2, 0.95, 6, closest=0.6) < many <= 95, (one, many)
    assert day.confidence_pct(20, 0.95, 60, closest=0.6) < many and day.confidence_pct(20, 0.95, 6, closest=1.5) < many
    assert day.confidence_pct(3, 0.5, 0) < day.confidence_pct(3, 0.9, 0) and 1 <= day.confidence_pct(1, 0.0, 600, closest=9) <= 99
    assert all(isinstance(e["confidence_pct"], int) for e in entries if e["kind"] in ("room", "place", "walk", "event", "question", "reminder"))
    assert all(e["confidence_pct"] is None for e in entries if e["kind"] in ("gap", "item"))
    assert day.SPEAK_PCT <= events[0]["confidence_pct"] < 99 and events[1]["confidence_pct"] == 99, [e["confidence_pct"] for e in events]
    assert events[2]["confidence_pct"] < day.SPEAK_PCT, "a camera event is never surer than the camera said (0.6 at most)"
    assert "camera saw the keys" not in wearer and "The camera saw the keys in a different spot at 10:05" in carer
    # A visit the log is not sure of is left out of what is said to the wearer, and hedged when asked about.
    weak = day.DayLog(Path(tempfile.mkdtemp(prefix="day-test-")), clock=UTC, now=lambda: clock["now"])
    walk(weak, "08:00", "08:10", 4.0, 5.0)
    weak.fix(fix("found", 1.5, 5.0), at=at("08:10:06"))  # one fix, right at the limit
    walk(weak, "08:10:12", "08:20", 4.0, 5.0)
    weak.event({"id": "v1", "type": "object_moved", "source": "vision", "ts": "2026-11-15T08:15:00+00:00", "zone": "kitchen", "object": "keys", "confidence": 0.6})
    clock["now"] = at("08:30", "2026-11-15")
    spoken, shown = weak.summary(now=at("08:30")), weak.summary(for_="carer", now=at("08:30"))
    doubtful = [e for e in weak.timeline(now=at("08:30")) if e["kind"] == "place"]
    assert len(doubtful) == 1 and doubtful[0]["confidence_pct"] < day.SPEAK_PCT, doubtful
    assert "pill box" not in spoken and "The wearer was near the pill box at 08:10." in shown and "1 of these entries is uncertain (under 60%)" in shown, (spoken, shown)
    assert weak.answer("Did I go to the pill box today?", now=at("08:30"))["answer"] == "I may have seen you near the pill box at 08:10, but I am not sure."
    clock["now"] = at("09:00", "2026-11-15")
    assert all("ongoing" not in e or not e["ongoing"] for e in again.timeline(DAY)) and len([e for e in again.timeline(DAY) if e["kind"] == "gap"]) == 1
    short = day.DayLog(folder, clock=UTC, now=lambda: clock["now"], keep_days=3)
    short.fix(fix("found", 4.0, 5.0), at=clock["now"])
    assert short.stored_days() == [DAY, "2026-11-15"]
    clock["now"] = at("09:00", "2026-11-17")
    short.fix(fix("found", 4.0, 5.0), at=clock["now"])
    assert short.stored_days() == ["2026-11-15", "2026-11-17"], short.stored_days()
    print("day log: all checks passed")


if __name__ == "__main__":
    main()
