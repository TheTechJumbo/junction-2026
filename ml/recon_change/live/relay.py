#!/usr/bin/env python3
"""Relay. Runs on the laptop that holds the Iridis SSH key: HTTP in, one SSH connection out.

    python live/relay.py                    # this laptop only: http://127.0.0.1:8090
    python live/relay.py --host 0.0.0.0     # also reachable from a phone on the same network

It prints the link to open. The link carries an access key, and every request needs that key
(`?k=...` or an `X-Key` header), because the relay spends GPU time on your Iridis account.

    GET  /                        the phone page: record a baseline clip, record a check clip
    POST /baseline?zone=kitchen   body = video of the room as it should be
    POST /check?zone=kitchen      body = a new video -> what appeared and what is gone
                                  (either body may instead be a zip of frames 000.jpg, 001.jpg, ...
                                  that a phone app already picked: up to 24 for a baseline, 12 for a check)
    GET  /status                  is the vision worker running, which zones have a baseline
    POST /locate?zone=kitchen     body = a photo, a short video or a zip of frames of what the wearer
                                  sees now -> where they are and the way to each named place
                                  (add &view=1 to also get the room as a 3D file with them marked)
    GET  /where?zone=kitchen      the latest position fix for that zone (what the glasses display polls)
                                  (both replies carry "stable": the steady position made from the stream of
                                  fixes, with "confidence_pct" and "show", see track.py)
    GET  /waypoints?zone=kitchen  the zone's named places
    POST /waypoint?zone=kitchen&name=pill+box&frame=3&u=0.4&v=0.6   name a spot in baseline picture 3
    POST /waypoint?zone=kitchen&name=pill+box&delete=1
    GET  /hud                     the glasses display page: arrow and distance to a place
    GET  /places                  name places by clicking in a baseline picture, and test a fix
    POST /ask?zone=kitchen        body = {"question": "Did I take my pills?"} -> the answer in words
                                  ("Where are my keys?" is answered here: where a position fix last saw them)
    POST /forget?zone=kitchen&name=keys   forget where an item was seen (no name: every item in the zone)
    POST /speak                   body = {"text": "..."} -> the sentence as MP3 speech
    GET  /changes?zone=kitchen    what was moved in view while the camera stood still, with before and now pictures

Every photo sent to /locate first goes through a cheap gate (view_verdict): the same view as the last one
the model saw gets the last answer again in milliseconds ("reused": true, at most 45 s long); a view with a
small part changed is reported under "view" with the place of the change; &fresh=1 asks the model anyway.
    GET  /reminders               the reminders, each with its state today: later, due, done or missed
    POST /reminder                body = {"text": "Time to take your pills", "time": "14:00", "place": "pill box"}
    POST /reminder?id=ID&now=1    show it now (also &done=1: finished by hand, &delete=1)
    POST /route                   body = {"position": [east, north], "heading_deg": 0, "map": {...}, "waypoints": [...]}
                                  -> the way around furniture from that standpoint (route.py). /locate and
                                  /where carry the same under "route", from the wearer's own position
    GET  /day?date=YYYY-MM-DD     what the wearer did that day (no date: today): timeline and two summaries
    GET  /dayview                 the carer's page for one day

What the carer sets once, on /places (the gateway keeps it next to the baseline pictures):

    POST /scale?zone=kitchen&a=pill+box&b=front+door&metres=2.4   the real distance between two named
                                  places, so distances come out in real metres (&delete=1 removes it;
                                  GET /waypoints shows it as "scale")
    POST /watch?zone=kitchen&name=pill+box+shelf&frame=3&u0=0.2&v0=0.3&u1=0.5&v1=0.6
                                  a box in baseline picture 3 to check for changes (&name=...&delete=1 removes it)
    GET  /watch?zone=kitchen      the zone's watched spots
    POST /enroll?name=keys        body = one photo of that exact item (JPEG, PNG or WebP), up to five per item
    POST /enroll?name=keys&delete=1   forget the item (&n=1 too: only that photo)
    GET  /enrolled                the items and how many photos each has; a photo is /enrolled/keys/0.jpg

A reminder is shown and spoken on the glasses display when it is due, with the way to its place.
One for the pill box is done when a sensor reports the box was opened (read from the event API).

/ask and /speak are passed on to the team's event API, which holds the sensor events and writes
the answers (set EVENT_API_URL to its /events address and EVENT_API_TOKEN to its token). They exist
so the glasses display needs one address and one key. Without those settings they answer 503.

/check answers with a plain-language headline, events in the team's schema, and links to a
"before" and a "now" picture. If EVENT_API_URL is set, the events are also POSTed there. The
vision path only ever writes events: nothing that answers the wearer waits on this.

Uploaded videos never leave the laptop. The relay picks the sharp frames it needs with OpenCV
and sends those (about 1 MB) to Iridis. Without OpenCV it sends the video as it is.
The SSH connection is opened on first use and reopened if it drops.
"""
import argparse
import hmac
import io
import json
import math
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

HERE = Path(__file__).resolve().parent
REMOTE = "cd /scratch/$USER/junction-recon && exec .venv-recon/bin/python -u live/gateway.py"
MAX_BODY = 200 * 1024 * 1024
FRAMES = {"baseline": 24, "check": 12, "locate": 3}  # keep in step with worker.py
LONG_SIDE = 518  # the model's input size: no point sending more pixels
BIG_SIDE = 1280  # the wearer's newest photo is also sent at this size, for finding small items
REMINDER_WINDOW = 2 * 3600  # a reminder nobody finished within two hours counts as missed
NEAR_PLACE_M = 1.5  # an item this close to a named place is described as "near" it
FRESH_FIX_S = 60  # "to your left" is only said from a position fix this recent
MISSED_LOOKS = 2  # clear looks at a remembered spot without the item before the sighting is doubted
MOVED_M, MOVED_DEG = 0.3, 15  # a look only counts as another one from a different standpoint

# Clock times (reminders, "last seen at") are in the home's time zone, the same one the event API
# answers in, not the laptop's. Set BEACON_TZ to change it.
try:
    from zoneinfo import ZoneInfo
    HOME_TZ = ZoneInfo(os.environ.get("BEACON_TZ", "Europe/Helsinki"))
except Exception:  # no time zone data on this machine: fall back to the laptop's own zone
    HOME_TZ = None


def home_time(seconds):
    """Seconds since 1970 as a clock time in the home."""
    return datetime.fromtimestamp(seconds, HOME_TZ) if HOME_TZ else datetime.fromtimestamp(seconds).astimezone()

try:
    sys.path.insert(0, str(HERE.parent))
    import cv2
    import numpy as np
    import recon_change as rc
except ImportError:  # no OpenCV here: videos are forwarded whole
    cv2 = rc = None

sys.path.insert(0, str(HERE.parent))
import track  # noqa: E402  the steady position made from the stream of fixes: pure Python, needs nothing
# ---- day log (JUN-26): what the wearer did through the day. day.py is next to recon_change.py and needs nothing else.
import day as day_log  # noqa: E402

# --- route finding (route.py): plain Python, so it loads with or without OpenCV ---------------
try:
    if str(HERE.parent) not in sys.path:
        sys.path.insert(0, str(HERE.parent))
    import route as route_finder
except ImportError:  # route.py is missing: fixes go out without a route, the display points straight
    route_finder = None
# --- end route finding --------------------------------------------------------------------------


