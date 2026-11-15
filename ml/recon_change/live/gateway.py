#!/usr/bin/env python3
"""Gateway. Runs on the Iridis login node, at the far end of the relay's one SSH connection.

It speaks a tiny protocol on stdin/stdout so that a single connection carries every request:
a JSON line, then `size` raw bytes. Requests and replies have the same shape.

  {"op": "status"}
  {"op": "baseline", "zone": "kitchen", "size": N} + N bytes of video
  {"op": "check",    "zone": "kitchen", "size": N} + N bytes of video
  {"op": "locate",   "zone": "kitchen", "size": N} + N bytes: what the wearer sees now
  {"op": "waypoints", "zone": "kitchen"}                       the zone's named places
  {"op": "waypoint", "zone": "kitchen", "name": "pill box", "frame": 3, "u": 0.4, "v": 0.6}
  {"op": "waypoint", "zone": "kitchen", "name": "pill box", "delete": true}
  {"op": "baseline_image", "zone": "kitchen", "frame": 3}      one baseline picture, to pick a place in
  {"op": "scale", "zone": "kitchen", "a": "pill box", "b": "front door", "metres": 2.4}   (or "delete": true)
  {"op": "watches", "zone": "kitchen"}                         the zone's watched spots
  {"op": "watch", "zone": "kitchen", "name": "pill box shelf", "frame": 3, "box": [u0, v0, u1, v1]}
  {"op": "watch", "zone": "kitchen", "name": "pill box shelf", "delete": true}
  {"op": "enrolled"}                                           the items shown to the camera once
  {"op": "enroll", "name": "keys", "size": N} + N bytes: one photo of the item
  {"op": "enroll", "name": "keys", "delete": true}             the whole item ("n": 1 too: that photo only)
  {"op": "enrolled_image", "name": "keys", "n": 0}             one of its photos
  {"op": "worker_log", "lines": 60}                            the end of the worker's log (worker.log)
  {"op": "map", "zone": "kitchen"}                             the room map: the baseline in one pass, as room.splat

It only moves files: it drops the clip into queue/<job>/ for the GPU worker (see worker.py)
and waits for the result. Named places are a small JSON file next to the zone's baseline
pictures, and so are the room's real scale (scale.json) and its watched spots (watch.json).
Photos of enrolled items are in enrolled/<item>/00.jpg, 01.jpg, ... for the whole home.
Standard library only, so it runs on any Python 3. With OpenCV next to it (the cluster's
environment has it) an enrolled photo is also shrunk and rewritten as a JPEG.
"""
import io
import json
import math
import os
import re
import shutil
import struct
import sys
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(os.environ.get("BEACON_ROOT") or Path(__file__).resolve().parent.parent)
LOCAL = bool(os.environ.get("BEACON_ROOT"))  # the worker runs on this machine (relay.py --local), not on Iridis
QUEUE, BASELINES, ENROLLED = ROOT / "queue", ROOT / "baselines", ROOT / "enrolled"
IN, OUT = sys.stdin.buffer, sys.stdout.buffer
WAIT_S = 180  # give up on a job after this long
STALE_S = 90  # the worker's heartbeat is older than this: treat it as not running


def reply(header, blob=b""):
    header["size"] = len(blob)
    OUT.write(json.dumps(header).encode() + b"\n" + blob)
    OUT.flush()


def lane(device):
    """The GPU worker and the CPU worker each have their own queue folder and heartbeat file."""
    return "-cpu" if device == "cpu" else ""


def worker_age(device="gpu"):
    """Seconds since that worker's last heartbeat (it touches worker.alive), or None if it never started.
    Uses the file's modification time: reading a timestamp out of the file can catch it half-written."""
    try:
        return round(max(0.0, time.time() - (ROOT / f"worker{lane(device)}.alive").stat().st_mtime), 1)
    except OSError:
        return None


def worker_state(device):
    age = worker_age(device)
    running = age is not None and age < STALE_S
    try:
        info = json.loads((ROOT / f"worker{lane(device)}.json").read_text()) if running else None
    except (OSError, ValueError):
        info = None
    return {"running": running, "age_s": age, "info": info}


def places_of(zone):
    try:
        return json.loads((BASELINES / zone / "waypoints.json").read_text())
    except (OSError, ValueError):
        return []


