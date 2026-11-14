#!/usr/bin/env python3
"""Offline test of what the carer sets once: the room's real scale, watched spots, enrolled items.

No cluster: the relay's handler is the real one, and instead of the SSH connection it talks to
the real gateway code, working in a temporary folder (see dev_setup.LocalGateway). The test
checks every route, bad input, and the files on disk, byte for byte where the format is fixed.

    python live/test_setup_flow.py
"""
import json
import shutil
import socket
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dev_setup  # noqa: E402
import gateway  # noqa: E402
import relay  # noqa: E402

KEY = "test-key"
JPEG = dev_setup.TINY_JPEG  # a real 48 x 32 JPEG


def png(width, height):
    """A real, plain grey PNG, made with the standard library only."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = (b"\x00" + b"\x80" * width * 3) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def tree(root):
    """Every file under a folder with its size: to show that a refused request wrote nothing."""
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in Path(root).rglob("*") if p.is_file())


def main():
    have_opencv = gateway.opencv() is not None
    outer = Path(tempfile.mkdtemp(prefix="beacon-setup-test-"))  # the project folder is one level down, so that a
    root = outer / "project"                                    # path that climbed out of it would show up here
    gateway.set_root(root)
    kitchen = dev_setup.make_room(root, "kitchen", 6)
    link = dev_setup.LocalGateway(canned_fix=True)
    handler = relay.make_handler(link, outer / "evidence-not-used", KEY, None, pick=None)
    (outer / "evidence-not-used").mkdir()
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, raw=None, method=None, key=KEY):
        request = urllib.request.Request(url + path, data=raw, headers={"X-Key": key}, method=method or ("POST" if raw is not None else "GET"))
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read()
                return response.status, json.loads(data) if response.headers["Content-Type"] == "application/json" else data
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")

    def post(path, raw=b"", **query):
        return call(f"{path}?{urlencode(query)}", raw=raw, method="POST")

    # ---------------------------------------------------------------- the access key
    for path, method in (("/enrolled", "GET"), ("/watch?zone=kitchen", "GET"), ("/enroll?name=keys", "POST"),
                         ("/scale?zone=kitchen&delete=1", "POST"), ("/watch?zone=kitchen&name=x&delete=1", "POST")):
        assert call(path, raw=JPEG if method == "POST" else None, method=method, key="wrong")[0] == 403, path

    # ---------------------------------------------------------------- 1. the room's real scale
    code, body = call("/waypoints?zone=kitchen")
    assert code == 200 and body["frames"] == 6 and body["waypoints"] == [] and body["scale"] is None, body
    for name, u in (("pill box", 0.55), ("front door", 0.15), ("key hook", 0.9)):
        assert post("/waypoint", zone="kitchen", name=name, frame=2, u=u, v=0.4)[0] == 200
    scale_file = kitchen / "scale.json"
    code, body = post("/scale", zone="kitchen", a="pill box", b="front door", metres="2.4")
    assert code == 200 and body["scale"] == {"a": "pill box", "b": "front door", "metres": 2.4}, body
    assert scale_file.read_text() == '{"a": "pill box", "b": "front door", "metres": 2.4}', scale_file.read_text()
    code, body = call("/waypoints?zone=kitchen")
    assert body["scale"] == {"a": "pill box", "b": "front door", "metres": 2.4} and len(body["waypoints"]) == 3, body
    before = scale_file.read_text()
    for bad in ({"a": "pill box", "b": "pill box", "metres": "2"},        # the same place twice
                {"a": "pill box", "b": "garage", "metres": "2"},          # not a place of this room
                {"a": "pill box", "b": "front door", "metres": "0"},
                {"a": "pill box", "b": "front door", "metres": "-3"},
                {"a": "pill box", "b": "front door", "metres": "51"},
                {"a": "pill box", "b": "front door", "metres": "nan"},
                {"a": "pill box", "b": "front door", "metres": "inf"},
                {"a": "pill box", "b": "front door", "metres": "two"},
                {"a": "pill box", "b": "front door"},
                {"a": "pill box", "metres": "2"}):
        code, body = post("/scale", zone="kitchen", **bad)
        assert code == 400 and body["ok"] is False and body["error"], (bad, code, body)
    assert scale_file.read_text() == before, "a refused scale must not change the file"
    assert post("/scale", zone="../x", a="pill box", b="front door", metres="2")[0] == 400
    assert post("/scale", zone="attic", a="pill box", b="front door", metres="2")[0] == 400  # no baseline there
    code, body = post("/scale", zone="kitchen", a="key hook", b="pill box", metres="0.85")   # a new one replaces the old
    assert code == 200 and json.loads(scale_file.read_text()) == {"a": "key hook", "b": "pill box", "metres": 0.85}
    code, body = post("/scale", zone="kitchen", delete=1)
    assert code == 200 and body["scale"] is None and not scale_file.exists(), body
    assert post("/scale", zone="kitchen", delete=1)[0] == 200  # removing nothing is fine
    # The scale goes when one of its two places is removed: the distance then measures nothing.
    post("/scale", zone="kitchen", a="pill box", b="key hook", metres="1.2")
    assert scale_file.exists()
    code, body = post("/waypoint", zone="kitchen", name="front door", delete=1)   # not one of the two: the scale stays
    assert body["scale"] == {"a": "pill box", "b": "key hook", "metres": 1.2} and scale_file.exists(), body
    code, body = post("/waypoint", zone="kitchen", name="key hook", delete=1)
    assert code == 200 and body["scale"] is None and not scale_file.exists(), body
    assert not list(kitchen.glob("*.part")), "a temporary file was left behind"

    # ---------------------------------------------------------------- 3. spots to watch
    watch_file = kitchen / "watch.json"
    code, body = call("/watch?zone=kitchen")
    assert code == 200 and body["watch"] == [] and body["frames"] == 6, body
    code, body = post("/watch", zone="kitchen", name="pill box shelf", frame=3, u0=0.2, v0=0.3, u1=0.5, v1=0.6)
    assert code == 200 and body["watch"] == [{"name": "pill box shelf", "frame": 3, "box": [0.2, 0.3, 0.5, 0.6]}], body
    assert watch_file.read_text() == '[{"name": "pill box shelf", "frame": 3, "box": [0.2, 0.3, 0.5, 0.6]}]', watch_file.read_text()
    # Dragged from the bottom right to the top left: the box is stored the right way round.
    code, body = post("/watch", zone="kitchen", name="  Key   hook ", frame=0, u0=0.95, v0=0.4, u1=0.85, v1=0.2)
    assert code == 200 and body["watch"][1] == {"name": "Key hook", "frame": 0, "box": [0.85, 0.2, 0.95, 0.4]}, body
    code, body = post("/watch", zone="kitchen", name="pill box shelf", frame=5, u0=0, v0=0, u1=1, v1=1)  # same name: replaced
    assert code == 200 and [s["name"] for s in body["watch"]] == ["Key hook", "pill box shelf"] and body["watch"][1]["frame"] == 5
    assert call("/watch?zone=kitchen")[1]["watch"] == json.loads(watch_file.read_text())
    before = watch_file.read_text()
    good = {"name": "window", "frame": 1, "u0": 0.1, "v0": 0.1, "u1": 0.4, "v1": 0.4}
    for change in ({"frame": 6}, {"frame": 99}, {"frame": -1}, {"frame": "two"}, {"frame": 1.5}, {"u0": 1.5}, {"v1": -0.1},
                   {"u1": "nan"}, {"v0": "inf"}, {"u0": ""}, {"u1": 0.105}, {"v1": 0.1},   # the last two: too small
                   {"name": ""}, {"name": "<script>"}, {"name": "../../x"}, {"name": "a/b"}, {"name": "x" * 41},
                   {"name": "shelf\x00"}, {"name": "_hidden"}):
        code, body = post("/watch", zone="kitchen", **{**good, **change})
        assert code == 400 and body["ok"] is False and body["error"], (change, code, body)
    assert watch_file.read_text() == before, "a refused spot must not change the file"
    assert post("/watch", zone="Kitchen!", **good)[0] == 400
    assert post("/watch", zone="attic", **good)[0] == 400 and call("/watch?zone=attic")[0] == 404  # no baseline there
    assert call("/watch?zone=..")[0] == 400 and call("/watch")[0] == 400
    for i in range(gateway.MAX_SPOTS - 2):
        assert post("/watch", zone="kitchen", **{**good, "name": f"spot {i}"})[0] == 200
    code, body = post("/watch", zone="kitchen", **{**good, "name": "one too many"})
    assert code == 400 and "Remove one first" in body["error"], body
    assert post("/watch", zone="kitchen", **{**good, "name": "spot 0", "frame": 4})[0] == 200  # replacing one still works
    code, body = post("/watch", zone="kitchen", name="Key hook", delete=1)
    assert code == 200 and "Key hook" not in [s["name"] for s in body["watch"]] and len(body["watch"]) == gateway.MAX_SPOTS - 1
    assert post("/watch", zone="kitchen", name="never there", delete=1)[0] == 200
    for spot in json.loads(watch_file.read_text()):  # the exact shape a reader can rely on
        assert set(spot) == {"name", "frame", "box"} and isinstance(spot["frame"], int) and len(spot["box"]) == 4
        u0, v0, u1, v1 = spot["box"]
        assert 0 <= u0 < u1 <= 1 and 0 <= v0 < v1 <= 1

    # ---------------------------------------------------------------- 2. show an item once
    enrolled = root / "enrolled"
    code, body = call("/enrolled")
    assert code == 200 and body["items"] == [] and body["max_photos"] == 5, body
    code, body = post("/enroll", raw=JPEG, name="Remote  Control")
    assert code == 200 and body["items"] == [{"name": "remote_control", "photos": 1}], body
    first = enrolled / "remote_control" / "00.jpg"
    assert first.read_bytes()[:2] == b"\xff\xd8" and gateway.photo_size(first.read_bytes()) == (48, 32)
    code, picture = call("/enrolled/remote_control/0.jpg")
    assert code == 200 and picture == first.read_bytes()
    assert call("/enrolled/remote_control/1.jpg")[0] == 404 and call("/enrolled/keys/0.jpg")[0] == 404
    for path in ("/enrolled/Remote_Control/0.jpg", "/enrolled/remote control/0.jpg", "/enrolled/../baselines/kitchen/0.jpg",
                 "/enrolled/remote_control/000.jpg", "/enrolled/remote_control/0.png", "/enrolled/" + "a" * 33 + "/0.jpg"):
        assert call(quote(path))[0] == 404, path
    sizes = [(48, 32), (64, 40), (80, 48), (96, 56)]  # four more photos, each one recognisable by its size
    for size in sizes:
        assert post("/enroll", raw=png(*size) if have_opencv else JPEG, name="remote control")[0] == 200
    names = sorted(p.name for p in (enrolled / "remote_control").iterdir())
    assert names == ["00.jpg", "01.jpg", "02.jpg", "03.jpg", "04.jpg"], names
    code, body = post("/enroll", raw=JPEG, name="remote_control")
    assert code == 400 and "5 photos" in body["error"], body
    if have_opencv:  # a PNG is stored as a JPEG
        stored = [gateway.photo_size(p.read_bytes()) for p in sorted((enrolled / "remote_control").iterdir())]
        assert stored == [(48, 32), *sizes] and all(p.read_bytes()[:2] == b"\xff\xd8" for p in (enrolled / "remote_control").iterdir())
    # Removing one photo closes the gap: 00, 01, 02, 03, and what was photo 3 is now photo 2.
    third = (enrolled / "remote_control" / "03.jpg").read_bytes()
    code, body = post("/enroll", name="remote control", n=2, delete=1)
    assert code == 200 and body["items"] == [{"name": "remote_control", "photos": 4}], body
    assert sorted(p.name for p in (enrolled / "remote_control").iterdir()) == ["00.jpg", "01.jpg", "02.jpg", "03.jpg"]
    assert (enrolled / "remote_control" / "02.jpg").read_bytes() == third
    assert post("/enroll", name="remote control", n=4, delete=1)[0] == 400   # no such photo
    assert post("/enroll", name="remote control", n="-1", delete=1)[0] == 400
    assert post("/enroll", name="remote control", n="1e0", delete=1)[0] == 400
    assert len(list((enrolled / "remote_control").iterdir())) == 4

    before = tree(outer)
    for name in ("", "../../etc", "..", "a/b", "a\\b", "keys!", "k" * 33, "_keys", "keys_", "clés", "keys\x00", "c:keys", "keys.jpg"):
        code, body = post("/enroll", raw=JPEG, name=name)
        assert code == 400 and body["ok"] is False, (name, code, body)
        assert post("/enroll", name=name, delete=1)[0] == 400, name
    gif = b"GIF89a" + bytes(200)
    pdf = b"%PDF-1.4\n" + bytes(200)
    video = bytes(4) + b"ftypisom" + bytes(200)
    for what, raw in (("a GIF", gif), ("a PDF", pdf), ("a video", video), ("text", b"hello " * 50), ("a zip", b"PK\x03\x04" + bytes(100)),
                      ("a JPEG start and nothing else", b"\xff\xd8" + bytes(300)), ("a PNG start and nothing else", png(64, 64)[:40]),
                      ("a huge PNG by its header", png(64, 64)[:16] + struct.pack(">II", 60000, 60000) + png(64, 64)[24:]),
                      ("a 1 x 1 PNG", png(1, 1))):
        code, body = post("/enroll", raw=raw, name="keys")
        assert code == 400 and body["ok"] is False and body["error"], (what, code, body)
    assert post("/enroll", raw=b"", name="keys")[0] == 400  # no photo at all
    # A body larger than the cap is refused from its declared size, before any of it is read.
    with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=10) as raw_socket:
        raw_socket.sendall(f"POST /enroll?name=keys HTTP/1.0\r\nX-Key: {KEY}\r\nContent-Length: {relay.PHOTO_BYTES + 1}\r\n\r\n".encode())
        assert raw_socket.recv(200).split(b"\r\n")[0].endswith(b"400 Bad Request")
    assert tree(outer) == before, "a refused upload wrote something"
    assert not (enrolled / "keys").exists()

    # At most twelve items in the home.
    for i in range(gateway.MAX_ITEMS - 1):
        assert post("/enroll", raw=JPEG, name=f"thing {i}")[0] == 200
    code, body = post("/enroll", raw=JPEG, name="one too many")
    assert code == 400 and "Remove one first" in body["error"] and not (enrolled / "one_too_many").exists(), body
    assert post("/enroll", raw=JPEG, name="thing 3")[0] == 200  # another photo of an item that is there is fine
    assert (enrolled / "thing_3" / "01.jpg").exists()
    # Removing the last photo of an item removes the item, and so does removing the item.
    (enrolled / "thing_0" / "cache.bin").write_bytes(b"kept by a worker")
    code, body = post("/enroll", name="thing 0", n=0, delete=1)
    assert code == 200 and not (enrolled / "thing_0").exists() and "thing_0" not in [i["name"] for i in body["items"]], body
    code, body = post("/enroll", name="Remote Control", delete=1)
    assert code == 200 and not (enrolled / "remote_control").exists(), body
    assert post("/enroll", name="remote control", delete=1)[0] == 200  # removing nothing is fine
    for i in range(1, gateway.MAX_ITEMS - 1):
        assert post("/enroll", name=f"thing_{i}", delete=1)[0] == 200
    assert call("/enrolled")[1]["items"] == [] and not list(enrolled.iterdir())

    if have_opencv:  # a phone photo is shrunk to 1280 on its long side, upright or sideways, and stored as a JPEG
        import cv2
        import numpy as np
        noise = np.random.default_rng(0).integers(0, 255, (1500, 2000, 3), np.uint8)
        for kind, picture, expect in ((".jpg", noise, (1280, 960)), (".png", noise[:, :600], (512, 1280)), (".webp", noise[:900, :1200], (1200, 900))):
            made, data = cv2.imencode(kind, picture)
            assert made and gateway.photo_size(data.tobytes()) == picture.shape[1::-1], kind
            code, body = post("/enroll", raw=data.tobytes(), name="glasses")
            assert code == 200, (kind, body)
        stored = [p.read_bytes() for p in sorted((enrolled / "glasses").iterdir())]
        assert [gateway.photo_size(s) for s in stored] == [(1280, 960), (512, 1280), (1200, 900)] and all(s[:2] == b"\xff\xd8" for s in stored)
        assert all(cv2.imdecode(np.frombuffer(s, np.uint8), cv2.IMREAD_COLOR) is not None for s in stored)
        # The gateway shrinks on its own too (a relay without OpenCV passes the photo on as it is).
        made, data = cv2.imencode(".png", noise)
        header, _ = gateway.handle({"op": "enroll", "name": "glasses"}, data.tobytes())
        assert header["ok"] and gateway.photo_size((enrolled / "glasses" / "03.jpg").read_bytes()) == (1280, 960), header
        # Without OpenCV on either side: a small JPEG is kept as it is, anything else is refused in plain words.
        big_jpeg = cv2.imencode(".jpg", noise)[1].tobytes()
        real_opencv, real_cv2, gateway.opencv, relay.cv2 = gateway.opencv, relay.cv2, lambda: None, None
        try:
            assert post("/enroll", raw=JPEG, name="wallet")[0] == 200 and (enrolled / "wallet" / "00.jpg").read_bytes() == JPEG
            for raw in (png(64, 64), big_jpeg):
                code, body = post("/enroll", raw=raw, name="wallet")
                assert code == 400 and "1280" in body["error"], body
        finally:
            gateway.opencv, relay.cv2 = real_opencv, real_cv2
        assert [i["name"] for i in call("/enrolled")[1]["items"]] == ["glasses", "wallet"]
        shutil.rmtree(enrolled)

    # ---------------------------------------------------------------- the gateway on its own
    # The gateway must refuse on its own whatever a relay would never send: wrong types, numbers that
    # are not numbers, names that are paths. Nothing may be written, inside the project folder or out of it.
    post("/waypoint", zone="kitchen", name="front door", frame=1, u=0.2, v=0.5)
    before = tree(outer)
    nan, inf = float("nan"), float("inf")
    spot = {"op": "watch", "zone": "kitchen", "name": "sink", "frame": 1, "box": [0.1, 0.1, 0.5, 0.5]}
    scale = {"op": "scale", "zone": "kitchen", "a": "pill box", "b": "front door", "metres": 2.0}
    hostile = [
        {**spot, "zone": "../kitchen"}, {**spot, "zone": "kitchen/../../x"}, {**spot, "zone": ""}, {**spot, "zone": None}, {**spot, "zone": ["kitchen"]},
        {**spot, "name": "../../x"}, {**spot, "name": ["sink"]}, {**spot, "name": None}, {**spot, "name": 7}, {**spot, "name": "a\nb" * 30},
        {**spot, "frame": True}, {**spot, "frame": 1.0}, {**spot, "frame": "1"}, {**spot, "frame": None}, {**spot, "frame": 6}, {**spot, "frame": -1},
        {**spot, "box": [0.1, 0.1, 0.5]}, {**spot, "box": "abcd"}, {**spot, "box": None}, {**spot, "box": [0.1, 0.1, 0.5, nan]},
        {**spot, "box": [0.1, 0.1, inf, 0.5]}, {**spot, "box": [0.1, 0.1, 0.5, "0.5"]}, {**spot, "box": [0.1, 0.1, 0.5, True]},
        {**spot, "box": [-0.1, 0.1, 0.5, 0.5]}, {**spot, "box": [0.1, 0.1, 0.5, 1.01]}, {**spot, "box": [[0.1], 0.1, 0.5, 0.5]},
        {**scale, "metres": nan}, {**scale, "metres": inf}, {**scale, "metres": True}, {**scale, "metres": "2"}, {**scale, "metres": None},
        {**scale, "metres": 0.01}, {**scale, "metres": 1e9}, {**scale, "a": ["pill box"]}, {**scale, "a": None}, {**scale, "b": "pill box"},
        {**scale, "b": "../front door"}, {**scale, "zone": ".."},
        {"op": "watches", "zone": "../.."}, {"op": "watches", "zone": "attic"},
        {"op": "enroll", "name": "../x"}, {"op": "enroll", "name": "/tmp/x"}, {"op": "enroll", "name": ["keys"]}, {"op": "enroll", "name": None},
        {"op": "enroll"}, {"op": "enroll", "name": "keys"},  # the last one: no photo
        {"op": "enroll", "name": "..", "delete": True}, {"op": "enroll", "name": "", "delete": True}, {"op": "enroll", "name": ".", "delete": True},
        {"op": "enrolled_image", "name": "../baselines/kitchen", "n": 0}, {"op": "enrolled_image", "name": "keys", "n": 0},
        {"op": "enrolled_image", "name": "keys", "n": "0"}, {"op": "enrolled_image", "name": "keys"},
    ]
    for req in hostile:
        header, data = link.call(req, JPEG if req["op"] == "enroll" and "name" not in req else b"")
        assert header["ok"] is False and header["error"] and data == b"", (req, header)
    for name, raw in (("../x", JPEG), ("keys", b"\xff\xd8" + bytes(gateway.PHOTO_BYTES)), ("keys", b"not a photo"), ("keys", gif)):
        header, _ = link.call({"op": "enroll", "name": name}, raw)
        assert header["ok"] is False, (name, header)
    assert link.call({"op": "enroll", "name": "keys"}, JPEG)[0]["ok"]
    for bad_n in (True, 1.0, "0", -1, 5, [0], nan):
        header, _ = link.call({"op": "enroll", "name": "keys", "delete": True, "n": bad_n})
        assert header["ok"] is False, (bad_n, header)
        assert link.call({"op": "enrolled_image", "name": "keys", "n": bad_n})[0]["ok"] is False, bad_n
    assert (enrolled / "keys" / "00.jpg").exists()
    assert link.call({"op": "enroll", "name": "keys", "delete": True})[0]["ok"]
    assert tree(outer) == before, "a refused request wrote something"
    assert sorted(p.name for p in outer.iterdir()) == ["evidence-not-used", "project"], "something was written outside the project folder"

    # Names are checked as the docstrings say.
    assert gateway.item_folder("Remote  Control") == enrolled / "remote_control" and gateway.item_folder("key 2") == enrolled / "key_2"
    assert gateway.label("  Mum's  chair ") == "Mum's chair" and gateway.label("pöytä") == "pöytä" and gateway.label("x" * 40) == "x" * 40
    assert relay.item_name("Remote  Control") == "remote_control" and relay.plain_name(" pill  box shelf ") == "pill box shelf"
    for bad in ("", " ", "-x", "'x", "a/b", "a\\b", "a.b", "<b>", "a&b", 'a"b', "x" * 41):
        assert gateway.label(bad) == "" and relay.plain_name(bad) == "", bad
    # Picture sizes are read from the first bytes, without opening the picture.
    assert gateway.photo_size(JPEG) == (48, 32) and gateway.photo_size(png(70, 50)) == (70, 50)
    webp = b"RIFF" + bytes(4) + b"WEBP"
    assert gateway.photo_size(webp + b"VP8X" + bytes(8) + (1279).to_bytes(3, "little") + (719).to_bytes(3, "little")) == (1280, 720)
    assert gateway.photo_size(webp + b"VP8 " + bytes(7) + b"\x9d\x01\x2a" + struct.pack("<HH", 640, 480)) == (640, 480)
    assert gateway.photo_size(webp + b"VP8L" + bytes(4) + b"\x2f" + struct.pack("<I", 99 | (49 << 14))) == (100, 50)
    for junk in (b"", b"\xff", b"\xff\xd8", b"\xff\xd8\xff", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xc0\x00", webp, webp + b"VP8L", b"\x89PNG\r\n\x1a\n", gif):
        assert gateway.photo_size(junk) is None, junk

    # ---------------------------------------------------------------- 4. what the room map knows
    code, fix = post("/locate", raw=JPEG, zone="kitchen")
    assert code == 200 and fix["state"] == "found" and [w["name"] for w in fix["waypoints"]] == ["pill box", "front door"], fix
    code, body = call("/where?zone=kitchen")
    seen = body["seen"]
    assert code == 200 and len(seen) == 1 and seen[0]["name"] == "keys" and seen[0]["near"] == "pill box", seen
    assert seen[0]["doubt"] is False and len(seen[0]["time"]) == 5 and seen[0]["age_s"] < 60, seen
    code, body = post("/forget", zone="kitchen", name="keys")
    assert code == 200 and body["seen"] == [] and call("/where?zone=kitchen")[1]["seen"] == [], body

    # ---------------------------------------------------------------- 5. a new baseline
    # The worker throws away what was picked in the old pictures: places, watched spots and the scale.
    post("/scale", zone="kitchen", a="pill box", b="front door", metres="3")
    post("/watch", zone="kitchen", **good)
    assert all((kitchen / name).exists() for name in ("waypoints.json", "watch.json", "scale.json"))
    try:
        import worker
    except ImportError as err:
        worker = None
        print(f"  (the worker's part is not tested here: {err})")
    if worker:
        import cv2
        import numpy as np
        job = outer / "project" / "queue" / "job1"
        job.mkdir(parents=True)
        rng = np.random.default_rng(1)
        with zipfile.ZipFile(job / "clip.zip", "w") as bundle:
            for i in range(8):
                bundle.writestr(f"{i:03d}.jpg", cv2.imencode(".jpg", rng.integers(0, 255, (120, 160, 3), np.uint8))[1].tobytes())
        worker.BASELINES = root / "baselines"
        result = worker.set_baseline(job, {"zone": "kitchen", "file": "clip.zip"})
        assert result["frames"] >= 6 and len(list(kitchen.glob("*.jpg"))) == result["frames"], result
        assert not any((kitchen / name).exists() for name in ("waypoints.json", "watch.json", "scale.json"))
        code, body = call("/waypoints?zone=kitchen")
        assert body["waypoints"] == [] and body["scale"] is None and call("/watch?zone=kitchen")[1]["watch"] == []

    server.shutdown()
    shutil.rmtree(outer, ignore_errors=True)
    print(f"setup flow: all checks passed ({'with' if have_opencv else 'without'} OpenCV)")


if __name__ == "__main__":
    main()
