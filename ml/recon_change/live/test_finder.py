#!/usr/bin/env python3
"""Offline tests of "find me X" (live/finder.py and its relay hooks): the questions, the name matching, the checks a box
must pass, sightings from agreeing views, moves, the answer in words, and the relay end to end with a made-up gateway
and event API: /ask, the watch list in look_for, the day log, the event API's timeline, /navigate to the thing, /find.
No cluster, no pictures kept.

    python live/test_finder.py
"""
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finder as finds  # noqa: E402
import relay  # noqa: E402
from test_view_gate import FIX, jpeg, room  # noqa: E402

KEY = "test-key"
ZONE = "find-test"
PLACES = [{"name": "our table", "position": [1.2, 2.2, 0.7]}, {"name": "snack table", "position": [-2.0, 1.0, 0.7]},
          {"name": "IronShard booth", "position": [4.0, 4.0, 0.0]}, {"name": "drinks fridge", "position": [-4.0, -1.0, 0.0]}]
TABLE, SNACKS = [1.0, 2.0, 0.8], [-2.1, 1.1, 0.8]


def box(position, score=0.45, size=0.3, colour=0.6, name="orange box"):
    return {"name": name, "position": position, "score": score, "size_m": size, "colour": colour, "box": [0.4, 0.4, 0.5, 0.5]}


def test_questions():
    asked = {
        "find me the orange box": "orange box", "Find the orange box for me, please.": "orange box",
        "Where is the orange box?": "orange box", "Hey Meta, where's my orange box?": "orange box",
        "where did I put the orange box": "orange box", "Where did I leave my orange box?": "orange box",
        "Where have I put the orange box": "orange box", "can you find the red cup": "red cup", "look for my keys": "keys",
        "I lost my orange box": "orange box", "I can't find the orange box": "orange box", "have you seen my wallet": "wallet",
        "do you know where the orange box is": "orange box", "where are my glasses": "glasses",
        "where can I find the orange box": "orange box", "Locate the orange box now": "orange box",
    }
    for question, thing in asked.items():
        assert finds.object_in(question) == thing, (question, finds.object_in(question))
    for question in ("where am I", "where was I at 10", "where is it", "what did I do today", "did I take my pills",
                     "take me to the snack table", "where have I been today", "stop", "find", "where did I go"):
        assert finds.object_in(question) is None, (question, finds.object_in(question))


def test_names():
    names = ["orange box", "pill box", "snack table"]
    assert finds.match("the Orange Boxes", names) == "orange box"
    assert finds.match("orangebox", names) == "orange box"
    assert finds.match("orange", names) == "orange box"
    assert finds.match("box", names) is None  # two boxes: not a guess
    assert finds.match("orang box", names) == "orange box"
    assert finds.match("red cup", names) is None
    assert finds.key("My Glasses") == "glass" and finds.key("the boxes") == "box" and finds.key("keys") == "key"


def test_checks():
    f = finds.Finder()
    assert f.check(box(TABLE))[0]
    assert not f.check(box(None))[0]
    ok, why = f.check(box(TABLE, score=0.2))
    assert not ok and "score" in why, why
    ok, why = f.check(box(TABLE, size=1.4))
    assert not ok and "size" in why, why
    ok, why = f.check(box(TABLE, size=0.04))
    assert not ok and "size" in why, why
    ok, why = f.check(box(TABLE, colour=0.05))
    assert not ok and "colour" in why, why
    assert f.check({**box(TABLE), "size_m": None, "colour": None})[0]  # an older worker: no size, no colour
    assert f.check(box(TABLE, name="red cup", score=0.4, colour=None))[0]  # another name: the default limit 0.35
    assert not f.check(box(TABLE, name="red cup", score=0.3, colour=None))[0]