class Link:
    """One long-lived connection to the gateway, speaking its JSON-line-plus-bytes protocol."""

    def __init__(self, argv):
        self.argv, self.proc, self.lock = argv, None, threading.Lock()

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise ConnectionError("connection to Iridis closed")
        header = json.loads(line)
        return header, self.proc.stdout.read(int(header.get("size", 0)))

    def _open(self):
        self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self._read()  # the gateway says hello first; this fails fast if SSH could not connect

    def close(self):
        if self.proc:
            self.proc.kill()
            self.proc = None

    def call(self, header, blob=b""):
        """Send one request and wait for its reply. One retry on a fresh connection if the link is down."""
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.proc is None or self.proc.poll() is not None:
                        self._open()
                    self.proc.stdin.write(json.dumps({**header, "size": len(blob)}).encode() + b"\n" + blob)
                    self.proc.stdin.flush()
                    return self._read()
                except (OSError, ValueError, ConnectionError) as err:
                    self.close()
                    if attempt == 2:
                        return {"ok": False, "error": f"Cannot reach Iridis: {err}"}, b""


def pick_frames(video, n):
    """Pick n sharp frames from an uploaded video and zip them as small JPEGs."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(video)
    try:
        frames = rc.sample_frames(tmp.name, n)
    except SystemExit as err:  # sample_frames is also a command-line helper and exits on bad input
        raise ValueError(str(err)) from None
    finally:
        os.unlink(tmp.name)
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as z:
        for i, frame in enumerate(frames):
            h, w = frame.shape[:2]
            scale = LONG_SIDE / max(h, w)
            if scale < 1:
                frame = cv2.resize(frame, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
            z.writestr(f"{i:03d}.jpg", cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
    if not frames:
        raise ValueError("no frames in that file")
    return bundle.getvalue(), len(frames)


def is_photo(data):
    """JPEG, PNG or WebP, by the first bytes."""
    return data[:2] == b"\xff\xd8" or data[:8] == b"\x89PNG\r\n\x1a\n" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def one_photo(photo):
    """A single photo as a one-frame bundle, shrunk to the model's input size when OpenCV is here."""
    if cv2 is None and photo[:2] != b"\xff\xd8":
        raise ValueError("only JPEG photos without OpenCV")
    big = None
    if cv2 is not None:
        frame = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("not a picture")

        def shrunk(side):
            scale = side / max(frame.shape[:2])
            small = frame if scale >= 1 else cv2.resize(
                frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
            return cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()

        photo = shrunk(LONG_SIDE)
        if max(frame.shape[:2]) > LONG_SIDE:
            big = shrunk(BIG_SIDE)  # keys are a few pixels wide at the model's size: the item finder gets more
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as z:
        z.writestr("000.jpg", photo)
        if big:
            z.writestr("big.jpg", big)  # a worker without the item finder ignores it
    return bundle.getvalue()


# ---- the cheap gate: has the view changed at all? ----
# A position fix costs seconds of a large model. So every new photo is first compared with the last photo
# the model saw, as two small grey pictures cut into 6 x 8 blocks, by cosine similarity block by block.
# Three outcomes, a mix of cheap and expensive work:
#   same    no block differs: the camera has not moved and nothing in view changed. The last answer is
#           given again and the model is not asked (milliseconds).
#   local   a few blocks differ and the rest match: the camera is still and something in view was moved,
#           taken or put down. The model is asked, and the place of the change is reported with a before
#           and a now picture.
#   moved   most blocks differ: the camera moved. The model is asked as usual.
BLOCKS = (6, 8)       # rows and columns of blocks in the 54 x 96 picture
FLAT = 0.2            # a block with less contrast than this (in units of the picture's own contrast) has no pattern
BLOCK_CHANGED = 0.06  # a block has changed above this difference (1 minus cosine similarity; a flat block: its mean)
LOCAL_SHARE = 0.35    # up to this share of changed blocks it is a change in view; above it the camera moved
REUSE_FOR_S = 45      # an answer is given again for at most this long; then the model is asked anyway


def view_print(photo):
    """A photo as a 54 x 96 grey picture with its own brightness and contrast taken out, or None."""
    if cv2 is None:
        return None
    frame = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_GRAYSCALE)
    if frame is None:
        return None
    small = cv2.GaussianBlur(cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA), (3, 3), 0).astype(np.float32)
    return (small - small.mean()) / (small.std() + 1e-3)


