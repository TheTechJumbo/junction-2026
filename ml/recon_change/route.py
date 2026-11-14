#!/usr/bin/env python3
"""A way to walk to a named place that goes around furniture and walls.

locate.py answers "how far and in which direction is the pill box": a straight line. This file
turns the floor plan a position fix already carries into a grid of where a person can walk, and
finds the shortest way on it from the wearer to each place. Plain Python, no numpy, so the relay
can run it on any laptop. Measured in eval/ROUTE_RESULTS.md.

What it works from (all in the fix, all in the map's east/north metres):
    map.points     the room's points seen from above, floor and ceiling removed (at most 1500)
    map.cameras    where the baseline camera stood, in the order the clip was recorded
    position       where the wearer stands

How a cell is decided (cells are CELL_M = 0.1 m squares):
    solid    a map point fell in it. One point is enough: the plan has 1500 points for a whole
             room, so one is often all a chair gets (dropping lone points as noise was measured
             and never did better).
    closed   solid, or closer than half a body to a solid cell, or outside the outline of
             everything the baseline saw (the convex hull of the points and the cameras): the route
             never leaves the part of the room the baseline looked at.
    open     everything else. NOTE: "open" means "nothing was seen there", not "the floor was
             seen there". The fix carries no floor, so an unseen gap looks the same as free floor.
             To lean towards floor that is known, cells far from where the baseline camera walked
             cost more (UNSEEN_COST), and so do cells within COMFORT_M of the body's edge.

How wide a person is: BODY_M = 0.6 m (shoulders plus a little room), in the MAP's metres. Those are
24% to 54% too long unless a known distance is set (eval/LOCATE_WALK_RESULTS.md), so today this is
about 0.4 to 0.5 true metres.

The route: one shortest-path search from the wearer over the open cells (Dijkstra, which is A*
without a guess: one search serves every place), then the cell path is cut down to its corners
(a corner is dropped when the straight line past it is open and no dearer). A place is usually ON
something (a shelf, a wall, a table), so the route ends at the open cell that is nearest to it,
at most REACH_M away, and the last leg is the reach from there to the place.

When there is no route the answer is the straight line, and it says why:
    "the map has too few points"       nothing to build a grid from
    "the map is too large"             more cells than MAX_CELLS even at 0.25 m cells
    "no open floor where you stand"    nothing open within START_SNAP_M of the wearer
    "no way through on the map"        nothing open within REACH_M of the place can be reached

    plan(fix) -> {"ok", "note", "cell_m", "body_m", "ms", "to": {place name: way}}
    way = {"way": "route" or "straight", "why", "points": [[east, north], ...] from the wearer to
           the place, "length_m" along those points, "straight_m", "reach_m" (the last leg),
           "corners" (turns on the way), "next": {"distance_m", "bearing_deg"} to the first point}
"""
import heapq
import math
import time

CELL_M = 0.10        # side of a grid cell
BODY_M = 0.60        # width of a person; cells closer than half of it to something solid are closed
COMFORT_M = 0.30     # beyond the body's edge, cells this close to something solid cost more
NEAR_COST = 1.0      # a cell right at the body's edge costs 1 + NEAR_COST times its length
UNSEEN_COST = 2.0    # a cell far from where the baseline camera walked costs up to this much more (see eval/ROUTE_RESULTS.md)
UNSEEN_FROM_M, UNSEEN_FULL_M = 0.5, 2.0  # "far" starts and is complete at these distances from the walked line
WALK_GAP_M = 2.5     # two baseline cameras in a row this close are joined: the camera was carried between them
KEEP_LONE = True     # a single map point with none beside it is solid too (False: it is dropped as noise)
CORRIDOR_M = None    # a number: cells farther than this from where the baseline camera walked (or the wearer stands) are closed
MERGE_M = 0.5        # corners closer together than this are made one
REACH_M = 1.2        # the route may end this far from the place
START_SNAP_M = 0.75  # the wearer may be this far from the nearest open cell (standing at a counter)
MIN_POINTS = 200     # fewer map points than this: no grid
MAX_CELLS = 60000    # the search is plain Python: larger maps get larger cells, up to 0.25 m
_A, _B = 0.95509, 1.36930  # distance of a straight and a diagonal step that best matches true distance


