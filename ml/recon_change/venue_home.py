#!/usr/bin/env python3
"""A recorded place as the family's home in 3D: blocks, possible trip hazards and a simulated night
walk, from ONE trained splat and the relay's room data. No GPU, no model: a few seconds on a laptop.

    python venue_home.py --relay http://127.0.0.1:8090 --zone kitchen OUT_DIR [--fixture]
    python venue_home.py --space care_space.json OUT_DIR [--splat trained.splat]

The relay's GET /care/space?zone=<zone> (key in X-Key; --relay reads RELAY_KEY from the environment)
gives everything else: the matrix from the trained splat into the room map's axes (room_frame), the
named places in those axes, and where the recording's cameras stood. The splat is the one that reply
names, under --evidence (the relay's evidence folder), unless --splat says otherwise.

    home.ply            the place as a floor plan in blocks (floor_plan, meshed by lowpoly.py), in the
                        room map's axes (z up): low clutter, seats and tables, tall things
    hazards.json        splat_hazards.py's guesses, for the walks from --base to every other place
    night-<day>.json    night.py on a SIMULATED night (night.json with --fixture): the wearer walks from
                        --base to the places in --trips and back, routed round what stands by route.py,
                        a fix every 5 s like the relay's day log. Labelled as simulated.

OUT_DIR is the backend's <LOCAL_DATA_DIR>/mesh (GET /mesh, /hazards, /night serve it), or, with
--fixture, dashboard/fixtures/venue (the family app's example home with ?home=venue).

What the home's own pipeline works out is given here instead, because a glasses recording of a busy
hall is not a flat:
    floor    glasses look at table tops far more than at the floor, so the most crowded low band
             (hazards.floor_height) is a table. Here the floor is the LOWEST height band holding at
             least FLOOR_SHARE of the Gaussians (or --floor).
    outline  the room's box takes in every named place and camera, so a place at the far end of a
             sparse recording (a fridge by the door) is not outside the walls.
    blocks   lowpoly square by square (--raw) gives every 12 cm square its own noisy height and colour:
             a pixel city nobody can read. floor_plan smooths the heights, snaps them to three
             levels with one colour each, drops specks, what was not seen from nearby and where the
             glasses walked, and leaves out what is more than --ceiling above the floor (a hall's
             ceiling, lights and people standing; they would also hide the night trail).
"""
import argparse
import json
import math
import os
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import hazards as hz
import lowpoly
import night
import room_frame
import route
import splat_hazards as sh
import splat_refine as sr

FLOOR_SHARE = 0.005   # the floor: the lowest 5 cm band with at least this share of the Gaussians
OUTLINE_PCT = 1       # the room's box: all but this percent of the points each side, plus places and cameras
MARGIN_M = 0.5
CEILING_M = 1.3       # the blocks leave out what is higher: a hall's ceiling, lights, banners and standing people
WALK_MS = 0.8         # walking speed (map metres per second)
FIX_S = 5             # a fix every 5 s while walking, as the relay's day log keeps them
REST_FIX_S = 30       # and one every 30 s while staying put (keeps the night under night.MAX_POINTS, so no corner is thinned out)
APPROACH_M = 0.35     # the walk ends this far short of a place it cannot stand on (a table, a fridge, a door)
DEFAULT_TRIPS = "01:12 snack table/4; 02:40 drinks fridge/2, exit door/3; 03:50 IronShard booth/6, snack table/2"
DEFAULT_BASE = "our table"
LABEL = "{name}, recorded with the glasses ({session}). The night walk is simulated."


def fetch_space(relay, zone):
    key = os.environ.get("RELAY_KEY", "")
    request = urllib.request.Request(f"{relay.rstrip('/')}/care/space?zone={zone}", headers={"X-Key": key})
    with urllib.request.urlopen(request, timeout=30) as reply:
        return json.load(reply)


