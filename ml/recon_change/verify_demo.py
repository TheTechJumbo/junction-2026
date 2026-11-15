#!/usr/bin/env python3
"""Check the whole vision stage end to end, with a synthetic room: no camera, no glasses, no person.

    bash ml/recon_change/verify_demo.sh [options]       reads RELAY_KEY from .env.demo; start the servers first
    RELAY_KEY=... python ml/recon_change/verify_demo.py [--out FOLDER] [--zone verify-synthetic] [--relay URL] [--api URL]

Against the running relay (run_demo.sh, best with --local) and event API, it:
  1. checks that the event API answers /health and that the relay and its vision worker are up
  2. draws a synthetic room (synthetic_room.py) and records its baseline
  3. names two places ("pill box", "front door"), their true distance and a watched spot, as a carer would
  4. sends photos of a short walk for position fixes and compares them with where the camera really was
  5. asks "Where is the pill box?" and "Did I take my pills?" through the relay
  6. sends the same view twice with an object moved in between, then once more: the cheap gate
  7. runs a change check with the object moved
  8. builds the room map (one splat of the baseline) and plays a turning camera through the instant fixes
  9. reports what the item finder did, or why it is off
 10. fetches what the dashboard shows: the camera frame, the worker's log, the baseline pictures, the 3D files
and prints PASS, FAIL or SKIPPED for each, with the seconds and the peak GPU memory of each model job.
Exit code 1 if anything FAILED.

Safe to run again: it only touches its own zone (default verify-synthetic: a new baseline replaces the old
one there), never the home's rooms, and writes its pictures into --out (default: beacon-verify in the system's
temporary folder), never into the repo. The key is read from RELAY_KEY and never printed.
"""
import argparse
import io
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:  # clock times as the event API says them (Europe/Helsinki unless BEACON_TZ says otherwise)
    from zoneinfo import ZoneInfo
    HOME_TZ = ZoneInfo(os.environ.get("BEACON_TZ", "Europe/Helsinki"))
except Exception:  # no time zone data: the laptop's own zone
    HOME_TZ = None

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import synthetic_room as room  # noqa: E402

# How close a found fix must be to the truth to count as right (the synthetic room has an exact answer).
POSITION_M, HEADING_DEG, DISTANCE_SHARE, BEARING_DEG = 0.5, 15.0, 0.25, 20.0


class Relay:
    def __init__(self, url, key, api):
        self.url, self.key, self.api = url.rstrip("/"), key, api.rstrip("/")

    def call(self, path, body=None, timeout=300, raw=False, base=None):
        """(HTTP status, JSON reply or bytes, seconds)."""
        headers = {"X-Key": self.key} if base is None else {}
        if isinstance(body, (dict, list)):
            body, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        request = urllib.request.Request((base or self.url) + path, data=body, headers=headers,
                                         method="POST" if body is not None else "GET")
        t0 = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data, code = response.read(), response.status
        except urllib.error.HTTPError as err:
            data, code = err.read(), err.code
        except OSError as err:
            return 0, {"ok": False, "error": f"cannot connect: {err}"}, time.time() - t0
        took = time.time() - t0
        if raw:
            return code, data, took
        try:
            return code, json.loads(data), took
        except ValueError:
            return code, {"ok": False, "error": data[:200].decode("utf-8", "replace")}, took


def as_zip(files):
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_STORED) as bundle:
        for i, f in enumerate(files):
            bundle.writestr(f"{i:03d}.jpg", Path(f).read_bytes())
    return packed.getvalue()


def angle(a):
    return (a + 180) % 360 - 180