class Grid:
    """The walkable-space grid of one room. Cell (ix, iy) covers x0 + ix*cell .. + cell east and
    y0 + iy*cell .. + cell north. Lists are row by row: index = iy * w + ix."""
    __slots__ = ("x0", "y0", "cell", "w", "h", "solid", "closed", "cost", "clear", "body")

    def index(self, x, y):
        ix, iy = int(math.floor((x - self.x0) / self.cell)), int(math.floor((y - self.y0) / self.cell))
        return iy * self.w + ix if 0 <= ix < self.w and 0 <= iy < self.h else -1

    def centre(self, i):
        return (self.x0 + (i % self.w + 0.5) * self.cell, self.y0 + (i // self.w + 0.5) * self.cell)


def _pairs(rows):
    """[[x, y, ...], ...] -> [(x, y)] of the finite ones."""
    out = []
    for p in rows or []:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            out.append((x, y))
    return out


def _hull(points):
    """Convex hull, counter-clockwise (Andrew's monotone chain)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0]) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]

    return half(pts) + half(pts[::-1])


def _span(hull, y):
    """The stretch of x that a convex outline covers at height y, or None."""
    xs = []
    for i, (ax, ay) in enumerate(hull):
        bx, by = hull[(i + 1) % len(hull)]
        if ay == by:
            if ay == y:
                xs += [ax, bx]
        elif min(ay, by) <= y <= max(ay, by):
            xs.append(ax + (bx - ax) * (y - ay) / (by - ay))
    return (min(xs), max(xs)) if xs else None


def _spread_distance(dist, w, h):
    """dist is 0 at the sources and large elsewhere; afterwards every cell holds its distance in
    cells to the nearest source (two sweeps, within about 5% of the true distance)."""
    for iy in range(h):
        row = iy * w
        for ix in range(w):
            i = row + ix
            v = dist[i]
            if ix and dist[i - 1] + _A < v:
                v = dist[i - 1] + _A
            if iy:
                if dist[i - w] + _A < v:
                    v = dist[i - w] + _A
                if ix and dist[i - w - 1] + _B < v:
                    v = dist[i - w - 1] + _B
                if ix + 1 < w and dist[i - w + 1] + _B < v:
                    v = dist[i - w + 1] + _B
            dist[i] = v
    for iy in range(h - 1, -1, -1):
        row = iy * w
        for ix in range(w - 1, -1, -1):
            i = row + ix
            v = dist[i]
            if ix + 1 < w and dist[i + 1] + _A < v:
                v = dist[i + 1] + _A
            if iy + 1 < h:
                if dist[i + w] + _A < v:
                    v = dist[i + w] + _A
                if ix + 1 < w and dist[i + w + 1] + _B < v:
                    v = dist[i + w + 1] + _B
                if ix and dist[i + w - 1] + _B < v:
                    v = dist[i + w - 1] + _B
            dist[i] = v


def build(points, cameras=(), also=(), cell=CELL_M, body=BODY_M, comfort=COMFORT_M, near_cost=NEAR_COST,
          unseen_cost=UNSEEN_COST, keep_lone=KEEP_LONE, corridor=CORRIDOR_M):
    """The grid of one room from a fix's map. `also`: more spots known to be stood on (the wearer).
    Returns (grid, "") or (None, why not)."""
    pts, cams, more = _pairs(points), _pairs(cameras), _pairs(also)
    if len(pts) < MIN_POINTS:
        return None, "the map has too few points"
    # The frame: the points without their outermost 1% (strays far away would blow the grid up),
    # plus every spot someone stood on, plus a margin.
    xs, ys = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
    cut = len(pts) // 100
    stood = cams + more
    x_lo = min([xs[cut]] + [p[0] for p in stood]) - 0.5
    x_hi = max([xs[-1 - cut]] + [p[0] for p in stood]) + 0.5
    y_lo = min([ys[cut]] + [p[1] for p in stood]) - 0.5
    y_hi = max([ys[-1 - cut]] + [p[1] for p in stood]) + 0.5
    while (x_hi - x_lo) * (y_hi - y_lo) / (cell * cell) > MAX_CELLS:
        cell = round(cell + 0.05, 2)
        if cell > 0.25:
            return None, "the map is too large"
    g = Grid()
    g.x0, g.y0, g.cell, g.body = x_lo, y_lo, cell, body
    g.w, g.h = int(math.ceil((x_hi - x_lo) / cell)), int(math.ceil((y_hi - y_lo) / cell))
    w, h, n = g.w, g.h, g.w * g.h

    hits = [0] * n
    for x, y in pts:
        i = g.index(x, y)
        if i >= 0:
            hits[i] += 1
    g.solid = bytearray(n)
    for i in range(n):
        if not hits[i]:
            continue
        if hits[i] >= 2 or keep_lone:
            g.solid[i] = 1
            continue
        ix, iy = i % w, i // w
        for jy in range(max(0, iy - 1), min(h, iy + 2)):
            for jx in range(max(0, ix - 1), min(w, ix + 2)):
                if (jx != ix or jy != iy) and hits[jy * w + jx]:
                    g.solid[i] = 1
    solid = [i for i in range(n) if g.solid[i]]
    if len(solid) < 30:
        return None, "the map has too few points"

    # Outside the outline of everything seen: closed.
    hull = _hull([g.centre(i) for i in solid] + stood)
    inside = bytearray(n)
    for iy in range(h):
        y = g.y0 + (iy + 0.5) * cell
        spans = [s for s in (_span(hull, y - cell), _span(hull, y), _span(hull, y + cell)) if s]
        if not spans:
            continue
        lo = max(0, int(math.floor((min(s[0] for s in spans) - cell - g.x0) / cell)))
        hi = min(w - 1, int(math.floor((max(s[1] for s in spans) + cell - g.x0) / cell)))
        for ix in range(lo, hi + 1):
            inside[iy * w + ix] = 1

    # Distance to the nearest solid cell, and from it what is closed and what costs more.
    far = float(w + h)
    clear = [far] * n
    for i in solid:
        clear[i] = 0.0
    _spread_distance(clear, w, h)
    g.clear = clear = [c * cell for c in clear]

    # Where the baseline camera was carried: its positions, and the line between two in a row when
    # they are close and nothing solid lies between them. Known to be walkable, or close to it.
    walked = [far] * n
    for p in stood:
        i = g.index(*p)
        if i >= 0:
            walked[i] = 0.0
    for a, b in zip(cams, cams[1:]):
        gap = math.hypot(b[0] - a[0], b[1] - a[1])
        if gap > WALK_GAP_M:
            continue
        steps = max(1, int(gap / (cell * 0.5)))
        on = [g.index(a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps) for k in range(steps + 1)]
        if all(i >= 0 and not g.solid[i] for i in on):
            for i in on:
                walked[i] = 0.0
    _spread_distance(walked, w, h)

    half = body / 2
    g.closed = bytearray(n)
    g.cost = [1.0] * n
    for i in range(n):
        if not inside[i] or clear[i] < half or (corridor is not None and walked[i] * cell > corridor):
            g.closed[i] = 1
            continue
        near = max(0.0, 1.0 - (clear[i] - half) / comfort) if comfort > 0 else 0.0
        unseen = min(1.0, max(0.0, (walked[i] * cell - UNSEEN_FROM_M) / (UNSEEN_FULL_M - UNSEEN_FROM_M)))
        g.cost[i] = 1.0 + near_cost * near + unseen_cost * unseen
    return g, ""


def _open_near(g, x, y, limit):
    """The open cell whose centre is nearest to (x, y), within `limit` metres, or -1."""
    here = g.index(x, y)
    if here >= 0 and not g.closed[here]:
        return here
    r = int(math.ceil(limit / g.cell)) + 1
    cx, cy = int(math.floor((x - g.x0) / g.cell)), int(math.floor((y - g.y0) / g.cell))
    best, best_d = -1, limit
    for iy in range(max(0, cy - r), min(g.h, cy + r + 1)):
        for ix in range(max(0, cx - r), min(g.w, cx + r + 1)):
            i = iy * g.w + ix
            if g.closed[i]:
                continue
            px, py = g.centre(i)
            d = math.hypot(px - x, py - y)
            if d < best_d:
                best, best_d = i, d
    return best


def _search(g, start):
    """Cheapest way from the start cell to every open cell it can reach: (cost so far, the cell
    before) per cell. Eight directions; a diagonal step needs both cells beside it open, so the
    way never squeezes between two corners."""
    w, h, closed, cost = g.w, g.h, g.closed, g.cost
    inf = float("inf")
    dist, before = [inf] * (w * h), [-1] * (w * h)
    dist[start] = 0.0
    heap = [(0.0, start)]
    diagonal = math.sqrt(2.0)
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i]:
            continue
        ix, iy = i % w, i // w
        ci = cost[i]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            jx, jy = ix + dx, iy + dy
            if not (0 <= jx < w and 0 <= jy < h):
                continue
            j = jy * w + jx
            if closed[j]:
                continue
            if dx and dy and (closed[iy * w + jx] or closed[jy * w + ix]):
                continue
            nd = d + (diagonal if dx and dy else 1.0) * g.cell * (ci + cost[j]) / 2
            if nd < dist[j]:
                dist[j], before[j] = nd, i
                heapq.heappush(heap, (nd, j))
    return dist, before


def _line(g, a, b):
    """What it costs to walk straight from a to b, or None when the line leaves the open cells."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    steps = max(1, int(math.ceil(length / (g.cell * 0.35))))
    total = 0.0
    for k in range(steps + 1):
        i = g.index(a[0] + dx * k / steps, a[1] + dy * k / steps)
        if i < 0 or g.closed[i]:
            return None
        total += g.cost[i]
    return total / (steps + 1) * length


def _corners(g, cells, dist, first):
    """The cell path cut down to its corners. `first` is the exact spot inside the first cell.
    A stretch of the path is replaced by a straight line when that line is open and costs no more
    than the stretch did (so the line does not trade the path's clearance for a shortcut)."""
    pick = list(range(0, len(cells) - 1, 2)) + [len(cells) - 1] if len(cells) > 1 else [0]  # every second cell is enough
    spots = [first] + [g.centre(cells[k]) for k in pick[1:]]
    paid = [dist[cells[k]] for k in pick]  # what the cell path cost up to each spot
    keep, at = [0], 0
    while at < len(spots) - 1:
        nxt = at + 1
        for far in range(len(spots) - 1, at + 1, -1):
            price = _line(g, spots[at], spots[far])
            if price is not None and price <= (paid[far] - paid[at]) * 1.02 + 1e-6:
                nxt = far
                break
        keep.append(nxt)
        at = nxt
    # A corner the first pass left in for nothing (a small jog): drop it when the line past it is open and no dearer.
    changed = True
    while changed and len(keep) > 2:
        changed = False
        for k in range(1, len(keep) - 1):
            a, b, c = spots[keep[k - 1]], spots[keep[k]], spots[keep[k + 1]]
            past, one, two = _line(g, a, c), _line(g, a, b), _line(g, b, c)
            if past is not None and (one is None or two is None or past <= (one + two) * 1.02 + 1e-6):
                del keep[k]
                changed = True
                break
    # Corners closer together than MERGE_M are one turn to a person, and an arrow that jumps from one
    # to the next every step is no help. Drop one of them, or put a single corner halfway, wherever
    # the lines stay on open cells (the body's margin is kept; a little of the extra clearance is given up).
    line = [spots[k] for k in keep]
    k = 1
    while k < len(line) - 1:
        a, b, c = line[k - 1], line[k], line[k + 1]
        near_a, near_c = math.hypot(b[0] - a[0], b[1] - a[1]) < MERGE_M, math.hypot(c[0] - b[0], c[1] - b[1]) < MERGE_M
        if (near_a or near_c) and _line(g, a, c) is not None:
            del line[k]
            k = max(1, k - 1)
            continue
        if near_c and k + 2 < len(line):
            mid = ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)
            if _line(g, a, mid) is not None and _line(g, mid, line[k + 2]) is not None:
                line[k:k + 2] = [mid]
                k = max(1, k - 1)
                continue
        k += 1
    return line


def _straight(position, place, why):
    d = math.hypot(place[0] - position[0], place[1] - position[1])
    return {"way": "straight", "why": why, "points": [list(position), list(place)], "length_m": d, "straight_m": d,
            "reach_m": 0.0, "corners": 0}


def routes(g, position, targets, reach=REACH_M, snap=START_SNAP_M):
    """The way from `position` (east, north) to each target ({"name", "position"}) on a grid."""
    position = (float(position[0]), float(position[1]))
    places = [(t["name"], (float(t["position"][0]), float(t["position"][1]))) for t in targets if t.get("position")]
    start = _open_near(g, position[0], position[1], snap)
    if start < 0:
        return {name: _straight(position, place, "no open floor where you stand") for name, place in places}
    dist, before = _search(g, start)
    stepped = g.index(*position) != start  # the wearer stands in a closed cell: the first leg is the step out of it
    out = {}
    r = int(math.ceil(reach / g.cell)) + 1
    for name, place in places:
        # Where to stand: an open cell that can be reached, near the place. Nearness counts most;
        # the walk there counts a little, so the near side of a table wins over its far side.
        cx, cy = int(math.floor((place[0] - g.x0) / g.cell)), int(math.floor((place[1] - g.y0) / g.cell))
        best, best_score = -1, float("inf")
        for iy in range(max(0, cy - r), min(g.h, cy + r + 1)):
            for ix in range(max(0, cx - r), min(g.w, cx + r + 1)):
                i = iy * g.w + ix
                if dist[i] == float("inf"):
                    continue
                px, py = g.centre(i)
                d = math.hypot(px - place[0], py - place[1])
                if d <= reach and d + 0.15 * dist[i] < best_score:
                    best, best_score = i, d + 0.15 * dist[i]
        if best < 0:
            out[name] = _straight(position, place, "no way through on the map")
            continue
        cells = [best]
        while cells[-1] != start:
            cells.append(before[cells[-1]])
        cells.reverse()
        line = _corners(g, cells, dist, g.centre(start) if stepped else position)
        if stepped:
            line = [position] + line
        if g.index(*place) == best:  # the place itself is on open floor: walk right up to it
            line[-1] = place
            if len(line) == 1:
                line = [position, place]
        else:
            line.append(place)
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(line, line[1:]))
        out[name] = {"way": "route", "why": "", "points": [list(p) for p in line], "length_m": length,
                     "straight_m": math.hypot(place[0] - position[0], place[1] - position[1]),
                     "reach_m": 0.0 if g.index(*place) == best else math.hypot(line[-1][0] - line[-2][0], line[-1][1] - line[-2][1]),
                     "corners": max(0, len(line) - 2 - (0 if g.index(*place) == best else 1))}
    return out


