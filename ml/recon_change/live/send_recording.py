#!/usr/bin/env python3
"""Play a video into a running relay as if the app were recording: chunks of frames to POST /recording.

    RELAY_KEY=... python live/send_recording.py VIDEO --zone hall-rec --session test1 [--fps 2] [--chunk 30] [--every 0]

Frames are taken at --fps, upright, long side 960, named NNNNNN_<epoch ms>.jpg like the app's, and sent in zips of
--chunk frames, --every seconds apart; the last chunk carries final=1. One chunk is sent twice, as a retry would, to show
that it counts once. Prints the relay's reply to each chunk, then the session's state (GET /recording).
"""
import argparse
import io
import json
import os
import time
import urllib.request
import zipfile

import cv2


def post(relay, key, path, body):
    request = urllib.request.Request(relay + path, body, {"X-Key": key, "Content-Type": "application/zip"}, method="POST")
    with urllib.request.urlopen(request, timeout=300) as reply:
        return json.loads(reply.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--relay", default="http://127.0.0.1:8090")
    ap.add_argument("--zone", default="hall-rec")
    ap.add_argument("--session", default=time.strftime("s%H%M%S"))
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--chunk", type=int, default=30)
    ap.add_argument("--every", type=float, default=0.0)
    args = ap.parse_args()
    key = os.environ["RELAY_KEY"]
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step, n, frames, start = max(1, round(fps / args.fps)), 0, [], int(time.time() * 1000)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if n % step == 0:
            scale = 960 / max(frame.shape[:2])
            if scale < 1:
                frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            frames.append((f"{len(frames):06d}_{start + int(1000 * n / fps)}.jpg", cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()))
        n += 1
    chunks = [frames[i:i + args.chunk] for i in range(0, len(frames), args.chunk)]
    print(f"{len(frames)} frames from {n} in {len(chunks)} chunks to zone {args.zone}, session {args.session}")
    order = list(range(len(chunks)))
    if len(chunks) > 2:
        order.insert(2, 1)  # chunk 1 again, as after a lost reply
    for seq in order:
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w", zipfile.ZIP_STORED) as bundle:
            for name, data in chunks[seq]:
                bundle.writestr(name, data)
        final = int(seq == len(chunks) - 1)
        t0 = time.time()
        reply = post(args.relay, key, f"/recording?zone={args.zone}&session={args.session}&seq={seq}&final={final}", body.getvalue())
        print(f"seq {seq} final={final}: {time.time() - t0:.2f} s {json.dumps(reply)}", flush=True)
        time.sleep(args.every)
    request = urllib.request.Request(f"{args.relay}/recording?zone={args.zone}", headers={"X-Key": key})
    print(json.dumps(json.loads(urllib.request.urlopen(request, timeout=30).read()), indent=1))


if __name__ == "__main__":
    main()
