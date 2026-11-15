#!/usr/bin/env python3
"""Offline test of the day log in the relay: /locate feeds it, /ask is answered from it, /day reads it.

The relay's handler is the real one; the gateway and the event API are the stand-ins of
test_care_flow.py. No cluster.

    python live/test_day_flow.py
"""
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402
from test_care_flow import FIX, JPEG, KEY, TOKEN, EventApi, Gateway, serve  # noqa: E402


def main():
    relay.cv2 = None
    EventApi.events[:] = []
    EventApi.asked[:] = []
    api_server, api_url = serve(EventApi)
    evidence = Path(tempfile.mkdtemp(prefix="beacon-test-"))
    gateway = Gateway()
    handler = relay.make_handler(gateway, evidence, KEY, f"{api_url}/events", pick=None, api_token=TOKEN)
    handler.log_message = lambda *args: None
    relay_server, url = serve(handler)

    def call(path, body=None, raw=None, parse=True):
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url + path, data=data, headers={"X-Key": KEY}, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read()) if parse else response.read()
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")

    # No key, no day.
    try:
        urllib.request.urlopen(url + "/day", timeout=5)
        raise AssertionError("a request without the key was let in")
    except urllib.error.HTTPError as err:
        assert err.code == 403

    # An empty day is still a day. The page is served.
    code, body = call("/day")
    assert code == 200 and body["timeline"] == [] and body["summary"]["wearer"] == ["I have nothing recorded for today."], body
    assert call("/day?date=tomorrow")[0] == 400 and call("/day?date=2026-13-45")[0] == 400
    code, page = call("/dayview", parse=False)
    assert code == 200 and b"<title>Beacon Day Log</title>" in page
    # Before any fix the log knows no place, so a day question is not its to answer: it goes on to the event API.
    code, body = call("/ask?zone=kitchen", {"question": "Did I go to the pill box today?"})
    assert code == 200 and "answered_by" not in body, body

    # Fixes next to the pill box: a room visit and a place visit, and the sensor's event joined to it.
    near = {**FIX, "waypoints": [{**FIX["waypoints"][0], "distance_m": 0.8}, FIX["waypoints"][1]]}
    gateway.fix = near
    for _ in range(3):
        code, fix = call("/locate?zone=kitchen", raw=JPEG)
        assert code == 200 and fix["state"] == "found"
    gateway.fix = {**near, "state": "lost", "confidence": 0.1}  # a lost fix that still claims to be next to the front door
    gateway.fix["waypoints"] = [near["waypoints"][0], {**FIX["waypoints"][1], "distance_m": 0.5}]
    assert call("/locate?zone=kitchen", raw=JPEG)[1]["state"] == "lost"
    # The sensor reports the pill box opening; an event that is neither a sensor's nor the camera's is not for this log.
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    EventApi.events[:] = [{"id": "e1", "type": "pillbox_open", "source": "sensor", "zone": "kitchen", "object": "pillbox", "ts": stamp,
                           "evidence_url": None, "confidence": 1},
                          {"id": "e2", "type": "question_asked", "source": "voice", "zone": None, "object": None, "ts": stamp, "confidence": 1}]
    time.sleep(3.2)  # the day page reads the event list at most every 3 s
    code, body = call("/day")
    kinds = [e["kind"] for e in body["timeline"]]
    assert code == 200 and body["today"] and kinds.count("room") == 1 and kinds.count("place") == 1, body["timeline"]
    place = next(e for e in body["timeline"] if e["kind"] == "place")
    assert place["place"] == "pill box" and place["fixes"] == 3 and 60 <= place["confidence_pct"] <= 99, place
    assert not [e for e in body["timeline"] if e["place"] == "front door"], "a lost fix made a visit"
    events = [e for e in body["timeline"] if e["kind"] == "event"]
    assert len(events) == 1 and events[0]["text"].endswith("while the wearer was next to it.") and events[0]["text"].startswith("The pill box was opened at "), events
    items = [e for e in body["timeline"] if e["kind"] == "item"]
    assert len(items) == 1 and items[0]["text"].startswith("The keys were seen near the pill box at "), items  # seen in three fixes, logged once
    assert any("while you were next to it." in s for s in body["summary"]["wearer"]) and body["summary"]["carer"][0].startswith("Position was known for ")
    assert not any(key in json.dumps(body) for key in ("jpg", "/evidence/", "splat")), "a picture link reached /day"

    # Questions about the day are answered from the log, and still counted by the event API.
    code, body = call("/ask?zone=kitchen", {"question": "Did I go to the pill box today?"})
    assert code == 200 and body["answered_by"] == "day log" and body["answer"].startswith("Yes. You were near the pill box at "), body
    assert body["point"] is None and body["evidence"] == [] and body["confidence_pct"] >= 60
    code, body = call("/ask?zone=kitchen", {"question": "When was I last in the kitchen?"})
    assert body["answered_by"] == "day log" and body["answer"].startswith("You are in the kitchen now."), body
    code, body = call("/ask", {"question": "What did I do today?"})
    assert body["answered_by"] == "day log" and body["answer"].startswith("You were in the kitchen at ") and "The pill box was opened at" in body["answer"], body
    code, body = call("/ask?zone=kitchen", {"question": "Did I go to the front door today?"})
    assert body["answer"].startswith("I have not seen you near the front door today."), body
    # Everything else is untouched: the event API, the relay's own place answer, the lost-item answer.
    gateway.fix = near  # the relay's own place answer wants a fresh found fix
    call("/locate?zone=kitchen", raw=JPEG)
    code, body = call("/ask", {"question": "Did I take my pills?"})
    assert body["answer"].startswith("I have no recent pill box opening") and "answered_by" not in body, body
    code, body = call("/ask?zone=kitchen", {"question": "Where is the front door?"})
    assert body.get("answered_by") == "relay" and body["point"]["name"] == "front door", body
    code, body = call("/ask?zone=kitchen", {"question": "Where are my keys?"})
    assert body["answer"].startswith("Your keys were last seen at ") and "answered_by" not in body, body
    time.sleep(0.3)
    assert sorted(EventApi.asked) == sorted(["Did I go to the pill box today?"] * 2 + ["When was I last in the kitchen?", "What did I do today?",
                                             "Did I go to the front door today?", "Did I take my pills?", "Where is the front door?", "Where are my keys?"]), EventApi.asked
    code, body = call("/day")
    asked = [e for e in body["timeline"] if e["kind"] == "question"]
    assert sum(e["count"] for e in asked) == 8 and any(e["topic"] == "keys" for e in asked), asked
    assert any(e["count"] == 2 and "twice" in e["text"] for e in asked), asked

    # A reminder that becomes due and is ended by the pill box opening shows up as one entry.
    code, body = call("/reminder?zone=kitchen", {"text": "Time to take your pills", "time": "23:59", "place": "pill box"})
    rid = body["reminders"][0]["id"]
    call(f"/reminder?id={rid}&now=1", raw=b"")
    EventApi.events.append({"id": "e3", "type": "pillbox_open", "source": "sensor", "zone": "kitchen", "object": "pillbox",
                            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "confidence": 1})
    time.sleep(4.5)  # the relay asks the sensors about a due reminder at most every few seconds
    code, body = call("/day")
    reminder = [e for e in body["timeline"] if e["kind"] == "reminder"]
    assert len(reminder) == 1 and reminder[0]["state"] == "done" and "The pill box was opened at " in reminder[0]["text"], reminder
    assert "took" not in json.dumps(body) and "taken" not in json.dumps(body).replace("Time to take", ""), "the log claimed more than it saw"

    # A restart loses nothing: a new handler on the same folder reads the same day.
    before = body["timeline"]
    again = relay.make_handler(Gateway(), evidence, KEY, None, pick=None)
    again.log_message = lambda *args: None
    again_server, url = serve(again)
    code, body = call("/day")
    same = lambda entries: [(e["kind"], e["from"], e["text"]) for e in entries if not e.get("ongoing")]  # noqa: E731
    assert code == 200 and same(body["timeline"]) == same(before), "the day changed across a restart"
    code, body = call("/ask?zone=kitchen", {"question": "Did I go to the pill box today?"})
    assert code == 200 and body["answered_by"] == "day log", body  # no event API needed for a day question
    files = sorted(p.name for p in (evidence / "day").iterdir())
    assert len(files) == 1 and files[0].endswith(".jsonl"), files

    for server in (relay_server, again_server, api_server):
        server.shutdown()
    print("day flow: all checks passed")


if __name__ == "__main__":
    main()