def handle_places(op, zone, req):
    """Named places: list them, add or remove one, or fetch the baseline picture to pick one in."""
    folder = BASELINES / zone
    frames = len(list(folder.glob("*.jpg")))
    if not frames:
        return {"ok": False, "error": f"No baseline for '{zone}' yet. Record the baseline clip first."}, b""
    if op == "waypoints":
        return {"ok": True, "zone": zone, "frames": frames, "waypoints": places_of(zone), "scale": scale_of(zone)}, b""
    if op == "baseline_image":
        picture = folder / f"{int(req.get('frame', 0)):02d}.jpg"
        if not picture.exists():
            return {"ok": False, "error": "no such baseline picture"}, b""
        return {"ok": True}, picture.read_bytes()
    name = " ".join(str(req.get("name", "")).split())[:40]
    if not name:
        return {"ok": False, "error": "a place needs a name"}, b""
    places = [p for p in places_of(zone) if p["name"] != name]
    if not req.get("delete"):
        frame, u, v = int(req["frame"]), float(req["u"]), float(req["v"])
        if not (0 <= frame < frames and 0 <= u <= 1 and 0 <= v <= 1):
            return {"ok": False, "error": "frame must be a baseline picture, u and v between 0 and 1"}, b""
        places.append({"name": name, "frame": frame, "u": round(u, 4), "v": round(v, 4)})
    (folder / "waypoints.json").write_text(json.dumps(places))
    if scale_of(zone) is None:
        (folder / "scale.json").unlink(missing_ok=True)  # it was measured from a place that is now gone
    return {"ok": True, "zone": zone, "frames": frames, "waypoints": places, "scale": scale_of(zone)}, b""


# ---------------------------------------------------------------- what the carer sets once
# Everything below is typed or uploaded by someone in a browser, and this runs under the owner's
# account on a shared cluster. So: every name is checked against a pattern, every number is
# checked to be a finite number in range, every photo is checked to be a photo, and the only
# text that ever becomes part of a path is an item name that matched ITEM_NAME.
ITEM_NAME = r"[a-z0-9]+(?:_[a-z0-9]+)*"  # "remote_control": lowercase letters and digits, joined by single underscores
MAX_ITEMS, MAX_PHOTOS, MAX_SPOTS = 12, 5, 12  # items in the home, photos of one item, watched spots in one room
PHOTO_BYTES, PHOTO_SIDE = 20 * 1024 * 1024, 1280  # largest upload; long side of the stored JPEG
PHOTO_PIXELS = 12000  # a "photo" wider or taller than this is not opened at all


def set_root(folder):
    """Work in another project folder. The offline tests use a temporary one."""
    global ROOT, QUEUE, BASELINES, ENROLLED
    ROOT = Path(folder)
    QUEUE, BASELINES, ENROLLED = ROOT / "queue", ROOT / "baselines", ROOT / "enrolled"


def write_json(path, data):
    """Write through a second file and rename it, so the worker never reads half a file."""
    part = path.with_suffix(".part")
    part.write_text(json.dumps(data))
    os.replace(part, path)


def label(text):
    """A name someone typed, with single spaces, or "" if it is not a plain name: it must start with a
    letter or a digit, then letters, digits, spaces, ' and -, at most 40 characters in all."""
    name = " ".join(text.split()) if isinstance(text, str) else ""
    return name if re.fullmatch(r"[^\W_][\w '-]{0,39}", name) else ""


def number(value, low, high):
    """The value as a float if it is a finite number from low to high, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if low <= value <= high else None


def whole(value, count):
    """The value if it is a whole number from 0 to count - 1, else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count else None


def scale_of(zone):
    """The room's real scale: {"a": place, "b": place, "metres": the distance between them}, or None.
    None too when one of the two places is no longer named: the distance then measures nothing."""
    try:
        scale = json.loads((BASELINES / zone / "scale.json").read_text())
        names = {p["name"] for p in places_of(zone)}
        return scale if scale["a"] in names and scale["b"] in names else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def watched_of(zone):
    try:
        spots = json.loads((BASELINES / zone / "watch.json").read_text())
        return spots if isinstance(spots, list) else []
    except (OSError, ValueError):
        return []


