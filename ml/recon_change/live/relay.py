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
FRAMES = {"baseline": 24, "check": 12}  # keep in step with worker.py
LONG_SIDE = 518  # the model's input size: no point sending more pixels

try:
    sys.path.insert(0, str(HERE.parent))
    import cv2
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
    return bundle.getvalue(), len(frames)


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


def make_handler(link, evidence_dir, token, event_api=None, pick=pick_frames if rc else None):
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
            if url.path == "/":
                return self.send_bytes(200, (HERE / "phone.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/status":
                header, _ = link.call({"op": "status"})
                return self.send_json(200 if header.get("ok") else 503, header)
            match = re.fullmatch(r"/evidence/([\w-]+)/(\w+)\.jpg", url.path)
            if match and (evidence_dir / match[1] / f"{match[2]}.jpg").exists():
                return self.send_bytes(200, (evidence_dir / match[1] / f"{match[2]}.jpg").read_bytes(), "image/jpeg")
            self.send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self.allowed(query):
                return
            op, zone = url.path.strip("/"), query.get("zone", [""])[0]
            size = int(self.headers.get("Content-Length", 0))
            if op not in FRAMES or not zone or not 0 < size <= MAX_BODY:
                return self.send_json(400, {"ok": False, "error": "POST /baseline?zone=NAME or /check?zone=NAME with a video as the body."})
            t0 = time.time()
            clip = self.rfile.read(size)
            seconds = {}
            if pick and clip[:2] != b"PK":  # a zip is a frame bundle the phone app already picked: pass it on
                try:
                    clip, n = pick(clip, FRAMES[op])
                except ValueError:
                    return self.send_json(400, {"ok": False, "error": "Could not read that file as a video."})
                seconds["pick_frames"] = round(time.time() - t0, 2)
            header, blob = link.call({"op": op, "zone": zone}, clip)
            seconds["total"] = round(time.time() - t0, 2)
            if not header.get("ok"):
                return self.send_json(502, {**header, "seconds": seconds})
            result = header["result"]
            if op == "baseline":
                return self.send_json(200, {"ok": True, "zone": zone, "frames": result["frames"], "seconds": seconds})
            images = {}
            if blob:
                folder = evidence_dir / header["job"]
                folder.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
                    for name in bundle.namelist():
                        if re.fullmatch(r"\w+\.jpg", name):
                            (folder / name).write_bytes(bundle.read(name))
                            images[name[:-4]] = f"/evidence/{header['job']}/{name}"
            headline, detail = in_plain_words(result)
            events = to_events(result, zone, images)
            body = {"ok": True, "zone": zone, "job": header["job"], "headline": headline, "detail": detail,
                    "verdict": result["verdict"], "events": events, "images": images,
                    "seconds": {**seconds, **result.get("seconds", {})}, "report": result}
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
    print("relay running. Open one of:", flush=True)
    for address in addresses:
        print(f"  http://{address}:{args.port}/?k={args.token}", flush=True)
    print(f"frame picking on this laptop: {'on' if rc else 'off (OpenCV not found)'}; evidence images in {evidence_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        link.close()


if __name__ == "__main__":
    main()
