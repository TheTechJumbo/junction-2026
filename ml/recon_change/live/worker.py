#!/usr/bin/env python3
"""Vision worker. Runs inside a Slurm GPU job on Iridis and keeps VGGT loaded.

GPU nodes cannot be reached from outside, so requests arrive as folders on shared scratch:
the gateway (login node) writes queue/<job>/ and then a READY marker; this worker renames it
to RUNNING, does the work, writes result.json, and renames the marker to DONE or FAILED.

  baseline  keep up to 24 sharp frames of a clip as the zone's "how it should look"
  check     reconstruct the zone's baseline frames together with 12 frames of a new clip,
            and report what appeared and what is gone
  locate    reconstruct the zone's baseline frames together with up to 3 frames of what the
            wearer sees now, and report where they stand, which way they face, and the
            distance and direction to each named place (see locate.py). Metres come from one
            known distance between two places when the zone has a scale.json, else from an
            assumed viewing distance (RECON_TYPICAL_M). With RECON_ITEMS not
            empty it also reports small things seen in the newest frame ("keys", "glasses",
            ...) and where they are on the same map (see items.py)

A clip is either a video file or a zip of frames (000.jpg, 001.jpg, ...) that the relay
already picked, which is far smaller to send than a phone video.

Stop it cleanly with `touch STOP` in the folder above this one, or `scancel`.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import items  # noqa: E402
import locate  # noqa: E402
import recon_change as rc  # noqa: E402
import splat  # noqa: E402
import watch  # noqa: E402

# Settings come from the environment, so this one script is both the GPU worker and the CPU worker.
DEVICE = os.environ.get("RECON_DEVICE") or None  # "cuda", "cpu", or unset to use a GPU when there is one
N_BASELINE, N_AFTER = (int(n) for n in os.environ.get("RECON_FRAMES", "24,12").split(","))
# Baseline pictures kept when a baseline is recorded (default: as many as a check uses). A laptop keeps more than
# its check can hold (run_local.sh: 16), so that the room map below has enough overlap between neighbouring pictures.
N_KEEP = int(os.environ.get("RECON_BASELINE_KEEP") or N_BASELINE)
# The room map (op "map"): the zone's baseline pictures in one pass, as one Gaussian splat in the map's own axes
# (east, north, up, in metres), built once per baseline. The live 3D page shows it and moves the wearer in it.
MAP_FRAMES = int(os.environ.get("RECON_MAP_FRAMES", "16"))
MAP_GAUSSIANS = 800_000
MAP_CONF_PCT = 35.0  # the least confident share of pixels left out of the map (locate itself uses 25)
# Has the room's map seen this view? Every picture of the zone (baseline, and views added since) is kept as its DINOv2
# patch tokens (rc.patch_tokens), centred on their own mean and normalised. A patch of a new photo is "mapped" when its
# best cosine similarity to any patch of those pictures reaches PATCH_SIM; the mapped share is the share of such patches.
# RECON_PICK=similar: a fix uses the first baseline picture (the map's origin) and the pictures with the largest mapped
# share of this view, instead of pictures spread evenly over the clip. RECON_NEW_VIEW: a found or unsure fix whose
# mapped share is below this is a part of the room not sampled yet, and the photo is added to the zone's pictures
# (0 = never). Both off by default (the cluster's behaviour); run_local.sh turns them on with measured values.
PATCH_SIM = float(os.environ.get("RECON_PATCH_SIM", "0.6"))
PICK = os.environ.get("RECON_PICK", "spread")
NEW_VIEW = float(os.environ.get("RECON_NEW_VIEW", "0"))
MAX_PICTURES = 120  # the most pictures a zone grows to (the hall: 74 from its baseline and the app's photos)
TOKENS = {}  # {zone: {"names": [file names], "keys": (N, P, C) centred unit tokens, "centre": (C,)}}


def zone_tokens(model, zone, files):
    """The patch tokens of the zone's pictures (see PATCH_SIM), worked out again only when the pictures changed."""
    names = [f.name for f in files]
    got = TOKENS.get(zone)
    if got and got["names"] == names:
        return got
    import torch
    tokens = torch.cat([rc.patch_tokens(model, [cv2.imread(str(f)) for f in files[i:i + 8]], LONG_SIDE)
                        for i in range(0, len(files), 8)]).float()
    centre = tokens.reshape(-1, tokens.shape[-1]).mean(0)
    keys = torch.nn.functional.normalize(tokens - centre, dim=-1)
    TOKENS[zone] = got = {"names": names, "keys": keys.half() if keys.is_cuda else keys, "centre": centre}
    return got


def mapped_share(model, zone, files, frame):
    """How much of `frame` (a picture, already cropped like the baseline) the zone's pictures show. Returns
    (the mapped share of its patches, the share per picture (N,), the median best similarity of its patches)."""
    import torch
    got = zone_tokens(model, zone, files)
    keys = got["keys"]
    new = torch.nn.functional.normalize(rc.patch_tokens(model, [frame], LONG_SIDE)[0].float() - got["centre"], dim=-1)
    best = torch.einsum("pc,nqc->pnq", new.to(keys.dtype), keys).amax(-1).float()  # (patches, pictures): best per picture
    share = (best >= PATCH_SIM).float().mean(0)
    overall = best.amax(1)
    return float((overall >= PATCH_SIM).float().mean()), share.cpu().numpy(), float(overall.median())