def test_sightings_and_moves():
    now = [1_000_000.0]
    f = finds.Finder(now=lambda: now[0])
    # one view is a maybe, not a sighting
    assert f.observe(ZONE, [box(TABLE)], PLACES, "j1", at=now[0]) == []
    assert f.sighting(ZONE, "orange box") is None and f.single_view(ZONE, "orange box")["job"] == "j1"
    assert f.target(ZONE, "orange box")["confirmed"] is False
    # an unwatched name and a rejected box change nothing
    assert f.observe(ZONE, [box(TABLE, name="teapot"), box(SNACKS, colour=0.0)], PLACES, "j2", at=now[0]) == []
    assert f.listing(ZONE)[0]["rejected"]["why"].startswith("colour"), f.listing(ZONE)
    # a second fix that agrees: a sighting, on our table
    now[0] += 5
    events = f.observe(ZONE, [box([1.1, 2.1, 0.8], score=0.5), box(TABLE, score=0.31)], PLACES, "j3", at=now[0])
    assert [e["kind"] for e in events] == ["seen"] and events[0]["near"] == "our table" and events[0]["views"] == 2, events
    assert "on our table" in events[0]["text"], events[0]["text"]
    s = f.sighting(ZONE, "orange box")
    assert abs(s["position"][0] - 1.1) < 0.11 and s["at"] == now[0] and s["score"] == 0.5
    # seen again in the same spot: no event, the time moves on
    now[0] += 30
    assert f.observe(ZONE, [box([1.05, 2.0, 0.8])], PLACES, "j4", at=now[0]) == []
    assert f.sighting(ZONE, "orange box")["at"] == now[0]
    # moved: one view elsewhere is only a maybe; the second agreeing view is the move
    now[0] += 60
    assert f.observe(ZONE, [box(SNACKS)], PLACES, "j5", at=now[0]) == []
    assert f.single_view(ZONE, "orange box")["job"] == "j5" and f.sighting(ZONE, "orange box")["near"] == "our table"
    now[0] += 6
    events = f.observe(ZONE, [box([-2.0, 1.2, 0.8])], PLACES, "j6", at=now[0])
    assert [e["kind"] for e in events] == ["moved"], events
    assert events[0]["text"].startswith("The orange box moved from our table to the snack table at "), events[0]["text"]
    assert events[0]["from_near"] == "our table" and events[0]["near"] == "snack table"
    assert f.target(ZONE, "orange box")["confirmed"] and f.target(ZONE, "orange box")["position"][0] < -1.5
    assert len(f.things[ZONE]["orange box"]["moves"]) == 1
    # older pictures that disagree with the newer sighting change nothing
    old = now[0] - 3600
    assert f.observe(ZONE, [box(TABLE)], PLACES, "p1", at=old, source="pictures") == []
    assert f.observe(ZONE, [box(TABLE)], PLACES, "p2", at=old, source="pictures") == []
    assert f.sighting(ZONE, "orange box")["near"] == "snack table"
    # kept across a restart
    path = Path(tempfile.mkdtemp(prefix="beacon-find-test-")) / "finds.json"
    f.path = path
    f.save()
    again = finds.Finder(path, now=lambda: now[0])
    assert again.sighting(ZONE, "orange box")["near"] == "snack table" and "orange box" in again.watched(ZONE)


