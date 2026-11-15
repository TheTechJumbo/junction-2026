#!/usr/bin/env python3
"""Offline test of the relay's care flow: questions, reminders and lost items.

No cluster and no real event API: a stand-in gateway returns one canned position fix (with the
keys in view), and a stand-in event API answers /ask and lists sensor events. The relay's own
handler is the real one.

    python live/test_care_flow.py
"""
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402

KEY, TOKEN = "test-key", "test-token"
FIX = {"state": "found", "confidence": 0.9, "position": [0.0, 0.0, 0.0], "heading_deg": 0,
       "waypoints": [{"name": "pill box", "distance_m": 2.0, "bearing_deg": 0, "position": [0.0, 2.0, 0.0]},
                     {"name": "front door", "distance_m": 3.0, "bearing_deg": 180, "position": [0.0, -3.0, 0.0]}],
       "track": [[0, 0]], "map": {"points": [], "cameras": []}, "match_per_frame": [0.9], "seconds": {"reconstruct": 0.1},
       "items": [{"name": "keys", "score": 0.4, "box": [0.1, 0.1, 0.2, 0.2], "position": [-1.0, 1.9, 0.0],
                  "distance_m": 2.1, "bearing_deg": -28, "image": "item_keys"},
                 {"name": "wallet", "score": 0.3, "box": [0, 0, 0.1, 0.1], "position": None}],
       "items_looked_for": ["keys", "glasses", "wallet", "phone", "remote control"]}
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")  # starts like a JPEG: enough for the relay without OpenCV


class Gateway:
    """Stands in for the SSH link to the cluster. `fix` is what the next position fix returns."""

    def __init__(self):
        self.fix, self.calls = FIX, []

    def call(self, header, blob=b""):
        self.calls.append(header)
        if header["op"] == "locate":
            return {"ok": True, "job": "job1", "result": json.loads(json.dumps(self.fix))}, b""
        if header["op"] == "status":
            return {"ok": True, "zones": ["kitchen"], "workers": {}}, b""
        return {"ok": False, "error": "not in this test"}, b""


