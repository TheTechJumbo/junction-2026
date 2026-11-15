#!/usr/bin/env python3
"""Relay. Runs on the laptop that holds the GPU cluster's SSH key: HTTP in, one SSH connection out.

    python live/relay.py                    # this laptop only: http://127.0.0.1:8090
    python live/relay.py --host 0.0.0.0     # also reachable from a phone on the same network

It prints the link to open. The link carries an access key, and every request needs that key
(`?k=...` or an `X-Key` header), because the relay spends GPU time on your GPU cluster account.

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
                                  fixes, with "confidence_pct" and "show", see track.py. In a zone with a trained
                                  splat every photo is also placed against the splat's keyframes on this laptop,
                                  splat_locate.py; while that pose is fresh it is "position" and "stable".
                                  Both carry "source": "fused" (that pose), "full" (the model's fix) or "instant"
                                  (the cosine match), and "uncertainty_m": the 95% radius in metres, null if unknown)
    GET  /waypoints?zone=kitchen  the zone's named places
    POST /waypoint?zone=kitchen&name=pill+box&frame=3&u=0.4&v=0.6   name a spot in baseline picture 3
    POST /waypoint?zone=kitchen&name=pill+box&delete=1
    POST /waypoint?zone=kitchen&name=front+door&live=1&u=0.4&v=0.6   name a spot on a photo (the body; without
                                  one, the newest photo, GET /frame): placed in 3D from that photo's own fix, no
                                  picture added to the room
    POST /waypoint?zone=kitchen&name=exit&east=1.2&north=3.4&up=0.9   a place at a map position (east, north, up in
                                  map metres): picked on the 3D page's view from above
    POST /places?zone=kitchen     place the named places in the room map's axes from its pictures as they are (no
                                  new picture, no map built, a trained splat kept); also done after each place named
                                  in a picture. GET /room lists them as "places"
    GET  /hud                     the glasses display page: arrow and distance to a place
    GET  /places                  name places by clicking in a baseline picture, and test a fix
    POST /ask?zone=kitchen        body = {"question": "Did I take my pills?"} -> the answer in words
                                  ("Where are my keys?" is answered here: where a position fix last saw them)
    POST /forget?zone=kitchen&name=keys   forget where an item was seen (no name: every item in the zone)
    POST /speak                   body = {"text": "..."} -> the sentence as MP3 speech
    GET  /changes?zone=kitchen    what was moved in view while the camera stood still, with before and now pictures
    GET  /viewer?live=kitchen     the room in 3D with the wearer moving in it, live (needs the room map below)
    POST /map?zone=kitchen        build the room map: the baseline pictures in one pass, as one splat (10 to 20 s)
    POST /locate?...&quick=0      with --quick: ask the model (or the cheap gate), not the instant fix
    POST /recording?zone=Z&session=S&seq=N&final=0|1   one chunk of a room recorded continuously by the phone or the
                                  glasses: a zip of frames NNNNNN_<ms>.jpg (or a video). A chunk sent twice counts once.
                                  Every REC_MAP_EVERY new frames the room map is built again from the recording's
                                  keyframes; at final=1, and every REC_TRAIN_EVERY_S while it runs, a splat is trained
                                  on the GPU (--train-host) and /viewer?live=Z shows it
    GET  /recording?zone=Z        the newest session: chunks, frames, gaps, room map updates, training
    GET  /room?zone=kitchen       the room map: its splat (/evidence/.../room.splat), places and baseline cameras
    GET  /frame?zone=kitchen      the newest photo that reached /locate, as it came (the dashboard's camera view)
    GET  /worker_log?lines=60     the end of the worker's log: model load, timings and peak GPU memory per job

Every photo sent to /locate first goes through a cheap gate (view_verdict): the same view as the last one
the model saw gets the last answer again in milliseconds ("reused": true, at most 45 s long); a view with a
small part changed is reported under "view" with the place of the change; &fresh=1 asks the model anyway.
    GET  /reminders               the reminders, each with its state today: later, due, done or missed
    POST /reminder                body = {"text": "Time to take your pills", "time": "14:00", "place": "pill box"}
    POST /reminder?id=ID&now=1    show it now (also &done=1: finished by hand, &delete=1)
    GET  /navigate?zone=kitchen   the wearer's map: where they are and face, the places, the floor plan (no route);
                                  with no fix since the newest room map, that map (cut from its splat) and no position
    GET  /navigate?zone=kitchen&to=pill+box   the way from where the wearer is now to a named place (the name may be
                                  loose: "Pill box", "pillbox", "the pill box"): the route around furniture, the
                                  next turn in short words, whether they are there, and the floor plan to draw it on.
                                  "stale": true when the newest fix is over NAV_STALE_S old or not sure. While it is
                                  polled (NAV_GUIDE_S), /where carries "navigate": {"name", "active", "distance_m"};
                                  &stop=1 ends it, arriving ends it
    POST /route                   body = {"position": [east, north], "heading_deg": 0, "map": {...}, "waypoints": [...]}
                                  -> the way around furniture from that standpoint (route.py). /locate and
                                  /where carry the same under "route", from the wearer's own position
    GET  /day?date=YYYY-MM-DD     what the wearer did that day (no date: today): timeline and two summaries
    GET  /dayview                 the carer's page for one day
    GET  /care?zone=kitchen       the caretaker's page: the room as a Gaussian splat with the wearer moving in it,
                                  the named places as labels in 3D, and what the wearer is doing (W A S D to move)
    GET  /care/space?zone=kitchen the splat that page shows, and the matrix that puts a trained splat into the room
                                  map's axes, where the fixes and places are (care_space.py)

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
and sends those (about 1 MB) to the GPU cluster. Without OpenCV it sends the video as it is.
The SSH connection is opened on first use and reopened if it drops.
"""
import argparse
import shlex
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
def remote_command(root):
    if root is None:
        return "cd /scratch/$USER/junction-recon && exec .venv-recon/bin/python -u live/gateway.py"
    # SSH passes this as shell text. Require an absolute path and quote it as one argument.
    if not root.startswith("/") or "\n" in root or "\x00" in root:
        raise ValueError("remote root must be an absolute path")
    return f"cd -- {shlex.quote(root)} && exec .venv-recon/bin/python -u live/gateway.py"
MAX_BODY = 200 * 1024 * 1024
FRAMES = {"baseline": 24, "check": 12, "locate": 3}  # keep in step with worker.py
LONG_SIDE = 518  # the model's input size: no point sending more pixels
BIG_SIDE = 1280  # the wearer's newest photo is also sent at this size, for finding small items
REMINDER_WINDOW = 2 * 3600  # a reminder nobody finished within two hours counts as missed
NEAR_PLACE_M = 1.5  # an item this close to a named place is described as "near" it
# GET /navigate: there within NAV_ARRIVED_M; a fix older than NAV_STALE_S, or not "found", is said to be stale; the
# floor plan sent with it is cut to NAV_PLAN_POINTS points.
NAV_ARRIVED_M = 0.7
NAV_STALE_S = 10.0
NAV_PLAN_POINTS = 600
NAV_GUIDE_S = 15.0
RESUME_POLL_S = 30.0  # how often a training left running by a restart is looked at; up to 10x this between tries when
# the GPU machine does not answer (a refused SSH connection must not end the wait)  # a guidance (GET /navigate&to=) not polled for this long is over: /where stops saying where to


def turn_words(bearing_deg, distance_m):
    """The next move in a few words: bearing_deg from straight ahead (+ to the right)."""
    if distance_m <= NAV_ARRIVED_M:
        return "you're there"
    b, side = abs(bearing_deg), "right" if bearing_deg > 0 else "left"
    if b <= 15:
        return "ahead"
    if b <= 45:
        return f"slightly {side}"
    if b <= 135:
        return f"turn {side}"
    return "turn around"


def match_place(asked, names):
    """The place a loosely typed or spoken name means, or None: the same words ignoring case, spaces, "the", "my" and
    punctuation; else one name starting with or containing it (or it containing a name); else the closest spelling."""
    import difflib

    def plain(text):
        words = re.sub(r"[^\w ]+", " ", str(text).lower()).split()
        return " ".join(w for w in words if w not in ("the", "my", "a", "an", "to"))

    want = plain(asked)
    if not want:
        return None
    by_plain = {plain(n): n for n in names}
    squashed = {k.replace(" ", ""): n for k, n in by_plain.items()}
    if want in by_plain:
        return by_plain[want]
    if want.replace(" ", "") in squashed:
        return squashed[want.replace(" ", "")]
    near = [n for k, n in by_plain.items() if k.startswith(want) or want in k or (k and k in want)]
    if len(near) == 1:
        return near[0]
    close = difflib.get_close_matches(want, list(by_plain), n=1, cutoff=0.6)
    return by_plain[close[0]] if close else None


# Things every fix also looks for (the item finder on the worker) and names as a place by themselves once AUTO_VIEWS
# fixes agree where one is: within AUTO_RADIUS_M of each other, each with the finder's score AUTO_SCORE or more. The
# place is marked "auto" and moves with later sightings; a place someone named keeps its own position.
AUTO_PLACES = [n.strip() for n in os.environ.get("BEACON_AUTO_PLACES", "pill box, front door").split(",") if n.strip()]
AUTO_SCORE = 0.4
AUTO_VIEWS = 2
AUTO_RADIUS_M = {"pill box": 0.4, "front door": 1.0}  # others: 0.6 m
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