def load_splat(data, frame):
    """Positions in the room map's axes and colours, faint and huge Gaussians left out (as lowpoly.from_splat)."""
    rec = np.frombuffer(bytes(data), sr.RECORD)
    keep = rec["rgba"][:, 3] >= sh.MIN_ALPHA
    if keep.sum() > 100:
        size = rec["scale"].max(1)
        keep &= size <= np.quantile(size[keep], 1 - sh.DROP_LARGEST)
    rec = rec[keep]
    return room_frame.to_room(rec["pos"].astype(np.float64), frame), rec["rgba"][:, :3]


def floor_of(height, share=FLOOR_SHARE):
    """The lowest well-seen height band: the floor, even when tables were seen far more."""
    bins = np.arange(np.percentile(height, 0.1), np.percentile(height, 50) + 0.05, 0.05)
    counts, edges = np.histogram(height, bins)
    k = int(np.argmax(counts >= share * len(height)))
    k = k + int(np.argmax(counts[k:k + 4]))  # the band's own peak, just above where it starts
    return float(np.median(height[(height >= edges[k]) & (height <= edges[k + 1])]))


def outline_of(flat, spots, pct=OUTLINE_PCT, margin=MARGIN_M):
    lo, hi = np.percentile(flat, pct, axis=0), np.percentile(flat, 100 - pct, axis=0)
    if len(spots):
        lo, hi = np.minimum(lo, spots.min(0) - margin), np.maximum(hi, spots.max(0) + margin)
    return (float(lo[0]), float(lo[1])), (float(hi[0]), float(hi[1]))


PLAN_M = 0.12          # the floor plan's squares (lowpoly's "medium")
PLAN_MIN = 60          # a square stands when this many Gaussians are in it between ankle and --ceiling (a trained glasses splat) ...
PLAN_TOP = 75          # ... as high as this percentile of them
SEEN_M = 3.0           # squares farther than this from where the glasses went (or a named place) were not seen well
PIECE_MIN = 4          # pieces of fewer squares are noise
RECT_SIDE = 3          # a table is at least this many squares each way (36 cm)
WALKED_M = 0.35        # the glasses walked here (between two standing cameras in a row, route.WALK_GAP_M apart at most): floor
STANDING = 0.85        # a camera stands when its eyes are this share of the cameras' usual height above the floor (else seated)
LOW, TABLE, TALL, SEAT = 0, 1, 2, 3
LEVELS = (  # (from height, drawn height, colour): low clutter, tables, tall things (people, booths, fridges), and
    (0.08, 0.16, (205, 196, 178)),  # seats, which only the vision labels say (a chair is as high as a table's edge)
    (0.30, 0.48, (186, 138, 88)),
    (0.80, 1.04, (112, 122, 148)),
    (None, 0.32, (92, 104, 116)),
)
ON_TABLES = ("laptop", "monitor", "cup", "bottle", "crisp packet", "3D printer", "keyboard", "mug", "plate")
SEATS = ("chair", "stool", "sofa", "bench")
FLOOR_RGB, WALL_RGB = (150, 146, 140), (196, 192, 186)


def axis_of(points, step=0.1):
    """The venue's own direction (radians, 0 to 90 degrees from east): rows of tables, aisles and walls
    run straight and at right angles, so turned the right way the points pile up in the fewest rows and
    columns (the lowest entropy of the two histograms)."""
    best, angle = np.inf, 0.0
    for deg in np.arange(0.0, 90.0, 1.0):
        uv = turn(points, np.radians(deg))
        e = 0.0
        for w in uv.T:
            c = np.bincount(np.floor((w - w.min()) / step).astype(int)).astype(float)
            q = c[c > 0] / c.sum()
            e -= float((q * np.log(q)).sum())
        if e < best:
            best, angle = e, float(np.radians(deg))
    return angle