def test_answers():
    now = [2_000_000.0]
    f = finds.Finder(now=lambda: now[0])
    clock = lambda t: finds.clock(t)  # noqa: E731
    got = f.answer(ZONE, "orange box", None, PLACES)
    assert got["answer"].startswith("I have not seen the orange box yet") and got["target"] is None, got
    got = f.answer(ZONE, "red cup", None, PLACES, looking=True)
    assert "looking for it now" in got["answer"], got
    f.observe(ZONE, [box(TABLE)], PLACES, "a1", at=now[0])
    got = f.answer(ZONE, "orange box", None, PLACES)
    assert got["answer"].startswith(f"I may have seen the orange box on our table at {clock(now[0])}, but only once."), got
    assert got["target"] == "orange box" and not got["confirmed"]
    f.observe(ZONE, [box(TABLE)], PLACES, "a2", at=now[0] + 4)
    # a fresh fix at the origin facing north: the table is 2.2 m ahead, a little right
    fix = {"state": "found", "position": [0.0, 0.0, 0.0], "heading_deg": 0, "at": now[0]}
    got = f.answer(ZONE, "orange box", fix, PLACES)
    assert got["answer"] == (f"The orange box is on our table, about 2 metres ahead on your right. I last saw it at "
                             f"{clock(now[0] + 4)}. Say take me there and I will guide you."), got["answer"]
    assert got["confirmed"] and got["target"] == "orange box" and got["near"] == "our table" and got["point"]["position"]
    # facing east: on the left; an old fix: no direction
    assert "on your left" in f.answer(ZONE, "orange box", {**fix, "heading_deg": 90}, PLACES)["answer"]
    now[0] += 120
    assert f.answer(ZONE, "orange box", fix, PLACES)["answer"].startswith("The orange box is on our table. I last saw it at")
    # right next to it; long ago
    assert "right by you" in f.answer(ZONE, "orange box", {**fix, "position": [1.0, 1.8, 0.0], "at": now[0]}, PLACES)["answer"]
    now[0] += 3 * 3600
    assert ", a while ago." in f.answer(ZONE, "orange box", None, PLACES)["answer"]
    # no named place near: only the way
    lone = finds.Finder(now=lambda: now[0])
    lone.observe(ZONE, [box([0.0, 6.0, 0.8])], PLACES, "b1", at=now[0])
    lone.observe(ZONE, [box([0.1, 6.0, 0.8])], PLACES, "b2", at=now[0])
    got = lone.answer(ZONE, "orange box", {**fix, "at": now[0]}, PLACES)
    assert got["answer"].startswith("The orange box is about 6 metres ahead. I last saw it at"), got["answer"]
    # the words for a place
    assert finds.at_place("our table", 0.3) == "on our table" and finds.at_place("snack table", 0.5) == "on the snack table"
    assert finds.at_place("IronShard booth", 0.5) == "at the IronShard booth" and finds.at_place("drinks fridge", 0.2) == "by the drinks fridge"
    assert finds.at_place("snack table", 1.3) == "near the snack table"
    assert finds.side_words(0) == "ahead" and finds.side_words(-45) == "ahead on your left" and finds.side_words(100) == "on your right"
    assert finds.side_words(170) == "behind you" and finds.metres(0.9) == "about 0.9 metres" and finds.metres(3.4) == "about 3 metres"


class Gateway:
    """The next fix it answers is whatever the test puts in self.next; it keeps every job it was sent."""

    def __init__(self):
        self.next, self.jobs, self.live = None, [], []

    def call(self, header, blob=b""):
        self.jobs.append(header)
        if header["op"] == "detect":  # the item finder alone on the newest photo (find me X, live)
            return {"ok": True, "job": f"live{len(self.jobs)}", "result": {"items_live": list(self.live), "seconds": {"items": 0.4}}}, b""
        if header["op"] == "locate" and header.get("grow") is False:  # a search of an older photo: nothing in it
            return {"ok": True, "job": f"old{len(self.jobs)}", "result": {**json.loads(json.dumps(self.next)), "items_candidates": []}}, b""
        if header["op"] == "locate":
            return {"ok": True, "job": f"fix{len(self.jobs)}", "result": json.loads(json.dumps(self.next))}, b""
        if header["op"] == "waypoints":
            return {"ok": True, "waypoints": []}, b""
        return {"ok": True, "zones": [ZONE], "workers": {}}, b""