LONG_SIDE = int(os.environ.get("RECON_LONG_SIDE", "518"))  # model input size: smaller is faster and coarser
N_LOCATE = int(os.environ.get("RECON_LOCATE_FRAMES", "3"))  # frames of the wearer's view used for one position fix
# Baseline photos used for a position fix. With the appearance check, 12 reject every wrong room on
# the PASLCD benchmark and halve the time of 24 (eval/LOCATE_LOOKALIKE_RESULTS.md).
N_LOCATE_BASE = int(os.environ.get("RECON_LOCATE_BASELINE", min(N_BASELINE, 12)))
# Metres of a position fix. A zone with baselines/<zone>/scale.json, {"a": "pill box", "b": "front door",
# "metres": 2.4}, is scaled so that this one distance between two of its named places comes out right
# (within 15% for every fix of the walking test, eval/LOCATE_WALK_RESULTS.md). Without the file the
# baseline's median viewing distance is assumed to be this many metres. The default, 1.5, is fitted to
# three desk-like rooms seen from 1 to 2.5 m; it was 2.0 before, which showed 24% to 54% too much there.
TYPICAL_M = float(os.environ.get("RECON_TYPICAL_M", locate.TYPICAL_M))
# A fix that is not found is tried once more with this many baseline photos (0 = never, the default).
# With 24: found 80% -> 90% in the walking test, and every fix that is not found at first, also every
# look into another room, takes about 5 s longer.
N_LOCATE_RETRY = int(os.environ.get("RECON_LOCATE_RETRY", "0"))
LANE = "-cpu" if DEVICE == "cpu" else ""  # the CPU worker has its own queue and heartbeat
# Where the queue, the baselines and the heartbeat live: next to the code on the cluster, or the folder in
# BEACON_ROOT when the worker and the gateway run on a laptop (the gateway reads the same setting).
DATA = Path(os.environ.get("BEACON_ROOT") or ROOT)
QUEUE, BASELINES = DATA / f"queue{LANE}", DATA / "baselines"
ITEMS = items.wanted()  # RECON_ITEMS: small things to look for in every position fix; empty = off
ITEM_DETECTOR = {}  # {"model": the loaded detector} or {"error": why it could not be loaded}
# Where the item finder runs: unset = where VGGT runs. "cpu" keeps it off a small graphics card altogether.
ITEMS_DEVICE = os.environ.get("RECON_ITEMS_DEVICE") or None
GPU = {}  # {"torch": the module, "small": a card under 10 GB}, filled in at start when VGGT is on a graphics card
INFO = {}  # what this worker is, filled in at start and attached to every result


def clip_frames(job, req, n):
    """Up to n frames from the job's clip: a video file, or a zip of frames picked by the relay."""
    clip = job / req["file"]
    try:
        if clip.suffix != ".zip":
            return rc.sample_frames(clip, n)
        folder = job / "frames"
        folder.mkdir(exist_ok=True)
        with zipfile.ZipFile(clip) as bundle:
            for name in bundle.namelist():
                if re.fullmatch(r"\d{1,4}\.jpg", name):  # nothing else is ever written to disk
                    (folder / name).write_bytes(bundle.read(name))
        return [f for f in rc.sample_frames(folder, n) if f is not None]
    except SystemExit:  # sample_frames is also a command-line helper and exits on unreadable input
        raise RuntimeError("Could not read any picture from that clip.") from None


def set_baseline(job, req):
    frames = clip_frames(job, req, N_KEEP)
    if len(frames) < 6:
        raise RuntimeError(f"The baseline clip gave only {len(frames)} frames. Record a longer one.")
    zone = BASELINES / req["zone"]
    zone.mkdir(parents=True, exist_ok=True)
    for old in zone.glob("*.jpg"):
        old.unlink()
    for chosen in ("waypoints.json", "watch.json", "scale.json"):  # places, watched spots and the scale
        (zone / chosen).unlink(missing_ok=True)  # were all picked in the old pictures
    for i, frame in enumerate(frames):
        cv2.imwrite(str(zone / f"{i:02d}.jpg"), square_up(frame), [cv2.IMWRITE_JPEG_QUALITY, 92])
    return {"frames": len(frames)}


def square_up(frame):
    """A portrait picture as its middle square. VGGT takes pictures 518 wide (its own preprocessing crops a tall one to
    its middle 518 rows); a phone held upright gives 294 x 518, which it reads far worse: on a hall, 7% of the pixels
    kept instead of 64% for the same pictures cropped. Landscape pictures are kept as they are. Every later picture of
    the zone and every photo of a fix is cropped like the first picture (crop_like), so all of them become square."""
    h, w = frame.shape[:2]
    if h <= 1.05 * w:
        return frame
    top = (h - w) // 2
    return frame[top:top + w]