def handle_setup(op, zone, req):
    """A room's real scale (scale.json) and its watched spots (watch.json): show, set or remove."""
    folder = BASELINES / zone
    frames = len(list(folder.glob("*.jpg")))
    if not frames:
        return {"ok": False, "error": f"No baseline for '{zone}' yet. Record the baseline clip first."}, b""
    if op == "scale":
        if req.get("delete"):
            (folder / "scale.json").unlink(missing_ok=True)
        else:
            a, b, metres = req.get("a"), req.get("b"), number(req.get("metres"), 0.1, 50)
            names = {p["name"] for p in places_of(zone)}  # the two names must be places of this room, nothing else
            if not isinstance(a, str) or not isinstance(b, str) or a == b or a not in names or b not in names:
                return {"ok": False, "error": "Pick two different named places in this room."}, b""
            if metres is None:
                return {"ok": False, "error": "The distance must be between 0.1 and 50 metres."}, b""
            write_json(folder / "scale.json", {"a": a, "b": b, "metres": round(metres, 3)})
        return {"ok": True, "zone": zone, "scale": scale_of(zone)}, b""
    spots = watched_of(zone)
    if op == "watch":
        name = label(req.get("name"))
        if not name:
            return {"ok": False, "error": "Give the spot a plain name: letters, digits and spaces, at most 40 characters."}, b""
        spots = [s for s in spots if not (isinstance(s, dict) and s.get("name") == name)]
        if not req.get("delete"):
            frame, box = whole(req.get("frame"), frames), req.get("box")
            box = [number(v, 0, 1) for v in box] if isinstance(box, list) and len(box) == 4 else [None]
            if frame is None or None in box:
                return {"ok": False, "error": "frame must be a baseline picture, the box four numbers between 0 and 1"}, b""
            (u0, u1), (v0, v1) = sorted(box[0::2]), sorted(box[1::2])  # whichever way the box was dragged
            if u1 - u0 < 0.01 or v1 - v0 < 0.01:
                return {"ok": False, "error": "That box is too small. Drag a bigger one."}, b""
            if len(spots) >= MAX_SPOTS:
                return {"ok": False, "error": f"A room can have {MAX_SPOTS} watched spots. Remove one first."}, b""
            spots.append({"name": name, "frame": frame, "box": [round(x, 4) for x in (u0, v0, u1, v1)]})
        write_json(folder / "watch.json", spots)
    return {"ok": True, "zone": zone, "frames": frames, "watch": spots}, b""