class EventApi(BaseHTTPRequestHandler):
    """Stands in for the team's event API: one canned answer, a list of sensor events, no voice."""
    events, asked = [], []

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.reply(401, {})
        self.reply(200, {"events": self.events, "next_cursor": None})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.reply(401, {})
        if self.path == "/ask":
            self.asked.append(body["question"])
            if body["question"].lower().startswith("where"):  # as when it cannot read the position in time
                return self.reply(200, {"answer": "I do not have a reliable current location. Please ask someone nearby for help.", "evidence": []})
            return self.reply(200, {"answer": "I have no recent pill box opening recorded today.", "evidence": []})
        self.reply(503, {"error": {"code": "speech_unavailable", "message": "no voice"}})

    def log_message(self, *args):
        pass


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main():
    relay.cv2 = None  # the canned photo is not a real picture: pass it through untouched
    api_server, api_url = serve(EventApi)
    evidence = Path(tempfile.mkdtemp(prefix="beacon-test-"))
    gateway = Gateway()
    handler = relay.make_handler(gateway, evidence, KEY, f"{api_url}/events", pick=None, api_token=TOKEN)
    handler.log_message = lambda *args: None
    relay_server, url = serve(handler)

    def call(path, body=None, raw=None):
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url + path, data=data, headers={"X-Key": KEY}, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")

    # No key, no answer.
    try:
        urllib.request.urlopen(url + "/reminders", timeout=5)
        raise AssertionError("a request without the key was let in")
    except urllib.error.HTTPError as err:
        assert err.code == 403

    # Before any fix: the relay cannot look for keys, and says so instead of "not seen".
    code, body = call("/ask?zone=kitchen", {"question": "Where are my keys?"})
    assert code == 200 and body["answer"] == "I cannot look for your keys yet." and body["point"] is None, body

    # A position fix that has the keys in view: they are remembered, with the place they are near.
    code, fix = call("/locate?zone=kitchen", raw=JPEG)
    assert code == 200 and fix["state"] == "found", fix
    keys = [s for s in fix["seen"] if s["name"] == "keys"]
    assert len(keys) == 1 and keys[0]["near"] == "pill box" and keys[0]["distance_m"] == 2.1 and keys[0]["bearing_deg"] == -28, fix["seen"]
    assert not [s for s in fix["seen"] if s["name"] == "wallet"], "an item without a position must not be remembered"
    code, where = call("/where?zone=kitchen")
    assert code == 200 and where["seen"][0]["name"] == "keys" and where["zone"] == "kitchen"

    # The steady position rides along: /locate and /where both carry "stable" (track.py), and the fix
    # itself gets a percentage. One fix gives a pose that is still settling; a second one that agrees
    # makes it steady, with the way to each place recomputed from the steady pose.
    assert 0 <= fix["confidence_pct"] <= 100 and where["confidence_pct"] == fix["confidence_pct"], fix
    assert fix["stable"]["state"] == "settling" and fix["stable"]["position"] == [0.0, 0.0, 0.0] and not fix["stable"]["show"], fix["stable"]
    assert where["stable"]["state"] == "settling" and where["stable"]["age_s"] >= 0
    code, again = call("/locate?zone=kitchen", raw=JPEG)
    stable = again["stable"]
    assert stable["state"] == "steady" and stable["show"] and stable["agree"] == 2 and 0 < stable["confidence_pct"] <= 100, stable
    assert {w["name"]: (w["distance_m"], abs(w["bearing_deg"])) for w in stable["waypoints"]} == {"pill box": (2.0, 0), "front door": (3.0, 180)}, stable
    gateway.fix = {**FIX, "state": "lost", "confidence": 0.02, "position": [4.0, 4.0, 0.0], "heading_deg": 120, "items": []}
    code, missed = call("/locate?zone=kitchen", raw=JPEG)  # one lost fix among good ones: the raw fix says lost, the steady answer stays
    assert missed["state"] == "lost" and missed["stable"]["state"] == "steady" and missed["stable"]["position"] == [0.0, 0.0, 0.0], missed["stable"]
    assert json.loads((evidence / "track.json").read_text())["kitchen"]["state"] == "steady"  # kept for the next start
    gateway.fix = FIX
    code, fix = call("/locate?zone=kitchen", raw=JPEG)  # found again, for the checks below
    assert fix["state"] == "found" and fix["stable"]["state"] == "steady" and fix["stable"]["agree"] == 3, fix["stable"]
    assert call("/where?zone=hall")[0] == 404

    # "Where are my keys?" is answered by the relay, and still counted by the event API.
    code, body = call("/ask?zone=kitchen", {"question": "Where are my keys?"})
    assert code == 200 and body["point"]["name"] == "keys", body
    assert body["answer"].startswith("Your keys were last seen at ") and body["answer"].endswith(
        " near the pill box, about 2.1 metres to your left."), body["answer"]
    code, body = call("/ask?zone=kitchen", {"question": "Have you seen my wallet?"})
    assert body["answer"].startswith("I have not seen your wallet today."), body
    time.sleep(0.3)
    assert sorted(EventApi.asked) == ["Have you seen my wallet?", "Where are my keys?", "Where are my keys?"], EventApi.asked

    # Someone picks the keys up. Later fixes have a clear, close look at the spot and no keys there.
    # One look is not enough, and neither are two from the same standpoint; two from different
    # standpoints put the sighting in doubt, and the wearer is no longer sent there.
    look = {"name": "keys", "in_view": True, "blocked": False, "close_enough": True, "detected_near": False}
    gateway.fix = {**FIX, "items": [], "items_checked": [look]}
    code, fix = call("/locate?zone=kitchen", raw=JPEG)
    assert gateway.calls[-1]["remembered"] == [{"name": "keys", "position": [-1.0, 1.9, 0.0]}], gateway.calls[-1]
    assert fix["seen"][0]["doubt"] is False
    code, fix = call("/locate?zone=kitchen", raw=JPEG)
    assert fix["seen"][0]["doubt"] is False
    gateway.fix = {**FIX, "items": [], "items_checked": [{**look, "blocked": True}], "position": [0.5, 0.0, 0.0]}
    code, fix = call("/locate?zone=kitchen", raw=JPEG)
    assert fix["seen"][0]["doubt"] is False  # something was in the way: that look says nothing
    gateway.fix = {**FIX, "items": [], "items_checked": [look], "position": [0.5, 0.0, 0.0]}
    code, fix = call("/locate?zone=kitchen", raw=JPEG)
    assert fix["seen"][0]["doubt"] is True
    code, body = call("/ask?zone=kitchen", {"question": "Where are my keys?"})
    assert body["answer"].startswith("Your keys were near the pill box at ") and "but I did not see them there at " in body["answer"], body
    assert body["point"] is None
    gateway.fix = FIX  # the keys are back in view: a new sighting, no doubt
    code, fix = call("/locate?zone=kitchen", raw=JPEG)
    assert fix["seen"][0]["doubt"] is False
    code, body = call("/ask?zone=kitchen", {"question": "Where are my keys?"})
    assert body["answer"].startswith("Your keys were last seen at ") and body["point"]["name"] == "keys", body

    # Any other question goes to the event API and comes back in words.
    code, body = call("/ask", {"question": "Did I take my pills?"})
    assert code == 200 and body["answer"].startswith("I have no recent pill box opening") and body["evidence"] == [], body
    # When the event API wanted a position and could not get one, the relay answers from its own fix.
    code, body = call("/ask?zone=kitchen", {"question": "Where is the pill box?"})
    assert body["answer"] == "The pill box is estimated to be about 2.0 metres ahead." and body["point"]["name"] == "pill box", body
    assert body["answered_by"] == "relay"
    code, body = call("/ask?zone=kitchen", {"question": "Where is the garage?"})  # not a named place: the event API's answer stands
    assert body["answer"].startswith("I do not have a reliable current location.") and body["point"] is None, body
    assert call("/ask", {"wrong": 1})[0] == 400
    assert call("/speak", {"text": "hello"})[0] == 503  # no voice service: the page then uses the browser's voice

    # A reminder for later today is "later"; "show now" makes it due; the sensor's opening ends it.
    code, body = call("/reminder?zone=kitchen", {"text": "Time to take your pills", "time": "23:59", "place": "pill box"})
    assert code == 200 and body["reminders"][0]["state"] == "later" and body["reminders"][0]["until"] == "pillbox_open", body
    rid = body["reminders"][0]["id"]
    assert call("/reminder", {"text": "x", "time": "25:00"})[0] == 400
    code, body = call(f"/reminder?id={rid}&now=1", raw=b"")
    assert body["reminders"][0]["state"] == "due", body
    EventApi.events.append({"id": "e1", "type": "pillbox_open", "source": "sensor", "zone": "kitchen", "object": "pillbox",
                            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "evidence_url": None, "confidence": 1})
    time.sleep(4.5)  # the relay asks the sensors at most every few seconds
    code, body = call("/reminders")
    reminder = body["reminders"][0]
    assert reminder["state"] == "done" and reminder["done_note"].startswith("The pill box was opened at "), reminder

    # A reminder with no sensor is finished by hand, and can be removed.
    code, body = call("/reminder", {"text": "Drink some water", "time": "23:58", "place": ""})
    other = body["reminders"][1]["id"]
    assert body["reminders"][1]["until"] == ""
    call(f"/reminder?id={other}&now=1", raw=b"")
    code, body = call(f"/reminder?id={other}&done=1", raw=b"")
    assert body["reminders"][1]["state"] == "done" and body["reminders"][1]["done_note"] == "", body
    code, body = call(f"/reminder?id={other}&delete=1", raw=b"")
    assert len(body["reminders"]) == 1
    assert call("/reminder?id=nope&done=1", raw=b"")[0] == 404

    # Without the event API settings, questions other than lost items answer 503, and reminders still work.
    alone = relay.make_handler(Gateway(), evidence, KEY, None, pick=None)
    alone.log_message = lambda *args: None
    alone_server, url = serve(alone)
    assert call("/ask", {"question": "Did I take my pills?"})[0] == 503
    code, body = call("/ask?zone=kitchen", {"question": "Where is the front door?"})
    assert code == 200 and body["answer"] == "The front door is estimated to be about 3.0 metres behind you.", body
    code, body = call("/ask?zone=kitchen", {"question": "Where are my keys?"})
    assert code == 200 and body["answer"].startswith("Your keys were last seen at "), body  # remembered across a restart
    code, body = call("/reminders")
    assert code == 200 and body["sensors"] is False and len(body["reminders"]) == 1

    # Forgetting an item: the answer goes back to "not seen".
    code, body = call("/forget?zone=kitchen&name=keys", raw=b"")
    assert code == 200 and body["seen"] == [], body
    code, body = call("/ask?zone=kitchen", {"question": "Where are my keys?"})
    assert body["answer"].startswith("I have not seen your keys today."), body

    for server in (relay_server, alone_server, api_server):
        server.shutdown()
    print("care flow: all checks passed")


if __name__ == "__main__":
    main()