def gb(value):
    return None if value is None else float(value)


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, status, seconds=None, gpu=None, detail=""):
        self.rows.append((name, status, seconds, gpu, detail))
        mark = {"PASS": "PASS   ", "FAIL": "FAIL   ", "SKIPPED": "SKIPPED"}[status]
        sec = f"{seconds:6.1f} s" if seconds is not None else "       -"
        mem = f"{gpu:4.2f} GB" if gpu is not None else "      -"
        print(f"  {mark} {name:22s} {sec}  {mem}  {detail}", flush=True)

    def failed(self):
        return [r for r in self.rows if r[1] == "FAIL"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--relay", default="http://127.0.0.1:8090")
    ap.add_argument("--api", default="http://127.0.0.1:8000", help="the event API (its /health is checked)")
    ap.add_argument("--zone", default="verify-synthetic", help="a zone of its own: its baseline is replaced on every run")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "beacon-verify"), help="where the synthetic pictures go")
    ap.add_argument("--redraw", action="store_true", help="draw the pictures again even if they are there")
    ap.add_argument("--skip-pill-event", action="store_true",
                    help="do not post a pillbox_open event (it makes 'Did I take my pills?' answer yes for the rest of today)")
    args = ap.parse_args()
    key = os.environ.get("RELAY_KEY", "")
    if not key:
        sys.exit("Set RELAY_KEY (verify_demo.sh reads it from .env.demo).")
    if args.zone in ("kitchen", "lounge", "bedroom", "bathroom", "hall"):
        sys.exit(f"Use a zone of its own: '{args.zone}' could be a room of the home, and its baseline would be replaced.")
    relay, report, out = Relay(args.relay, key, args.api), Report(), Path(args.out)
    zone = args.zone
    print(f"Vision stage check: relay {args.relay}, event API {args.api}, zone '{zone}', pictures in {out}\n")
    print(f"  {'result':7s} {'stage':22s} {'time':>8s}  {'GPU peak':>7s}  what happened")

    # ---- 1. servers ------------------------------------------------------------------------------------
    code, health, took = relay.call("/health", base=relay.api, timeout=10)
    api_ok = code == 200 and health.get("status") == "ok"
    code, status, took2 = relay.call("/status", timeout=60)
    gpu_worker = ((status.get("workers") or {}).get("gpu") or {})
    info = gpu_worker.get("info") or {}
    settings = info.get("settings") or {}
    worker_ok = code == 200 and status.get("ok") and gpu_worker.get("running")
    where = status.get("worker_at") or "?"
    detail = (f"event API {'ok' if api_ok else 'NOT answering /health'}; relay ok ({where}); "
              + (f"worker on {info.get('device')} ({'GPU' if info.get('on_gpu') else 'CPU'}), "
                 f"{settings.get('baseline_frames')}+{settings.get('new_frames')} frames, fix with "
                 f"{settings.get('locate_baseline_frames')} baseline photos" if worker_ok else
                 f"worker NOT running: {status.get('error') or gpu_worker}"))
    report.add("servers", "PASS" if api_ok and worker_ok else "FAIL", took + took2, None, detail)
    if not worker_ok:
        return finish(report, args, key)

    # ---- 2. pictures -----------------------------------------------------------------------------------
    t0 = time.time()
    try:
        truth = json.loads((out / "truth.json").read_text())
        if args.redraw or truth.get("version") != room.VERSION:
            raise ValueError("draw again")
        drew = "already drawn"
    except (OSError, ValueError):
        truth = room.write_all(out)
        drew = "drawn now"
    files = {sub: sorted((out / sub).glob("*.jpg")) for sub in ("baseline", "walk", "check", "still", "turn")}
    report.add("synthetic room", "PASS", time.time() - t0, None,
               f"{drew}: {len(files['baseline'])} baseline, {len(files['walk'])} walk, {len(files['check'])} check, "
               f"{len(files['still'])} still pictures at {truth['size'][0]}x{truth['size'][1]}")

    # ---- 3. baseline -----------------------------------------------------------------------------------
    code, body, took = relay.call(f"/baseline?zone={zone}", as_zip(files["baseline"]))
    kept_n = body.get("frames") if code == 200 else None
    report.add("baseline", "PASS" if kept_n and kept_n >= 6 else "FAIL", took, None,
               f"{len(files['baseline'])} pictures sent, {kept_n} kept" if kept_n else f"HTTP {code}: {body.get('error')}")
    if not kept_n:
        return finish(report, args, key)
    n = len(files["baseline"])
    kept = sorted({int(i) for i in np.linspace(0, n - 1, min(kept_n, n)).round()})  # as recon_change.sample_frames
    setup = room.places_for(kept)

    # ---- 4. places, scale, watched spot ------------------------------------------------------------------
    t0 = time.time()
    problems = []
    for name, place in setup["places"].items():
        code, body, _ = relay.call(f"/waypoint?zone={zone}&name={name.replace(' ', '+')}&frame={place['frame']}"
                                   f"&u={place['u']}&v={place['v']}", b"")
        if code != 200:
            problems.append(f"{name}: {body.get('error')}")
    code, body, _ = relay.call(f"/scale?zone={zone}&a=pill+box&b=front+door&metres={setup['distance_m']}", b"")
    if code != 200:
        problems.append(f"scale: {body.get('error')}")
    if setup["watch"]:
        w = setup["watch"]
        u0, v0, u1, v1 = w["box"]
        code, body, _ = relay.call(f"/watch?zone={zone}&name=table+corner&frame={w['frame']}&u0={u0}&v0={v0}&u1={u1}&v1={v1}", b"")
        if code != 200:
            problems.append(f"watch: {body.get('error')}")
    code, listed, _ = relay.call(f"/waypoints?zone={zone}")
    names = sorted(p["name"] for p in listed.get("waypoints") or [])
    code_pic, picture, _ = relay.call(f"/baseline/{zone}/{setup['places']['pill box']['frame']}.jpg", raw=True)
    if names != sorted(setup["places"]) or not listed.get("scale"):
        problems.append(f"the relay lists {names}, scale {listed.get('scale')}")
    if code_pic != 200 or picture[:2] != b"\xff\xd8":
        problems.append("the baseline picture of the pill box could not be fetched")
    report.add("places", "FAIL" if problems else "PASS", time.time() - t0, None,
               "; ".join(problems) if problems else
               f"pill box, front door named; {setup['distance_m']} m apart; watched spot on the moved object")

    # ---- 4b. room map: the baseline in one pass, as one splat in map axes (POST /map) ------------------------
    code, built, took = relay.call(f"/map?zone={zone}", b"", timeout=300)
    if code == 200 and built.get("ok"):
        cams = {c["frame"]: c["heading_deg"] for c in built.get("cameras") or []}
        yaw0 = truth["baseline"][kept[0]]["yaw_deg"]
        errs = [abs(angle(cams[k] - (truth["baseline"][i]["yaw_deg"] - yaw0))) for k, i in enumerate(kept) if k in cams]
        med = statistics.median(errs) if errs else 180
        code_s, data, _ = relay.call(built["splat"], raw=True) if built.get("splat") else (0, b"", 0)
        ok = med <= 25 and code_s == 200 and len(data) // 32 == built.get("gaussians") and built.get("gaussians", 0) > 50_000
        report.add("room map", "PASS" if ok else "FAIL", took, gb(built.get("gpu_peak_gb")),
                   f"{built.get('frames')} pictures, {built.get('gaussians')} Gaussians, metres from {built.get('metres_from')}; "
                   f"camera headings off the truth by {med:.0f} deg (median, limit 25); field of view {built.get('fov_deg')} deg")
    else:
        report.add("room map", "FAIL", took, None, f"HTTP {code}: {built.get('error')}")

    # ---- 5. position fixes -----------------------------------------------------------------------------
    fixes, errors, times, peaks = [], [], [], []
    for i, f in enumerate(files["walk"]):
        code, fix, took = relay.call(f"/locate?zone={zone}&fresh=1", f.read_bytes())
        if code != 200:
            fixes.append({"state": f"HTTP {code}: {fix.get('error')}"})
            continue
        fixes.append(fix)
        times.append(took)
        if fix.get("gpu_peak_gb") is not None:
            peaks.append(fix["gpu_peak_gb"])
        if fix.get("state") != "found":
            continue
        t = truth["walk"][i]
        origin = truth["baseline"][kept[0]]  # the map starts at the first kept baseline camera and faces where it faced
        yaw0 = math.radians(origin["yaw_deg"])
        dx, dy = t["position"][0] - origin["position"][0], t["position"][1] - origin["position"][1]
        true_east, true_north = dx * math.cos(yaw0) - dy * math.sin(yaw0), dx * math.sin(yaw0) + dy * math.cos(yaw0)
        e = {"position_m": math.hypot(fix["position"][0] - true_east, fix["position"][1] - true_north),
             "heading_deg": abs(angle(fix["heading_deg"] - (t["yaw_deg"] - origin["yaw_deg"])))}
        for w in fix.get("waypoints") or []:
            dist, bearing = room.truth_of_fix(t, setup["places"][w["name"]]["point"])
            e.setdefault("distance_share", []).append(abs(w["distance_m"] - dist) / dist)
            e.setdefault("bearing_deg", []).append(abs(angle(w["bearing_deg"] - bearing)))
        errors.append(e)
    states = [fx.get("state") for fx in fixes]
    found = states.count("found")
    if errors:
        med = {k: statistics.median([x for e in errors for x in (e[k] if isinstance(e[k], list) else [e[k]])]) for k in errors[0]}
        right = (med["position_m"] <= POSITION_M and med["heading_deg"] <= HEADING_DEG
                 and med.get("distance_share", 0) <= DISTANCE_SHARE and med.get("bearing_deg", 0) <= BEARING_DEG)
    else:
        med, right = {}, False
    last = next((fx for fx in reversed(fixes) if fx.get("ok")), {})
    report.add("position fixes", "PASS" if found >= len(files["walk"]) // 2 else "FAIL",
               statistics.median(times) if times else None, max(peaks) if peaks else None,
               f"{found} of {len(fixes)} found ({', '.join(str(s) for s in states)}); median {statistics.median(times):.1f} s per photo"
               if times else f"no fix: {states}")
    report.add("fix matches the truth", "PASS" if right else "FAIL", None, None,
               (f"median error: position {med['position_m']:.2f} m, heading {med['heading_deg']:.0f} deg, "
                f"distance to places {100 * med.get('distance_share', 0):.0f}%, bearing {med.get('bearing_deg', 0):.0f} deg "
                f"(limits {POSITION_M} m, {HEADING_DEG:.0f} deg, {100 * DISTANCE_SHARE:.0f}%, {BEARING_DEG:.0f} deg)") if med
               else "no found fix to compare")
    # The arrow and the 3D map (wearer and places marked) from the walk photo that matched the map best, asked again
    # with &view=1: the map is what the dashboard shows, and the arrow is what the glasses display would draw.
    found_at = [i for i, fx in enumerate(fixes) if fx.get("state") == "found"]
    best = max(found_at, key=lambda i: fixes[i].get("confidence") or 0) if found_at else len(files["walk"]) - 1
    true_dist, true_bearing = room.truth_of_fix(truth["walk"][best], setup["places"]["pill box"]["point"])
    code, last, took = relay.call(f"/locate?zone={zone}&fresh=1&view=1", files["walk"][best].read_bytes())
    last = last if code == 200 else {}
    retried = ""
    if last.get("state") != "found":  # with 5 baseline photos the same photo can come back lost (seen 1 in 12): once more
        retried = f" (try 1 came back {last.get('state') or f'HTTP {code}'}, this is try 2)"
        code, last, took = relay.call(f"/locate?zone={zone}&fresh=1&view=1", files["walk"][best].read_bytes())
        last = last if code == 200 else {}
    arrow = next((w for w in (last.get("waypoints") or []) if w["name"] == "pill box"), None)
    splat_ok = False
    if last.get("splat"):
        code, data, _ = relay.call(last["splat"], raw=True)
        splat_ok = code == 200 and len(data) % 32 == 0 and len(data) // 32 == last.get("gaussians")
    arrow_ok = (last.get("state") == "found" and arrow is not None and abs(arrow["distance_m"] - true_dist) <= DISTANCE_SHARE * true_dist
                and abs(angle(arrow["bearing_deg"] - true_bearing)) <= BEARING_DEG)
    report.add("arrow and 3D map", "PASS" if arrow_ok and splat_ok else "FAIL", took, last.get("gpu_peak_gb"),
               f"walk photo {best} ({last.get('state')}{retried}): \"{last.get('headline')}\"; "
               + (f"arrow to pill box {arrow['distance_m']} m at {arrow['bearing_deg']:+d} deg (true {true_dist:.1f} m at {true_bearing:+.0f} deg); "
                  if arrow else "no arrow to the pill box; ")
               + (f"map.splat {last.get('gaussians')} Gaussians" if splat_ok else f"3D map missing ({last.get('splat')})"))

    # ---- 6. questions ------------------------------------------------------------------------------------
    if last.get("state") != "found" or time.time() - last.get("at", 0) > 50:  # the relay answers from a fix under 60 s old
        relay.call(f"/locate?zone={zone}&fresh=1", files["walk"][best].read_bytes())
    code, answer, took = relay.call(f"/ask?zone={zone}", {"question": "Where is the pill box?"}, timeout=30)
    text = answer.get("answer") or answer.get("error") or ""
    said = re.search(r"about ([\d.]+) metres", text)
    said_ok = bool(said) and abs(float(said[1]) - true_dist) <= max(DISTANCE_SHARE * true_dist, 0.15)  # it rounds to 0.1 m
    report.add("ask: where is the pill box", "PASS" if code == 200 and "pill box" in text.lower() and said_ok else "FAIL",
               took, None, f"\"{text[:110]}\" (answered by {answer.get('answered_by', 'the event API')}; true {true_dist:.1f} m)")
    # A pill box opening, as the UNO Q board (or its simulator) reports it. The event API's pill answer only counts
    # sensor events of the pill box in the kitchen, so this one is there; its id is new on every run.
    api_token = os.environ.get("API_TOKEN", "")
    if args.skip_pill_event or not api_token:
        report.add("pill box event", "SKIPPED", None, None,
                   "--skip-pill-event given" if args.skip_pill_event else "API_TOKEN not set (verify_demo.sh reads it from .env.demo)")
        opened = None
    else:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        event = {"id": f"verify-demo-pillbox-{int(now.timestamp())}", "type": "pillbox_open", "source": "sensor",
                 "ts": now.isoformat(), "zone": "kitchen", "object": "pillbox", "evidence_url": None, "confidence": 1.0}
        request = urllib.request.Request(args.api.rstrip("/") + "/events", json.dumps(event).encode(),
                                         {"Content-Type": "application/json", "Authorization": f"Bearer {api_token}"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                code, body = response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as err:
            code, body = err.code, {"error": err.read()[:200].decode("utf-8", "replace")}
        except OSError as err:
            code, body = 0, {"error": str(err)}
        opened = now.astimezone(HOME_TZ).strftime("%H:%M") if code == 200 else None
        report.add("pill box event", "PASS" if code == 200 else "FAIL", time.time() - t0, None,
                   f"pillbox_open from 'sensor' in the kitchen at {opened} posted to the event API ({body.get('durability', 'stored')})"
                   if code == 200 else f"HTTP {code}: {body.get('error') or body}")
    code, answer, took = relay.call(f"/ask?zone={zone}", {"question": "Did I take my pills?"}, timeout=30)
    text = answer.get("answer") or answer.get("error") or ""
    if opened:  # the answer must name the opening just sent (or a later one) and not claim more than an opening
        right = code == 200 and "opened at" in text and text.split("opened at", 1)[1].strip()[:5] >= opened
    else:
        right = code == 200 and bool(text)
    report.add("ask: did I take my pills", "PASS" if right else "FAIL", took, None,
               f"\"{text[:110]}\"" + (f" (expected the opening at {opened})" if opened and not right else ""))

    # ---- 7. the cheap gate -----------------------------------------------------------------------------
    # The camera stands still: the first photo is the view the model saw last (no &fresh, so the gate keeps it),
    # the second is the same view with the red box taken off the table, the third is the second again.
    still = files["still"]
    steps = []
    for f in (still[0], still[1], still[1]):  # &quick=0: the cheap gate, not the instant fix (relay --quick)
        code, fix, took = relay.call(f"/locate?zone={zone}&quick=0", f.read_bytes())
        steps.append((code, fix, took))
    kinds = [((s[1].get("view") or {}).get("kind"), bool(s[1].get("reused")), s[2]) for s in steps]
    moved_pics = ((steps[1][1].get("view") or {}).get("images") or {})
    code, changes, _ = relay.call(f"/changes?zone={zone}")
    listed = [c for c in changes.get("changes") or [] if c.get("job") == steps[1][1].get("job")]
    pics_ok = all(relay.call(u, raw=True)[0] == 200 for u in moved_pics.values()) and len(moved_pics) == 2
    gate_ok = kinds[1][0] == "local" and pics_ok and listed and kinds[2][1] and kinds[2][2] < 1.0
    report.add("cheap gate", "PASS" if gate_ok else "FAIL", kinds[2][2], steps[1][1].get("gpu_peak_gb"),
               f"object moved in a still view -> '{kinds[1][0]}' (box {(steps[1][1].get('view') or {}).get('box')}), "
               f"before/now crops {'ok' if pics_ok else 'MISSING'}, on /changes {'yes' if listed else 'NO'}; "
               f"same view again -> {'reused' if kinds[2][1] else 'model asked'} in {kinds[2][2]:.2f} s")

    # ---- 8. change check ---------------------------------------------------------------------------------
    code, check, took = relay.call(f"/check?zone={zone}", as_zip(files["check"]))
    if code == 200:
        rep = check.get("report") or {}
        pics = {k: relay.call(check["images"][k], raw=True)[0] == 200 for k in ("before", "now", "changes") if k in check.get("images", {})}
        splat_ok = bool(check.get("splat")) and relay.call(check["splat"], raw=True)[0] == 200
        watched = {w["name"]: w.get("state") for w in check.get("watch") or []}
        ran = all(pics.values()) and len(pics) == 3 and splat_ok
        report.add("change check", "PASS" if ran else "FAIL", took, gb(rep.get("gpu_peak_gb")),
                   f"\"{check.get('headline')}\"; verdict {check.get('verdict', '')[:40]}; before/now/all views "
                   f"{'ok' if ran else pics}; scene.splat {check.get('gaussians')} Gaussians; "
                   f"model {check.get('seconds', {}).get('reconstruct')} s")
        spotted = len(rep.get("appeared") or []) + len(rep.get("gone") or [])
        report.add("change check found it", "PASS" if spotted or "changed" in watched.values() else "FAIL", None, None,
                   f"room-wide: {len(rep.get('appeared') or [])} appeared, {len(rep.get('gone') or [])} gone; "
                   f"watched spot over the box: {watched or 'none'}"
                   + ("; found by the watched spot only" if not spotted and "changed" in watched.values() else ""))
    else:
        report.add("change check", "FAIL", took, None, f"HTTP {code}: {check.get('error')}")

    # ---- 8b. instant fixes (relay --quick): a camera turning 2 degrees a frame, sent back to back ------------
    turn_truth = room.turn_poses()
    yaw0 = truth["baseline"][kept[0]]["yaw_deg"]
    kinds, times, errs = [], [], []
    for i, f in enumerate(files["turn"]):
        fresh = "&fresh=1" if i == 0 else ""  # the first one by the model: the instant ones carry on from it
        code, fix, took = relay.call(f"/locate?zone={zone}{fresh}", f.read_bytes())
        q = fix.get("quick") or {}
        kind = "skipped" if q.get("skipped") else "instant" if q else "model"
        kinds.append(kind)
        if kind == "instant":
            times.append(took)
            errs.append(abs(angle(fix.get("heading_deg", 0) - (turn_truth[i][1] - yaw0))))
    n_instant = kinds.count("instant")
    if not n_instant and "model" in kinds and len(set(kinds)) == 1:
        report.add("instant fixes", "SKIPPED", None, None, "the relay answers every photo with the model (start it with --quick)")
    else:
        ok = n_instant >= 0.7 * len(kinds) and times and statistics.median(times) < 0.5 and statistics.median(errs) <= 8
        report.add("instant fixes", "PASS" if ok else "FAIL", statistics.median(times) if times else None, None,
                   f"{n_instant} of {len(kinds)} frames answered by cosine similarity, {kinds.count('model')} by the model, "
                   f"{kinds.count('skipped')} skipped; " + (f"median {1000 * statistics.median(times):.0f} ms; heading off the truth by "
                   f"{statistics.median(errs):.1f} deg median, {max(errs):.1f} worst (limits 500 ms, 8 deg)" if times else "none instant"))

    # ---- 9. item finder ----------------------------------------------------------------------------------
    items = settings.get("items")
    if not items:
        report.add("item finder", "SKIPPED", None, None,
                   "off in this worker (RECON_ITEMS is empty). It needs transformers and the OWLv2 weights (DEMO.md); on the "
                   "RTX 2060 laptop it took about 70 s per photo on the CPU and sat at the card's limit on the GPU")
    elif items.get("error"):
        report.add("item finder", "FAIL", None, None, f"did not load: {items['error'][:150]}")
    else:
        frame = last.get("items_frame") or {}
        looked = last.get("items_looked_for") or []
        report.add("item finder", "PASS" if frame.get("passes") and looked and not last.get("items_note") else "FAIL",
                   (last.get("seconds") or {}).get("items"), None,
                   f"looked for {', '.join(looked)} on {items.get('device')} (batch {items.get('batch', '?')}) in "
                   f"{frame.get('passes')} passes; found {[i['name'] for i in last.get('items') or []] or 'nothing (none in this room)'}"
                   + (f"; note: {last['items_note'][:100]}" if last.get("items_note") else ""))

    # ---- 10. what the dashboard shows ----------------------------------------------------------------------
    t0 = time.time()
    problems = []
    code, page, _ = relay.call("/dashboard", raw=True)
    if code != 200 or b'id="live"' not in page:
        problems.append("the dashboard has no live section")
    code, frame, _ = relay.call(f"/frame?zone={zone}", raw=True)
    if code != 200 or frame[:2] != b"\xff\xd8":
        problems.append("no camera frame on /frame")
    code, log, _ = relay.call("/worker_log?lines=200")
    lines = log.get("lines") or []
    loaded = next((ln for ln in lines if ln.startswith("VGGT loaded")), None)
    if code != 200 or not any("peak GPU memory" in ln for ln in lines):
        problems.append(f"no worker log with GPU memory lines ({log.get('error')})")
    code, history, _ = relay.call("/history")
    if code != 200 or not history.get("checks") or history["checks"][0].get("zone") != zone:
        problems.append("the change check is not on /history")
    code, latest, _ = relay.call(f"/where?zone={zone}")
    if code != 200:
        problems.append("no fix on /where")
    report.add("dashboard data", "FAIL" if problems else "PASS", time.time() - t0, None,
               "; ".join(problems) if problems else
               f"camera frame, fix, check, gate changes, baseline pictures and worker log all served"
               + (f"; log: \"{loaded}\"" if loaded else ""))
    return finish(report, args, key)


def finish(report, args, key):
    failed = report.failed()
    counts = {s: sum(1 for r in report.rows if r[1] == s) for s in ("PASS", "FAIL", "SKIPPED")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIPPED']} skipped.")
    print(f"See it: {args.relay}/dashboard?k=<RELAY_KEY>  (the key is RELAY_KEY in .env.demo)")
    print(f"        {args.relay}/places?k=<RELAY_KEY>, then pick '{args.zone}'; the pictures sent are in {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