# ---- caretaker's page (/care): the trained splat put into the room map's axes (care_space.py, needs NumPy) ----
try:
    import care_space
except ImportError:  # no NumPy here: the page shows the room map, where the wearer is drawn as before
    care_space = None

# ---- the wearer in the trained splat (splat_locate.py): every photo placed against the splat's own keyframes on this
# laptop's CPU (SIFT + PnP, ~0.3 s), in the splat's axes, so no room-map-to-splat fit error. BEACON_SPLAT_POSE=0: off.
try:
    import splat_locate
except ImportError:  # no OpenCV or NumPy: the instant and the model's fixes as before
    splat_locate = None
SPLAT_POSE = os.environ.get("BEACON_SPLAT_POSE", "1") != "0"


class Link:
    """One long-lived connection to the gateway, speaking its JSON-line-plus-bytes protocol."""

    def __init__(self, argv, name="the GPU cluster"):
        self.argv, self.proc, self.lock = argv, None, threading.Lock()
        self.name = name  # where the gateway runs, for error messages: "the GPU cluster" or "the local gateway"

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise ConnectionError(f"connection to {self.name} closed")
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
                        return {"ok": False, "error": f"Cannot reach {self.name}: {err}"}, b""


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


# ---- instant pose (relay --quick): cosine similarity to the room's pictures, no model ----
# The room map (POST /map) knows where each of the room's pictures was taken and which way it faced. A clean photo
# (sharp, not dark) is compared with each picture as a small grey picture with its brightness and contrast taken out,
# slid sideways: the best cosine similarity names the picture the wearer stands at, and the slide turns its heading
# by that share of the camera's field of view. Milliseconds on the laptop's CPU. The model (a full fix) still runs
# at least every FULL_EVERY_S while photos come, and gives the places their positions.
QUICK_W = 64          # the small pictures are this many pixels wide (one pixel: about 1 degree of a phone camera)
QUICK_SHIFT = 0.25    # slid sideways up to this share of the width each way (about 12 degrees of a phone held upright)
QUICK_FOUND, QUICK_MIN = 0.7, 0.45  # similarity for "found", and below which the photo is not placed at all
QUICK_SHARP = 60.0    # Laplacian variance of a 320 px wide grey copy below this: a blurred photo, skipped
FULL_EVERY_S = 4.0


