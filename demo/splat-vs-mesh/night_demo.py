"""An example night walk for the splat room, in the same levelled frame as the block mesh -> night.json.

The walk has to stay on open floor, so the floor plan is read back from the block mesh itself
(room_blocks_fine.ply, the one the page shows): its floor quad gives the room's outline and floor
height, and every flat block top above the floor marks its squares as taken. A body-width margin is
grown round the blocks and the walls, the walker's places are picked on what is left, and each walk
between them is a shortest path over the free squares (Dijkstra, with a cost for passing close to
things so it keeps to the middle of gaps), pulled straight where the line of sight allows. The fixes
every 5 s, the stops and the timestamps are as in demo_room.night_track; night.analyse makes the JSON.

    python night_demo.py [folder]      (writes folder/night.json and folder/night_plan.png)
"""
import heapq
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml" / "recon_change"))  # the repo's vision code
import night  # noqa: E402

folder = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
MESH = folder / "room_blocks_fine.ply"
RES = 0.05        # floor plan squares (m)
BODY_M = 0.20     # keep the walker's centre this far from blocks
WALL_M = 0.25     # ... and from the room's outline
SPEED = 0.12      # m/s: a slow shuffle at night (so a fix every 5 s is a short straight step, as in the app example)
FIX_S = 5


def read_ply(path):
    """Vertices and triangles of a binary PLY as splat_mesh.write_mesh writes it."""
    raw = path.read_bytes()
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    head = raw[:end].decode().split("\n")
    nv = nf = 0; props = []
    for line in head:
        w = line.split()
        if w[:2] == ["element", "vertex"]: nv = int(w[2])
        elif w[:2] == ["element", "face"]: nf = int(w[2])
        elif w[:1] == ["property"] and w[1] != "list" and nf == 0: props.append((w[2], {"float": "<f4", "uchar": "u1"}[w[1]]))
    v = np.frombuffer(raw, np.dtype(props), nv, end)
    f = np.frombuffer(raw, np.dtype([("n", "u1"), ("i", "<i4", 3)]), nf, end + nv * np.dtype(props).itemsize)
    xyz = np.stack([v["x"], v["y"], v["z"]], 1).astype(float)
    rgb = np.stack([v["red"], v["green"], v["blue"]], 1)
    return xyz, rgb, f["i"].astype(int)


xyz, rgb, tri = read_ply(MESH)
floor = float(xyz[0, 2])                       # the floor quad is written first (lowpoly.build)
(x0, y0), (x1, y1) = xyz[:4, :2].min(0), xyz[:4, :2].max(0)
nx, ny = int(np.ceil((x1 - x0) / RES)), int(np.ceil((y1 - y0) / RES))
height = np.zeros((nx, ny))                   # what stands on each square, above the floor
colour = np.zeros((nx, ny, 3), np.uint8)
corners = xyz[tri]
flat_top = (np.ptp(corners[:, :, 2], axis=1) < 1e-4) & (corners[:, 0, 2] > floor + 0.01)
for c, k in zip(corners[flat_top], np.nonzero(flat_top)[0]):
    a = np.floor((c[:, :2].min(0) - (x0, y0)) / RES + 0.5).astype(int)
    b = np.ceil((c[:, :2].max(0) - (x0, y0)) / RES - 0.5).astype(int)
    h = c[0, 2] - floor
    sl = (slice(max(a[0], 0), min(b[0], nx)), slice(max(a[1], 0), min(b[1], ny)))
    if h > height[sl].max(initial=0):
        colour[sl] = rgb[tri[k, 0]]
    height[sl] = np.maximum(height[sl], h)
taken = height > 0.02


def grow(mask, r):
    """Squares within r metres of a taken one (a disc of squares, by shifting)."""
    out = mask.copy(); n = int(np.ceil(r / RES))
    for di in range(-n, n + 1):
        for dj in range(-n, n + 1):
            if di * di + dj * dj <= (r / RES) ** 2:
                s = np.zeros_like(mask)
                s[max(di, 0):nx + min(di, 0), max(dj, 0):ny + min(dj, 0)] = mask[max(-di, 0):nx + min(-di, 0), max(-dj, 0):ny + min(-dj, 0)]
                out |= s
    return out


edge = np.zeros_like(taken); m = int(WALL_M / RES)
edge[:m] = edge[-m:] = True; edge[:, :m] = edge[:, -m:] = True
free = ~(grow(taken, BODY_M) | edge)
# How far each free square is from the nearest thing (for keeping to the middle of gaps).
clear = np.ones((nx, ny)); solid = taken | edge
for r in np.arange(1.0, BODY_M - 1e-9, -0.1):  # from far to near, so the nearest wins
    clear[grow(solid, r)] = r