def view_compare(a, b, flat=None):
    """How much each of the 6 x 8 blocks differs between two view prints. A block with a pattern in it:
    1 minus the cosine similarity of the two blocks, each with its own mean taken out, so a block that only
    got brighter or darker still matches. A flat block has no pattern to compare: the difference of its mean."""
    rows, cols = BLOCKS
    cut = lambda v: v.reshape(rows, 54 // rows, cols, 96 // cols).transpose(0, 2, 1, 3).reshape(rows, cols, -1)  # noqa: E731
    A, B = cut(a), cut(b)
    mean_a, mean_b = A.mean(-1), B.mean(-1)
    A, B = A - mean_a[..., None], B - mean_b[..., None]
    size_a, size_b = np.linalg.norm(A, axis=-1), np.linalg.norm(B, axis=-1)
    cosine = (A * B).sum(-1) / (size_a * size_b + 1e-6)
    no_pattern = np.maximum(size_a, size_b) < (FLAT if flat is None else flat) * np.sqrt(A.shape[-1])
    return np.where(no_pattern, np.abs(mean_a - mean_b), 1.0 - cosine)


def view_verdict(a, b):
    """same, local or moved (see above), with the share of changed blocks and, for a local change, its box
    as fractions of the picture."""
    rows, cols = BLOCKS
    differs = view_compare(a, b)
    changed = differs > BLOCK_CHANGED
    share = float(changed.mean())
    kind = "same" if not changed.any() else "local" if share <= LOCAL_SHARE else "moved"
    box = None
    if kind == "local":
        r, c = np.nonzero(changed)
        box = [round(float(v), 3) for v in (c.min() / cols, r.min() / rows, (c.max() + 1) / cols, (r.max() + 1) / rows)]
    return {"kind": kind, "share": round(share, 3), "worst": round(float(differs.max()), 3), "box": box}


# Things the wearer may ask for, and the words that mean them. Keep in step with the worker's RECON_ITEMS.
ITEM_WORDS = {"keys": ("key", "keys"), "glasses": ("glasses", "spectacles"), "wallet": ("wallet", "purse"),
              "phone": ("phone", "mobile"), "remote control": ("remote",)}


# Other words for a named place, used only when a place of that name exists in the room.
PLACE_WORDS = {"pill box": ("pills", "pillbox", "medication", "medicine"), "front door": ("door",), "bathroom": ("toilet",)}
# How the event API starts its answer when it could not get a position.
NO_POSITION = "I do not have a reliable current location."


def item_in(question):
    """The item a question is about ("Where are my keys?" -> "keys"), or None."""
    words = set(re.sub(r"[^\w]+", " ", question.casefold()).split())
    return next((name for name, aliases in ITEM_WORDS.items() if words & set(aliases)), None)


def side_of(bearing):
    return "ahead" if abs(bearing) <= 20 else "behind you" if abs(bearing) >= 150 else "to your right" if bearing > 0 else "to your left"


def from_here(position, fix):
    """Distance in metres and bearing (0 ahead, + to the right) from the wearer of a fix to a spot on the map."""
    east, north = position[0] - fix["position"][0], position[1] - fix["position"][1]
    bearing = (math.degrees(math.atan2(east, north)) - fix["heading_deg"] + 180) % 360 - 180
    return round(math.hypot(east, north), 1), int(round(bearing))


def where_in_plain_words(fix):
    """One line about a position fix, for a screen or for speech."""
    if fix["state"] == "lost":
        return "Not sure where you are", "Look around slowly so more of the room is in view."
    places = sorted(fix["waypoints"], key=lambda w: w["distance_m"])
    if not places:
        return "Found you in the room", "No places are named in this room yet."

    def side(w):
        b = w["bearing_deg"]
        return "ahead" if abs(b) <= 20 else "behind you" if abs(b) >= 150 else "to your right" if b > 0 else "to your left"

    nearest = places[0]
    headline = f"The {nearest['name']} is about {max(1, round(nearest['distance_m']))} m {side(nearest)}"
    return headline, ("This fix is shaky, so treat it as a rough guide." if fix["state"] == "unsure" else "")


def in_plain_words(report):
    """The verdict for someone reading a phone screen."""
    appeared, gone = len(report["appeared"]), len(report["gone"])
    if report["verdict"].startswith("UNSTABLE"):
        return "Couldn't rebuild the room reliably", "Record again: move slower and keep more of the room in view."
    if not appeared and not gone:
        return "No change found", "The room matches the baseline."
    parts = []
    if appeared:
        parts.append(f"{appeared} thing{'s' if appeared > 1 else ''} appeared")
    if gone:
        parts.append(f"{gone} thing{'s are' if gone > 1 else ' is'} gone")
    if report["verdict"].startswith("UNCLEAR"):
        return "Possible change: " + " and ".join(parts), "This scan is noisy, so treat it as a maybe."
    return " and ".join(parts).capitalize(), "Green marks what appeared. Red marks what is gone."


def to_events(report, zone, images):
    """Change clusters -> events in the team's schema. Vision events are evidence, never ground truth."""
    # Rough. Even with a clean control, video tests produced false changes, so never claim more than 0.6.
    confidence = 0.6 if report["verdict"].startswith("PASS") else 0.3
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    picture = {"appeared": images.get("now"), "gone": images.get("before")}
    return [
        {"type": f"object_{kind}", "source": "vision", "ts": now, "zone": zone, "object": None,
         "evidence_url": picture[kind], "confidence": confidence, "size_cm": c["size_cm_approx"]}
        for kind in ("appeared", "gone") for c in report[kind]
    ]


def post_events(url, events):
    request = urllib.request.Request(url, json.dumps(events).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status


# ---------------------------------------------------------------- what the carer sets once
# The room's real scale, the spots to watch and the items shown to the camera once. The relay
# checks what the page sends, and the gateway, which writes the files, checks all of it again.
ZONE_NAME = r"[a-z0-9_-]{1,32}"
ITEM_NAME = r"[a-z0-9]+(?:_[a-z0-9]+)*"  # an enrolled item as its folder is named: "remote_control"
PHOTO_BYTES = 20 * 1024 * 1024  # the largest photo of an item


def item_name(text):
    """"Remote control" -> "remote_control", or "" if it is not a plain name of at most 32 characters."""
    name = "_".join(text.lower().split())
    return name if len(name) <= 32 and re.fullmatch(ITEM_NAME, name) else ""


def plain_name(text):
    """A typed name with single spaces, or "" unless it starts with a letter or a digit and goes on
    with letters, digits, spaces, ' and -, at most 40 characters in all."""
    name = " ".join(text.split())
    return name if re.fullmatch(r"[^\W_][\w '-]{0,39}", name) else ""


def in_range(text, low, high):
    """A number from the query string, if it is a finite one from low to high. ValueError otherwise."""
    value = float(text)
    if not (math.isfinite(value) and low <= value <= high):
        raise ValueError(text)
    return value


def item_photo(photo):
    """A photo of an item as a JPEG with its long side at most BIG_SIDE, so little goes over the SSH
    connection. Without OpenCV it is passed on as it is, and the gateway shrinks it."""
    if cv2 is None:
        return photo
    frame = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("not a picture")
    scale = BIG_SIDE / max(frame.shape[:2])
    if scale < 1:
        frame = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()


REPO = "TheTechJumbo/junction-2026"


def make_handler(link, evidence_dir, token, event_api=None, pick=pick_frames if rc else None, api_token=None):
    # The event API's address without the /events at its end: where /ask and /speak live.
    api_base = re.sub(r"/events/?$", "", event_api).rstrip("/") if event_api and api_token else None
    history_file = evidence_dir / "history.json"
    try:
        history = json.loads(history_file.read_text())  # earlier checks, so the dashboard survives a restart
    except (OSError, ValueError):
        history = []
    work = {"at": 0.0, "pull_requests": []}
    where_file = evidence_dir / "where.json"
    try:
        where = json.loads(where_file.read_text())  # latest position fix per zone
    except (OSError, ValueError):
        where = {}

    seen_file = evidence_dir / "seen.json"
    try:
        seen = json.loads(seen_file.read_text())  # {zone: {item: where and when it was last seen}}
    except (OSError, ValueError):
        seen = {}

    # One tracker per zone turns the stream of fixes into one steady position with a confidence
    # percentage (track.py). Its state is a little JSON, kept next to where.json across a restart.
    track_file = evidence_dir / "track.json"
    try:
        trackers = {z: track.Tracker(state) for z, state in json.loads(track_file.read_text()).items()}
    except (OSError, ValueError, AttributeError, TypeError):
        trackers = {}
    track_lock = threading.Lock()

    def steady(zone, fix=None, taken_at=None):
        """The steady position of a zone as of now (the "stable" part of the /locate and /where replies),
        or None before the zone's first fix. With `fix`, that fix (photo taken at `taken_at`) is taken in first."""
        try:
            with track_lock:
                if fix is not None:
                    trackers.setdefault(zone, track.Tracker(zone=zone)).update({**fix, "zone": zone}, taken_at)
                    try:
                        track_file.write_text(json.dumps({z: t.dump() for z, t in trackers.items()}))
                    except OSError:
                        pass
                return trackers[zone].current(time.time()) if zone in trackers else None
        except Exception:  # the steady position is an extra: it must never cost the wearer a fix
            return None

    # ---- day log (JUN-26): one file per day in the evidence folder, no pictures in it. See day.py. ----
    try:  # BEACON_NEAR_M: how close to a place counts as a visit, in the fix's metres (see eval/DAY_RESULTS.md)
        near_m = float(os.environ.get("BEACON_NEAR_M") or day_log.ENTER_M)
    except ValueError:
        near_m = day_log.ENTER_M
    log = day_log.DayLog(evidence_dir / "day", clock=home_time, name=os.environ.get("BEACON_WEARER_NAME") or None,
                         enter=near_m, leave=near_m + day_log.LEAVE_M - day_log.ENTER_M)
    asked_events = {"at": 0.0}

    def log_fix(zone, fix, result):
        """Hand a finished position fix to the day log, with the items it placed. When the fix carries the
        stabiliser's answer under "stable", day.py uses that. Never costs the wearer the fix itself."""
        try:
            log.fix(fix)
            for item in result.get("items") or []:
                if item.get("position"):
                    log.item(item["name"], near=(seen.get(zone, {}).get(item["name"]) or {}).get("near", ""), zone=zone, position=item["position"])
        except Exception as err:  # noqa: BLE001
            sys.stderr.write(f"day log: {type(err).__name__}: {err}\n")

    def log_sensor_events(wait=False):
        """Read the sensor and camera events the event API has collected since the last one in the day log.
        In the background after a fix, at most every 15 s; at once for the day page (wait), at most every 3 s.
        Reads the event list, so no question is logged."""
        if not api_base or time.time() - asked_events["at"] < (3 if wait else 15):
            return
        asked_events["at"] = time.time()

        def pull():
            start = log.last_event_time() or log.window(log.day_of(time.time()), None)[0]  # else from midnight in the home
            params = {"since": datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="seconds"), "limit": 100}
            try:
                for _ in range(5):
                    request = urllib.request.Request(f"{api_base}/events?{urlencode(params)}",
                                                     headers={"Authorization": f"Bearer {api_token}"})
                    with urllib.request.urlopen(request, timeout=4) as response:
                        page = json.loads(response.read())
                    for event in page.get("events", []):
                        if isinstance(event, dict) and event.get("source") in ("sensor", "vision"):
                            log.event(event)
                    if not page.get("events") or not page.get("next_cursor"):
                        break
                    params = {"cursor": page["next_cursor"], "limit": 100}
            except Exception as err:  # noqa: BLE001  the event API being down must not break a page or a fix
                sys.stderr.write(f"day log: events not read: {type(err).__name__}\n")

        if wait:
            pull()
        else:
            threading.Thread(target=pull, daemon=True).start()
    # ---- end of the day log's own functions ----

    def remember_items(zone, result, images):
        """Keep where each item in this fix was seen. Only with a position: a lost fix places nothing."""
        found = [item for item in result.get("items") or [] if item.get("position")]
        for item in found:
            near = min(result.get("waypoints") or [], default=None,
                       key=lambda w: math.dist(w["position"][:2], item["position"][:2]))
            if near and math.dist(near["position"][:2], item["position"][:2]) > NEAR_PLACE_M:
                near = None
            seen.setdefault(zone, {})[item["name"]] = {
                "name": item["name"], "position": item["position"], "score": item.get("score"), "at": time.time(),
                "near": near["name"] if near else "", "image": images.get(item.get("image") or "")}
        # A sighting goes stale: someone picks the keys up, and "last seen by the pill box" would still be
        # said with full confidence. So every later fix reports, for each remembered spot, whether it had a
        # clear, close look at the spot and whether the item was there (the worker's "items_checked").
        # MISSED_LOOKS clear looks without the item, from different standpoints, put the sighting in doubt.
        here = [item["name"] for item in found]
        looked = False
        for look in result.get("items_checked") or []:
            spot = seen.get(zone, {}).get(look.get("name"))
            if not spot or look.get("name") in here:
                continue
            looked = True
            if look.get("detected_near"):
                spot.update(missed=0)
            elif look.get("in_view") and look.get("close_enough") and not look.get("blocked"):
                last = spot.get("missed_from")
                moved = last is None or math.dist(last[:2], result["position"][:2]) >= MOVED_M or abs(
                    (result["heading_deg"] - last[2] + 180) % 360 - 180) >= MOVED_DEG
                if moved:  # two looks from the same standpoint would miss it for the same reason
                    spot.update(missed=spot.get("missed", 0) + 1, missed_at=time.time(),
                                missed_from=[*result["position"][:2], result["heading_deg"]])
        if found or looked:
            try:
                seen_file.write_text(json.dumps(seen))
            except OSError:
                pass

    def in_doubt(spot):
        return spot.get("missed", 0) >= MISSED_LOOKS

    def seen_list(zone, fix):
        """The items seen in a zone, each with how long ago and, from a usable fix, the way to it.
        "doubt" marks a sighting the glasses have since looked at without finding the item."""
        out = []
        for spot in seen.get(zone, {}).values():
            entry = {**spot, "age_s": round(time.time() - spot["at"]),
                     "time": home_time(spot["at"]).strftime("%H:%M"), "doubt": in_doubt(spot)}
            if fix and fix.get("state") != "lost":
                entry["distance_m"], entry["bearing_deg"] = from_here(spot["position"], fix)
            out.append(entry)
        return out

    def item_answer(name, zone, question):
        """Answer "where are my keys?" from what the position fixes saw: last seen where and when."""
        fix = where.get(zone) or max(where.values(), key=lambda f: f["at"], default=None)
        zone = fix["zone"] if fix else zone
        spot = seen.get(zone, {}).get(name)
        today = spot and home_time(spot["at"]).date() == home_time(time.time()).date()
        them, were = ("them", "were") if name.endswith("s") else ("it", "was")
        reply = {"ok": True, "question": question, "evidence": [], "item": name, "point": None}
        if not today:
            watching = fix and name in (fix.get("items_looked_for") or [])
            reply["answer"] = (f"I have not seen your {name} today. Look around slowly and I will keep watching for {them}."
                               if watching else f"I cannot look for your {name} yet.")
            return reply
        if in_doubt(spot):  # do not send them to a place the glasses have since looked at in vain
            place = f"near the {spot['near']}" if spot.get("near") else "seen"
            reply["answer"] = (f"Your {name} {were} {place} at {home_time(spot['at']):%H:%M}, but I did not see {them} there "
                               f"at {home_time(spot['missed_at']):%H:%M}. Look around slowly and I will keep watching for {them}.")
            return reply
        text = f"Your {name} {were} last seen at {home_time(spot['at']):%H:%M}"
        if spot.get("near"):
            text += f" near the {spot['near']}"
        if fix and fix.get("state") != "lost" and time.time() - fix["at"] < FRESH_FIX_S:
            distance, bearing = from_here(spot["position"], fix)
            text += ", right by you" if distance < 0.8 else f", about {distance:.1f} metres {side_of(bearing)}"
        reply.update(answer=text + ".", point={"name": name, "position": spot["position"], "image": spot.get("image")})
        return reply

    def place_answer(question, zone):
        """Answer "where is the pill box?" from the latest position fix, in the event API's own wording.
        Only from a fix that is found and fresh; otherwise None. Used when the event API could not read
        the position in time (it allows itself 0.4 s), or when no event API is connected."""
        words = " " + re.sub(r"[^\w]+", " ", question.casefold()).strip() + " "
        if not re.search(r" (where|find|way|directions?|navigate|get to|take me|show me) ", words):
            return None  # "Did I take my pills?" is not a request for the way to the pill box
        fix = where.get(zone) or max(where.values(), key=lambda f: f["at"], default=None)
        if not fix or fix.get("state") != "found" or time.time() - fix["at"] > FRESH_FIX_S:
            return None
        places = {w["name"]: w for w in fix.get("waypoints") or []}
        named = [n for n in sorted(places, key=len, reverse=True) if f" {n.casefold()} " in words]
        named += [name for name, aliases in PLACE_WORDS.items() if name in places and any(f" {a} " in words for a in aliases)]
        if not named:
            return {"answer": f"The latest location estimate places you in {fix['zone']}.", "point": None} if " where am i " in words else None
        place = places[named[0]]
        return {"answer": f"The {place['name']} is estimated to be about {place['distance_m']:.1f} metres {side_of(place['bearing_deg'])}.",
                "point": {"name": place["name"], "position": place["position"]}}

    def log_question(question):
        """Tell the event API a question was asked, so the carer's count of questions stays complete."""
        try:
            request = urllib.request.Request(f"{api_base}/ask", json.dumps({"question": question}).encode(),
                                             {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"})
            urllib.request.urlopen(request, timeout=8).close()
        except OSError:
            pass

    reminders_file = evidence_dir / "reminders.json"
    try:
        reminders = json.loads(reminders_file.read_text())  # what to remind the wearer of, and when
    except (OSError, ValueError):
        reminders = []
    reminder_lock = threading.Lock()
    asked_sensors = {"at": 0.0}

    def save_reminders():
        try:
            reminders_file.write_text(json.dumps(reminders))
        except OSError:
            pass

    def starts_at(reminder, now):
        """When today's reminder starts: its time of day, or the moment someone pressed "show now" today."""
        local = home_time(now)
        forced = reminder.get("now_at")
        if forced and home_time(forced).date() == local.date():
            return forced
        hour, minute = map(int, reminder["time"].split(":"))
        return local.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()

    def pill_box_opened_since(start):
        """The time (HH:MM) of a pill box opening that a sensor reported at or after `start`, else None.
        Read from the event API's event list, so no question is logged."""
        params = {"since": datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="seconds"), "limit": 100}
        try:
            for _ in range(5):
                request = urllib.request.Request(f"{api_base}/events?{urlencode(params)}",
                                                 headers={"Authorization": f"Bearer {api_token}"})
                with urllib.request.urlopen(request, timeout=4) as response:
                    page = json.loads(response.read())
                for event in page.get("events", []):
                    if event.get("type") == "pillbox_open" and event.get("source") == "sensor":
                        at = datetime.fromisoformat(event["ts"])
                        if at.timestamp() >= int(start):  # sensors stamp whole seconds
                            return home_time(at.timestamp()).strftime("%H:%M")
                if not page.get("events") or not page.get("next_cursor"):
                    break
                params = {"cursor": page["next_cursor"], "limit": 100}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return None

    def reminder_list():
        """Every reminder with its state today: later, due, done or missed. Call with reminder_lock held.
        A pill box reminder is done when a sensor reports the box was opened: that is an observation
        of the box, never a claim that the pills were taken."""
        now = time.time()
        ask_sensors = bool(api_base) and now - asked_sensors["at"] > 4  # at most every few seconds
        out, changed = [], False
        for r in reminders:
            start = starts_at(r, now)
            done = bool(r.get("done_at")) and r["done_at"] >= start
            if not done and now >= start and r.get("until") == "pillbox_open" and ask_sensors:
                opened = pill_box_opened_since(start)
                if opened:
                    r.update(done_at=now, done_note=f"The pill box was opened at {opened}.")
                    done = changed = True
            state = "done" if done else "later" if now < start else "due" if now - start < REMINDER_WINDOW else "missed"
            out.append({**{k: r.get(k) for k in ("id", "text", "time", "place", "zone", "until")},
                        "state": state, "due_at": round(start), "done_note": (r.get("done_note") or "") if done else ""})
        if ask_sensors:
            asked_sensors["at"] = now
        if changed:
            save_reminders()
        try:  # day log (JUN-26): a reminder becoming due, done or missed is recorded once
            log.reminders(out, at=now)
        except Exception as err:  # noqa: BLE001
            sys.stderr.write(f"day log: {type(err).__name__}: {err}\n")
        return out

    # ---- the cheap gate (see view_verdict): per zone, the last photo the model saw and its answer ----
    views, changes = {}, {}
    gate_count = {"model_not_asked": 0, "something_moved": 0, "camera_moved": 0, "first_or_refresh": 0}

    def change_pictures(job, before, now, box):
        """The part of the view that changed, before and now, as two small pictures. Returns {name: url}."""
        folder = evidence_dir / job
        folder.mkdir(parents=True, exist_ok=True)
        made = {}
        for name, photo in (("moved_before", before), ("moved_now", now)):
            frame = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            h, w = frame.shape[:2]
            u0, v0, u1, v1 = box
            pad = 0.08  # a little of the surroundings, so the place can be recognised
            crop = frame[int(max(0, v0 - pad) * h):int(min(1, v1 + pad) * h), int(max(0, u0 - pad) * w):int(min(1, u1 + pad) * w)]
            scale = 480 / max(crop.shape[:2])
            if scale < 1:
                crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(folder / f"{name}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
            made[name.split("_")[1]] = f"/evidence/{job}/{name}.jpg"
        return made

    def keep_evidence(job, blob):
        """Unpack the pictures and the 3D file a job produced. Returns ({name: url}, splat url)."""
        images, splat_url = {}, None
        if blob:
            folder = evidence_dir / job
            folder.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
                for name in bundle.namelist():
                    if re.fullmatch(r"\w+\.(jpg|splat)", name):
                        (folder / name).write_bytes(bundle.read(name))
                        if name.endswith(".jpg"):
                            images[name[:-4]] = f"/evidence/{job}/{name}"
                        else:
                            splat_url = f"/evidence/{job}/{name}"
        return images, splat_url

    def pull_requests():
        """The repo's pull requests, for the dashboard's work panel. Asked from `gh` at most once a minute."""
        if time.time() - work["at"] > 60:
            work["at"] = time.time()
            try:
                out = subprocess.run(["gh", "pr", "list", "--repo", REPO, "--state", "all", "--limit", "15", "--json",
                                      "number,title,state,headRefName,url,updatedAt"], capture_output=True, text=True, timeout=20)
                work["pull_requests"] = json.loads(out.stdout) if out.returncode == 0 else []
            except (OSError, ValueError, subprocess.SubprocessError):
                work["pull_requests"] = []
        return work["pull_requests"]

    # --- route finding (route.py) ---------------------------------------------------------------
    route_cache = {}  # {zone: (what it was worked out for, the routes)}: /where is polled, a fix is routed once

    def routed(fix, stable=None):
        """The fix with "route" added: the way around furniture to each named place and each item
        seen (not the ones in doubt). It starts where the display stands: on the steady pose when
        there is one, else on the fix itself. A straight line, with the reason, where the map gives
        no route. The fix itself is not changed."""
        if route_finder is None:
            return fix
        start = fix
        if stable and stable.get("position") and stable.get("state") != "lost":
            start = {**fix, "state": "found", "position": stable["position"], "heading_deg": stable.get("heading_deg") or 0}
        targets = list(fix.get("waypoints") or []) + [s for s in seen.get(fix.get("zone"), {}).values() if not in_doubt(s)]
        key = (fix.get("job"), fix.get("at"), json.dumps([start.get("position"), [[t.get("name"), t.get("position")] for t in targets]]))
        if route_cache.get(fix.get("zone"), (None, None))[0] != key:
            route_cache[fix.get("zone")] = (key, route_finder.plan(start, targets))
        return {**fix, "route": route_cache[fix.get("zone")][1]}
    # --- end route finding ----------------------------------------------------------------------

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, code, data, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, code, body):
            # Compact on purpose: a position fix with its floor plan is about 25 kB this way and over 64 kB
            # indented, and the event API refuses to read more than 64 kB from /where.
            self.send_bytes(code, json.dumps(body, separators=(",", ":")).encode(), "application/json")

        def allowed(self, query):
            given = query.get("k", [""])[0] or self.headers.get("X-Key", "")
            if hmac.compare_digest(given.encode(), token.encode()):
                return True
            self.send_json(403, {"ok": False, "error": "Open the link that includes the access key (?k=...)."})
            return False

        def do_GET(self):
            url = urlparse(self.path)
            if not self.allowed(parse_qs(url.query)):
                return
            pages = {"/": "phone.html", "/dashboard": "dashboard.html", "/viewer": "viewer.html",
                     "/hud": "hud.html", "/places": "places.html"}
            if url.path in pages:
                return self.send_bytes(200, (HERE / pages[url.path]).read_bytes(), "text/html; charset=utf-8")
            if url.path == "/history":
                return self.send_json(200, {"ok": True, "checks": history[-40:][::-1]})
            if url.path == "/work":
                return self.send_json(200, {"ok": True, "repo": REPO, "pull_requests": pull_requests()})
            if url.path == "/changes":  # what was moved in view, newest first, and how often the model was spared
                zone = parse_qs(url.query).get("zone", [""])[0]
                listed = changes.get(zone, []) if zone else [c for per_zone in changes.values() for c in per_zone]
                return self.send_json(200, {"ok": True, "changes": sorted(listed, key=lambda c: -c["at"]), "counts": dict(gate_count)})
            if url.path == "/reminders":
                with reminder_lock:
                    return self.send_json(200, {"ok": True, "reminders": reminder_list(), "sensors": bool(api_base)})
            # ---- day log (JUN-26): the day as data, and the carer's page for it ----
            if url.path == "/dayview":
                return self.send_bytes(200, (HERE / "day.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/day":
                date = parse_qs(url.query).get("date", [""])[0]
                try:
                    datetime.fromisoformat(date + "T12:00:00") if date else None
                except ValueError:
                    return self.send_json(400, {"ok": False, "error": "GET /day?date=YYYY-MM-DD (no date: today)"})
                log_sensor_events(wait=True)
                with reminder_lock:
                    reminder_list()  # so a reminder that became due or missed since the last look is in the log
                return self.send_json(200, {"ok": True, **log.report(date or None)})
            # ---- end day log ----
            zone = parse_qs(url.query).get("zone", [""])[0]
            if url.path == "/where":
                fix = where.get(zone) if zone else max(where.values(), key=lambda f: f["ts"], default=None)
                if not fix:
                    return self.send_json(404, {"ok": False, "error": "No position fix yet. POST a photo to /locate first."})
                # --- route finding (route.py): adds "route" to the reply, from the pose the display stands on
                stable = steady(fix["zone"])
                fix = routed(fix, stable)
                return self.send_json(200, {**fix, "age_s": round(time.time() - fix["at"], 1), "seen": seen_list(fix["zone"], fix),
                                            "stable": stable})
            if url.path == "/waypoints":
                header, _ = link.call({"op": "waypoints", "zone": zone})
                return self.send_json(200 if header.get("ok") else 404, header)
            if url.path == "/watch":  # the zone's watched spots
                if not re.fullmatch(ZONE_NAME, zone):
                    return self.send_json(400, {"ok": False, "error": "GET /watch?zone=NAME"})
                header, _ = link.call({"op": "watches", "zone": zone})
                return self.send_json(200 if header.get("ok") else 404, header)
            if url.path == "/enrolled":  # the items shown to the camera once, for the whole home
                header, _ = link.call({"op": "enrolled"})
                return self.send_json(200 if header.get("ok") else 502, header)
            match = re.fullmatch(rf"/enrolled/({ITEM_NAME})/(\d{{1,2}})\.jpg", url.path)
            if match and len(match[1]) <= 32:
                header, picture = link.call({"op": "enrolled_image", "name": match[1], "n": int(match[2])})
                if header.get("ok"):
                    return self.send_bytes(200, picture, "image/jpeg")
                return self.send_json(404, header)
            match = re.fullmatch(r"/baseline/([a-z0-9_-]{1,32})/(\d{1,2})\.jpg", url.path)
            if match:
                header, picture = link.call({"op": "baseline_image", "zone": match[1], "frame": int(match[2])})
                if header.get("ok"):
                    return self.send_bytes(200, picture, "image/jpeg")
                return self.send_json(404, header)
            if url.path == "/status":
                header, _ = link.call({"op": "status"})
                return self.send_json(200 if header.get("ok") else 503, {**header, "answers": bool(api_base)})
            match = re.fullmatch(r"/evidence/([\w-]+)/(\w+)\.(jpg|splat)", url.path)
            if match and (evidence_dir / match[1] / f"{match[2]}.{match[3]}").exists():
                kind = "image/jpeg" if match[3] == "jpg" else "application/octet-stream"
                return self.send_bytes(200, (evidence_dir / match[1] / f"{match[2]}.{match[3]}").read_bytes(), kind)
            self.send_json(404, {"ok": False, "error": "not found"})

        def answer_service(self, op, raw, zone):
            """Answer a question (/ask) or speak a sentence (/speak). A question about a lost item is
            answered here, from what the position fixes saw. Everything else goes to the team's event API."""
            field = "question" if op == "ask" else "text"
            try:
                text = json.loads(raw)[field]
            except (ValueError, KeyError, TypeError):
                text = None
            if not isinstance(text, str) or not text.strip():
                return self.send_json(400, {"ok": False, "error": f'POST /{op} with a JSON body: {{"{field}": "..."}}'})
            text = text.strip()[:2000]
            # ---- day log (JUN-26): every question is logged, and a question about the day ("What did I do
            # today?", "When was I last in the kitchen?") is answered from the log before the event API is asked.
            if op == "ask":
                try:
                    log.question(text, topic=item_in(text))
                    about_day = None if item_in(text) else log.answer(text)
                except Exception as err:  # noqa: BLE001
                    about_day = None
                    sys.stderr.write(f"day log: {type(err).__name__}: {err}\n")
                if about_day:
                    if api_base:  # still counted by the event API, like a lost-item question
                        threading.Thread(target=log_question, args=(text,), daemon=True).start()
                    return self.send_json(200, {"ok": True, "question": text, "answer": about_day["answer"], "evidence": [], "point": None,
                                                "answered_by": "day log", "confidence_pct": about_day.get("confidence_pct")})
            # ---- end day log ----
            item = item_in(text) if op == "ask" else None
            if item:
                if api_base:
                    threading.Thread(target=log_question, args=(text,), daemon=True).start()
                return self.send_json(200, item_answer(item, zone, text))
            if not api_base:
                own = place_answer(text, zone) if op == "ask" else None
                if own:  # the way to a named place needs no event API
                    return self.send_json(200, {"ok": True, "question": text, "evidence": [], **own})
                return self.send_json(503, {"ok": False, "error": "The answer service is not connected. "
                                            "Start the relay with EVENT_API_URL and EVENT_API_TOKEN set."})
            request = urllib.request.Request(f"{api_base}/{op}", json.dumps({field: text}).encode(),
                                             {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"})
            try:
                with urllib.request.urlopen(request, timeout=8) as response:
                    data = response.read()
            except urllib.error.HTTPError as err:  # for /speak, 503 means no voice is set up: the page then uses its own
                return self.send_json(503 if err.code >= 500 else 502,
                                      {"ok": False, "error": f"The answer service refused that (HTTP {err.code})."})
            except OSError:
                return self.send_json(503, {"ok": False, "error": "Cannot reach the answer service."})
            if op == "speak":
                return self.send_bytes(200, data, "audio/mpeg")
            try:
                reply = json.loads(data)
                evidence = [{k: e.get(k) for k in ("type", "ts", "zone", "object")} for e in reply.get("evidence", [])]
                out = {"ok": True, "question": text, "answer": str(reply["answer"]), "evidence": evidence, "point": None}
                # The event API classified the question (and refused it if it was medical). If it wanted a
                # position and could not read one in time, the relay, which holds the position, answers.
                own = place_answer(text, zone) if out["answer"].startswith(NO_POSITION) else None
                if own:
                    out.update(own, answered_by="relay")
                return self.send_json(200, out)
            except (ValueError, KeyError, TypeError, AttributeError):
                return self.send_json(502, {"ok": False, "error": "The answer service sent something unreadable."})

        def change_reminder(self, query, zone, raw):
            """Add a reminder (JSON body), or with ?id=: delete=1, done=1 (finished by hand) or now=1 (show it now)."""
            flag = lambda name: bool(query.get(name, [""])[0])  # noqa: E731
            chosen = query.get("id", [""])[0]
            with reminder_lock:
                if chosen:
                    reminder = next((r for r in reminders if r["id"] == chosen), None)
                    if reminder is None:
                        return self.send_json(404, {"ok": False, "error": "No reminder with that id."})
                    if flag("delete"):
                        reminders.remove(reminder)
                    elif flag("done"):
                        reminder.update(done_at=time.time(), done_note="")
                    elif flag("now"):
                        reminder.update(now_at=time.time(), done_at=None, done_note="")
                    else:
                        return self.send_json(400, {"ok": False, "error": "POST /reminder?id=ID with delete=1, done=1 or now=1"})
                else:
                    try:
                        new = json.loads(raw)
                        text, at = str(new["text"]).strip()[:120], str(new["time"])
                        place = str(new.get("place") or "").strip()[:40]
                    except (ValueError, KeyError, TypeError, AttributeError):
                        text, at, place = "", "", ""
                    if not text or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at) or len(reminders) >= 20:
                        return self.send_json(400, {"ok": False, "error": 'POST /reminder with a JSON body: {"text": "...", '
                                                    '"time": "HH:MM", "place": "pill box"} (at most 20 reminders)'})
                    reminders.append({"id": secrets.token_hex(4), "text": text, "time": at, "place": place, "zone": zone,
                                      "until": "pillbox_open" if "pill" in place.lower() else ""})
                save_reminders()
                return self.send_json(200, {"ok": True, "reminders": reminder_list(), "sensors": bool(api_base)})

        def change_setup(self, op, query, zone, size):
            """What the carer sets once. /scale: the real distance between two named places. /watch: a box
            in a baseline picture to check for changes. /enroll: a photo of an item to recognise.
            Everything but the photo is in the query. The gateway checks it all again before it writes."""
            first = lambda name: query.get(name, [""])[0]  # noqa: E731
            delete = bool(first("delete"))
            if op == "enroll":
                name, photo = item_name(first("name")), b""
                req = {"op": "enroll", "name": name}
                if not name:
                    self.rfile.read(min(size, 65536))
                    return self.send_json(400, {"ok": False, "error": "Give the item a plain name: letters, digits and spaces, at most 32 characters."})
                if delete:  # the whole item, or with n= one of its photos
                    self.rfile.read(min(size, 65536))
                    req["delete"] = True
                    if first("n"):
                        if not re.fullmatch(r"\d{1,2}", first("n")):
                            return self.send_json(400, {"ok": False, "error": "POST /enroll?name=ITEM&n=PHOTO&delete=1"})
                        req["n"] = int(first("n"))
                else:
                    if not 0 < size <= PHOTO_BYTES:
                        return self.send_json(400, {"ok": False, "error": f"Send one photo of the item, at most {PHOTO_BYTES >> 20} MB."})
                    photo = self.rfile.read(size)
                    try:
                        if not is_photo(photo):
                            raise ValueError("not a picture")
                        photo = item_photo(photo)
                    except ValueError:
                        return self.send_json(400, {"ok": False, "error": "That is not a photo. Use a JPEG, PNG or WebP picture."})
                header, _ = link.call(req, photo)
                return self.send_json(200 if header.get("ok") else 400, header)
            self.rfile.read(min(size, 65536))  # scale and watch: everything is in the query
            if not re.fullmatch(ZONE_NAME, zone):
                return self.send_json(400, {"ok": False, "error": "Pick a room first."})
            req = {"op": op, "zone": zone}
            try:
                if op == "scale" and delete:
                    req["delete"] = True
                elif op == "scale":
                    a, b = " ".join(first("a").split()), " ".join(first("b").split())
                    if not a or not b or a == b or max(len(a), len(b)) > 40:
                        raise ValueError("two different places")
                    req.update(a=a, b=b, metres=in_range(first("metres"), 0.1, 50))
                else:
                    req["name"] = plain_name(first("name"))
                    if not req["name"]:
                        raise ValueError("a plain name")
                    if delete:
                        req["delete"] = True
                    else:
                        if not re.fullmatch(r"\d{1,2}", first("frame")):
                            raise ValueError("a baseline picture")
                        req.update(frame=int(first("frame")), box=[in_range(first(k), 0, 1) for k in ("u0", "v0", "u1", "v1")])
            except ValueError:
                return self.send_json(400, {"ok": False, "error": {
                    "scale": "Pick two different places and type the distance between them in metres, from 0.1 to 50.",
                    "watch": "Give the spot a plain name (letters, digits and spaces) and drag a box in the picture."}[op]})
            header, _ = link.call(req)
            return self.send_json(200 if header.get("ok") else 400, header)

        def do_POST(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self.allowed(query):
                return
            op, zone = url.path.strip("/"), query.get("zone", [""])[0]
            size = int(self.headers.get("Content-Length", 0))
            if op in ("ask", "speak"):
                return self.answer_service(op, self.rfile.read(min(size, 65536)), zone)
            if op == "reminder":
                return self.change_reminder(query, zone, self.rfile.read(min(size, 65536)))
            # --- route finding (route.py): the way from any standpoint on a map, without a new photo
            if op == "route":
                try:
                    ask = json.loads(self.rfile.read(min(size, 262144)))
                    asked = {"state": "found", "position": ask["position"], "heading_deg": ask.get("heading_deg", 0), "map": ask["map"]}
                    targets = [{"name": str(t["name"]), "position": t["position"]} for t in ask.get("waypoints") or []]
                except (ValueError, KeyError, TypeError, AttributeError):
                    return self.send_json(400, {"ok": False, "error": 'POST /route with a JSON body: {"position": [east, north], '
                                                '"heading_deg": 0, "map": {"points": [...], "cameras": [...]}, "waypoints": [...]}'})
                if route_finder is None:
                    return self.send_json(503, {"ok": False, "error": "route.py is not next to the relay."})
                return self.send_json(200, {"ok": True, "route": route_finder.plan(asked, targets)})
            # --- end route finding
            if op == "forget":  # drop what is remembered about one item in a zone, or about all of them
                self.rfile.read(min(size, 65536))
                name, spots = query.get("name", [""])[0], seen.get(zone, {})
                for known in [n for n in spots if not name or n == name]:
                    del spots[known]
                try:
                    seen_file.write_text(json.dumps(seen))
                except OSError:
                    pass
                return self.send_json(200, {"ok": True, "zone": zone, "seen": seen_list(zone, where.get(zone))})
            if op == "waypoint":  # name or remove a place; everything is in the query, the body is ignored
                self.rfile.read(min(size, 65536))
                req = {"op": "waypoint", "zone": zone, "name": query.get("name", [""])[0]}
                try:
                    if query.get("delete", [""])[0]:
                        req["delete"] = True
                    else:
                        req.update(frame=int(query["frame"][0]), u=float(query["u"][0]), v=float(query["v"][0]))
                except (KeyError, ValueError):
                    return self.send_json(400, {"ok": False, "error": "POST /waypoint?zone=NAME&name=PLACE&frame=N&u=0..1&v=0..1"})
                header, _ = link.call(req)
                return self.send_json(200 if header.get("ok") else 400, header)
            if op in ("scale", "watch", "enroll"):  # what the carer sets once: see change_setup
                return self.change_setup(op, query, zone, size)
            if op not in FRAMES or not zone or not 0 < size <= MAX_BODY:
                return self.send_json(400, {"ok": False, "error": "POST /baseline, /check or /locate with ?zone=NAME and a video (or, for /locate, a photo) as the body."})
            t0 = time.time()
            clip = self.rfile.read(size)
            seconds = {}
            shot = clip if op == "locate" and is_photo(clip) else None  # the photo as it came, for the cheap gate
            try:
                if op == "locate" and is_photo(clip):
                    clip = one_photo(clip)
                elif pick and clip[:2] != b"PK":  # a zip is a frame bundle the phone app already picked: pass it on
                    clip, n = pick(clip, FRAMES[op])
            except ValueError:
                return self.send_json(400, {"ok": False, "error": "Could not read that file as a video or a photo."})
            seconds["pick_frames"] = round(time.time() - t0, 2)
            # ---- the cheap gate: is this the view the model has already seen? (&fresh=1 asks the model anyway) ----
            flag = lambda name: bool(query.get(name, [""])[0])  # noqa: E731
            now_view, verdict, last = None, None, views.get(zone)
            if shot is not None and not flag("fresh") and not flag("view"):
                try:
                    now_view = view_print(shot)
                except Exception:  # noqa: BLE001  the gate is an extra: without it the model is simply asked
                    now_view = None
            if now_view is not None and last is not None:
                verdict = view_verdict(last["print"], now_view)
                if verdict["kind"] == "same" and time.time() - last["at"] <= REUSE_FOR_S:
                    gate_count["model_not_asked"] += 1
                    fix = {**last["fix"], "at": time.time(), "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "reused": True, "seconds": {"total": round(time.time() - t0, 3)},
                           "view": {**verdict, "model_asked": False, "counts": dict(gate_count)}}
                    where[zone] = fix
                    stable = steady(zone, last["result"], t0)  # the same pose, seen again now
                    log_fix(zone, fix, {**last["result"], "items": []})  # still here; nothing new was looked for
                    return self.send_json(200, {**routed(fix, stable), "age_s": 0.0, "seen": seen_list(zone, fix), "stable": stable})
            device = "cpu" if query.get("device", [""])[0] == "cpu" else "gpu"
            job = {"op": op, "zone": zone, "device": device, "view": bool(query.get("view", [""])[0])}
            if op == "locate":  # the spots where items were seen, so the worker can look at them again
                job["remembered"] = [{"name": s["name"], "position": s["position"]} for s in seen.get(zone, {}).values()]
            header, blob = link.call(job, clip)
            seconds["total"] = round(time.time() - t0, 2)
            if not header.get("ok"):
                return self.send_json(502, {**header, "seconds": seconds})
            result = header["result"]
            if op == "baseline":
                views.pop(zone, None)  # new pictures of the room: the gate starts again
                return self.send_json(200, {"ok": True, "zone": zone, "frames": result["frames"], "seconds": seconds})
            images, splat_url = keep_evidence(header["job"], blob)
            if op == "locate":
                for part in ("points", "cameras"):  # whole centimetres: 32-bit numbers print with 17 digits otherwise
                    if isinstance(result.get("map"), dict):
                        result["map"][part] = [[round(float(v), 2) for v in p] for p in result["map"].get(part) or []]
                headline, detail = where_in_plain_words(result)
                fix = {"ok": True, "zone": zone, "job": header["job"], "headline": headline, "detail": detail, **result,
                       "images": images, "splat": splat_url, "seconds": {**seconds, **result.get("seconds", {})},
                       "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "at": time.time()}
                fix["confidence_pct"] = track.fix_confidence_pct(result)  # this one fix on its own, as a percentage
                if now_view is not None:  # the cheap gate: this photo is now the one the model has seen
                    kind = verdict["kind"] if verdict else "first"
                    gate_count[{"local": "something_moved", "moved": "camera_moved"}.get(kind, "first_or_refresh")] += 1
                    fix["view"] = {**(verdict or {"kind": "first"}), "model_asked": True, "counts": dict(gate_count)}
                    if kind == "local":  # the camera stood still and something in view was moved, taken or put down
                        try:
                            fix["view"]["images"] = change_pictures(header["job"], last["photo"], shot, verdict["box"])
                        except Exception:  # noqa: BLE001
                            fix["view"]["images"] = {}
                        changes.setdefault(zone, []).append({
                            "zone": zone, "at": time.time(), "time": home_time(time.time()).strftime("%H:%M:%S"),
                            "since_s": round(time.time() - last["at"]), "box": verdict["box"], "share": verdict["share"],
                            "images": fix["view"]["images"], "job": header["job"]})
                        del changes[zone][:-20]
                    views[zone] = {"print": now_view, "photo": shot, "result": result, "fix": fix, "at": time.time()}
                where[zone] = fix
                try:
                    where_file.write_text(json.dumps(where))
                except OSError:
                    pass
                remember_items(zone, result, images)
                # t0: when the photo reached the relay. The steady position ages from the photo, not from the answer.
                stable = steady(zone, result, t0)
                # The day log gets the fix itself, not the steady answer. A found fix is right at the moment of its
                # photo, which is what a diary needs; the steady answer is cautious about the seconds after it and is
                # rarely "steady" at one photo every 6 s, so visits would go missing. The two were measured apart.
                log_fix(zone, fix, result)
                log_sensor_events()
                fix = routed(fix, stable)  # --- route finding (route.py): adds "route" to the reply; the day log got the fix without it
                return self.send_json(200, {**fix, "age_s": 0.0, "seen": seen_list(zone, fix), "stable": stable})
            headline, detail = in_plain_words(result)
            events = to_events(result, zone, images)
            body = {"ok": True, "zone": zone, "job": header["job"], "headline": headline, "detail": detail,
                    "verdict": result["verdict"], "events": events, "images": images, "splat": splat_url,
                    "device": result.get("device"), "on_gpu": result.get("on_gpu"), "host": result.get("host"),
                    "settings": result.get("settings"), "gaussians": result.get("gaussians"),
                    "median_depth": result.get("median_depth"), "watch": result.get("watch") or [],
                    "seconds": {**seconds, **result.get("seconds", {})}}
            history.append({**body, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            del history[:-200]
            try:
                history_file.write_text(json.dumps(history))
            except OSError:
                pass
            body["report"] = result
            if event_api and events:
                try:
                    body["posted_to_event_api"] = post_events(event_api, events)
                except OSError as err:
                    body["posted_to_event_api"] = f"failed: {err}"
            self.send_json(200, body)

        def log_message(self, fmt, *args):
            line = (fmt % args).replace(token, "KEY")  # keep the key out of the log
            sys.stderr.write(f"{time.strftime('%H:%M:%S')} {line}\n")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ssh-host", default="iridis", help="SSH host alias for the cluster")
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 lets a phone on the same network reach the relay")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--token", default=secrets.token_urlsafe(9), help="access key; a random one is made if not given")
    args = ap.parse_args()
    link = Link(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                 "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", args.ssh_host, REMOTE])
    evidence_dir = Path(tempfile.gettempdir()) / "beacon-evidence"
    evidence_dir.mkdir(exist_ok=True)
    event_api, api_token = os.environ.get("EVENT_API_URL"), os.environ.get("EVENT_API_TOKEN")
    handler = make_handler(link, evidence_dir, args.token, event_api, api_token=api_token)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    addresses = ["127.0.0.1"]
    if args.host == "0.0.0.0":
        addresses += sorted({a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127.")})
    print("relay running. Phone page, open one of:", flush=True)
    for address in addresses:
        print(f"  http://{address}:{args.port}/?k={args.token}", flush=True)
    print(f"dashboard: http://127.0.0.1:{args.port}/dashboard?k={args.token}", flush=True)
    print(f"day log:   http://127.0.0.1:{args.port}/dayview?k={args.token}", flush=True)
    print("questions (/ask, /speak): " + ("passed on to the event API" if event_api and api_token
                                           else "off (EVENT_API_URL and EVENT_API_TOKEN are not both set)"), flush=True)
    print(f"frame picking on this laptop: {'on' if rc else 'off (OpenCV not found)'}; evidence images in {evidence_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        link.close()


if __name__ == "__main__":
    main()