def quick_print(frame, shape=None):
    """A frame as a small grey picture (QUICK_W wide), cropped first to the aspect of `shape` (h, w), normalised."""
    if shape is not None:
        h, w = frame.shape[:2]
        want = shape[1] / shape[0]
        if w / h > want:
            keep = max(1, round(h * want))
            frame = frame[:, (w - keep) // 2:(w - keep) // 2 + keep]
        else:
            keep = max(1, round(w / want))
            frame = frame[(h - keep) // 2:(h - keep) // 2 + keep]
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    size = (QUICK_W, max(8, round(QUICK_W * grey.shape[0] / grey.shape[1])))
    small = cv2.GaussianBlur(cv2.resize(grey, size, interpolation=cv2.INTER_AREA), (3, 3), 0).astype(np.float32)
    return (small - small.mean()) / (small.std() + 1e-3)


def quick_match(now, keys):
    """[(cosine similarity, frame, slide as a share of the width)] for each key picture, best first. A positive slide:
    the room appears further right in the photo than in the picture, so the wearer faces further left."""
    out = []
    width = now.shape[1]
    reach = int(QUICK_SHIFT * width)
    for frame, key in keys:
        if key.shape != now.shape:
            continue
        scores = []
        for s in range(-reach, reach + 1):
            a = now[:, max(0, s):width + min(0, s)]
            b = key[:, max(0, -s):width - max(0, s)]
            a, b = a - a.mean(), b - b.mean()
            scores.append(float((a * b).sum() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-6)))
        i = int(np.argmax(scores))
        slide = float(i - reach)
        if 0 < i < len(scores) - 1:  # between whole pixels: the top of the parabola through the best three
            down = scores[i - 1] - 2 * scores[i] + scores[i + 1]
            if down < 0:
                slide += 0.5 * (scores[i - 1] - scores[i + 1]) / down
        out.append((scores[i], frame, slide / width))
    return sorted(out, reverse=True)


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


REC_MAP_EVERY = 60         # a recording's room map is built again after this many new frames
REC_TRAIN_EVERY_S = 600    # and a splat trained on the GPU this often while it runs (and when it ends)
REC_VIDEO_MAX = 4 << 30    # a whole video sent as one chunk (the glasses: about 110 MB a minute) may be this big
REC_VIDEO_FPS = 15         # frames a second taken from such a video (?fps=1..30)
REC_PART_FRAMES = 120      # and sent on to the gateway in parts of this many frames


def video_parts(path, fps, start_ms):
    """A video file as zips of frames NNNNNN_<ms>.jpg (upright, long side 960, like the app's), REC_PART_FRAMES each."""
    cap = cv2.VideoCapture(str(path))
    rate = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step, n, kept, part = max(1, round(rate / fps)), 0, 0, None
    parts = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if n % step == 0:
            scale = 960 / max(frame.shape[:2])
            if scale < 1:
                frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if part is None:
                buffer = io.BytesIO()
                part = zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED)
            part.writestr(f"{kept:06d}_{start_ms + int(1000 * n / rate)}.jpg", cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
            kept += 1
            if kept % REC_PART_FRAMES == 0:
                part.close()
                parts.append(buffer.getvalue())
                part = None
        n += 1
    cap.release()
    if part is not None:
        part.close()
        parts.append(buffer.getvalue())
    return parts, n
SESSION_NAME = r"[A-Za-z0-9_-]{1,64}"
AUTO_SPLATS_KEPT = 12  # with --splat-every-fix, only the newest few of those 3D files stay on disk (each about 4 MB)


def make_handler(link, evidence_dir, token, event_api=None, pick=pick_frames if rc else None, api_token=None,
                 splat_every_fix=False, quick=False, train_host=None, bg_link=None, remote_root=None):
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
                answer = trackers[zone].current(time.time()) if zone in trackers else None
        except Exception:  # the steady position is an extra: it must never cost the wearer a fix
            answer = None
        answer = splat_steady(zone, answer)  # in a zone with a trained splat, its own steady pose while it is fresh
        if isinstance(answer, dict) and "source" not in answer:  # track.py's, made from the model's fixes
            answer = {**answer, "source": "full", "uncertainty_m": None}
        return answer

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
    received = {}  # {zone: {"at", "photo"}}: the newest photo that reached /locate, model asked or not (the dashboard shows it)
    auto_splats = []  # the 3D files made by --splat-every-fix, oldest first (see AUTO_SPLATS_KEPT)
    rooms_file = evidence_dir / "rooms.json"
    try:
        rooms = json.loads(rooms_file.read_text())  # {zone: the room map (POST /map): its splat and its facts}
    except (OSError, ValueError):
        rooms = {}
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

    building = {}  # {zone: "running" or "again"}: one room map build at a time per zone, one more if asked meanwhile
    placing = {}  # {zone: "running" or "again"}: the same for placing the named places (place_room)
    auto_seen = {}  # {zone: {name: [sightings]}}: the AUTO_PLACES things the item finder saw, newest last
    # ---- caretaker's page (/care) ----
    space_made, space_lock = {}, threading.Lock()  # {zone: ((room map job, trained splat), the reply)}: worked out once
    space_numbers = {}  # {zone: (room map keyframe_numbers, trained keyframe numbers)} when one recording made both

    def care_view(zone):
        """The splat the caretaker's page shows for a zone, and the matrix that takes it into the room map's axes,
        where the wearer, the named places and the day's path are. The trained splat when its cameras line up with
        the room map's (care_space.py), else the room map itself. Returns (HTTP status, reply)."""
        got = rooms.get(zone)
        if not got or not got.get("splat"):
            return 404, {"ok": False, "error": f"No room map for '{zone}' yet: POST /map?zone={zone}."}
        trained = dict(got.get("trained") or {})
        match = re.fullmatch(r"/evidence/([\w-]+)/trained\.splat", trained.get("splat") or "")
        # Labels made again after training (video_map.py --labels-only, same cameras) sit next to the splat.
        if match and not trained.get("labels") and (evidence_dir / match[1] / "labels.json").exists():
            trained["labels"] = f"/evidence/{match[1]}/labels.json"
        plain = {"ok": True, "zone": zone, "kind": "map", "splat": got["splat"], "matrix": None, "fit": None,
                 "room_splat": got["splat"], "trained": trained or None, "median_depth_m": got.get("median_depth_m"),
                 "fov_deg": got.get("fov_deg"), "cameras": got.get("cameras") or [], "places": got.get("places") or [],
                 "map_at": got.get("at")}  # when this room map was made: positions from before are in another map's axes
        if not match:
            return 200, {**plain, "why": "No trained splat for this room yet: the room map is shown."}
        made_for = (got.get("job"), trained["splat"], trained.get("labels"))
        with space_lock:
            if zone in space_made and space_made[zone][0] == made_for:
                return 200, space_made[zone][1]
            fit, why = None, None
            if care_space is None:
                why = "NumPy is missing on this laptop: the room map is shown."
            else:
                hint = (got.get("median_depth_m") or 0) / trained["median_depth"] if trained.get("median_depth") else None
                try:
                    cameras_file = evidence_dir / match[1] / "cameras.npz"
                    # One recording behind both (Record room, then its training): pair the very same frames.
                    same = got.get("session") and got.get("session") == trained.get("session") and got.get("keyframe_numbers")
                    numbers = (got["keyframe_numbers"], care_space.load_keyframes(cameras_file)) if same else None
                    numbers = numbers if numbers and numbers[1] else None
                    fit = care_space.space(care_space.load_cameras(cameras_file), got.get("cameras") or [], hint, numbers)
                    space_numbers[zone] = numbers
                    why = fit.get("why")
                except (OSError, KeyError, ValueError) as err:
                    why = f"The trained splat's cameras could not be read ({type(err).__name__}): the room map is shown."
            reply = {**plain, "kind": "trained", "splat": trained["splat"], "matrix": fit["matrix"], "fit": fit} \
                if fit and fit.get("ok") else {**plain, "fit": fit, "why": why}
            space_made[zone] = (made_for, reply)
            return 200, reply

    views_kept = {}  # {trained splat folder: (views.json's modification time, care_space.load_views)}

    def care_named(zone, reply):
        """The zone's named places (/waypoints) in map axes from the trained splat's depth (care_space.place_points),
        so /care shows a place as soon as it is named, before a position fix or a new room map places it. Asked
        every time: places can be named at any moment. [] when the room has no trained splat with exported views."""
        paired = (reply.get("fit") or {}).get("paired")
        if reply.get("kind") != "trained" or not paired or care_space is None:
            return []
        folder = evidence_dir / reply["splat"].split("/")[2]
        try:
            stamp = (folder / "views.json").stat().st_mtime
            with space_lock:
                if views_kept.get(folder, (None,))[0] != stamp:
                    views_kept[folder] = (stamp, care_space.load_views(folder))
                views = views_kept[folder][1]
            header, _ = link.call({"op": "waypoints", "zone": zone})
            return care_space.place_points(header.get("waypoints") or [], paired, views, reply["matrix"],
                                           space_numbers.get(zone) if reply["fit"].get("paired_by") == "frame numbers" else None)                 if header.get("ok") else []
        except Exception as err:  # noqa: BLE001  the page still shows the places the fixes carry
            sys.stderr.write(f"care places: {type(err).__name__}: {err}\n")
            return []
    # ---- end caretaker's page ----

    # ---- the wearer in the trained splat (splat_locate.py) ----
    splat_live = splat_locate.Live(evidence_dir / "splat_track.json", log=lambda text: sys.stderr.write(text + "\n")) \
        if splat_locate is not None and cv2 is not None and SPLAT_POSE else None

    def splat_space(zone):
        """(the trained splat's folder, the matrix into map axes) when the caretaker's page shows a trained splat
        for this zone (care_view), else None."""
        if splat_live is None:
            return None
        code, space = care_view(zone)
        if code != 200 or space.get("kind") != "trained" or not space.get("matrix"):
            return None
        return evidence_dir / space["splat"].split("/")[2], space["matrix"]

    def splat_waypoints(zone, pose, places=None):
        """The zone's places measured from a splat pose (distance, bearing: 0 ahead, + to the right)."""
        last = where.get(zone) or {}
        places = places or [w for w in last.get("waypoints") or [] if w.get("position")] or (rooms.get(zone) or {}).get("places") or []
        out = []
        for w in places:
            if w.get("position") is None:
                continue
            distance, bearing = from_here(w["position"], pose)
            out.append({"name": w["name"], "distance_m": distance, "bearing_deg": bearing, "position": w["position"]})
        return out

    def splat_photo(zone, photo):
        """Place a photo in the zone's trained splat and take it into its steady pose. Returns a fix to answer with (in
        map axes: the splat's pose through the same matrix the caretaker's page draws the splat with), or None when the
        zone has no trained splat, its index is still being built, or the steady pose is lost."""
        got_space = splat_space(zone)
        if got_space is None:
            return None
        folder, matrix = got_space
        t0 = time.time()
        frame = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return None
        try:
            got = splat_live.photo(zone, folder, matrix, frame, t0)
            pose = splat_live.pose(zone, time.time()) if got is not None else None
        except Exception as err:  # noqa: BLE001  an extra: the instant and the model's fixes still answer
            sys.stderr.write(f"splat pose: {type(err).__name__}: {err}\n")
            return None
        if not pose:
            return None
        here = {"position": pose["position"], "heading_deg": pose["heading_deg"]}
        last = where.get(zone) or {}
        kf = got.get("keyframe")
        state = "found" if got["placed"] and pose["state"] == "found" else "unsure"
        fix = {"ok": True, "zone": zone, "job": None, "state": state, "confidence": round(splat_locate.chance_within(pose["sigma_m"]), 2),
               "position": pose["position"], "heading_deg": int(round(pose["heading_deg"])), "pitch_deg": pose["pitch_deg"],
               "source": "fused", "uncertainty_m": pose["radius95_m"], "waypoints": splat_waypoints(zone, here), "map": last.get("map"),
               "items": [], "items_looked_for": last.get("items_looked_for") or [],
               "splat_pose": {**{k: v for k, v in got.items() if k != "fix"}, "raw": got.get("fix"), "sigma_m": pose["sigma_m"],
                              "uncertainty_m": pose["radius95_m"], "trail": pose["trail"],
                              "keyframe_photo": f"/evidence/{folder.name}/kf{kf:03d}.jpg" if kf is not None else None},
               "at": time.time(), "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "seconds": {"total": round(time.time() - t0, 3)}}
        fix["headline"], fix["detail"] = where_in_plain_words(fix)
        return fix

    def splat_steady(zone, answer):
        """The splat's steady pose in the shape of track.Tracker.current() while it is fresh, else `answer` unchanged.
        confidence_pct: the chance the wearer is within 0.5 m of it, from its uncertainty."""
        if splat_live is None or splat_space(zone) is None:
            return answer
        try:
            pose = splat_live.pose(zone, time.time())
        except Exception:  # noqa: BLE001
            return answer
        if not pose:
            return answer
        here = {"position": pose["position"], "heading_deg": pose["heading_deg"]}
        found = pose["state"] == "found"
        pct = int(round(100 * splat_locate.chance_within(pose["sigma_m"])))
        recent = [p for p in pose["trail"] if p[0] >= time.time() - 30]
        return {"state": "steady" if found else "settling", "position": [round(float(v), 2) for v in pose["position"]],
                "heading_deg": int(round(pose["heading_deg"])), "pitch_deg": pose["pitch_deg"], "confidence_pct": pct, "next_pct": pct,
                "show": found, "agree": len(recent), "of": len(recent), "age_s": pose["age_s"], "waiting": pose["held"] > 0,
                "waypoints": splat_waypoints(zone, here, (answer or {}).get("waypoints")), "source": "fused",
                "sigma_m": pose["sigma_m"], "uncertainty_m": pose["radius95_m"], "speed_m_s": pose["speed_m_s"],
                "trail": [[p[0], p[1], p[2], p[3]] for p in pose["trail"]]}

    def splat_status(zone):
        """For /care/space: is the splat pose on for this zone, and how far along."""
        got = splat_space(zone)
        if got is None:
            return {"on": False}
        z = splat_live.zones.get(zone) or {}
        state = splat_live.state(got[0])
        if state == "not asked":
            splat_live.index(got[0])  # start building it now, so it is ready when the photos come
            state = "building"
        return {"on": True, "index": state, "focal": z.get("focal")}

    if splat_live is not None:  # build the trained splats' indexes now (~30 s each), so the first photos are placed
        def warm_splats():
            for z in list(rooms):
                try:
                    got = splat_space(z)
                except Exception:  # noqa: BLE001
                    got = None
                if got:
                    splat_live.index(got[0])
        threading.Thread(target=warm_splats, daemon=True).start()
    # ---- end trained splat ----
    quick_keys, full_at = {}, {}  # --quick: {zone: the room pictures as small prints}, {zone: when the model last placed a photo}
    recent = {}  # --quick: {zone: {"model": (print, pose) of the newest model fix's photo, "quick": (print, pose) of the newest instant one}}

    def keys_for(zone):
        """The room map's pictures as quick prints, with where each was taken (see quick_match). None without a map."""
        room = rooms.get(zone)
        if not room or not room.get("cameras") or cv2 is None:
            return None
        got = quick_keys.get(zone)
        if got and got["job"] == room["job"]:
            return got
        keys, cams, shape = [], {}, None
        for cam in room["cameras"]:
            if "frame" not in cam:
                continue
            header, picture = link.call({"op": "baseline_image", "zone": zone, "frame": cam["frame"]})
            frame = cv2.imdecode(np.frombuffer(picture, np.uint8), cv2.IMREAD_COLOR) if header.get("ok") else None
            if frame is None:
                continue
            shape = shape or frame.shape[:2]
            keys.append((cam["frame"], quick_print(frame, shape)))
            cams[cam["frame"]] = cam
        quick_keys[zone] = got = {"job": room["job"], "keys": keys, "cams": cams, "shape": shape, "fov": room.get("fov_deg") or 50.0}
        return got

    def quick_fix(zone, photo):
        """An instant fix from the room's pictures, or None when this zone has no room map. A blurred or dark photo,
        or one no picture resembles, gets the latest fix back, marked as such."""
        t0 = time.time()
        keys = keys_for(zone)
        if not keys or not keys["keys"]:
            return None
        frame = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return None
        last = where.get(zone) or {}
        clean = rc.sharpness(frame) >= QUICK_SHARP and frame.mean() > 30
        if not clean:
            return {**last, "zone": zone, "quick": {"skipped": "blurred or dark", "seconds": round(time.time() - t0, 3)}} if last else None
        # References with a known pose: the room's pictures, the photo of the newest model fix and the newest instant
        # frame. With the camera running, the newest frames are a few degrees apart and match strongly, so the heading
        # follows the camera from frame to frame; the model's fix every FULL_EVERY_S takes the drift out, and the
        # room's pictures catch the wearer again after a jump.
        now = quick_print(frame, keys["shape"])
        refs = [(("picture", f), pr) for f, pr in keys["keys"]]
        for kind in ("model", "quick"):
            got = recent.get(zone, {}).get(kind)
            if got and got[0].shape == now.shape:
                refs.append(((kind, 0), got[0]))
        sim, ref, slide = quick_match(now, refs)[0]
        if sim < QUICK_MIN:
            return {**last, "zone": zone, "quick": {"skipped": f"nothing the room map knows looks like this (best {sim:.2f})",
                                                     "similarity": round(sim, 2), "seconds": round(time.time() - t0, 3)}} if last else None
        base = keys["cams"][ref[1]] if ref[0] == "picture" else recent[zone][ref[0]][1]
        frame_k = ref[1] if ref[0] == "picture" else ref[0]
        heading = (base["heading_deg"] - slide * keys["fov"] + 180) % 360 - 180
        pose = {"position": base["position"], "heading_deg": heading}
        if sim >= QUICK_FOUND:  # a strong match is the next frame's reference
            recent.setdefault(zone, {})["quick"] = (now, pose)
        places = [w for w in last.get("waypoints") or [] if w.get("position")] or (rooms.get(zone) or {}).get("places") or []
        waypoints = []
        for w in places:
            distance, bearing = from_here(w["position"], pose)
            waypoints.append({"name": w["name"], "distance_m": distance, "bearing_deg": bearing, "position": w["position"]})
        state = "found" if sim >= QUICK_FOUND else "unsure"
        fix = {"ok": True, "zone": zone, "job": None, "source": "instant", "uncertainty_m": None, "state": state,
               "confidence": round(sim, 2), "position": base["position"],
               "heading_deg": int(round(heading)), "waypoints": waypoints, "map": last.get("map"),
               "items": [], "items_looked_for": last.get("items_looked_for") or [],
               "quick": {"similarity": round(sim, 2), "picture": frame_k, "turn_deg": round(-slide * keys["fov"], 1),
                         "seconds": round(time.time() - t0, 3)},
               "at": time.time(), "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "seconds": {"total": round(time.time() - t0, 3)}}
        fix["headline"], fix["detail"] = where_in_plain_words(fix)
        return fix

    def build_room(zone, op="map", session=None):
        """Ask the worker for the zone's room map (worker.room_map, or recording_map from a recording) and keep it.
        The zone's trained splat (with its views and labels) stays with the room: it has its own axes, so a new map
        does not move it. Only a map of another recording replaces it (that recording's own training follows).
        Returns the reply header."""
        t0 = time.time()
        # Its own connection to the gateway when there is one: a room map takes seconds to minutes, and photos for fixes
        # and recording chunks must not wait behind it (one connection answers one request at a time).
        header, blob = (bg_link or link).call({"op": op, "zone": zone, "device": "gpu", **({"session": session} if session else {})})
        if header.get("ok"):
            _, splat_url = keep_evidence(header["job"], blob)
            result = header["result"]
            trained = (rooms.get(zone) or {}).get("trained")
            if trained and op == "recording_map" and trained.get("session") != result.get("session"):
                trained = None
            rooms[zone] = {**result, "zone": zone, "job": header["job"], "splat": splat_url, "at": time.time(), "trained": trained,
                           "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "seconds": {**result.get("seconds", {}), "total": round(time.time() - t0, 2)}}
            try:
                rooms_file.write_text(json.dumps(rooms))
            except OSError:
                pass
        return header

    def save_rooms():
        try:
            rooms_file.write_text(json.dumps(rooms))
        except OSError:
            pass

    def set_place(zone, place):
        """One named place with its map position into the zone's room (GET /room "places"), replacing its old one."""
        if zone in rooms:
            rooms[zone]["places"] = [q for q in rooms[zone].get("places") or [] if q["name"] != place["name"]] + [place]
            save_rooms()

    def place_spots(zone):
        """The named places' map positions from the zone's pictures as they are (worker op "spots": one pass over the
        pictures the room map uses, in its axes; nothing added, nothing built). Returns the reply header."""
        header, _ = (bg_link or link).call({"op": "spots", "zone": zone, "device": "gpu"})
        if header.get("ok") and zone in rooms:
            rooms[zone]["places"] = header["result"].get("places") or []
            rooms[zone]["places_at"] = time.time()
            save_rooms()
        return header

    def place_room(zone):
        """place_spots in the background (after a place is named in a picture), one at a time per zone."""
        if placing.get(zone):
            placing[zone] = "again"
            return
        placing[zone] = "running"

        def run():
            try:
                while True:
                    header = place_spots(zone)
                    if not header.get("ok"):
                        sys.stderr.write(f"places for {zone} not placed: {header.get('error')}" + chr(10))
                    if placing.get(zone) != "again":
                        break
                    placing[zone] = "running"
            finally:
                placing.pop(zone, None)

        threading.Thread(target=run, daemon=True).start()

    def auto_places(zone, result, job):
        """A thing of AUTO_PLACES this fix saw, with a position: kept. When AUTO_VIEWS fixes agree where it is, it is
        named as a place ("auto", at the middle of the agreeing sightings), and moved when later sightings move it by
        more than half its radius. Returns the places named or moved."""
        named = []
        for item in result.get("items") or []:
            name = item.get("name")
            if name not in AUTO_PLACES or not item.get("position") or (item.get("score") or 0) < AUTO_SCORE:
                continue
            mine = auto_seen.setdefault(zone, {}).setdefault(name, [])
            mine.append({"position": item["position"], "score": item.get("score"), "job": job, "at": time.time()})
            del mine[:-30]
            radius = AUTO_RADIUS_M.get(name, 0.6)
            best = max(([s for s in mine if math.dist(s["position"], c["position"]) <= radius] for c in mine), key=len)
            if len({s["job"] for s in best}) < AUTO_VIEWS:
                continue
            position = [round(sorted(s["position"][k] for s in best)[len(best) // 2], 3) for k in range(3)]
            listed, _ = link.call({"op": "waypoints", "zone": zone})
            old = next((q for q in listed.get("waypoints") or [] if q["name"] == name), None)
            if old and old.get("source") != "auto":
                continue  # someone named this place: theirs stays
            if old and old.get("position") and math.dist(old["position"], position) < radius / 2:
                continue  # where it already is
            place = {"name": name, "position": position, "source": "auto", "views": len({s["job"] for s in best}),
                     "score": round(max(s["score"] or 0 for s in best), 3)}
            header, _ = link.call({"op": "waypoint", "zone": zone, **place, "auto": True})
            if header.get("ok"):
                set_place(zone, place)
                named.append(place)
        return named

    def grow_room(zone):
        """A fix added a picture of a part of the room not sampled before: build the room map again, in the background."""
        if building.get(zone):
            building[zone] = "again"
            return
        building[zone] = "running"

        def run():
            try:
                while True:
                    header = build_room(zone)
                    if not header.get("ok"):
                        sys.stderr.write(f"room map for {zone} not built: {header.get('error')}" + chr(10))
                    if building.get(zone) != "again":
                        break
                    building[zone] = "running"
            finally:
                building.pop(zone, None)

        threading.Thread(target=run, daemon=True).start()

    # ---- recordings (POST /recording): the room recorded continuously, mapped as it comes, trained on the GPU ----
    recs_file = evidence_dir / "recordings.json"
    try:
        recs = json.loads(recs_file.read_text())  # {zone: the newest session's progress}
    except (OSError, ValueError):
        recs = {}
    rec_lock = threading.Lock()

    def save_recs():
        try:
            recs_file.write_text(json.dumps(recs))
        except OSError:
            pass

    def rec_map(zone, session, frames):
        """Build the room map from the recording so far (in the background)."""
        try:
            header = build_room(zone, "recording_map", session)
        except Exception as err:  # noqa: BLE001
            header = {"ok": False, "error": f"{type(err).__name__}: {err}"}
        with rec_lock:
            rec = recs.get(zone)
            if rec and rec.get("session") == session:
                rec["map_state"] = "done" if header.get("ok") else "failed"
                if header.get("ok"):
                    rec.update(map_frames=frames, map_updates=rec.get("map_updates", 0) + 1, map_at=time.time(),
                               map_keyframes=(header.get("result") or {}).get("keyframes"))
                rec["map_error"] = None if header.get("ok") else header.get("error")
                again = rec["map_state"] == "done" and rec.get("frames", 0) - frames >= REC_MAP_EVERY
                save_recs()
            else:
                again = False
        if again:
            start_map(zone, session)

    def start_map(zone, session):
        with rec_lock:
            rec = recs.get(zone)
            if not rec or rec.get("map_state") == "running":
                return
            rec["map_state"] = "running"
            frames = rec.get("frames", 0)
        threading.Thread(target=rec_map, args=(zone, session, frames), daemon=True).start()

    def rec_train(zone, session):
        """Train a splat of the recording so far on the GPU and make the room show it (in the background)."""
        import remote_train
        local = os.environ.get("BEACON_ROOT")  # --local: the frames are on this laptop; else the gateway keeps them remotely
        root = Path(local or ".")
        remote = None if local else f"{remote_root or remote_train.DEFAULT_ROOT}/recordings/{zone}/{session}/frames"
        while True:
            with rec_lock:
                tr = recs[zone]["training"]
                runs = tr.get("runs", 0) + 1
                tr.update(state="running", started_at=time.time(), runs=runs, again=False, error=None)
                save_recs()
            out = evidence_dir / f"train-{zone}-{session}-{runs}"
            try:
                got = remote_train.train(root / "recordings" / zone / session / "frames", f"{zone}-{session}", out, host=train_host,
                                         root=remote_root, remote_frames=remote,
                                         log=lambda text: sys.stderr.write(f"training {zone}/{session}: {text}" + chr(10)))
            except Exception as err:  # noqa: BLE001  GPU cluster unreachable, the job refused: say so on the page
                got = {"state": "failed", "error": f"{type(err).__name__}: {err}"[:300]}
            if not trained_result(zone, session, out, got):
                return

    def trained_result(zone, session, out, got):
        """A training's result (remote_train: state, summary, job, error), its files in OUT: the recording's status,
        and when it is done, the zone's trained splat (if the recording is still the zone's newest). Returns whether
        it should run once more (the recording moved on while it trained)."""
        summary = got.get("summary") or {}
        with rec_lock:
            current = recs.get(zone) or {}
            # a newer session may have started meanwhile: then this result does not overwrite its status
            tr = current["training"] if current.get("session") == session else {"again": False}
            tr.update(state=got["state"], finished_at=time.time(), job=got.get("job"), error=got.get("error"),
                      psnr=summary.get("psnr_median"), keyframes=summary.get("keyframes"), gaussians=summary.get("trained_gaussians"),
                      frames=summary.get("frames"))
            if got["state"] == "done":
                room = rooms.setdefault(zone, {"zone": zone, "job": None, "at": time.time()})
                room["trained"] = {"splat": f"/evidence/{out.name}/trained.splat", "session": session, "at": time.time(),
                                   "psnr": summary.get("psnr_median"), "keyframes": summary.get("keyframes"),
                                   "gaussians": summary.get("trained_gaussians"), "median_depth": summary.get("median_depth"),
                                   "fov_deg": summary.get("fov_deg"),
                                   "views": f"/evidence/{out.name}/views.json" if (out / "views.json").exists() else None,
                                   "labels": f"/evidence/{out.name}/labels.json" if (out / "labels.json").exists() else None}
                try:
                    rooms_file.write_text(json.dumps(rooms))
                except OSError:
                    pass
                tr["splat"] = room["trained"]["splat"]
            repeat = tr.get("again")
            if not repeat:
                tr["state"] = got["state"]
            save_recs()
        return bool(repeat)

    def complete(out):
        """Are a training's files all here (a relay stopped while fetching leaves keyframes/ unrenamed)?"""
        return (out / "trained.splat").exists() and (out / "summary.json").exists() and not (out / "keyframes").exists() \
            and (not (out / "views.json").exists() or any(out.glob("kf*.jpg")))

    def resume_trainings():
        """At start: the trainings the relay was waiting on when it stopped (still "running" in recordings.json). Each
        one's result is registered as it would have been: from its folder here when that is complete, else fetched
        from the GPU machine once it has ended there (checked every 30 s for remote_train.WAIT_S). In the background."""
        waiting = [(zone, rec["session"], (rec.get("training") or {}).get("runs", 1)) for zone, rec in list(recs.items())
                   if (rec.get("training") or {}).get("state") == "running" and rec.get("session")]
        if not waiting:
            return

        def run(zone, session, runs):
            import remote_train
            out = evidence_dir / f"train-{zone}-{session}-{runs}"
            say = lambda text: sys.stderr.write(f"training {zone}/{session} (after a restart): {text}" + chr(10))  # noqa: E731
            t0 = time.time()
            try:
                wait = RESUME_POLL_S
                while not complete(out):
                    try:
                        if remote_train.finished(f"{zone}-{session}", host=train_host, root=remote_root):
                            remote_train.fetch(f"{zone}-{session}", out, host=train_host, root=remote_root, log=say)
                            break
                        wait = RESUME_POLL_S
                    except Exception as err:  # noqa: BLE001  the GPU machine did not answer: try again, less often
                        say(f"no answer ({type(err).__name__}: {str(err)[:120]}); again in {wait:.0f} s")
                        wait = min(2 * wait, 10 * RESUME_POLL_S)
                    if time.time() - t0 > remote_train.WAIT_S:
                        raise RuntimeError("it did not end in time")
                    time.sleep(wait)
                ok = (out / "trained.splat").exists()
                try:
                    summary = json.loads((out / "summary.json").read_text())
                except (OSError, ValueError):
                    summary = {}
                got = {"state": "done" if ok else "failed", "summary": summary, "error": None if ok else "no trained splat"}
            except Exception as err:  # noqa: BLE001  the GPU machine unreachable: say so on the page
                got = {"state": "failed", "error": f"after a restart: {type(err).__name__}: {err}"[:300]}
            say(got["state"])
            trained_result(zone, session, out, got)

        for zone, session, runs in waiting:
            threading.Thread(target=run, args=(zone, session, runs), daemon=True).start()

    def start_training(zone, session):
        with rec_lock:
            tr = recs[zone]["training"]
            if tr.get("state") == "running":
                tr["again"] = True  # once more when this one ends: the recording has moved on
                return
            tr["state"] = "running"
        threading.Thread(target=rec_train, args=(zone, session), daemon=True).start()

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

    plans = {}  # {room map job: its floor plan}: worked out once per map
    guiding = {}  # {zone: {"name", "active", "distance_m", "at"}}: the place someone is being guided to (/navigate&to=)

    def going_to(zone):
        """The guidance active in a zone, as /where shows it ("navigate"), or None: over on arrival, on
        /navigate&stop=1, or NAV_GUIDE_S after its last poll."""
        going = guiding.get(zone)
        if going and time.time() - going["at"] > NAV_GUIDE_S:
            guiding.pop(zone, None)
            going = None
        return {k: going[k] for k in ("name", "active", "distance_m")} if going else None

    def room_plan(got):
        """A room map's floor plan, as a fix carries one: its splat's points seen from above (east, north in metres),
        without the floor and the ceiling (40th to 90th percentile of height, as locate.py), at most NAV_PLAN_POINTS."""
        job = got.get("job")
        if job in plans:
            return plans[job]
        points = []
        try:
            raw = np.fromfile(evidence_dir / job / "room.splat", dtype=[("pos", "<f4", 3), ("rest", "u1", 20)])["pos"]
            raw = raw[np.isfinite(raw).all(1)]
            if len(raw):
                low, high = np.percentile(raw[:, 2], [40, 90])
                flat = raw[(raw[:, 2] >= low) & (raw[:, 2] <= high)][:, :2]
                flat = flat[np.random.default_rng(0).permutation(len(flat))[:NAV_PLAN_POINTS]]
                points = np.round(flat.astype(np.float64), 2).tolist()
        except Exception:  # noqa: BLE001  no NumPy, no file: a map without a plan
            points = []
        plans[job] = {"points": points, "cameras": [c["position"][:2] for c in got.get("cameras") or []], "from": "room map"}
        return plans[job]

    def navigate(zone, asked):
        """GET /navigate: (HTTP status, reply). The places are the newest fix's waypoints (placed in the same pass as
        the wearer) and the room's named places; the start is the steady pose when there is one (as routed()).
        Without `asked`: the wearer's map alone (position, heading, places, floor plan), no route."""
        fix = where.get(zone) if zone else max(where.values(), key=lambda f: f["ts"], default=None)
        zone = zone or (fix or {}).get("zone", "")
        got = rooms.get(zone) or {}
        if got.get("job") and (evidence_dir / got["job"] / "room.splat").exists() and (not fix or fix["at"] < got.get("at", 0)):
            # No fix since the newest room map was made: an older fix's map may be another one (another recording, even
            # another venue). The map is the room map's; where the wearer is waits for the next fix.
            listing = sorted(({"name": q["name"], "position": [round(float(c), 2) for c in q["position"]]}
                              for q in got.get("places") or [] if q.get("name") and q.get("position")), key=lambda q: q["name"])
            made = home_time(got.get("at", time.time())).strftime("%H:%M")
            why = f"no position fix since the room map was made at {made}"
            if asked:
                return 409, {"ok": False, "zone": zone, "stale": True, "stale_reason": why, "places": listing,
                             "error": f"The glasses have not been placed in this room's map yet ({why}): look around for a moment."}
            return 200, {"ok": True, "zone": zone, "position": None, "heading_deg": None, "places": listing, "plan": room_plan(got),
                         "at": fix["at"] if fix else None, "age_s": round(time.time() - fix["at"], 1) if fix else None,
                         "state": None, "stale": True, "stale_reason": why, "map_at": got.get("at")}
        if not fix:
            return 404, {"ok": False, "error": f"No position fix yet{f' in {zone}' if zone else ''}: the glasses have not sent a photo."}
        zone = fix["zone"]
        places = {}
        for p in ((rooms.get(zone) or {}).get("places") or []) + list(fix.get("waypoints") or []):
            if p.get("name") and p.get("position"):
                places[p["name"]] = {"name": p["name"], "position": [round(float(c), 2) for c in p["position"]]}
        listing = sorted(places.values(), key=lambda p: p["name"])
        if not asked:  # the wearer's map: where they are now, the places, the floor plan
            return 200, map_now(fix, listing)
        if not places:
            return 404, {"ok": False, "zone": zone, "error": f"No named places in {zone} yet: name one on /places or the dashboard.", "places": []}
        name = match_place(asked, list(places))
        if not name:
            return 404, {"ok": False, "zone": zone, "places": listing,
                         "error": f'No place called "{asked}" in {zone}. Places: {", ".join(p["name"] for p in listing)}.'}
        # Where they stand: the steady pose when there is one (as routed()); which way they face: the newest fix when it
        # is sure (the head turns faster than the steady pose follows), else the steady pose's.
        start, age, why = pose_now(fix)
        if fix.get("state") == "lost" or not start.get("position"):
            return 409, {"ok": False, "zone": zone, "to": name, "stale": True, "places": listing,
                         "error": "The glasses do not know where they are right now: look around the room for a moment."}
        here, heading = [round(float(c), 2) for c in start["position"]], float(start.get("heading_deg") or 0)
        goal = places[name]["position"]
        straight = math.hypot(goal[0] - here[0], goal[1] - here[1])
        plan = route_finder.plan(start, [places[name]]) if route_finder is not None else {"ok": False, "note": "no route finder", "to": {}}
        way = (plan.get("to") or {}).get(name)
        if way and len(way.get("points") or []) > 1:
            route, distance, bearing = way["points"], way.get("length_m", straight), way["next"]["bearing_deg"]
        else:  # a straight line where the map gives no route
            route = [here[:2], goal[:2]]
            distance = straight
            bearing = int(round((math.degrees(math.atan2(goal[0] - here[0], goal[1] - here[1])) - heading + 180) % 360 - 180))
        arrived = straight <= NAV_ARRIVED_M
        if arrived:  # there: the guidance is over
            guiding.pop(zone, None)
        else:
            guiding[zone] = {"name": name, "active": True, "distance_m": round(float(distance), 1), "at": time.time()}
        return 200, {"ok": True, "zone": zone, "to": name, "asked": asked, "arrived": arrived,
                     "distance_m": round(float(distance), 2), "straight_m": round(straight, 2), "bearing_deg": int(bearing),
                     "instruction": turn_words(bearing, straight), "route": route, "position": here,
                     "heading_deg": int(round(heading)), "at": fix["at"], "age_s": round(age, 1), "state": fix.get("state"),
                     "stale": why is not None, **({"stale_reason": why} if why else {}), "places": listing,
                     "plan": plan_of(fix), "route_note": plan.get("note") or ""}

    def pose_now(fix):
        """(start, age_s, stale reason or None): where the wearer stands, the steady pose when there is one (as
        routed()), facing the way the newest fix does when it is sure (the head turns faster than the steady pose
        follows), else the steady pose's way."""
        stable = steady(fix["zone"])
        start = fix
        if stable and stable.get("position") and stable.get("state") != "lost":
            facing = fix.get("heading_deg") if fix.get("state") == "found" else stable.get("heading_deg")
            start = {**fix, "state": "found", "position": stable["position"], "heading_deg": facing or 0}
        age = time.time() - fix["at"]
        why = (f"the newest fix is {age:.0f} s old" if age > NAV_STALE_S else
               f"the newest fix is {fix.get('state')}" if fix.get("state") != "found" else None)
        return start, age, why

    def plan_of(fix):
        """The fix's floor plan to draw (cut to NAV_PLAN_POINTS points) and where the baseline camera stood."""
        points = (fix.get("map") or {}).get("points") or []
        step = max(1, math.ceil(len(points) / NAV_PLAN_POINTS))
        return {"points": points[::step][:NAV_PLAN_POINTS], "cameras": (fix.get("map") or {}).get("cameras") or []}

    def map_now(fix, listing):
        """GET /navigate without a place: the wearer's map. A lost fix keeps the places and the plan, without a pose."""
        start, age, why = pose_now(fix)
        known = fix.get("state") != "lost" and start.get("position")
        return {"ok": True, "zone": fix["zone"], "position": [round(float(c), 2) for c in start["position"]] if known else None,
                "heading_deg": int(round(float(start.get("heading_deg") or 0))) if known else None, "places": listing,
                "plan": plan_of(fix), "at": fix["at"], "age_s": round(age, 1), "state": fix.get("state"),
                "stale": why is not None or not known, **({"stale_reason": why or "the glasses do not know where they are"}
                                                          if why or not known else {})}

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
                     "/hud": "hud.html", "/places": "places.html", "/care": "care.html"}
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
            if url.path == "/recording":  # the zone's newest recording session
                with rec_lock:
                    rec = recs.get(zone)
                    if rec:
                        return self.send_json(200, {"ok": True, "zone": zone, **rec, "viewer": f"/viewer?live={zone}",
                                                    "age_s": round(time.time() - rec.get("last_chunk_at", time.time()))})
                header, _ = link.call({"op": "recording_status", "zone": zone}) if re.fullmatch(ZONE_NAME, zone) else ({"ok": False}, b"")
                return self.send_json(200 if header.get("ok") else 404, header if header.get("ok") else
                                      {"ok": False, "error": f"No recording of '{zone}' yet."})
            if url.path == "/room":  # the room map of a zone (POST /map makes it), for the live 3D page
                got = rooms.get(zone)
                if not got or not got.get("splat") or not (evidence_dir / got["job"] / "room.splat").exists():
                    return self.send_json(404, {"ok": False, "error": f"No room map for '{zone}' yet: POST /map?zone={zone}."})
                return self.send_json(200, {"ok": True, **got, "age_s": round(time.time() - got["at"]), "building": bool(building.get(zone))})
            if url.path == "/care/space":  # the splat the caretaker's page shows, and the matrix into the room map's axes
                code, reply = care_view(zone)
                return self.send_json(code, {**reply, "named": care_named(zone, reply), "splat_pose": splat_status(zone)}
                                      if code == 200 else reply)
            if url.path == "/where":
                fix = where.get(zone) if zone else max(where.values(), key=lambda f: f["ts"], default=None)
                if not fix:
                    return self.send_json(404, {"ok": False, "error": "No position fix yet. POST a photo to /locate first."})
                # --- route finding (route.py): adds "route" to the reply, from the pose the display stands on
                stable = steady(fix["zone"])
                fix = routed(fix, stable)
                going = going_to(fix["zone"])  # where someone is being guided to now (GET /navigate&to=), for the pages
                if going:
                    fix = {**fix, "navigate": going}
                    stable = {**stable, "navigate": going} if isinstance(stable, dict) else stable
                return self.send_json(200, {**fix, "age_s": round(time.time() - fix["at"], 1), "seen": seen_list(fix["zone"], fix),
                                            "stable": stable})
            if url.path == "/navigate":  # the way to a named place, from where the wearer is now
                if parse_qs(url.query).get("stop", [""])[0]:  # the guidance is over (the wearer said stop)
                    guiding.pop(zone, None)
                    return self.send_json(200, {"ok": True, "zone": zone, "stopped": True})
                return self.send_json(*navigate(zone, parse_qs(url.query).get("to", [""])[0]))
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
                return self.send_json(200 if header.get("ok") else 503, {**header, "answers": bool(api_base), "worker_at": link.name})
            if url.path == "/frame":  # the newest photo that reached /locate (in this zone, or in any)
                got = received.get(zone) if zone else max(received.values(), key=lambda r: r["at"], default=None)
                if not got:
                    return self.send_json(404, {"ok": False, "error": "No photo has reached /locate since the relay started."})
                return self.send_bytes(200, got["photo"], "image/png" if got["photo"][:4] == b"\x89PNG" else
                                       "image/webp" if got["photo"][:4] == b"RIFF" else "image/jpeg")
            if url.path == "/worker_log":  # the end of the worker's log, with its timing and GPU memory lines
                try:
                    lines = int(parse_qs(url.query).get("lines", ["60"])[0])
                except ValueError:
                    lines = 60
                header, _ = link.call({"op": "worker_log", "lines": lines,
                                       "device": "cpu" if parse_qs(url.query).get("device", [""])[0] == "cpu" else "gpu"})
                return self.send_json(200 if header.get("ok") else 404, header)
            match = re.fullmatch(r"/evidence/([\w-]+)/(\w+)\.(jpg|splat|mp4|webm|json|bin)", url.path)
            if match and (evidence_dir / match[1] / f"{match[2]}.{match[3]}").exists():
                kind = {"jpg": "image/jpeg", "mp4": "video/mp4", "webm": "video/webm", "json": "application/json"}.get(match[3], "application/octet-stream")
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
            if op == "waypoint":  # name or remove a place; everything is in the query (the body: a live=1 photo)
                body = self.rfile.read(size) if 0 < size <= MAX_BODY else self.rfile.read(min(size, 65536)) and b""
                req = {"op": "waypoint", "zone": zone, "name": query.get("name", [""])[0]}
                live = bool(query.get("live", [""])[0])
                try:
                    if query.get("delete", [""])[0]:
                        req["delete"] = True
                    elif live:
                        u, v = float(query["u"][0]), float(query["v"][0])
                        if not (0 <= u <= 1 and 0 <= v <= 1):
                            raise ValueError("u and v")
                    elif "east" in query:  # a spot picked on a map of the room (the 3D page's view from above)
                        at = [float(query[k][0]) for k in ("east", "north", "up")]
                        if not all(math.isfinite(c) and abs(c) < 1000 for c in at):
                            raise ValueError("east, north, up")
                        req.update(position=[round(c, 3) for c in at], source="map")
                    else:
                        req.update(frame=int(query["frame"][0]), u=float(query["u"][0]), v=float(query["v"][0]))
                except (KeyError, ValueError):
                    return self.send_json(400, {"ok": False, "error": "POST /waypoint?zone=NAME&name=PLACE&frame=N&u=0..1&v=0..1 "
                                                "(or &live=1&u=..&v=.. on the newest photo)"})
                if live:  # a spot on a photo: the photo sent (the one the page showed), else the newest that reached
                    # /locate; that photo's own fix places it (no picture added to the room)
                    got = {"photo": body} if body and is_photo(body) else received.get(zone)
                    if not got:
                        return self.send_json(404, {"ok": False, "error": f"No photo of '{zone}' has reached /locate yet."})
                    try:
                        clip = one_photo(got["photo"])
                    except ValueError:
                        return self.send_json(400, {"ok": False, "error": "The newest photo could not be read."})
                    fixed, _ = link.call({"op": "locate", "zone": zone, "device": "gpu", "view": False, "grow": False,
                                          "point": {"u": u, "v": v}, "remembered": []}, clip)
                    point = (fixed.get("result") or {}).get("point") if fixed.get("ok") else None
                    if not point:
                        why = fixed.get("error") or (fixed.get("result") or {}).get("point_note") \
                            or f"the photo was {(fixed.get('result') or {}).get('state', 'not placed')} in the room"
                        return self.send_json(422, {"ok": False, "error": f"Could not place it: {why}."})
                    req.update(position=point)
                header, _ = link.call(req)
                if header.get("ok"):
                    if live or req.get("position"):
                        set_place(zone, {"name": " ".join(req["name"].split())[:40], "position": req["position"],
                                         "source": req.get("source", "live")})
                    elif req.get("delete") and zone in rooms:
                        rooms[zone]["places"] = [q for q in rooms[zone].get("places") or [] if q["name"] != req["name"]]
                        save_rooms()
                    else:
                        place_room(zone)  # its position, from the pictures as they are
                return self.send_json(200 if header.get("ok") else 400, {**header, **({"position": point} if live else {})})
            if op == "places":  # the named places' map positions from the zone's pictures as they are
                self.rfile.read(min(size, 65536))
                if not re.fullmatch(ZONE_NAME, zone):
                    return self.send_json(400, {"ok": False, "error": "POST /places?zone=NAME"})
                header = place_spots(zone)
                if not header.get("ok"):
                    return self.send_json(502, header)
                return self.send_json(200, {"ok": True, "zone": zone, "places": header["result"].get("places") or []})
            if op in ("scale", "watch", "enroll"):  # what the carer sets once: see change_setup
                return self.change_setup(op, query, zone, size)
            if op == "recording":  # one chunk of a continuous recording
                first = lambda name, default="": query.get(name, [default])[0]  # noqa: E731
                session, final = first("session"), first("final", "0") == "1"
                try:
                    seq = int(first("seq"))
                except ValueError:
                    seq = -1
                if not re.fullmatch(ZONE_NAME, zone) or not re.fullmatch(SESSION_NAME, session) or seq < 0 or not 0 <= size <= REC_VIDEO_MAX \
                        or (size == 0 and not final):
                    if size <= 65536:
                        self.rfile.read(size)
                    return self.send_json(400, {"ok": False, "error": "POST /recording?zone=ROOM&session=NAME&seq=N&final=0|1 "
                                                f"with a zip of frames NNNNNN_<ms>.jpg (up to {MAX_BODY >> 20} MB) or a video "
                                                f"(up to {REC_VIDEO_MAX >> 30} GB) as the body"})
                head = self.rfile.read(min(size, 4)) if size else b""
                if head[:2] == b"PK" or not size:  # a zip of frames, as the app streams them
                    if size > MAX_BODY:
                        return self.send_json(400, {"ok": False, "error": f"A zip of frames may be at most {MAX_BODY >> 20} MB: send smaller chunks."})
                    header, _ = link.call({"op": "recording", "zone": zone, "session": session, "seq": seq, "final": final},
                                          head + self.rfile.read(size - len(head)))
                else:  # a whole video: to disk here, frames taken here, sent on as parts with the following seq numbers
                    if cv2 is None:
                        return self.send_json(400, {"ok": False, "error": "A video needs OpenCV next to the relay: send zips of frames."})
                    try:
                        rate = min(30.0, max(1.0, float(first("fps", str(REC_VIDEO_FPS)))))
                    except ValueError:
                        rate = float(REC_VIDEO_FPS)
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as video:
                        video.write(head)
                        left = size - len(head)
                        while left > 0:
                            piece = self.rfile.read(min(left, 1 << 20))
                            if not piece:
                                break
                            video.write(piece)
                            left -= len(piece)
                    try:
                        parts, read = video_parts(video.name, rate, int(time.time() * 1000))
                    finally:
                        os.unlink(video.name)
                    if not parts:
                        return self.send_json(400, {"ok": False, "error": "Could not read any frame from that video."})
                    for k, part in enumerate(parts):
                        header, _ = link.call({"op": "recording", "zone": zone, "session": session, "seq": seq + k,
                                               "final": final and k == len(parts) - 1}, part)
                        if not header.get("ok"):
                            break
                    header["video"] = {"frames_read": read, "frames_kept": header.get("frames_total"), "parts": len(parts), "fps": rate}
                    seq += len(parts) - 1
                if not header.get("ok"):
                    return self.send_json(400, header)
                now = time.time()
                with rec_lock:
                    rec = recs.get(zone)
                    if not rec or rec.get("session") != session:
                        rec = recs[zone] = {"session": session, "started_at": now, "map_frames": 0, "map_updates": 0, "map_state": "none",
                                            "training": {"state": "none", "runs": 0}}
                    rec.update(frames=header["frames_total"], chunks=header["chunks"], missing=header["missing"], final=header["final"],
                               last_chunk_at=now, last_seq=seq)
                    need_map = rec["frames"] >= 6 and rec["map_state"] != "running" and (
                        rec["frames"] - rec["map_frames"] >= REC_MAP_EVERY or (header["final"] and rec["frames"] > rec["map_frames"]))
                    tr = rec["training"]
                    since = now - (tr.get("started_at") or rec["started_at"])
                    need_train = bool(train_host) and rec["frames"] >= 20 and (
                        (header["final"] and not tr.get("final_started")) or (not header["final"] and since >= REC_TRAIN_EVERY_S))
                    if need_train and header["final"]:
                        tr["final_started"] = True
                    save_recs()
                if need_map:
                    start_map(zone, session)
                if need_train:
                    start_training(zone, session)
                with rec_lock:
                    state = {"map": rec["map_state"], "training": rec["training"]["state"]}
                return self.send_json(200, {"ok": True, "session": session, "seq": seq, "frames_total": header["frames_total"],
                                            "status": "final" if header["final"] else "receiving", "viewer": f"/viewer?live={zone}",
                                            "duplicate": header.get("duplicate"), "missing": header.get("missing"),
                                            **({"video": header["video"]} if header.get("video") else {}), **state})
            if op == "map":  # the room map: the zone's baseline pictures in one pass, as one splat in map axes
                self.rfile.read(min(size, 65536))
                if not re.fullmatch(ZONE_NAME, zone):
                    return self.send_json(400, {"ok": False, "error": "POST /map?zone=NAME"})
                header = build_room(zone)
                if not header.get("ok"):
                    return self.send_json(502, header)
                return self.send_json(200, {"ok": True, **rooms[zone]})
            if op not in FRAMES or not zone or not 0 < size <= MAX_BODY:
                return self.send_json(400, {"ok": False, "error": "POST /baseline, /check or /locate with ?zone=NAME and a video (or, for /locate, a photo) as the body."})
            t0 = time.time()
            clip = self.rfile.read(size)
            seconds = {}
            shot = clip if op == "locate" and is_photo(clip) else None  # the photo as it came, for the cheap gate
            if shot is not None:
                received[zone] = {"at": time.time(), "photo": shot}
            # ---- the wearer in the trained splat: every photo, on this laptop; between the model's fixes it is the answer ----
            placed = splat_photo(zone, shot) if shot is not None and op == "locate" else None
            if placed is not None and quick and not query.get("fresh", [""])[0] and not query.get("view", [""])[0] \
                    and query.get("quick", [""])[0] != "0" and time.time() - full_at.get(zone, 0) < FULL_EVERY_S:
                where[zone] = placed
                stable = steady(zone)
                return self.send_json(200, {**routed(placed, stable), "age_s": 0.0, "seen": seen_list(zone, placed), "stable": stable})
            # ---- --quick: an instant fix from cosine similarity, unless it is time for the model again ----
            if quick and shot is not None and not query.get("fresh", [""])[0] and not query.get("view", [""])[0] \
                    and query.get("quick", [""])[0] != "0" and time.time() - full_at.get(zone, 0) < FULL_EVERY_S:
                try:
                    fast = quick_fix(zone, shot)
                except Exception as err:  # noqa: BLE001  the instant fix is an extra: the model is asked instead
                    sys.stderr.write(f"quick fix: {type(err).__name__}: {err}" + chr(10))
                    fast = None
                if fast is not None:
                    if "skipped" not in fast["quick"]:
                        where[zone] = fast
                    return self.send_json(200, {**fast, "age_s": round(time.time() - fast.get("at", time.time()), 1),
                                                "seen": seen_list(zone, fast) if fast.get("position") else [], "stable": None})
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
            # --splat-every-fix: every fix the model makes also builds the room as a 3D file (about 0.1 s on the worker),
            # for the live 3D page (/viewer?live=ZONE). The cheap gate still answers an unchanged view without the model.
            auto_splat = op == "locate" and splat_every_fix and not flag("view")
            job = {"op": op, "zone": zone, "device": device, "view": flag("view") or auto_splat}
            # A zone with a trained splat keeps its pictures as they are (its map was made for that splat) unless the
            # photo asks to grow it (?grow=1); other zones grow with each part of the room not sampled before.
            growing = flag("grow") or not (rooms.get(zone) or {}).get("trained")
            if op == "locate":  # the spots where items were seen, so the worker can look at them again
                job["remembered"] = [{"name": s["name"], "position": s["position"]} for s in seen.get(zone, {}).values()]
                job["grow"] = growing
                job["look_for"] = AUTO_PLACES
            header, blob = link.call(job, clip)
            seconds["total"] = round(time.time() - t0, 2)
            if not header.get("ok"):
                return self.send_json(502, {**header, "seconds": seconds})
            result = header["result"]
            if op == "baseline":
                views.pop(zone, None)  # new pictures of the room: the gate starts again
                rooms.pop(zone, None)  # and the room map was made from the old ones
                return self.send_json(200, {"ok": True, "zone": zone, "frames": result["frames"], "seconds": seconds})
            images, splat_url = keep_evidence(header["job"], blob)
            if auto_splat and splat_url:  # keep only the newest few of the automatic 3D files
                auto_splats.append(evidence_dir / header["job"] / "map.splat")
                for old in auto_splats[:-AUTO_SPLATS_KEPT]:
                    old.unlink(missing_ok=True)
                del auto_splats[:-AUTO_SPLATS_KEPT]
            if op == "locate":
                for part in ("points", "cameras"):  # whole centimetres: 32-bit numbers print with 17 digits otherwise
                    if isinstance(result.get("map"), dict):
                        result["map"][part] = [[round(float(v), 2) for v in p] for p in result["map"].get(part) or []]
                headline, detail = where_in_plain_words(result)
                fix = {"ok": True, "zone": zone, "job": header["job"], "headline": headline, "detail": detail, "source": "full",
                       "uncertainty_m": None, **result,
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
                full_at[zone] = time.time()  # --quick: the next photos are placed by similarity for a while
                keys = keys_for(zone) if quick and fix.get("state") == "found" and shot is not None else None
                if keys and keys["shape"]:  # this photo, with the model's pose, is a reference for the next instant fixes
                    photo = cv2.imdecode(np.frombuffer(shot, np.uint8), cv2.IMREAD_COLOR)
                    if photo is not None:
                        recent.setdefault(zone, {})["model"] = (quick_print(photo, keys["shape"]),
                                                                {"position": fix["position"], "heading_deg": fix["heading_deg"]})
                try:
                    where_file.write_text(json.dumps(where))
                except OSError:
                    pass
                remember_items(zone, result, images)
                if AUTO_PLACES and result.get("items"):
                    fix["places_named"] = auto_places(zone, result, header["job"])
                if result.get("picture_added") and growing:  # a part of the room not sampled before: the room map grows
                    grow_room(zone)
                # t0: when the photo reached the relay. The steady position ages from the photo, not from the answer.
                stable = steady(zone, result, t0)
                if isinstance(stable, dict) and stable.get("source") == "fused":
                    # The trained splat placed this photo too (above), far closer than the model's pass does in its own
                    # map axes (eval/POSITION_RESULTS.md): its pose is the position; the model's is kept beside it.
                    here = {"position": stable["position"], "heading_deg": stable["heading_deg"]}
                    fix.update(model_pose={"position": fix.get("position"), "heading_deg": fix.get("heading_deg"), "state": fix.get("state")},
                               position=stable["position"], heading_deg=stable["heading_deg"], pitch_deg=stable.get("pitch_deg"),
                               source="fused", uncertainty_m=stable["uncertainty_m"], waypoints=splat_waypoints(zone, here),
                               state="found" if stable["state"] == "steady" else "unsure",
                               **({"splat_pose": placed["splat_pose"]} if placed else {}))
                    fix["headline"], fix["detail"] = where_in_plain_words(fix)
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
                    "settings": result.get("settings"), "rust_enabled": result.get("rust_enabled"),
                    "rust_threads": result.get("rust_threads"), "rust_disabled_functions": result.get("rust_disabled_functions"), "revision": result.get("revision"), "source_dirty": result.get("source_dirty"), "gaussians": result.get("gaussians"),
                    "median_depth": result.get("median_depth"), "watch": result.get("watch") or [],
                    "gpu_peak_gb": result.get("gpu_peak_gb"),
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

    resume_trainings()  # trainings the relay was waiting on when it last stopped
    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ssh-host", default="cluster", help="SSH host alias for the cluster")
    ap.add_argument("--remote-root", help="absolute path of the persistent remote checkout")
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 lets a phone on the same network reach the relay")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--token", default=secrets.token_urlsafe(9), help="access key; a random one is made if not given")
    ap.add_argument("--train-host", help="train splats of recordings (POST /recording) on this SSH host, e.g. cluster "
                    "(live/remote_train.py: video_map.py deployed there with the workers; BEACON_TRAIN_LAUNCH=direct for a "
                    "machine without Slurm)")
    ap.add_argument("--quick", action="store_true", help="place photos instantly by cosine similarity to the room map's "
                    "pictures (POST /map), with the model at least every 4 s")
    ap.add_argument("--splat-every-fix", action="store_true", help="every position fix also builds the room as a 3D file, "
                    "for the live 3D page /viewer?live=ZONE (about 4 MB each: best with --local, not over SSH)")
    ap.add_argument("--local", metavar="FOLDER", help="no cluster: the gateway runs on this machine and keeps its files in "
                    "FOLDER; start live/worker.py here too, with BEACON_ROOT set to the same folder (see run_local.sh)")
    args = ap.parse_args()
    if args.local:  # everything on this machine: the same gateway code, started here instead of over SSH
        Path(args.local).mkdir(parents=True, exist_ok=True)
        os.environ["BEACON_ROOT"] = str(Path(args.local).resolve())
        link = Link([sys.executable, "-u", str(HERE / "gateway.py")], name="the local gateway")
        bg_link = Link([sys.executable, "-u", str(HERE / "gateway.py")], name="the local gateway (room maps)")
    else:
        link = Link(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                     "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", args.ssh_host, remote_command(args.remote_root)])
    evidence_dir = Path(tempfile.gettempdir()) / "beacon-evidence"
    evidence_dir.mkdir(exist_ok=True)
    event_api, api_token = os.environ.get("EVENT_API_URL"), os.environ.get("EVENT_API_TOKEN")
    if not args.local:
        bg_link = Link(link.argv, name="the GPU cluster (room maps)")
    handler = make_handler(link, evidence_dir, args.token, event_api, api_token=api_token, splat_every_fix=args.splat_every_fix, bg_link=bg_link,
                           quick=args.quick, train_host=args.train_host, remote_root=args.remote_root)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    addresses = ["127.0.0.1"]
    if args.host == "0.0.0.0":
        addresses += sorted({a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127.")})
    print("relay running. Phone page, open one of:", flush=True)
    for address in addresses:
        print(f"  http://{address}:{args.port}/?k={args.token}", flush=True)
    print(f"dashboard: http://127.0.0.1:{args.port}/dashboard?k={args.token}", flush=True)
    print(f"day log:   http://127.0.0.1:{args.port}/dayview?k={args.token}", flush=True)
    print(f"live 3D:   http://127.0.0.1:{args.port}/viewer?live=kitchen&k={args.token} (the room map and the wearer in it; "
          "change live= to the room)", flush=True)
    if args.splat_every_fix:
        print("every fix also builds its own 3D file (--splat-every-fix)", flush=True)
    print("questions (/ask, /speak): " + ("passed on to the event API" if event_api and api_token
                                           else "off (EVENT_API_URL and EVENT_API_TOKEN are not both set)"), flush=True)
    print(f"frame picking on this laptop: {'on' if rc else 'off (OpenCV not found)'}; evidence images in {evidence_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        link.close()
        bg_link.close()


if __name__ == "__main__":
    main()