def cell(p):
    return int((p[0] - x0) / RES), int((p[1] - y0) / RES)


def point(c):
    return [x0 + (c[0] + 0.5) * RES, y0 + (c[1] + 0.5) * RES]


def path(a, b):
    """Shortest walk over free squares from a to b (8 neighbours), cheaper away from things."""
    start, goal = cell(a), cell(b)
    best, came, todo = {start: 0.0}, {}, [(0.0, start)]
    steps = [(di, dj, np.hypot(di, dj)) for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]
    while todo:
        d, c = heapq.heappop(todo)
        if c == goal:
            break
        if d > best[c]:
            continue
        for di, dj, s in steps:
            n = (c[0] + di, c[1] + dj)
            if not (0 <= n[0] < nx and 0 <= n[1] < ny) or not free[n]:
                continue
            nd = d + s * (1 + 0.6 / clear[n])
            if nd < best.get(n, 1e18):
                best[n], came[n] = nd, c; heapq.heappush(todo, (nd, n))
    if goal not in best:
        raise SystemExit(f"no walk on open floor from {a} to {b}")
    cells = [goal]
    while cells[-1] != start:
        cells.append(came[cells[-1]])
    cells = cells[::-1]

    def sight(p, q):  # the straight line from p to q stays on free squares
        n = int(np.hypot(q[0] - p[0], q[1] - p[1]) * 2) + 2
        return all(free[int(round(p[0] + (q[0] - p[0]) * f)), int(round(p[1] + (q[1] - p[1]) * f))] for f in np.linspace(0, 1, n))
    keep = [cells[0]]; i = 0
    while i < len(cells) - 1:  # string-pulling: go as far as the line of sight allows
        j = len(cells) - 1
        while j > i + 1 and not sight(cells[i], cells[j]):
            j -= 1
        keep.append(cells[j]); i = j
    return [point(c) for c in keep]


print(f"floor {floor:.3f} m, outline x {x0:.2f}..{x1:.2f} y {y0:.2f}..{y1:.2f}, {free.mean():.0%} of the floor open")

# The largest piece of open floor (4 neighbours): every place goes on it, so every walk exists.
seen = np.zeros_like(free); best_piece = []
for s in map(tuple, np.argwhere(free)):
    if seen[s]:
        continue
    piece, todo = [], [s]; seen[s] = True
    while todo:
        c = todo.pop(); piece.append(c)
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + di, c[1] + dj)
            if 0 <= n[0] < nx and 0 <= n[1] < ny and free[n] and not seen[n]:
                seen[n] = True; todo.append(n)
    if len(piece) > len(best_piece):
        best_piece = piece
main = np.zeros_like(free); main[tuple(np.array(best_piece).T)] = True
free &= main


def spot(xs, prefer):
    """The roomiest open square with x in xs (metres), leaning towards `prefer` ([x, y]) on a tie."""
    cand = [c for c in best_piece if xs[0] <= point(c)[0] <= xs[1]]
    return [round(float(v), 3) for v in point(max(cand, key=lambda c: clear[c] - 0.05 * np.hypot(*np.subtract(point(c), prefer))))]


# Places along the open band from one end of the room to the other: the bed at one end (left, by the
# sofa), the bathroom door at the far end, and two places in between.
PLACES = {"bed": spot((x0, x0 + 1.3), (x0, 0)), "table": spot((-1.7, -0.7), (-1.2, 0.5)),
          "armchair": spot((-0.2, 0.7), (0.3, -0.6)), "bathroom": spot((x1 - 1.6, x1), (x1, 0))}
PLACES = {k: [*v, round(floor, 3)] for k, v in PLACES.items()}  # on the floor, in the mesh frame (z up)
print("places", PLACES)
WALKS = {}


def walk(a, b):
    WALKS[(a, b)] = w = path(PLACES[a][:2], PLACES[b][:2])
    return w