def photo_size(data):
    """(width, height) of a JPEG, PNG or WebP, read from its first bytes, or None if it is none of them."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X":  # the extended format: the canvas size, each minus one, in three bytes
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if kind == b"VP8L" and data[20] == 0x2F:  # lossless: two 14-bit numbers, each minus one
                bits = struct.unpack("<I", data[21:25])[0]
                return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
            if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":  # lossy
                width, height = struct.unpack("<HH", data[26:30])
                return width & 0x3FFF, height & 0x3FFF
            return None
        if data[:2] != b"\xff\xd8":
            return None
        at = 2
        while at + 4 <= len(data):  # walk the JPEG's segments to the one that holds the size
            if data[at] != 0xFF:
                return None
            marker = data[at + 1]
            if marker == 0xFF:  # padding
                at += 1
            elif marker == 0x01 or 0xD0 <= marker <= 0xD8:  # markers without a length
                at += 2
            elif marker in (0xD9, 0xDA):  # the picture data starts or ends before any size was given
                return None
            elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[at + 5:at + 9])
                return width, height
            else:
                at += 2 + struct.unpack(">H", data[at + 2:at + 4])[0]
    except (struct.error, IndexError):
        pass
    return None


def opencv():
    """OpenCV and NumPy if they are installed next to this script, else None."""
    try:
        import cv2
        import numpy
        return cv2, numpy
    except ImportError:
        return None


def as_jpeg(data):
    """A photo as a JPEG with its long side at most PHOTO_SIDE. ValueError says, in plain words, why not."""
    size = photo_size(data)
    if not size or min(size) < 32 or max(size) > PHOTO_PIXELS:
        raise ValueError("That is not a photo. Use a JPEG, PNG or WebP picture.")
    tools = opencv()
    if tools is None:  # plain Python cannot shrink a picture: keep a JPEG that is small enough, refuse the rest
        if data[:2] == b"\xff\xd8" and max(size) <= PHOTO_SIDE:
            return data
        raise ValueError(f"Send a JPEG with its long side at most {PHOTO_SIDE} (no OpenCV on this side to shrink it).")
    cv2, numpy = tools
    frame = cv2.imdecode(numpy.frombuffer(data, numpy.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("That photo could not be read.")
    scale = PHOTO_SIDE / max(frame.shape[:2])
    if scale < 1:
        frame = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    made, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])  # rewriting also drops the phone's location tag
    if not made:
        raise ValueError("That photo could not be read.")
    return jpeg.tobytes()


def item_folder(name):
    """The folder of an enrolled item ("Remote control" -> enrolled/remote_control), or None if the
    name is not a plain one. No other text from a request ever becomes part of a path."""
    name = "_".join(name.lower().split()) if isinstance(name, str) else ""
    return ENROLLED / name if len(name) <= 32 and re.fullmatch(ITEM_NAME, name) else None


def photos_of(folder):
    return sorted(folder.glob("[0-9][0-9].jpg"))


def enrolled_items():
    """Every enrolled item with its number of photos, by name."""
    return [{"name": folder.name, "photos": len(photos_of(folder))} for folder in sorted(ENROLLED.glob("*"))
            if folder.is_dir() and re.fullmatch(ITEM_NAME, folder.name) and photos_of(folder)]


def handle_enrolled(op, req, blob):
    """Items shown to the camera once, so the item finder can look for that exact item: list them,
    add a photo, fetch one, remove one photo or the whole item. They belong to the home, not to a room."""
    if op != "enrolled":
        folder = item_folder(req.get("name"))
        if folder is None:
            return {"ok": False, "error": "Give the item a plain name: letters, digits and spaces, at most 32 characters."}, b""
        photos = photos_of(folder)
        if op == "enrolled_image":
            n = whole(req.get("n"), len(photos))
            return ({"ok": True}, photos[n].read_bytes()) if n is not None else ({"ok": False, "error": "no such photo"}, b"")
        if req.get("delete"):
            if req.get("n") is not None:
                n = whole(req.get("n"), len(photos))
                if n is None:
                    return {"ok": False, "error": "no such photo"}, b""
                photos.pop(n).unlink()
                for i, photo in enumerate(photos):  # close the gap: the photos stay 00, 01, ...
                    os.replace(photo, folder / f"{i:02d}.jpg")
            else:
                photos = []
            if not photos and folder.is_dir() and not folder.is_symlink():
                shutil.rmtree(folder)  # the whole item, with anything a worker kept next to its photos
        else:
            if not blob or len(blob) > PHOTO_BYTES:
                return {"ok": False, "error": f"Send one photo of the item, at most {PHOTO_BYTES >> 20} MB."}, b""
            if len(photos) >= MAX_PHOTOS:
                return {"ok": False, "error": f"This item already has {MAX_PHOTOS} photos. Remove one first."}, b""
            if not photos and len(enrolled_items()) >= MAX_ITEMS:
                return {"ok": False, "error": f"The home can have {MAX_ITEMS} items. Remove one first."}, b""
            try:
                jpeg = as_jpeg(blob)
            except ValueError as err:
                return {"ok": False, "error": str(err)}, b""
            folder.mkdir(parents=True, exist_ok=True)
            part = folder / f"{len(photos):02d}.part"  # under another name first: the worker reads *.jpg
            part.write_bytes(jpeg)
            os.replace(part, folder / f"{len(photos):02d}.jpg")
    return {"ok": True, "items": enrolled_items(), "max_items": MAX_ITEMS, "max_photos": MAX_PHOTOS}, b""


def remembered(req):
    """The spots where items were seen, as the relay sent them: [{"name", "position": [east, north, up]}].
    Only well-formed entries are passed on to the worker, at most ten."""
    spots = []
    listed = req.get("remembered")
    for spot in listed[:10] if isinstance(listed, list) else []:
        try:
            position = [float(v) for v in spot["position"]]
            if len(position) == 3 and all(abs(v) < 1e4 for v in position):  # also rejects nan and inf
                spots.append({"name": str(spot["name"])[:40], "position": position})
        except (KeyError, TypeError, ValueError):
            continue
    return spots


def worker_log(device, lines):
    """The last lines of the worker's log: worker.log in the data folder (run_local.sh writes it there), or on
    the cluster the newest Slurm output file of a worker job next to the code."""
    lines = lines if isinstance(lines, int) and not isinstance(lines, bool) and 0 < lines <= 400 else 60
    found = ROOT / f"worker{lane(device)}.log"
    if not found.exists():
        outputs = sorted(ROOT.glob("slurm-*worker*.out"), key=lambda f: f.stat().st_mtime)
        found = outputs[-1] if outputs else None
    if found is None:
        return {"ok": False, "error": "no worker log here"}
    with open(found, "rb") as log:  # read only the end: a worker that ran for hours has a long log
        log.seek(0, os.SEEK_END)
        start = max(0, log.tell() - 200 * lines)
        log.seek(start)
        text = log.read().decode("utf-8", "replace")
    if start:
        text = text.partition("\n")[2]  # the first line was cut in the middle
    return {"ok": True, "file": found.name, "local": LOCAL, "lines": text.splitlines()[-lines:]}


def handle(req, blob):
    op = req.get("op")
    device = "cpu" if req.get("device") == "cpu" else "gpu"
    age = worker_age(device)
    if op == "status":
        zones = sorted(p.name for p in BASELINES.glob("*") if any(p.glob("*.jpg")))
        workers = {d: worker_state(d) for d in ("gpu", "cpu")}
        return {"ok": True, "worker_running": any(w["running"] for w in workers.values()), "workers": workers, "zones": zones}, b""
    if op == "worker_log":  # the end of the worker's log, for the dashboard
        return worker_log(device, req.get("lines")), b""
    if op in ("enrolled", "enroll", "enrolled_image"):  # items belong to the home: no zone
        return handle_enrolled(op, req, blob)
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", str(req.get("zone", ""))):
        return {"ok": False, "error": "bad request: zone must be a-z 0-9 _ -"}, b""
    if op in ("waypoints", "waypoint", "baseline_image"):
        return handle_places(op, req["zone"], req)
    if op in ("scale", "watch", "watches"):
        return handle_setup(op, req["zone"], req)
    if op not in ("baseline", "check", "locate", "map"):
        return {"ok": False, "error": "bad request: unknown op"}, b""
    if not blob and op != "map":  # the room map is made from the zone's own baseline pictures: nothing is sent
        return {"ok": False, "error": "empty clip"}, b""
    if age is None or age > STALE_S:
        if LOCAL:
            return {"ok": False, "error": f"the local {device.upper()} worker is not running (start it with run_local.sh; "
                                          f"its log is {ROOT / f'worker{lane(device)}.log'})"}, b""
        script = "worker_cpu.slurm" if device == "cpu" else "worker.slurm"
        return {"ok": False, "error": f"the {device.upper()} worker is not running on Iridis (sbatch iridis/{script})"}, b""

    job = ROOT / f"queue{lane(device)}" / f"{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
    job.mkdir(parents=True)
    clip = "clip.zip" if blob[:2] == b"PK" else "clip.mp4"  # a zip of frames from the relay, or a video
    if blob:
        (job / clip).write_bytes(blob)
    (job / "request.json").write_text(json.dumps({"op": op, "zone": req["zone"], "file": clip, "view": bool(req.get("view")),
                                                  "remembered": remembered(req)}))
    (job / "READY").touch()  # last, so the worker never sees a half-written job

    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        if (job / "DONE").exists():
            images = io.BytesIO()  # every picture the worker produced, as one zip
            with zipfile.ZipFile(images, "w", zipfile.ZIP_STORED) as bundle:
                for made in sorted((job / "out").glob("*")):
                    if made.suffix in (".jpg", ".splat"):
                        bundle.write(made, made.name)
            return {"ok": True, "job": job.name, "result": json.loads((job / "result.json").read_text())}, images.getvalue()
        if (job / "FAILED").exists():
            lines = (job / "error.txt").read_text().strip().splitlines()
            return {"ok": False, "job": job.name, "error": lines[-1] if lines else "worker failed"}, b""
        time.sleep(0.2)
    return {"ok": False, "job": job.name, "error": f"no result after {WAIT_S} s"}, b""


def main():
    reply({"ok": True, "hello": "gateway"})
    while True:
        line = IN.readline()
        if not line:
            break  # the relay closed the connection
        try:
            req = json.loads(line)
            blob = IN.read(int(req.get("size", 0)))
            header, data = handle(req, blob)
        except Exception as err:  # never let one bad request kill the connection
            header, data = {"ok": False, "error": f"{type(err).__name__}: {err}"}, b""
        reply(header, data)


if __name__ == "__main__":
    main()