def turn(xy, angle):
    """Map (east, north) -> the venue's own axes (along, across); turn(uv, -angle) turns back."""
    xy = np.asarray(xy, float)
    c, s = np.cos(angle), np.sin(angle)
    return np.stack([xy[..., 0] * c + xy[..., 1] * s, -xy[..., 0] * s + xy[..., 1] * c], -1)


def vision_objects(folder, frame, angle, floor):
    """The vision stage's labelled objects (labels.json next to the trained splat, judged by CLIP
    ViT-L/14) as (name, (along, across), height above the floor). [] without the file."""
    path = Path(folder) / "labels.json"
    if not path.is_file():
        return []
    objects = json.loads(path.read_text(encoding="utf-8")).get("objects") or []
    if not objects:
        return []
    p = room_frame.to_room(np.array([o["position"] for o in objects], float), frame)
    return [(o["name"], turn(q[:2], angle), float(q[2] - floor)) for o, q in zip(objects, p)]


def plan_levels(uv, h, outline, spots, cameras, eyes, objects=(), ceiling=CEILING_M):
    """The splat as a calm floor plan on PLAN_M squares in the venue's own axes: a splat of a busy hall
    is too noisy to mesh square by square (every square its own height and colour: a pixel city). Each
    square gets a robust height, smoothed over its neighbours and snapped to one of LEVELS; the vision
    labels put tables under laptops and cups and seats where chairs are; table pieces become rectangles;
    pieces smaller than PIECE_MIN squares, squares not seen from nearby and the path the glasses walked
    standing are floor. Returns the grid of levels (-1 floor) and its origin."""
    (x0, y0), (x1, y1) = outline
    w, d = int(np.ceil((x1 - x0) / PLAN_M)), int(np.ceil((y1 - y0) / PLAN_M))
    body = (h > LEVELS[LOW][0]) & (h < ceiling)
    ij = np.floor((uv[body] - (x0, y0)) / PLAN_M).astype(int)
    ok = (ij[:, 0] >= 0) & (ij[:, 0] < w) & (ij[:, 1] >= 0) & (ij[:, 1] < d)
    ij, hb = ij[ok], h[body][ok]
    key = ij[:, 0] * d + ij[:, 1]
    order = np.argsort(key, kind="stable")
    hb = hb[order]
    cells, starts, counts = np.unique(key[order], return_index=True, return_counts=True)
    top = np.zeros(w * d)
    for k, s, n in zip(cells, starts, counts):
        if n >= PLAN_MIN:
            top[k] = np.percentile(hb[s:s + n], PLAN_TOP)
    top = top.reshape(w, d)
    pad = np.pad(top, 1)  # smoothed: the median of each square and its 8 neighbours (an empty square counts as floor)
    top = np.median(np.stack([pad[1 + a:1 + a + w, 1 + b:1 + b + d] for a in (-1, 0, 1) for b in (-1, 0, 1)]), 0)
    level = np.full((w, d), -1)
    for n in (LOW, TABLE, TALL):
        level[top >= LEVELS[n][0]] = n
    centres = np.stack(np.meshgrid((np.arange(w) + 0.5) * PLAN_M + x0, (np.arange(d) + 0.5) * PLAN_M + y0, indexing="ij"), -1)
    near = np.zeros((w, d), bool)
    for s in spots:
        near |= ((centres - s) ** 2).sum(-1) <= SEEN_M ** 2
    level[~near] = -1
    # What the vision labels say: a table under what stands on tables, a seat where a chair is.
    for name, at, _ in objects:
        i, j = np.floor((np.asarray(at) - (x0, y0)) / PLAN_M).astype(int)
        if not (0 <= i < w and 0 <= j < d):
            continue
        patch = (slice(max(i - 1, 0), i + 2), slice(max(j - 1, 0), j + 2))
        if name in ON_TABLES:
            level[patch] = np.where(level[patch] == TALL, TALL, TABLE)
        elif name in SEATS:
            level[patch] = np.where(level[patch] <= LOW, SEAT, level[patch])
    # Where the glasses walked standing is floor: nobody walks through a table (smeared depth puts table
    # tops there). Seated at a table the head moves too, so only cameras at standing height count.
    standing = eyes >= STANDING * np.median(eyes) if len(eyes) else np.zeros(0, bool)
    for (a, b), up in zip(zip(cameras, cameras[1:]), standing[:-1] & standing[1:]):
        if up and np.linalg.norm(b - a) <= route.WALK_GAP_M:
            ab = b - a
            f = np.clip(((centres - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
            level[(((centres - a) - f[..., None] * ab) ** 2).sum(-1) <= WALKED_M ** 2] = -1
    # Furniture is rectangles: each level is redrawn as the largest whole rectangles that fit in it
    # (tables at least RECT_SIDE squares each way); what is left over (ragged edges, slivers) is floor.
    out = np.full((w, d), -1)
    for n, side in ((TALL, 1), (TABLE, RECT_SIDE), (SEAT, RECT_SIDE), (LOW, RECT_SIDE)):
        for i0, j0, i1, j1 in rectangles(level == n, side, PIECE_MIN):
            out[i0:i1, j0:j1] = n
    return out, (x0, y0)


def rectangles(mask, side=2, area=4):
    """The largest whole rectangles in a grid mask, one after another (largest rectangle in a histogram,
    row by row), until none of at least `side` squares each way and `area` squares is left."""
    m, found = mask.copy(), []
    while True:
        best, heights = (0, None), np.zeros(m.shape[1], int)
        for i in range(m.shape[0]):
            heights = np.where(m[i], heights + 1, 0)
            stack = []  # (start column, height), heights rising
            for j in range(len(heights) + 1):
                hj, start = (heights[j] if j < len(heights) else 0), j
                while stack and stack[-1][1] >= hj:
                    s, hs = stack.pop()
                    if hs >= side and j - s >= side and hs * (j - s) > best[0]:
                        best = (hs * (j - s), (i - hs + 1, s, i + 1, j))
                    start = s
                stack.append((start, hj))
        if best[0] < area:
            return found
        i0, j0, i1, j1 = best[1]
        m[i0:i1, j0:j1] = False
        found.append(best[1])


def plan_points(level, origin, outline, floor):
    """The floor plan as points for lowpoly.build (16 per square at its level's height and colour, on the
    same squares lowpoly uses, so its boxes merge into whole tables), with the floor and, round the edge,
    the walls: their colour, and the room's height above the blocks, so lowpoly keeps TALL as blocks."""
    pos, rgb = [], []
    for n, (_, drawn, colour) in enumerate(LEVELS):
        c = (np.argwhere(level == n) + 0.5) * PLAN_M + origin
        pos.append(np.column_stack([np.repeat(c, 16, 0), np.full(16 * len(c), floor + drawn)]))
        rgb.append(np.tile(colour, (16 * len(c), 1)))
    (x0, y0), (x1, y1) = outline
    fx, fy = np.meshgrid(np.arange(x0, x1, 0.5), np.arange(y0, y1, 0.5))
    fl = np.column_stack([fx.ravel(), fy.ravel(), np.full(fx.size, floor)])
    edge = np.array([(x, y) for x in np.arange(x0, x1, 0.25) for y in (y0 + 0.05, y1 - 0.05)] +
                    [(x, y) for y in np.arange(y0, y1, 0.25) for x in (x0 + 0.05, x1 - 0.05)])
    blocks = np.concatenate(pos)
    each = int(np.ceil(max(2000, 0.03 * len(blocks)) / len(edge)))  # over 1% of the points: lowpoly's top of the room
    wall = np.column_stack([np.repeat(edge, each, 0), np.full(each * len(edge), floor + LEVELS[TALL][1] + 0.5)])
    return (np.concatenate([fl, blocks, wall]),
            np.concatenate([np.tile(FLOOR_RGB, (len(fl), 1)), np.concatenate(rgb), np.tile(WALL_RGB, (len(wall), 1))]))


def walk_grid(level, origin, cameras):
    """route.py's grid on the floor plan: everything standing on it is in the way, and the cells far from
    where the glasses walked cost more, so the walks keep to the aisles people used."""
    c = (np.argwhere(level >= 0) + 0.5) * PLAN_M + origin
    sub = np.array([(-0.3, -0.3), (-0.3, 0.3), (0.3, -0.3), (0.3, 0.3)]) * PLAN_M
    solid = (c[:, None, :] + sub[None]).reshape(-1, 2)
    return route.build(solid.tolist(), np.asarray(cameras).tolist())


def stand_at(g, start, place):
    """Where to stand at a place (the open floor next to it) and the way there from `start`."""
    if g is None:
        return list(place), [list(start), list(place)]
    way = route.routes(g, start, [{"name": "x", "position": place}])["x"]
    points = [list(p) for p in way["points"]]
    if len(points) > 1 and way.get("reach_m"):  # the last leg reaches onto the place: stop just short of it
        (ax, ay), (bx, by) = points[-2], points[-1]
        reach = math.hypot(bx - ax, by - ay)
        if reach <= APPROACH_M:
            points.pop()
        else:
            f = (reach - APPROACH_M) / reach
            points[-1] = [ax + (bx - ax) * f, ay + (by - ay) * f]
    return points[-1], points


def simulate(g, places, base, trips, day, start="00:30", end="04:40", floor=0.0, seed=7):
    """A night of fixes: resting at `base`, then each trip ("HH:MM place/minutes, place/minutes") and back."""
    rng = np.random.default_rng(seed)
    t0 = datetime.fromisoformat(f"{day}T{start}:00+02:00")
    home, _ = stand_at(g, places[base][:2], places[base][:2])
    track, events, now, here = [], [], t0, list(home)

    def rest(until, at):
        nonlocal now
        while now < until:
            jitter = rng.normal(0, 0.01, 2)  # a steady fix wavers a little, not enough to add up to metres walked
            track.append({"t": now.isoformat(timespec="seconds"), "p": [round(at[0] + jitter[0], 3), round(at[1] + jitter[1], 3), round(floor, 3)]})
            now += timedelta(seconds=min(REST_FIX_S, max(1, (until - now).total_seconds())))

    def walk(points):
        nonlocal now
        for a, b in zip(points, points[1:]):
            steps = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / WALK_MS / FIX_S))
            for s in range(steps):
                f = s / steps
                sway = 0.04 * math.sin(len(track) * 1.3)
                track.append({"t": now.isoformat(timespec="seconds"),
                              "p": [round(a[0] + (b[0] - a[0]) * f + sway, 3), round(a[1] + (b[1] - a[1]) * f - sway, 3), round(floor, 3)]})
                now += timedelta(seconds=FIX_S)

    for trip in [x.strip() for x in trips.split(";") if x.strip()]:
        clock, stops = trip.split(" ", 1)
        rest(datetime.fromisoformat(f"{day}T{clock}:00+02:00"), home)
        for stop in stops.split(","):
            name, minutes = stop.strip().rsplit("/", 1)
            target = places[name][:2]
            spot, points = stand_at(g, here, target)
            nearest = min(places, key=lambda k: math.dist(places[k][:2], spot))
            if nearest != name or math.dist(spot, here) <= night.STOP_M:  # places side by side (a fridge by a door):
                spot, points = list(target), points[:-1] + [list(target)]  # walk right up to it, or it reads as the other
            walk(points)
            arrived = now
            if "door" in name.lower():  # the door opens and closes while the wearer is there
                events += [{"t": (arrived + timedelta(seconds=30)).isoformat(timespec="seconds"), "type": "door_open", "place": name},
                           {"t": (arrived + timedelta(minutes=float(minutes)) - timedelta(seconds=40)).isoformat(timespec="seconds"),
                            "type": "door_closed", "place": name}]
            rest(arrived + timedelta(minutes=float(minutes)), spot)
            here = spot
        _, points = stand_at(g, here, home)
        walk(points[:-1] + [home])
        here = list(home)
    rest(datetime.fromisoformat(f"{day}T{end}:00+02:00"), home)
    return track, events


def recase(out, names):
    """night.py and hazards.py speak in lower case; give the places back their own names."""
    text = json.dumps(out)
    for name in sorted(names, key=len, reverse=True):
        if name != name.lower():
            text = text.replace(f'"{name.lower()}"', json.dumps(name)).replace(f"the {name.lower()}", f"the {name}")
    return json.loads(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir")
    ap.add_argument("--relay", help="the relay, e.g. http://127.0.0.1:8090 (RELAY_KEY from the environment)")
    ap.add_argument("--zone", default="kitchen")
    ap.add_argument("--name", help="what the family app calls the place (default: the zone)")
    ap.add_argument("--space", help="a saved GET /care/space reply, instead of --relay")
    ap.add_argument("--splat", help="the trained splat (default: the one the reply names, under --evidence)")
    ap.add_argument("--evidence", default=str(Path(os.environ.get("TEMP", "/tmp")) / "beacon-evidence"))
    ap.add_argument("--quality", choices=list(lowpoly.QUALITY), default="medium", help="with --raw only")
    ap.add_argument("--raw", action="store_true", help="lowpoly square by square on the splat's colours, instead of the floor plan")
    ap.add_argument("--floor", type=float, help="the floor's height in the room map (default: worked out)")
    ap.add_argument("--ceiling", type=float, default=CEILING_M, help="blocks leave out what is higher above the floor than this (metres)")
    ap.add_argument("--axis", type=float, help="the venue's direction in degrees from east (default: worked out)")
    ap.add_argument("--base", default=DEFAULT_BASE, help="where the night starts and ends (a named place)")
    ap.add_argument("--trips", default=DEFAULT_TRIPS, help='"HH:MM place/minutes, place/minutes; HH:MM ..."')
    ap.add_argument("--day", default="2026-11-15")
    ap.add_argument("--fixture", action="store_true", help="write night.json (the dashboard's example) instead of night-<day>.json")
    a = ap.parse_args()
    t0 = time.time()
    space = fetch_space(a.relay, a.zone) if a.relay else json.load(open(a.space, encoding="utf-8"))
    if space.get("kind") != "trained" or not space.get("matrix"):
        raise SystemExit(f"{a.zone}: the relay has no trained splat placed in the room map yet ({space.get('why') or space.get('error')}).")
    frame = room_frame.from_matrix(space["matrix"])
    splat = Path(a.splat) if a.splat else Path(a.evidence) / Path(*space["splat"].split("/")[2:])
    pos, rgb = load_splat(splat.read_bytes(), frame)
    flat, height = hz.split_up(pos, "z")
    places = {p["name"]: [float(v) for v in p["position"]] for p in space.get("places") or []}
    places.update({p["name"]: [float(v) for v in p["position"]] for p in space.get("named") or []})  # placed on this splat's depth
    if a.base not in places:
        raise SystemExit(f"--base {a.base!r} is not a named place: {sorted(places)}")
    cameras3 = np.array([c["position"][:3] for c in space.get("cameras") or []], float).reshape(-1, 3)
    floor = a.floor if a.floor is not None else floor_of(height)
    h = height - floor
    # The venue's own axes: rows of tables and aisles are straight, so the plan is drawn along them.
    standing = (h > LEVELS[TABLE][0]) & (h < a.ceiling)
    angle = axis_of(flat[standing][::max(1, int(standing.sum()) // 100_000)]) if a.axis is None else float(np.radians(a.axis))
    uv, cams_uv = turn(flat, angle), turn(cameras3[:, :2], angle)
    places_uv = {k: turn(p[:2], angle).tolist() for k, p in places.items()}
    spots = np.concatenate([np.array(list(places_uv.values())).reshape(-1, 2), cams_uv])
    outline = outline_of(uv, spots)  # a rectangle along the venue's axes: the hall's walls
    objects = vision_objects(splat.parent, frame, angle, floor)
    loaded = time.time()

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    level, origin = plan_levels(uv, h, outline, spots, cams_uv, cameras3[:, 2] - floor, objects, a.ceiling)
    if a.raw:  # lowpoly square by square, on the splat's own colours
        low = h < a.ceiling
        v, n, c, t = lowpoly.build(np.column_stack([uv[low], height[low]]), rgb[low], "z", a.quality, floor=floor, outline=outline)
    else:
        plan, plan_rgb = plan_points(level, origin, outline, floor)
        v, n, c, t = lowpoly.build(plan, plan_rgb, "z", "medium", cell=PLAN_M, floor=floor, outline=outline)
    v[:, :2] = turn(v[:, :2], -angle)  # back to the room map's axes, where the relay's places and fixes are
    trained = space.get("trained") or {}
    label = LABEL.format(name=a.name or a.zone, session=trained.get("session") or splat.parent.name)
    lowpoly.write(v, n, c, t, out / "home.ply", "z", [f"from {splat.parent.name}", "simulated night walk"])
    meshed = time.time()

    source = {"zone": a.zone, "splat": splat.parent.name, "session": trained.get("session"), "psnr": trained.get("psnr"),
              "fit_rms_m": (space.get("fit") or {}).get("rms_m"), "axis_deg": round(float(np.degrees(angle)), 1),
              "vision": {"from": "labels.json", "on_tables": sum(o[0] in ON_TABLES for o in objects), "seats": sum(o[0] in SEATS for o in objects)}}
    walks = [(a.base.lower(), k.lower()) for k in places if k != a.base]
    found = sh.find(pos, places, walks, "z", floor=floor)
    found.update(generated_at=datetime.now().astimezone().isoformat(timespec="seconds"), places=places, label=label, source=source)
    found = recase(found, places)
    (out / "hazards.json").write_text(json.dumps(found, indent=1), encoding="utf-8")
    checked = time.time()

    g, why = walk_grid(level, origin, cams_uv)
    track, events = simulate(g, places_uv, a.base, a.trips, a.day, floor=floor)
    for f in track:  # the walk was planned in the venue's axes
        f["p"][:2] = [round(float(x), 3) for x in turn(f["p"][:2], -angle)]
    walked = night.analyse(track, places, events, up="z", day=a.day, base=a.base)
    walked.update(places=places, zone=a.zone, label=label, simulated=True, source=source,
                  route="route.py round the blocks" if g is not None else f"straight lines ({why})")
    walked = recase(walked, places)
    (out / ("night.json" if a.fixture else f"night-{a.day}.json")).write_text(json.dumps(walked, indent=1), encoding="utf-8")
    done = time.time()

    print(json.dumps({
        "splat": str(splat.parent.name), "gaussians_used": len(pos), "floor": round(floor, 3),
        "axis_deg": source["axis_deg"], "vision": source["vision"],
        "levels": {name: int((level == k).sum()) for k, name in enumerate(("low", "table", "tall", "seat"))},
        "mesh": {"vertices": len(v), "triangles": len(t), "bytes": (out / "home.ply").stat().st_size},
        "hazards": {"found": len(found["hazards"]), "in_the_way": sum(z["in_the_way"] for z in found["hazards"]),
                    "words": [z["words"] for z in found["hazards"][:6]]},
        "night": {**walked["summary"], "points": len(walked["points"]), "route": walked["route"]},
        "seconds": {"load": round(loaded - t0, 1), "mesh": round(meshed - loaded, 1), "hazards": round(checked - meshed, 1),
                    "night": round(done - checked, 1), "total": round(done - t0, 1)},
    }, indent=1))


if __name__ == "__main__":
    main()