def agreeing(recon, valid, tol=0.03):
    """Per pixel: in how many other pictures of the pass the same point lands at the same depth (within tol of it).
    The room map keeps points that two others confirm, instead of trusting VGGT's confidence (see rc.valid_mask)."""
    import torch
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    depth = torch.as_tensor(recon.depth, dtype=torch.float32, device=dev)
    K = torch.as_tensor(recon.K, dtype=torch.float32, device=dev)
    E = torch.as_tensor(recon.E, dtype=torch.float32, device=dev)
    S, H, W = depth.shape
    v, u = torch.meshgrid(torch.arange(H, device=dev, dtype=torch.float32), torch.arange(W, device=dev, dtype=torch.float32), indexing="ij")
    mask = torch.as_tensor(valid, device=dev)
    count = torch.zeros((S, H, W), dtype=torch.int16, device=dev)
    for q in range(S):
        sel = mask[q]
        d = depth[q][sel]
        cam = torch.stack([(u[sel] - K[q, 0, 2]) / K[q, 0, 0] * d, (v[sel] - K[q, 1, 2]) / K[q, 1, 1] * d, d], 1)
        world = (cam - E[q, :, 3]) @ E[q, :, :3]
        hits = torch.zeros(len(d), dtype=torch.int16, device=dev)
        for o in range(S):
            if o == q:
                continue
            c = world @ E[o, :, :3].T + E[o, :, 3]
            z = c[:, 2]
            uu = K[o, 0, 0] * c[:, 0] / z.clamp(min=1e-6) + K[o, 0, 2]
            vv = K[o, 1, 1] * c[:, 1] / z.clamp(min=1e-6) + K[o, 1, 2]
            inside = (z > 0) & (uu >= 0) & (uu <= W - 1) & (vv >= 0) & (vv <= H - 1)
            seen = depth[o][vv.round().long().clamp(0, H - 1), uu.round().long().clamp(0, W - 1)]
            hits += (inside & ((seen - z).abs() < tol * z)).to(torch.int16)
        count[q][sel] = hits
    return count.cpu().numpy()


def qmul(a, b):
    """Quaternion products a * b, (w, x, y, z), a single one or rows of them."""
    w1, x1, y1, z1 = np.moveaxis(np.asarray(a, np.float64), -1, 0)
    w2, x2, y2, z2 = np.moveaxis(np.asarray(b, np.float64), -1, 0)
    return np.stack([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2], -1)


REC_PICTURES = int(os.environ.get("RECON_REC_PICTURES") or 48)  # the room's pictures taken from a recording, at most