class EventApi(BaseHTTPRequestHandler):
    posted = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.path.startswith("/events"):
            if self.headers.get("Authorization") != "Bearer token":  # the event API refuses events without its token
                return self.reply({"error": "unauthorised"}, 401)
            EventApi.posted += body
            return self.reply({"accepted": len(body)})
        return self.reply({"answer": "The event API answered.", "evidence": []})

    def do_GET(self):
        return self.reply({"events": [], "next_cursor": None})

    def reply(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def fix(boxes=(), position=(0.0, 0.0, 0.0), heading=0):
    made = json.loads(json.dumps(FIX))
    made.update(position=list(position), heading_deg=heading,
                waypoints=[{**p, "distance_m": 1, "bearing_deg": 0} for p in PLACES],
                items=[], items_candidates=list(boxes), items_looked_for=["orange box"])
    return made


def test_relay():
    gateway = Gateway()
    events_api = ThreadingHTTPServer(("127.0.0.1", 0), EventApi)
    threading.Thread(target=events_api.serve_forever, daemon=True).start()
    evidence = Path(tempfile.mkdtemp(prefix="beacon-find-relay-"))
    handler = relay.make_handler(gateway, evidence, KEY, f"http://127.0.0.1:{events_api.server_address[1]}/events",
                                 pick=None, api_token="token")
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    seed = [20]

    def call(path, body=None):
        request = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={"X-Key": KEY}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def send(made):
        gateway.next = made
        seed[0] += 1
        request = urllib.request.Request(f"{url}/locate?zone={ZONE}", data=jpeg(room(seed[0])), headers={"X-Key": KEY}, method="POST")
        return json.loads(urllib.request.urlopen(request, timeout=20).read())

    def ask(question):
        return call(f"/ask?zone={ZONE}", {"question": question})[1]

    send(fix())
    got = ask("Find me the orange box")
    assert got["answered_by"] == "finder" and got["answer"].startswith("I have not seen the orange box yet"), got
    got = ask("can you find the red cup?")
    assert got["answered_by"] == "finder" and "looking for it now" in got["answer"] and got["item"] == "red cup", got
    # the next fix looks for both
    send(fix())
    looked = [j for j in gateway.jobs if j["op"] == "locate"][-1]["look_for"]
    assert "orange box" in looked and "red cup" in looked, looked
    # a newly asked name is looked for in the latest photos too (background jobs that never become the wearer's fix)
    for _ in range(50):
        if sum(1 for j in gateway.jobs if j["op"] == "locate" and j.get("look_for") == ["red cup"]) >= 1:
            break
        time.sleep(0.1)
    assert any(j.get("look_for") == ["red cup"] and j.get("grow") is False for j in gateway.jobs), gateway.jobs
    # two fixes see the orange box on our table (and an orange thing that is too big): a sighting
    put_down = time.time()
    send(fix([box(TABLE), box([3.0, 0.0, 0.5], size=1.5)]))
    reply = send(fix([box([1.1, 2.1, 0.8], score=0.52)]))
    assert [e["kind"] for e in reply["found"]] == ["seen"], reply.get("found")
    got = ask("Where is the orange box?")
    assert got["answer"].startswith("The orange box is on our table, about 2 metres ahead on your right. I last saw it at"), got
    assert got["target"] == "orange box" and got["confirmed"] and got["point"]["position"], got
    assert got["seen_at"] >= put_down
    # "take me there": /navigate knows the thing
    status, way = call(f"/navigate?zone={ZONE}&to={urllib.parse.quote('orange box')}")
    assert status == 200 and way["to"] == "orange box" and way["distance_m"] > 1.5, (status, way)
    assert {"name": "orange box", "position": [1.05, 2.05, 0.8], "kind": "object"} in way["places"] or \
        any(p["name"] == "orange box" and p.get("kind") == "object" for p in way["places"]), way["places"]
    assert way["instruction"].startswith("The orange box is on our table, about 2 metres "), way["instruction"]
    # GET /find?what= (the app and the caretaker's page): always 200, with the outline of the newest photo
    status, got = call(f"/find?zone={ZONE}&what={urllib.parse.quote('the orange box')}")
    assert status == 200 and got["ok"] and got["found"] and got["confirmed"] and not got["looking"] and got["what"] == "orange box", got
    assert got["place"] == "our table" and got["moved_from"] is None and got["distance_m"] > 1.5 and isinstance(got["bearing_deg"], int), got
    assert got["answer"].startswith("The orange box is on our table") and not got["stale"] and got["at"] >= put_down, got
    assert got["box2d"] == [0.4, 0.4, 0.5, 0.5] and got["image"].endswith("/seen.jpg") and len(got["size_m"]) == 3, got
    assert all(0 < v < 1 for v in got["size_m"]) and len(got["position"]) == 3, got["size_m"]
    request = urllib.request.Request(url + got["image"], headers={"X-Key": KEY})
    assert urllib.request.urlopen(request, timeout=20).read()[:2] == b"\xff\xd8"  # the very photo the box is in
    status, got = call(f"/find?zone={ZONE}&what={urllib.parse.quote('green bottle')}")
    assert status == 200 and got["ok"] and not got["found"] and got["looking"] and got["position"] is None, got
    status, got = call(f"/waypoints?zone={ZONE}")
    assert any(w["name"] == "orange box" and w.get("kind") == "object" for w in got["waypoints"]), got
    # live: after a /find, each new photo also gets the item finder alone; its box outlines the newest photo
    gateway.live = [{"name": "orange box", "score": 0.41, "box": [0.2, 0.3, 0.35, 0.5], "accepted": False, "colour": 0.7},
                    {"name": "orange box", "score": 0.5, "box": [0.6, 0.6, 0.7, 0.7], "accepted": False, "colour": 0.05}]
    detects = sum(1 for j in gateway.jobs if j["op"] == "detect")
    send(fix([box(TABLE)]))
    for _ in range(50):
        status, got = call(f"/find?zone={ZONE}&what=orange+box")
        if got.get("live"):
            break
        time.sleep(0.1)
    assert sum(1 for j in gateway.jobs if j["op"] == "detect") == detects + 1, "one detect per new photo while asked"
    assert got["live"]["box2d"] == [0.2, 0.3, 0.35, 0.5] and got["box2d"] == [0.2, 0.3, 0.35, 0.5], got  # not the grey one
    assert got["live"]["image"].endswith("/seen.jpg") and got["live"]["latency_s"] < 5 and got["image"] == got["live"]["image"], got
    gateway.live = []
    # moved to the snack table
    send(fix([box(SNACKS)]))
    reply = send(fix([box([-2.0, 1.2, 0.8])]))
    assert [e["kind"] for e in reply["found"]] == ["moved"], reply.get("found")
    status, got = call(f"/find?zone={ZONE}&what=orange+box")
    assert got["found"] and got["place"] == "snack table" and got["moved_from"] == "our table", got
    got = ask("where did I put my orange box")
    assert got["answer"].startswith("The orange box is on the snack table") and "It was moved there from our table." in got["answer"], got
    # a place keeps the way to it; the five items keep their own answer
    assert ask("where is the snack table").get("answered_by") != "finder"
    assert ask("Where is the garage?").get("answered_by") != "finder" and finds.place_like("the garage")
    keys = ask("where are my keys")
    assert keys.get("item") == "keys" and keys.get("answered_by") != "finder", keys
    # the day log and the event API's timeline both have the sighting and the move, once each
    for _ in range(50):
        if len([e for e in EventApi.posted if e["object"] == "orange box"]) >= 2:
            break
        time.sleep(0.1)
    posted = [e for e in EventApi.posted if e["object"] == "orange box"]
    assert [e["type"] for e in posted] == ["object_appeared", "object_moved"], posted
    assert all(e["source"] == "vision" and e["zone"] == ZONE and e["id"].startswith("find-") and 0 <= e["confidence"] <= 1 for e in posted)
    day = call("/day")[1]
    said = [e["text"] for e in day["timeline"] if e["kind"] == "item" and "orange box" in e["text"]]
    assert len(said) == 2 and "near our table" in said[0] and "near the snack table" in said[1], said
    # the relay logs the same id it posted: pulling the event back changes nothing
    relay_log = relay.day_log.DayLog(evidence / "day")
    assert all(relay_log.event(e) is None for e in posted)
    # GET /find: the watch list, the sighting, the move and why the big orange thing was not taken
    status, got = call(f"/find?zone={ZONE}")
    thing = next(t for t in got["things"] if t["name"] == "orange box")
    assert status == 200 and "red cup" in got["watching"] and thing["sighting"]["near"] == "snack table" and len(thing["moves"]) == 1, got
    # POST /find: watch for a name without asking; forget it
    status, got = call(f"/find?zone={ZONE}&name=blue+mug", {})
    assert status == 200 and "blue mug" in got["watching"], got
    status, got = call(f"/find?zone={ZONE}&name=blue+mug&forget=1", {})
    assert "blue mug" not in got["watching"], got
    server.shutdown()
    events_api.shutdown()


def main():
    test_questions()
    test_names()
    test_checks()
    test_sightings_and_moves()
    test_answers()
    test_relay()
    print("find me X: all checks passed")


if __name__ == "__main__":
    main()
