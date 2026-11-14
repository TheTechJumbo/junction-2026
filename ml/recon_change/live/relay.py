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
    GET  /waypoints?zone=kitchen  the zone's named places
    POST /waypoint?zone=kitchen&name=pill+box&frame=3&u=0.4&v=0.6   name a spot in baseline picture 3
    POST /waypoint?zone=kitchen&name=pill+box&delete=1
    GET  /hud                     the glasses display page: arrow and distance to a place
    GET  /places                  name places by clicking in a baseline picture, and test a fix

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
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
REMOTE = "cd /scratch/$USER/junction-recon && exec .venv-recon/bin/python -u live/gateway.py"
MAX_BODY = 200 * 1024 * 1024
FRAMES = {"baseline": 24, "check": 12, "locate": 3}  # keep in step with worker.py
LONG_SIDE = 518  # the model's input size: no point sending more pixels

try:
    sys.path.insert(0, str(HERE.parent))
    import cv2
    import numpy as np
    import recon_change as rc
except ImportError:  # no OpenCV here: videos are forwarded whole
    cv2 = rc = None


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
    if cv2 is not None:
        frame = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("not a picture")
        scale = LONG_SIDE / max(frame.shape[:2])
        if scale < 1:
            frame = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        photo = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as z:
        z.writestr("000.jpg", photo)
    return bundle.getvalue()


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


REPO = "TheTechJumbo/junction-2026"


def make_handler(link, evidence_dir, token, event_api=None, pick=pick_frames if rc else None):
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

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, code, data, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, code, body):
            self.send_bytes(code, json.dumps(body, indent=2).encode(), "application/json")

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
            zone = parse_qs(url.query).get("zone", [""])[0]
            if url.path == "/where":
                fix = where.get(zone) if zone else max(where.values(), key=lambda f: f["ts"], default=None)
                if not fix:
                    return self.send_json(404, {"ok": False, "error": "No position fix yet. POST a photo to /locate first."})
                return self.send_json(200, {**fix, "age_s": round(time.time() - fix["at"], 1)})
            if url.path == "/waypoints":
                header, _ = link.call({"op": "waypoints", "zone": zone})
                return self.send_json(200 if header.get("ok") else 404, header)
            match = re.fullmatch(r"/baseline/([a-z0-9_-]{1,32})/(\d{1,2})\.jpg", url.path)
            if match:
                header, picture = link.call({"op": "baseline_image", "zone": match[1], "frame": int(match[2])})
                if header.get("ok"):
                    return self.send_bytes(200, picture, "image/jpeg")
                return self.send_json(404, header)
            if url.path == "/status":
                header, _ = link.call({"op": "status"})
                return self.send_json(200 if header.get("ok") else 503, header)
            match = re.fullmatch(r"/evidence/([\w-]+)/(\w+)\.(jpg|splat)", url.path)
            if match and (evidence_dir / match[1] / f"{match[2]}.{match[3]}").exists():
                kind = "image/jpeg" if match[3] == "jpg" else "application/octet-stream"
                return self.send_bytes(200, (evidence_dir / match[1] / f"{match[2]}.{match[3]}").read_bytes(), kind)
            self.send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self.allowed(query):
                return
            op, zone = url.path.strip("/"), query.get("zone", [""])[0]
            size = int(self.headers.get("Content-Length", 0))
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
            if op not in FRAMES or not zone or not 0 < size <= MAX_BODY:
                return self.send_json(400, {"ok": False, "error": "POST /baseline, /check or /locate with ?zone=NAME and a video (or, for /locate, a photo) as the body."})
            t0 = time.time()
            clip = self.rfile.read(size)
            seconds = {}
            try:
                if op == "locate" and is_photo(clip):
                    clip = one_photo(clip)
                elif pick and clip[:2] != b"PK":  # a zip is a frame bundle the phone app already picked: pass it on
                    clip, n = pick(clip, FRAMES[op])
            except ValueError:
                return self.send_json(400, {"ok": False, "error": "Could not read that file as a video or a photo."})
            seconds["pick_frames"] = round(time.time() - t0, 2)
            device = "cpu" if query.get("device", [""])[0] == "cpu" else "gpu"
            header, blob = link.call({"op": op, "zone": zone, "device": device, "view": bool(query.get("view", [""])[0])}, clip)
            seconds["total"] = round(time.time() - t0, 2)
            if not header.get("ok"):
                return self.send_json(502, {**header, "seconds": seconds})
            result = header["result"]
            if op == "baseline":
                return self.send_json(200, {"ok": True, "zone": zone, "frames": result["frames"], "seconds": seconds})
            images, splat_url = keep_evidence(header["job"], blob)
            if op == "locate":
                headline, detail = where_in_plain_words(result)
                fix = {"ok": True, "zone": zone, "job": header["job"], "headline": headline, "detail": detail, **result,
                       "images": images, "splat": splat_url, "seconds": {**seconds, **result.get("seconds", {})},
                       "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "at": time.time()}
                where[zone] = fix
                try:
                    where_file.write_text(json.dumps(where))
                except OSError:
                    pass
                return self.send_json(200, {**fix, "age_s": 0.0})
            headline, detail = in_plain_words(result)
            events = to_events(result, zone, images)
            body = {"ok": True, "zone": zone, "job": header["job"], "headline": headline, "detail": detail,
                    "verdict": result["verdict"], "events": events, "images": images, "splat": splat_url,
                    "device": result.get("device"), "on_gpu": result.get("on_gpu"), "host": result.get("host"),
                    "settings": result.get("settings"), "gaussians": result.get("gaussians"),
                    "median_depth": result.get("median_depth"),
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
    handler = make_handler(link, evidence_dir, args.token, os.environ.get("EVENT_API_URL"))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    addresses = ["127.0.0.1"]
    if args.host == "0.0.0.0":
        addresses += sorted({a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127.")})
    print("relay running. Phone page, open one of:", flush=True)
    for address in addresses:
        print(f"  http://{address}:{args.port}/?k={args.token}", flush=True)
    print(f"dashboard: http://127.0.0.1:{args.port}/dashboard?k={args.token}", flush=True)
    print(f"frame picking on this laptop: {'on' if rc else 'off (OpenCV not found)'}; evidence images in {evidence_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        link.close()


if __name__ == "__main__":
    main()