def pick_keyframes(frames, most, change=0.9):
    """[(number, picture)] from an iterable of (number, picture) in time order: the sharpest picture since the last
    keyframe, taken once the view has moved on (cosine similarity of small grey copies of the middle squares under
    `change`), so neighbouring keyframes overlap a lot. At most `most`, spread over the whole run. video_map.py and
    recordings (recording_map) both pick with this."""
    def small(frame):
        g = cv2.cvtColor(cv2.resize(square_up(frame), (48, 48), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
        g -= g.mean()
        return (g / (np.linalg.norm(g) + 1e-6)).ravel()

    picked, since, last = [], [], None
    for number, frame in frames:
        if frame is None:
            continue
        since.append((rc.sharpness(frame), number, frame))
        if last is None or float(small(frame) @ last) < change:
            _, best_number, best = max(since, key=lambda s: s[0])
            picked.append((best_number, best))
            last = small(best)
            since = []
        elif len(since) > 90:  # a long stretch with nothing new: only the newest frames stay candidates
            since = since[-30:]
    if len(picked) > most:
        keep = np.linspace(0, len(picked) - 1, most).round().astype(int)
        picked = [picked[i] for i in sorted(set(keep))]
    return picked


def zone_places(zone, count):
    """The zone's named places: those named in one of its `count` pictures (frame, u, v), and those with a map position
    of their own (position: named on a live photo, or found by the item finder)."""
    try:
        places = json.loads((zone / "waypoints.json").read_text())
    except (OSError, ValueError):
        return []
    return [p for p in places if ("frame" in p and p["frame"] < count) or ("frame" not in p and p.get("position"))]


def recording_map(model, job, req):
    """A room recorded as a stream of frames (relay POST /recording, stored under recordings/<zone>/<session>/frames):
    the keyframes of everything so far become the zone's pictures (square, see square_up), then its room map is built
    (room_map). Called again as the recording grows. Places named in the zone's old pictures no longer apply."""
    folder = DATA / "recordings" / req["zone"] / req["session"] / "frames"
    paths = sorted(folder.glob("*.jpg"))
    if len(paths) < 6:
        raise RuntimeError(f"Only {len(paths)} frames recorded so far: the room map needs a few more.")
    t0 = time.time()
    picked = pick_keyframes(((i, cv2.imread(str(p))) for i, p in enumerate(paths)), REC_PICTURES)
    zone = BASELINES / req["zone"]
    zone.mkdir(parents=True, exist_ok=True)
    for old in zone.glob("*.jpg"):
        old.unlink()
    for chosen in ("waypoints.json", "watch.json", "scale.json"):
        (zone / chosen).unlink(missing_ok=True)
    for k, (_, frame) in enumerate(picked):
        square = square_up(frame)
        side = min(518, square.shape[1])
        cv2.imwrite(str(zone / f"{k:02d}.jpg"), cv2.resize(square, (side, side * square.shape[0] // square.shape[1]),
                                                           interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 92])
    chosen_s = round(time.time() - t0, 2)
    result = room_map(model, job, req)
    result.update(session=req["session"], recorded_frames=len(paths), keyframes=len(picked),
                  keyframe_numbers=[n for n, _ in picked])
    result["seconds"]["keyframes"] = chosen_s
    return result


def room_map(model, job, req):
    """The zone's baseline pictures in one pass (up to MAP_FRAMES, plus every picture a place was named in), as one
    splat in map axes: x east, y north, z up, in metres from the first baseline camera, the same axes the position
    fixes report in (locate.build_frame). Written to out/room.splat. Returns the map's facts for the page.
    op "spots": the same pass and axes without the splat: the named places' positions, and req["spots"] (picture
    spots {frame, u, v}) placed in the map, None where the pass has no depth there. Nothing is added or rebuilt."""
    zone = BASELINES / req["zone"]
    files = sorted(zone.glob("*.jpg"))
    if len(files) < 3:
        raise RuntimeError(f"No baseline for '{req['zone']}' yet. Record the baseline clip first.")
    places = zone_places(zone, len(files))
    asked = [s for s in req.get("spots") or [] if 0 <= int(s["frame"]) < len(files)] if req["op"] == "spots" else []
    try:
        known = json.loads((zone / "scale.json").read_text())
    except (OSError, ValueError):
        known = None
    seconds = {}
    spread = np.linspace(0, len(files) - 1, min(MAP_FRAMES, len(files))).round().astype(int)
    pick = sorted({int(i) for i in spread} | {p["frame"] for p in places if "frame" in p} | {int(s["frame"]) for s in asked})
    frames = [cv2.imread(str(files[i])) for i in pick]
    t0 = time.time()
    recon = rc.reconstruct(model, frames, long_side=LONG_SIDE)
    seconds["reconstruct"] = round(time.time() - t0, 2)
    t0 = time.time()
    base = list(range(len(frames)))
    valid = rc.valid_mask(recon, MAP_CONF_PCT)
    # Points two other pictures confirm (same point, same depth within 3%): on the hall's 74 views this kept 20% of the
    # pixels, 640,000 Gaussians, where confidence kept 1%. One confirmation when two leave too little.
    agree = agreeing(recon, valid)
    shown = valid & (agree >= 2)
    if shown.mean() < 0.03:
        shown = valid & (agree >= 1)
    seconds["agree"] = round(time.time() - t0, 2)
    slot = {f: i for i, f in enumerate(pick)}
    spots = {p["name"]: locate.spot(recon, {**p, "frame": slot[p["frame"]]}) for p in places if "frame" in p}
    spots = {name: x for name, x in spots.items() if x is not None}
    room = locate.build_frame(recon, base, valid, TYPICAL_M, "surface", known, spots)
    if not (np.isfinite(room["metres"]) and np.all(np.isfinite(room["origin"]))):
        raise RuntimeError("The room could not be rebuilt from these pictures: record the baseline again, slower and "
                           "with more of the same things in neighbouring pictures.")
    named = [{"name": n, "position": [round(float(v), 3) for v in locate.to_map(room, x)[0]]} for n, x in spots.items()]
    named += [{"name": p["name"], "position": p["position"], **({"source": p["source"]} if p.get("source") else {})}
              for p in places if "frame" not in p]
    if req["op"] == "spots":
        placed = []
        for s in asked:
            x = locate.spot(recon, {"frame": slot[int(s["frame"])], "u": float(s["u"]), "v": float(s["v"])})
            placed.append(None if x is None else [round(float(v), 4) for v in locate.to_map(room, x)[0]])
        return {"frames": len(frames), "of": len(files), "places": named, "spots": placed, "metres_from": room["metres_from"],
                "seconds": seconds, **INFO}
    nothing = {"points": np.zeros((0, 3)), "frames": np.zeros(0, int), "pixels": np.zeros(0, int), "member": np.zeros(0, int)}
    detail = {"after": base, "valid": shown, "appeared": nothing, "gone": nothing}
    pos, scale, rgba, quat = splat.gaussians(recon, detail, MAP_GAUSSIANS, max_frames=len(base))
    turn = np.stack([room["east"], room["north"], room["up"]])  # the pass's axes -> map axes (a proper rotation)
    pos = (pos - room["origin"]) @ turn.T * room["metres"]
    scale = scale * room["metres"]
    quat = qmul(splat.quaternion(turn), quat)
    keep = np.isfinite(pos).all(1) & np.isfinite(scale).all(1)
    record = np.zeros(int(keep.sum()), dtype=[("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    record["pos"], record["scale"], record["rgba"] = pos[keep], scale[keep], rgba[keep]
    record["rot"] = np.clip(quat[keep] * 128 + 128, 0, 255).astype(np.uint8)
    out = job / "out"
    out.mkdir(exist_ok=True)
    (out / "room.splat").write_bytes(record.tobytes())
    seconds["splat"] = round(time.time() - t0, 2)
    cameras = [locate.to_map(room, camera_position(recon.E[q]))[0] for q in base]
    looks = [(recon.E[q][2, :3] @ room["east"], recon.E[q][2, :3] @ room["north"]) for q in base]
    return {"frames": len(frames), "of": len(files), "gaussians": int(keep.sum()), "metres_from": room["metres_from"],
            "metres_note": room["metres_note"], "up_from": room["up_how"]["from"],
            "places": named,
            "cameras": [{"frame": int(f), "position": [round(float(v), 2) for v in c],
                         "heading_deg": round(float(np.degrees(np.arctan2(*l))), 1)} for f, c, l in zip(pick, cameras, looks)],
            "fov_deg": round(float(np.degrees(2 * np.arctan(recon.depth.shape[2] / 2 / np.median(recon.K[:, 0, 0])))), 1),
            "median_depth_m": round(float(np.median(recon.depth[shown])) * room["metres"], 2) if shown.any() else None,
            "kept_share": round(float(shown.mean()), 3), "seconds": seconds, **INFO}


def crop_like(frame, like):
    """Centre-crop a frame to another frame's shape. The model resizes every frame to the first
    one's size, so a portrait photo next to a landscape baseline would come out squashed."""
    h, w = frame.shape[:2]
    shape = like.shape[1] / like.shape[0]
    if w / h > shape:
        keep = max(1, round(h * shape))
        return frame[:, (w - keep) // 2:(w - keep) // 2 + keep]
    keep = max(1, round(w / shape))
    return frame[(h - keep) // 2:(h - keep) // 2 + keep]


def camera_position(E):
    return -E[:, :3].T @ E[:, 3]


def before_and_now(recon, detail):
    """One "before" and one "now" picture for a phone screen: the after frame showing most of
    what appeared (green) and the baseline frame showing most of what is gone (red). When only
    one side has something to show, the other is the view taken from the nearest spot."""
    def most(found, frames):
        hits = found["member"] >= 0
        count = {q: int((hits & (found["frames"] == q)).sum()) for q in frames}
        best = max(frames, key=lambda q: count[q])
        return best, count[best], found["pixels"][hits & (found["frames"] == best)]

    def nearest(frames, to):
        return min(frames, key=lambda q: float(np.linalg.norm(camera_position(recon.E[q]) - camera_position(recon.E[to]))))

    now, n_now, px_now = most(detail["appeared"], detail["after"])
    before, n_before, px_before = most(detail["gone"], detail["reference"])
    if n_now == 0 and n_before > 0:
        now, px_now = nearest(detail["after"], before), px_now[:0]
    elif n_before == 0:
        if n_now == 0:
            now = detail["after"][len(detail["after"]) // 2]
        before, px_before = nearest(detail["reference"], now), px_before[:0]
    return rc.tint(recon.rgb[before], px_before, rc.RED), rc.tint(recon.rgb[now], px_now, rc.GREEN)


def watched_spots(zone, count):
    """The zone's watched spots (baselines/<zone>/watch.json): [{"name", "frame", "box": [u0, v0, u1, v1]}].
    frame is the number of the baseline picture the box was drawn in, the box is in fractions of it."""
    try:
        listed = json.loads((BASELINES / zone / "watch.json").read_text())
    except (OSError, ValueError):
        return []
    return [s for s in listed if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("frame"), int)
            and 0 <= s["frame"] < count and isinstance(s.get("box"), list) and len(s["box"]) == 4][:20]


def look_at_spots(recon, spots, pick, pictures, n_base, out, seconds):
    """Answer for each watched spot: same, changed or not seen (watch.py). pick lists the baseline picture
    numbers used in this pass, in order; pictures are this pass's frames, baseline first. Each answer also
    gets a "before" and a "now" crop with the spot outlined, for a person to judge."""
    if not spots:
        return []
    t0 = time.time()
    asked = [{"name": s["name"], "frame": pick.index(s["frame"]), "box": [float(v) for v in s["box"]]} for s in spots]
    answers = watch.check(recon, list(range(n_base)), list(range(n_base, len(pictures))), asked)
    for i, answer in enumerate(answers):
        for key, frame, box in (("before", answer.get("frame_base"), answer.get("box_base")),
                                ("now", answer.get("frame"), answer.get("box_new"))):
            if frame is None or box is None:
                continue
            picture = pictures[int(frame)]
            h, w = picture.shape[:2]
            u0, v0, u1, v1 = (float(v) for v in box)
            pad = max(0.75 * max(u1 - u0, v1 - v0), 0.06)  # some of the surroundings, so the spot can be recognised
            x0, x1 = int(max(0, u0 - pad) * w), int(min(1, u1 + pad) * w)
            y0, y1 = int(max(0, v0 - pad) * h), int(min(1, v1 + pad) * h)
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            crop = picture[y0:y1, x0:x1].copy()
            cv2.rectangle(crop, (int(u0 * w) - x0, int(v0 * h) - y0), (int(u1 * w) - x0, int(v1 * h) - y0), (0, 220, 255), 1)
            grow = max(1, round(160 / max(crop.shape[:2])))  # small spots are a few pixels: enlarge the crop
            crop = cv2.resize(crop, None, fx=grow, fy=grow, interpolation=cv2.INTER_NEAREST) if grow > 1 else crop
            cv2.imwrite(str(out / f"spot{i}_{key}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
            answer[f"image_{key}"] = f"spot{i}_{key}"
    seconds["watch"] = round(time.time() - t0, 2)
    return json.loads(json.dumps(answers, default=float))  # plain numbers, ready for JSON


def check(model, job, req):
    files = sorted((BASELINES / req["zone"]).glob("*.jpg"))
    if not files:
        raise RuntimeError(f"No baseline for '{req['zone']}' yet. Record the baseline clip first.")
    seconds = {}
    t0 = time.time()
    spots = watched_spots(req["zone"], len(files))
    spread = np.linspace(0, len(files) - 1, min(N_BASELINE, len(files))).round().astype(int)
    pick = sorted({int(i) for i in spread} | {s["frame"] for s in spots})  # a spot needs the picture it was drawn in
    files = [files[i] for i in pick]
    base = [cv2.imread(str(f)) for f in files]
    after = [crop_like(f, base[0]) for f in clip_frames(job, req, N_AFTER)]
    if len(after) < 3:
        raise RuntimeError(f"The clip gave only {len(after)} frames. Record a longer one.")
    seconds["frames"] = round(time.time() - t0, 2)
    t0 = time.time()
    recon = rc.reconstruct(model, base + after, long_side=LONG_SIDE)
    seconds["reconstruct"] = round(time.time() - t0, 2)
    t0 = time.time()
    report, detail = rc.analyse(recon, len(base))
    out = job / "out"
    out.mkdir(exist_ok=True)
    rc.contact_sheet(recon, detail, out / "changes.jpg")
    before, now = before_and_now(recon, detail)
    cv2.imwrite(str(out / "before.jpg"), before, [cv2.IMWRITE_JPEG_QUALITY, 90])
    cv2.imwrite(str(out / "now.jpg"), now, [cv2.IMWRITE_JPEG_QUALITY, 90])
    seconds["detect"] = round(time.time() - t0, 2)
    try:  # the watched spots: a marked place is checked far more closely than the room as a whole
        report["watch"] = look_at_spots(recon, spots, pick, base + after, len(base), out, seconds)
    except Exception as error:  # the room-wide answer must still go out
        report.update({"watch": [], "watch_note": f"Watched spots were not checked: {type(error).__name__}: {error}"[:300]})
        print(f"{job.name} watched spots FAILED: {traceback.format_exc()}", flush=True)
    t0 = time.time()
    report["gaussians"] = splat.write_splat(recon, detail, out / "scene.splat")  # 3D view, built on the CPU
    seconds["splat"] = round(time.time() - t0, 2)
    report["seconds"] = seconds
    report["median_depth"] = round(float(np.median(recon.depth)), 4)
    report.update(INFO)
    return report


def where(model, job, req):
    """Where the wearer is in the zone's map, and the way to each named place."""
    zone = BASELINES / req["zone"]
    files = sorted(zone.glob("*.jpg"))
    if not files:
        raise RuntimeError(f"No baseline for '{req['zone']}' yet. Record the baseline clip first.")
    places = zone_places(zone, len(files))
    try:  # one known distance between two named places, written by the carer's page
        known = json.loads((zone / "scale.json").read_text())
    except (OSError, ValueError):
        known = None
    seconds = {}
    t0 = time.time()
    spots = watched_spots(req["zone"], len(files))
    seen = clip_frames(job, req, N_LOCATE)
    if not seen:
        raise RuntimeError("No picture of what the wearer sees.")
    read = time.time() - t0
    coverage, per_picture = None, None
    if PICK == "similar" or NEW_VIEW > 0:  # how much of this view the zone's pictures show (see PATCH_SIM)
        t0 = time.time()
        first = cv2.imread(str(files[0]))
        view = crop_like(seen[-1], first)
        share, per_picture, median = mapped_share(model, req["zone"], files, view)
        top = np.argsort(-per_picture)[:3]
        coverage = {"mapped_share": round(share, 2), "median_similarity": round(median, 2), "patch_sim": PATCH_SIM,
                    "best_pictures": [{"frame": int(i), "share": round(float(per_picture[i]), 2)} for i in top],
                    "seconds": round(time.time() - t0, 2)}

    def fix(n):
        """One pass with n baseline photos: the photos, the wearer's frames, the reconstruction and locate's answer."""
        t0 = time.time()
        spread = np.linspace(0, len(files) - 1, min(n, len(files))).round().astype(int)
        if PICK == "similar" and per_picture is not None:  # the origin, and the pictures that show most of this view
            spread = [0] + [int(i) for i in np.argsort(-per_picture) if i != 0][:max(0, n - 1)]
        # a place or a watched spot needs the picture it was picked in
        pick = sorted({int(i) for i in spread} | {p["frame"] for p in places if "frame" in p} | {s["frame"] for s in spots})
        base = [cv2.imread(str(files[i])) for i in pick]
        new = [crop_like(f, base[0]) for f in seen]
        took = {"frames": round(time.time() - t0, 2)}
        t0 = time.time()
        recon = rc.reconstruct(model, base + new, long_side=LONG_SIDE)
        took["reconstruct"] = round(time.time() - t0, 2)
        t0 = time.time()
        slot = {f: i for i, f in enumerate(pick)}
        result, marks, valid = locate.locate(
            recon, list(range(len(base))), list(range(len(base), len(base) + len(new))),
            [{**p, "frame": slot[p["frame"]]} if "frame" in p else p for p in places], typical_m=TYPICAL_M, known=known)
        took["locate"] = round(time.time() - t0, 2)
        return base, new, recon, result, marks, valid, took, pick

    base, new, recon, result, marks, valid, took, pick = fix(N_LOCATE_BASE)
    seconds.update(took)
    seconds["frames"] = round(seconds["frames"] + read, 2)
    if result["state"] != "found" and min(N_LOCATE_RETRY, len(files)) > len(base):  # once more, with more of the baseline
        t0 = time.time()
        again = fix(N_LOCATE_RETRY)
        better = ["lost", "unsure", "found"].index(again[3]["state"]) > ["lost", "unsure", "found"].index(result["state"])
        retried = {"baseline": len(again[0]), "first_state": result["state"], "kept": better}
        if better:
            base, new, recon, result, marks, valid, _, pick = again
        result["retried"] = retried
        seconds["retry"] = round(time.time() - t0, 2)
    t0 = time.time()
    out = job / "out"
    out.mkdir(exist_ok=True)
    cv2.imwrite(str(out / "view.jpg"), new[-1], [cv2.IMWRITE_JPEG_QUALITY, 88])
    seconds["locate"] = round(seconds["locate"] + time.time() - t0, 2)
    point = req.get("point")  # a spot picked on this photo (relay POST /waypoint?live=1): where it is on the map
    if point and result["state"] != "lost" and getattr(recon, "room", None) is not None:
        h, w = seen[-1].shape[:2]  # u, v are in the photo as sent; the model saw its middle cropped like the pictures
        ch, cw = new[-1].shape[:2]
        u, v = (point["u"] * w - (w - cw) / 2) / cw, (point["v"] * h - (h - ch) / 2) / ch
        x = locate.spot(recon, {"frame": len(base) + len(new) - 1, "u": u, "v": v}) if 0 <= u <= 1 and 0 <= v <= 1 else None
        result["point"] = None if x is None else [round(float(c), 3) for c in locate.to_map(recon.room, x)[0]]
        if x is None:
            result["point_note"] = "outside the part of the photo the model sees" if not (0 <= u <= 1 and 0 <= v <= 1) else "no depth there"
    looking = ITEMS + [n for n in req.get("look_for") or [] if n not in ITEMS]  # the relay's own (BEACON_AUTO_PLACES)
    result.update({"items": [], "items_looked_for": looking, "items_checked": [], "items_candidates": []})
    if looking:  # lost items: found in the newest picture, placed with this fix's depth (items.py)
        t0 = time.time()
        if GPU.get("small"):  # hand VGGT's spare working memory back first: the detector needs its own on a 6 GB card
            GPU["torch"].cuda.empty_cache()
        try:
            if "model" not in ITEM_DETECTOR:
                raise RuntimeError(ITEM_DETECTOR.get("error", "not loaded"))
            found, item_marks = items.look(ITEM_DETECTOR["model"], looking, job / req["file"], seen[-1], base[0], recon,
                                           list(range(len(base))), len(base) + len(new) - 1, valid, result["state"], out,
                                           remembered=req.get("remembered"))
            result.update(found)
            marks += item_marks
        except Exception as error:  # the position fix must still go out; nothing was looked for, so the list is empty
            result.update({"items": [], "items_checked": [], "items_candidates": [], "items_looked_for": [],
                           "items_note": f"Items were not looked for: {type(error).__name__}: {error}"[:300]})
            print(f"{job.name} items FAILED: {traceback.format_exc()}", flush=True)
        seconds["items"] = round(time.time() - t0, 2)
    try:  # the watched spots this view happens to show; a lost fix has no pose to project them with
        result["watch"] = look_at_spots(recon, spots, pick, base + new, len(base), out, seconds) if result["state"] != "lost" else []
    except Exception as error:
        result.update({"watch": [], "watch_note": f"Watched spots were not checked: {type(error).__name__}: {error}"[:300]})
        print(f"{job.name} watched spots FAILED: {traceback.format_exc()}", flush=True)
    if req.get("view"):  # the room as a splat, with the wearer (blue) and the places (yellow) marked
        t0 = time.time()
        nothing = {"points": np.zeros((0, 3)), "frames": np.zeros(0, int), "pixels": np.zeros(0, int), "member": np.zeros(0, int)}
        detail = {"after": list(range(len(base))), "valid": valid, "appeared": nothing, "gone": nothing}
        result["gaussians"] = splat.write_splat(recon, detail, out / "map.splat", marks=marks)
        seconds["splat"] = round(time.time() - t0, 2)
    if coverage is not None:
        coverage["used"] = [int(i) for i in pick]
        result["coverage"] = coverage
        seconds["coverage"] = coverage.pop("seconds")
        grey = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
        usable = grey.mean() > 30 and cv2.Laplacian(grey, cv2.CV_64F).var() > 40  # not dark, not a blur
        # Inside one room, a view its pictures do not show is a part not sampled yet. So is a view the fix could not
        # place although it looks like the room: in a hall full of look-alike tables the pictures can match by
        # appearance and still not overlap enough to be put together (measured: 8 portrait pictures round a turn).
        # Unless one picture already shows nearly all of this view, the photo becomes a picture of the zone, the next
        # room map includes it (the relay asks for one), and the next fix can pick it.
        unplaced = result["state"] == "lost" and float(per_picture.max()) < 0.9
        coverage["unplaced"] = bool(unplaced)
        grows = req.get("grow", True)  # the relay: not for a zone with a trained splat, unless asked (?grow=1)
        if NEW_VIEW > 0 and (coverage["mapped_share"] < NEW_VIEW or unplaced) and grows and usable and len(files) < MAX_PICTURES:
            name = f"{len(files):02d}.jpg"
            h, w = first.shape[:2]
            cv2.imwrite(str(zone / name), cv2.resize(view, (w, h), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 92])
            result["picture_added"] = {"name": name, "frame": len(files), "pictures": len(files) + 1}
        elif NEW_VIEW > 0 and (coverage["mapped_share"] < NEW_VIEW or unplaced):
            result["picture_not_added"] = ("growth is off for this zone (it has a trained splat; ?grow=1 grows it)" if not grows
                                           else "too dark or blurred" if not usable else f"the zone has {MAX_PICTURES} pictures")
    result["seconds"] = seconds
    result["frames"] = {"baseline": len(base), "new": len(new)}
    result["median_depth"] = round(float(np.median(recon.depth)), 4)
    result.update(INFO)
    return result


def main():
    QUEUE.mkdir(exist_ok=True)
    BASELINES.mkdir(exist_ok=True)
    stop, alive = DATA / f"STOP{LANE}", DATA / f"worker{LANE}.alive"
    stop.unlink(missing_ok=True)
    import torch
    if DEVICE == "cpu":
        torch.set_num_threads(int(os.environ.get("SLURM_CPUS_PER_TASK", os.cpu_count() or 4)))
    model = rc.load_model(device=DEVICE)
    on_gpu = next(model.parameters()).is_cuda
    if on_gpu:
        GPU.update(torch=torch, small=torch.cuda.get_device_properties(0).total_memory < 10e9)
    try:
        revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no", "--", "."], text=True))
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    INFO.update({
        "revision": revision, "source_dirty": dirty,
        "device": torch.cuda.get_device_name(0) if on_gpu else f"CPU, {torch.get_num_threads()} threads",
        "on_gpu": on_gpu, "host": socket.gethostname().split(".")[0],
        "rust_enabled": rc.recon_rust.enabled(),
        "rust_threads": rc.recon_rust._rs.threads() if rc.recon_rust.enabled() else None,
        "rust_disabled_functions": sorted(getattr(rc.recon_rust, "OFF", ())),
        "settings": {"baseline_frames": N_BASELINE, "new_frames": N_AFTER, "long_side": LONG_SIDE,
                     "locate_baseline_frames": N_LOCATE_BASE, "typical_m": TYPICAL_M, "locate_retry_frames": N_LOCATE_RETRY},
    })
    if ITEMS:
        try:
            ITEM_DETECTOR["model"] = items.load(device=ITEMS_DEVICE or ("cuda" if on_gpu else "cpu"))
            INFO["settings"]["items"] = ITEM_DETECTOR["model"].settings(ITEMS)
        except Exception as error:  # the worker still serves checks and position fixes
            ITEM_DETECTOR["error"] = f"{type(error).__name__}: {error}"[:300]
            INFO["settings"]["items"] = {"model": items.MODEL_ID, "names": ITEMS, "error": ITEM_DETECTOR["error"]}
            print(f"Item detector NOT loaded: {traceback.format_exc()}", flush=True)
    (DATA / f"worker{LANE}.json").write_text(json.dumps(INFO))
    print("worker ready", flush=True)

    def beat():  # the heartbeat also while a long job runs (a room map on a hot laptop card took minutes)
        while not stop.exists():
            alive.touch()
            time.sleep(5)

    import threading
    threading.Thread(target=beat, daemon=True).start()
    while not stop.exists():
        alive.touch()  # heartbeat: the gateway reads this file's age, and reports "offline" when it goes stale
        for ready in sorted(QUEUE.glob("*/READY")):
            job = ready.parent
            ready.rename(job / "RUNNING")
            t0 = time.time()
            if on_gpu:
                torch.cuda.reset_peak_memory_stats()
            try:
                req = json.loads((job / "request.json").read_text())
                run = {"baseline": lambda: set_baseline(job, req), "locate": lambda: where(model, job, req),
                       "map": lambda: room_map(model, job, req), "spots": lambda: room_map(model, job, req),
                       "recording_map": lambda: recording_map(model, job, req)}
                result = run.get(req["op"], lambda: check(model, job, req))()
                memory = ""
                if on_gpu:  # the most this job held at once (the model's weights included), for the dashboard
                    result["gpu_peak_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
                    memory = f", peak GPU memory {result['gpu_peak_gb']:.2f} GB"
                (job / "result.json").write_text(json.dumps(result))
                (job / "RUNNING").rename(job / "DONE")
                print(f"{job.name} {req['op']} {req['zone']} done in {time.time() - t0:.1f} s{memory}", flush=True)
            except (Exception, SystemExit):  # one bad request must never stop the worker
                (job / "error.txt").write_text(traceback.format_exc())
                (job / "RUNNING").rename(job / "FAILED")
                print(f"{job.name} FAILED\n{traceback.format_exc()}", flush=True)
            alive.touch()
        time.sleep(0.25)
    alive.unlink(missing_ok=True)
    print("worker stopped", flush=True)


if __name__ == "__main__":
    main()