def night_track(day="2026-11-15"):
    """Up at 02:47 for the bathroom (the door opens and shuts), back at 02:56; up again at 03:08 to the
    table and the armchair, back to bed by 03:19, still in bed at 03:25. A fix every 5 s; the walker
    shuffles at SPEED between the corners of each walk; a few cm of wobble while moving."""
    t0 = datetime.fromisoformat(f"{day}T02:40:00+02:00")
    plan = [("bed", 7.0), ("bathroom", 7.0), ("bed", 12.0), ("table", 2.0), ("armchair", 4.0), ("bed", 9.0)]
    legs, t, at = [(0.0, PLACES["bed"][:2])], 0.0, "bed"   # (seconds, [x, y]) corners
    visits = []                                            # (place, arrived, left) in seconds
    for place, stay_min in plan:
        if place != at:
            w = walk(at, place)
            for p, q in zip(w, w[1:]):
                t += np.hypot(q[0] - p[0], q[1] - p[1]) / SPEED; legs.append((t, q))
        visits.append((place, t, t + stay_min * 60))
        t += stay_min * 60; legs.append((t, PLACES[place][:2])); at = place
    track, arrive = [], {}
    rng = np.random.default_rng(3)
    times = [s for s, _ in legs]
    for k, s in enumerate(np.arange(0, times[-1] + 1e-9, FIX_S)):
        i = min(np.searchsorted(times, s, side="right") - 1, len(legs) - 2)
        (ta, a), (tb, b) = legs[i], legs[i + 1]
        f = 0 if tb == ta else (s - ta) / (tb - ta)
        x, y = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        if np.hypot(b[0] - a[0], b[1] - a[1]) > 0.05:  # walking, not staying
            x, y = x + rng.normal(0, 0.02), y + rng.normal(0, 0.02)
        track.append({"t": (t0 + timedelta(seconds=float(s))).isoformat(timespec="seconds"), "p": [round(x, 3), round(y, 3), round(floor, 3)]})
    return track, visits


track, visits = night_track()
# Every straight step between two fixes stays off the blocks (checked on the 5 cm plan, with 10 cm to spare).
near_block = grow(taken, 0.10)
bad = 0
for a, b in zip(track, track[1:]):
    for f in np.linspace(0, 1, 12):
        c = cell((a["p"][0] + (b["p"][0] - a["p"][0]) * f, a["p"][1] + (b["p"][1] - a["p"][1]) * f))
        bad += bool(near_block[c])
print(f"{len(track)} fixes, {bad} samples of the steps within 10 cm of a block")


def at_min(m):
    return (datetime.fromisoformat("2026-11-15T02:40:00+02:00") + timedelta(minutes=m)).isoformat(timespec="seconds")


# Door events at the bathroom: the door opens as she gets there and shuts behind her, and again as she leaves.
arrive_bath, leave_bath = next((a / 60, b / 60) for k, a, b in visits if k == "bathroom")
events = [{"t": at_min(arrive_bath + 0.1), "type": "door_open", "place": "bathroom"},
          {"t": at_min(arrive_bath + 0.6), "type": "door_closed", "place": "bathroom"},
          {"t": at_min(leave_bath - 0.4), "type": "door_open", "place": "bathroom"},
          {"t": at_min(leave_bath + 0.3), "type": "door_closed", "place": "bathroom"}]
for e in events:  # whole seconds, like the sensors send
    e["t"] = datetime.fromisoformat(e["t"]).replace(microsecond=0).isoformat()
walked = night.analyse(track, PLACES, events, up="z", day="2026-11-15")
walked.update(places=PLACES, floor=round(floor, 3), example=True,
              note="An example night, made up for this room (night_demo.py): not a real recording.")
json.dump(walked, open(folder / "night.json", "w"), indent=1)
print("night.json", walked["summary"], [(x["start"][11:16], x["end"][11:16], x["visits"]) for x in walked["trips"]])

if "--plan" in sys.argv:  # a top-down picture of the floor plan, the places and the track
    from PIL import Image, ImageDraw
    Z = 4
    img = np.full((nx, ny, 3), 40, np.uint8)
    img[~taken & ~edge] = (120, 120, 120); img[free] = (200, 230, 200); img[taken] = colour[taken]
    im = Image.fromarray(np.flipud(img.transpose(1, 0, 2))).resize((nx * Z, ny * Z), Image.NEAREST)
    d = ImageDraw.Draw(im)
    px = lambda p: ((p[0] - x0) / RES * Z, (y1 - p[1]) / RES * Z)
    d.line([px(f["p"]) for f in track], fill=(220, 0, 200), width=2)
    for k, p in PLACES.items():
        X, Y = px(p); d.ellipse([X - 5, Y - 5, X + 5, Y + 5], outline=(0, 0, 255), width=2); d.text((X + 7, Y - 6), k, fill=(0, 0, 160))
    im.save(folder / "night_plan.png")
