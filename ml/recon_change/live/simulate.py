#!/usr/bin/env python3
"""Simulate the glasses: play recorded photos through a running relay as if someone were wearing them.

    python live/simulate.py --relay http://127.0.0.1:8090 --key KEY --zone lounge \\
        --baseline room_photos.zip --photos walk_photos.zip --every 3 --repeat 2

--baseline  a zip of photos 000.jpg, 001.jpg, ... (or a folder of pictures, or a video) of the room as it
            should be; left out, the zone's existing baseline is used
--photos    a zip or folder of the pictures the wearer "sees", played in name order
--every     seconds between pictures, as if the wearer moved on
--repeat    send each picture this many times in a row, as if the wearer stood still for a moment: the
            relay's cheap gate should answer the repeats without asking the model

Open /hud on the same relay in a browser to watch the display follow along. Prints, for every picture, what
the wearer would see and what it cost. Nothing here needs the cluster: it works against any relay, also one
started with --local.
"""
import argparse
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PICTURES = (".jpg", ".jpeg", ".png", ".webp")


def pictures(source):
    """[(name, bytes)] from a zip or a folder, in name order."""
    source = Path(source)
    if source.is_dir():
        return [(p.name, p.read_bytes()) for p in sorted(source.iterdir()) if p.suffix.lower() in PICTURES]
    with zipfile.ZipFile(source) as bundle:
        return [(name, bundle.read(name)) for name in sorted(bundle.namelist()) if name.lower().endswith(PICTURES)]


def as_zip(source):
    """A baseline as the bytes the relay takes: a video as it is, pictures as a zip of 000.jpg, 001.jpg, ..."""
    source = Path(source)
    if source.is_file() and source.suffix.lower() != ".zip":
        return source.read_bytes()
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_STORED) as bundle:
        for i, (_, data) in enumerate(pictures(source)):
            bundle.writestr(f"{i:03d}.jpg", data)
    return packed.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--relay", default="http://127.0.0.1:8090")
    ap.add_argument("--key", required=True, help="the relay's access key")
    ap.add_argument("--zone", required=True)
    ap.add_argument("--baseline")
    ap.add_argument("--photos", required=True)
    ap.add_argument("--every", type=float, default=3.0)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--to", default="", help="the place to walk to; default: the first named place")
    args = ap.parse_args()

    def call(path, raw=None, timeout=900):
        request = urllib.request.Request(args.relay + path, data=raw, headers={"X-Key": args.key}, method="POST" if raw is not None else "GET")
        t0 = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read()), time.time() - t0
        except urllib.error.HTTPError as err:
            try:
                return err.code, json.loads(err.read()), time.time() - t0
            except ValueError:
                return err.code, {}, time.time() - t0

    if args.baseline:
        code, body, took = call(f"/baseline?zone={args.zone}", as_zip(args.baseline))
        print(f"baseline: {code} in {took:.1f} s -> {body.get('frames', body.get('error'))} pictures kept")
        if code != 200:
            raise SystemExit(1)

    asked = reused = 0
    waited = []
    for name, photo in pictures(args.photos):
        for again in range(args.repeat):
            code, fix, took = call(f"/locate?zone={args.zone}", photo)
            if code != 200:
                print(f"{name}: {code} {fix.get('error')}")
                continue
            waited.append(took)
            reused += bool(fix.get("reused"))
            asked += not fix.get("reused")
            stable, view = fix.get("stable") or {}, fix.get("view") or {}
            places = (stable.get("waypoints") if stable.get("state") == "steady" else None) or fix.get("waypoints") or []
            to = next((w for w in places if w["name"] == args.to), places[0] if places else None)
            arrow = "no arrow (look around slowly)" if fix["state"] == "lost" or stable.get("state") not in (None, "steady") else (
                f"arrow to {to['name']}: {to['distance_m']} m, {to['bearing_deg']:+d} deg" if to else "no places named")
            seen = ", ".join(i["name"] for i in fix.get("items") or [])
            print(f"{name}{' (again)' if again else '':8s} {took:6.2f} s  {'model asked' if not fix.get('reused') else 'reused     '}"
                  f"  view {view.get('kind', '-'):6s} {fix['state']:6s} {str(stable.get('confidence_pct', fix.get('confidence_pct'))):>3s}%  {arrow}"
                  f"{'  | sees: ' + seen if seen else ''}{'  | SOMETHING MOVED at ' + str(view.get('box')) if view.get('kind') == 'local' else ''}")
            time.sleep(args.every if again == args.repeat - 1 else min(1.0, args.every))
    if waited:
        waited.sort()
        print(f"\n{len(waited)} pictures: the model was asked {asked} times, {reused} answers were reused; "
              f"waiting per picture: median {waited[len(waited) // 2]:.2f} s, longest {waited[-1]:.1f} s")


if __name__ == "__main__":
    main()