def plan(fix, targets=None, **settings):
    """The way from the wearer of a position fix to each target (default: the fix's named places).
    Never raises on a strange fix: the answer then has ok False and a note."""
    t0 = time.time()
    out = {"ok": False, "note": "", "cell_m": settings.get("cell", CELL_M), "body_m": settings.get("body", BODY_M), "ms": 0, "to": {}}
    try:
        targets = [t for t in (fix.get("waypoints") or [] if targets is None else targets) if t.get("position") and t.get("name")]
        position, heading = fix.get("position"), float(fix.get("heading_deg") or 0)
        if fix.get("state") == "lost" or not position:
            out["note"] = "no position"
            return out
        room = fix.get("map") or {}
        # "points" is the floor plan every fix carries today. It is cut for drawing, and it misses
        # furniture (eval/ROUTE_RESULTS.md). A fix that also carries "obstacles" (the same kind of
        # list, cut for walking: everything from ankle to head height above the floor) is routed on that.
        g, why = build(room.get("obstacles") or room.get("points"), room.get("cameras") or (), also=[position], **settings)
        if g is None:
            ways = {t["name"]: _straight(position[:2], t["position"][:2], why) for t in targets}
            out["note"] = why
        else:
            ways = routes(g, position, targets)
            out.update(ok=True, cell_m=g.cell)
        for way in ways.values():
            first = way["points"][1]
            east, north = first[0] - position[0], first[1] - position[1]
            bearing = (math.degrees(math.atan2(east, north)) - heading + 180) % 360 - 180  # 0 ahead, + to the right
            way["next"] = {"distance_m": round(math.hypot(east, north), 2), "bearing_deg": int(round(bearing))}
            way["points"] = [[round(v, 2) for v in p] for p in way["points"]]
            for key in ("length_m", "straight_m", "reach_m"):
                way[key] = round(way[key], 2)
        out["to"] = ways
    except Exception as error:  # a route is an extra: a fix must go out without one rather than fail
        out.update(ok=False, note=f"route finding failed: {type(error).__name__}", to={})
    out["ms"] = int(round((time.time() - t0) * 1000))
    return out
