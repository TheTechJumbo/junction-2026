"""Little things for the example home, as triangle meshes: laptops, plant pots, mugs, books, a lamp.

    Mesh().box / .para / .cyl / .sphere / .quad    shapes, in metres, z up
    Mesh.sample(spacing)                            points on the surface (for a Gaussian splat of it)
    living_room(m)                                  the living room's own things (inside demo_room's room)
    house()                                         the rest of the house around it: {part: Mesh}, labels

The living room's things stay off the night's walks (bed to bathroom, front door and kitchen).
"""
import math

import numpy as np

# Colours (0-255)
DARK, ALU, SCREEN, KEYS = (48, 50, 56), (168, 172, 178), (70, 120, 190), (36, 38, 42)
TERRACOTTA, SOIL, LEAF, LEAF2, LEAF3 = (184, 98, 64), (70, 50, 36), (62, 132, 70), (88, 160, 80), (40, 104, 60)
WHITE, CREAM, MUG, BROWN, OAK = (240, 240, 236), (236, 226, 204), (210, 80, 70), (120, 84, 56), (176, 136, 92)
STEEL, ORANGE, RED, YELLOW, GREEN = (190, 194, 200), (236, 140, 40), (200, 50, 50), (236, 204, 60), (120, 170, 60)
BOOKS = [(160, 60, 60), (60, 90, 150), (210, 170, 70), (70, 130, 100), (120, 80, 140), (220, 220, 210)]
CUSHION, CUSHION2, THROW, PILLOW, DUVET = (226, 196, 120), (180, 90, 90), (150, 170, 200), (250, 250, 248), (190, 208, 230)
FRAME, PICTURE, PICTURE2, LAMPSHADE = (60, 48, 40), (120, 170, 200), (220, 170, 120), (246, 232, 196)


class Mesh:
    def __init__(self):
        self.v, self.c, self.t = [], [], []

    def add(self, verts, tris, colour):
        base = len(self.v)
        self.v += [np.asarray(p, float) for p in verts]
        self.c += [colour] * len(verts)
        self.t += [(a + base, b + base, c + base) for a, b, c in tris]
        return self

    def extend(self, other):
        base = len(self.v)
        self.v += other.v; self.c += other.c
        self.t += [(a + base, b + base, c + base) for a, b, c in other.t]
        return self

    def quad(self, o, u, v, colour):
        o, u, v = (np.asarray(a, float) for a in (o, u, v))
        return self.add([o, o + u, o + u + v, o + v], [(0, 1, 2), (0, 2, 3)], colour)

    def para(self, o, a, b, c, colour, bottom=True):
        """A box along three edge vectors from corner o (any orientation)."""
        o, a, b, c = (np.asarray(x, float) for x in (o, a, b, c))
        p = [o, o + a, o + a + b, o + b, o + c, o + a + c, o + a + b + c, o + b + c]
        faces = [(4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)] + ([(0, 3, 2, 1)] if bottom else [])
        return self.add(p, [t for f in faces for t in ((f[0], f[1], f[2]), (f[0], f[2], f[3]))], colour)

    def box(self, lo, hi, colour, bottom=False):
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        return self.para((x0, y0, z0), (x1 - x0, 0, 0), (0, y1 - y0, 0), (0, 0, z1 - z0), colour, bottom)

    def cyl(self, base, axis, r0, r1, colour, sides=20, caps=(True, True)):
        """A (tapered) cylinder from base along axis, radius r0 at the base and r1 at the end."""
        base, axis = np.asarray(base, float), np.asarray(axis, float)
        w = axis / np.linalg.norm(axis)
        u = np.cross(w, (1, 0, 0) if abs(w[0]) < 0.9 else (0, 1, 0)); u /= np.linalg.norm(u)
        v = np.cross(w, u)
        ring = [(math.cos(2 * math.pi * k / sides), math.sin(2 * math.pi * k / sides)) for k in range(sides)]
        p = [base + r0 * (cx * u + cy * v) for cx, cy in ring] + [base + axis + r1 * (cx * u + cy * v) for cx, cy in ring]
        tris = []
        for k in range(sides):
            j = (k + 1) % sides
            tris += [(k, j, sides + j), (k, sides + j, sides + k)]
        p += [base, base + axis]
        if caps[0]:
            tris += [(2 * sides, j % sides, k) for k, j in ((k, k + 1) for k in range(sides))]
        if caps[1] and r1 > 0:
            tris += [(2 * sides + 1, sides + k, sides + (k + 1) % sides) for k in range(sides)]
        return self.add(p, tris, colour)

    def sphere(self, centre, radius, colour, rings=8, sides=14, squash=(1, 1, 1), half=False):
        cx, cy, cz = centre
        p, tris = [], []
        top = rings // 2 if half else rings
        for i in range(rings + 1):
            a = math.pi * i / rings
            for k in range(sides):
                b = 2 * math.pi * k / sides
                p.append((cx + radius * squash[0] * math.sin(a) * math.cos(b), cy + radius * squash[1] * math.sin(a) * math.sin(b),
                          cz + radius * squash[2] * math.cos(a) * (-1 if half else 1)))
        for i in range(top):
            for k in range(sides):
                j = (k + 1) % sides
                a, b, c, d = i * sides + k, i * sides + j, (i + 1) * sides + j, (i + 1) * sides + k
                tris += [(a, b, c), (a, c, d)]
        return self.add(p, tris, colour)

    def arrays(self):
        return (np.array(self.v, np.float32), np.array(self.c, np.uint8), np.array(self.t, np.int64))

    def sample(self, spacing, seed=3):
        """Points spread over the surface, about one per spacing x spacing: what a splat of it holds."""
        v, c, t = self.arrays()
        a, b, d = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
        area = 0.5 * np.linalg.norm(np.cross(b - a, d - a), axis=1)
        rng = np.random.default_rng(seed)
        n = rng.poisson(area / spacing ** 2) + (area > 0.2 * spacing ** 2)  # every visible face gets a point
        idx = np.repeat(np.arange(len(t)), n)
        r1, r2 = rng.random(len(idx)), rng.random(len(idx))
        flip = r1 + r2 > 1
        r1[flip], r2[flip] = 1 - r1[flip], 1 - r2[flip]
        pts = a[idx] + r1[:, None] * (b[idx] - a[idx]) + r2[:, None] * (d[idx] - a[idx])
        return pts.astype(np.float32), c[t[idx, 0]]


def turn(yaw):
    s, co = math.sin(yaw), math.cos(yaw)
    return lambda x, y: np.array((co * x - s * y, s * x + co * y, 0.0))


def laptop(m, at, yaw=0.0, colour=ALU, open_deg=105):
    """A laptop on a surface: base, keyboard, and the lid with its screen, hinged at the back."""
    r = turn(yaw)
    o = np.asarray(at, float) + r(-0.16, -0.11)
    w, d, base_h = r(0.32, 0), r(0, 0.22), np.array((0, 0, 0.016))
    m.para(o, w, d, base_h, colour)
    m.quad(o + base_h + 0.002 + r(0.025, 0.06), r(0.27, 0), r(0, 0.13), KEYS)        # keyboard
    m.quad(o + base_h + 0.002 + r(0.11, 0.012), r(0.10, 0), r(0, 0.04), DARK)         # trackpad
    tilt = math.radians(open_deg - 90)
    up = np.array((0, 0, 0.21 * math.cos(tilt))) + r(0, 0.21 * math.sin(tilt))
    hinge = o + d + base_h
    thick = r(0, 0.008)
    m.para(hinge, w, up, thick, colour)                                                  # lid
    face = up / np.linalg.norm(up)
    m.quad(hinge + r(0.015, -0.002) + 0.012 * face, r(0.29, 0), up * 0.88, SCREEN)       # the screen, lit


def pot_plant(m, at, pot_r=0.07, pot_h=0.11, leaves=5, size=0.09, seed=0):
    x, y, z = at
    m.cyl((x, y, z), (0, 0, pot_h), pot_r * 0.78, pot_r, TERRACOTTA, caps=(True, False))
    m.cyl((x, y, z + pot_h * 0.88), (0, 0, 0.001), pot_r * 0.95, pot_r * 0.95, SOIL, caps=(False, True))
    rng = np.random.default_rng(seed)
    for k in range(leaves):
        a = 2 * math.pi * k / leaves + rng.uniform(-0.3, 0.3)
        reach = size * rng.uniform(0.3, 0.7)
        m.sphere((x + reach * math.cos(a), y + reach * math.sin(a), z + pot_h + size * rng.uniform(0.5, 1.2)),
                 size * rng.uniform(0.55, 0.8), (LEAF, LEAF2, LEAF3)[k % 3], rings=6, sides=10, squash=(1, 1, 0.75))


def floor_plant(m, at, seed=1):
    """A tall house plant: a pot, a stem, and leaves up it."""
    x, y, z = at
    m.cyl((x, y, z), (0, 0, 0.34), 0.13, 0.18, TERRACOTTA, caps=(True, False))
    m.cyl((x, y, z + 0.30), (0, 0, 0.001), 0.17, 0.17, SOIL, caps=(False, True))
    m.cyl((x, y, z + 0.3), (0, 0, 0.75), 0.018, 0.012, BROWN, sides=8)
    rng = np.random.default_rng(seed)
    for k in range(9):
        a = 2 * math.pi * k / 9 * 2.3
        h = 0.55 + 0.6 * k / 9
        reach = 0.12 + rng.uniform(0, 0.12)
        m.sphere((x + reach * math.cos(a), y + reach * math.sin(a), z + h), 0.13 * rng.uniform(0.8, 1.15),
                 (LEAF, LEAF2, LEAF3)[k % 3], rings=6, sides=10, squash=(1.3, 0.55, 0.35))


def mug(m, at, colour=MUG):
    x, y, z = at
    m.cyl((x, y, z), (0, 0, 0.095), 0.04, 0.042, colour, caps=(True, False))
    m.cyl((x, y, z + 0.08), (0, 0, 0.001), 0.037, 0.037, (90, 60, 40), caps=(False, True))  # tea
    m.cyl((x + 0.04, y, z + 0.025), (0.028, 0, 0.045), 0.009, 0.009, colour, sides=6)


def books(m, at, n=5, yaw=0.0, lying=False, seed=2):
    """A row of books standing (or a pile lying), at a corner."""
    r = turn(yaw)
    rng = np.random.default_rng(seed)
    o = np.asarray(at, float)
    for k in range(n):
        h, d, w = rng.uniform(0.18, 0.26), rng.uniform(0.13, 0.17), rng.uniform(0.025, 0.045)
        colour = BOOKS[(k + seed) % len(BOOKS)]
        if lying:
            m.para(o + r(rng.uniform(-0.01, 0.01), rng.uniform(-0.01, 0.01)), r(h, 0), r(0, d), (0, 0, w), colour)
            o = o + np.array((0, 0, w))
        else:
            m.para(o, r(w, 0), r(0, d), (0, 0, h), colour)
            o = o + r(w + 0.003, 0)


def lamp(m, at):
    x, y, z = at
    m.cyl((x, y, z), (0, 0, 0.03), 0.14, 0.13, DARK)
    m.cyl((x, y, z + 0.03), (0, 0, 1.35), 0.012, 0.012, DARK, sides=8)
    m.cyl((x, y, z + 1.3), (0, 0, 0.26), 0.22, 0.13, LAMPSHADE, caps=(False, False))


def bookcase(m, x0, x1, y0, y1, h, shelves=(0.0, 0.4, 0.8, 1.2)):
    """Open shelves against a back wall at y1: two sides, a back, a top and the shelf boards."""
    m.box((x0, y0, 0), (x0 + 0.02, y1, h), OAK); m.box((x1 - 0.02, y0, 0), (x1, y1, h), OAK)
    m.box((x0, y1 - 0.015, 0), (x1, y1, h), BROWN)
    for z in shelves + (h - 0.02,):
        m.box((x0, y0, z), (x1, y1, z + 0.025), OAK)


def picture(m, x0, x1, z0, z1, y, colour):
    """A framed picture on the back wall (y), facing into the room."""
    m.box((x0, y - 0.025, z0), (x1, y, z1), FRAME)
    m.quad((x0 + 0.04, y - 0.026, z0 + 0.04), (x1 - x0 - 0.08, 0, 0), (0, 0, z1 - z0 - 0.08), colour)


def living_room(m):
    """The living room's things (demo_room's room: 5 x 4 m, back wall y = 4, left wall x = 0)."""
    # The table (top at 0.76): a laptop, a mug, a small plant and a pile of books.
    laptop(m, (2.05, 1.85, 0.76), yaw=0.15)
    mug(m, (2.45, 1.62, 0.76))
    pot_plant(m, (2.62, 2.02, 0.76), pot_r=0.055, pot_h=0.09, size=0.07, seed=4)
    books(m, (1.7, 1.48, 0.76), n=3, lying=True, yaw=0.1)
    # The kitchen counter (top at 0.9): a kettle, a fruit bowl, a chopping board, herbs on the windowside.
    m.cyl((3.15, 3.7, 0.9), (0, 0, 0.2), 0.085, 0.07, STEEL)
    m.cyl((3.15, 3.7, 1.1), (0, 0, 0.03), 0.05, 0.0, DARK)
    m.cyl((3.235, 3.7, 1.07), (0.05, 0, -0.08), 0.012, 0.012, DARK, sides=6)   # handle
    m.sphere((3.75, 3.62, 0.96), 0.13, CREAM, squash=(1, 1, 0.5), half=True)
    for (dx, dy, col) in ((-0.05, -0.02, ORANGE), (0.05, 0.03, RED), (0.0, 0.06, GREEN), (0.06, -0.05, YELLOW)):
        m.sphere((3.75 + dx, 3.62 + dy, 0.95), 0.042, col, rings=6, sides=10)
    m.box((4.05, 3.45, 0.9), (4.45, 3.72, 0.92), OAK)
    for k, x in enumerate((4.6, 4.78)):
        pot_plant(m, (x, 3.82, 0.9), pot_r=0.05, pot_h=0.08, size=0.06, seed=10 + k)
    # A laptop left on the sofa, and its cushions and a throw.
    m.box((3.5, 0.42, 0.45), (3.95, 0.58, 0.85), CUSHION)
    m.box((4.35, 0.42, 0.45), (4.8, 0.58, 0.85), CUSHION2)
    m.box((3.42, 0.6, 0.45), (3.75, 1.08, 0.47), THROW)
    laptop(m, (4.15, 0.82, 0.45), yaw=0.35, colour=DARK)
    # A tall plant in the front corner by the sofa, and a reading lamp in the other.
    floor_plant(m, (4.72, 1.42, 0.0))
    lamp(m, (0.3, 0.3, 0.0))
    # The bed: a pillow and the duvet folded back.
    m.box((0.12, 2.62, 0.5), (1.18, 3.4, 0.56), DUVET)
    m.box((0.2, 3.5, 0.5), (1.1, 3.88, 0.62), PILLOW)
    # A narrow bookcase between the bathroom door and the counter, full of books.
    bookcase(m, 2.42, 2.86, 3.66, 3.98, 1.6)
    for k, z in enumerate((0.03, 0.43, 0.83, 1.23)):
        books(m, (2.45, 3.7, z + 0.02), n=8 + (k % 2), seed=k)
    # On the walls: pictures over the bed and the table, and a clock.
    picture(m, 0.3, 0.95, 1.25, 1.75, 4.0, PICTURE)
    picture(m, 1.0, 1.4, 1.35, 1.75, 4.0, PICTURE2)
    m.cyl((2.65, 3.99, 1.95), (0, -0.03, 0), 0.12, 0.12, WHITE, sides=24)
    m.box((2.645, 3.955, 1.93), (2.655, 3.958, 2.04), DARK)
    m.box((2.65, 3.955, 1.945), (2.73, 3.958, 1.955), DARK)
    # The window sill (window over the bed, on the left wall) and two little pots on it.
    m.box((0.0, 2.72, 1.06), (0.16, 3.83, 1.1), WHITE)
    pot_plant(m, (0.08, 2.95, 1.1), pot_r=0.045, pot_h=0.07, size=0.05, seed=20)
    pot_plant(m, (0.08, 3.6, 1.1), pot_r=0.045, pot_h=0.07, size=0.05, seed=21)
    return m


# The rest of the house, around the living room (0..5 x 0..4). Rooms: name, (x0, y0, x1, y1), floor colour.
H = 2.5
ROOMS = [("Living room", (0, 0, 5, 4), (182, 160, 132)),
         ("Bathroom", (0, 4, 3, 6.4), (200, 214, 222)),
         ("Utility", (3, 4, 5, 6.4), (190, 190, 180)),
         ("Hall", (5, 0, 6.3, 6.4), (170, 140, 110)),
         ("Study", (6.3, 0, 9.5, 3.2), (160, 128, 96)),
         ("Spare bedroom", (6.3, 3.2, 9.5, 6.4), (196, 176, 160))]
# Walls: from, to, and the doors along them (distance from 'from', width). The living room's back and left
# walls are in the scan already; its front and right walls are not (the glasses never looked at them).
WALLS = [((0, 0), (5, 0), []), ((5, 0), (5, 4), [(1.5, 0.9)]),
         ((0, 4), (0, 6.4), []), ((0, 6.4), (9.5, 6.4), [(5.25, 0.8)]), ((9.5, 6.4), (9.5, 0), []),
         ((9.5, 0), (5, 0), [(3.4, 0.9)]), ((3, 4), (3, 6.4), []), ((5, 4), (5, 6.4), [(0.8, 0.8)]),
         ((6.3, 0), (6.3, 6.4), [(1.6, 0.8), (4.4, 0.8)]), ((6.3, 3.2), (9.5, 3.2), [])]
GLASS = (150, 190, 225)
WINDOWS = [((2.0, 0.0), (3.4, 0.0)), ((7.2, 0.0), (8.8, 0.0)), ((9.5, 4.2), (9.5, 5.6)), ((0.0, 5.0), (0.0, 5.8))]


def house():
    """The rest of the house: {'floors', 'walls', 'edges', 'things', 'outside': Mesh}, and room labels."""
    parts = {k: Mesh() for k in ("floors", "walls", "edges", "things", "outside")}
    for name, (x0, y0, x1, y1), colour in ROOMS[1:]:
        parts["floors"].quad((x0, y0, 0.002), (x1 - x0, 0, 0), (0, y1 - y0, 0), colour)
    for (ax, ay), (bx, by), doors in WALLS:
        a, b = np.array((ax, ay, 0.0)), np.array((bx, by, 0.0))
        length = np.linalg.norm(b - a); along = (b - a) / length
        side = np.array((-along[1], along[0], 0.0)) * 0.06
        cuts = [0.0] + [x for at, w in doors for x in (at, at + w)] + [length]
        for s, e in zip(cuts[::2], cuts[1::2]):
            parts["walls"].quad(a + s * along, (e - s) * along, (0, 0, H), (226, 230, 236))
            parts["edges"].quad(a + s * along - side / 2 + (0, 0, H), (e - s) * along, side, (240, 244, 250))
        for at, w in doors:  # a lintel over each door, and the threshold
            parts["walls"].quad(a + at * along + (0, 0, 2.05), w * along, (0, 0, H - 2.05), (226, 230, 236))
            parts["edges"].quad(a + at * along - side / 2 + (0, 0, H), w * along, side, (240, 244, 250))
            parts["things"].quad(a + at * along - side / 2 + (0, 0, 0.004), w * along, side, (120, 84, 56))
    for (ax, ay), (bx, by) in WINDOWS:
        a, b = np.array((ax, ay, 0.9)), np.array((bx, by, 0.9))
        parts["edges"].quad(a, b - a, (0, 0, 1.2), GLASS)
    t = parts["things"]
    # Bathroom: a bath along the left wall, the basin with a mirror, the toilet, a towel rail.
    t.box((0.08, 4.6, 0), (0.85, 6.32, 0.55), WHITE)
    t.box((0.16, 4.7, 0.3), (0.77, 6.22, 0.56), (170, 200, 220))
    t.box((1.5, 5.95, 0), (2.1, 6.35, 0.85), WHITE)
    t.box((1.6, 6.37, 1.05), (2.0, 6.39, 1.6), (200, 220, 235))
    t.box((2.5, 5.75, 0), (2.85, 6.2, 0.42), WHITE); t.box((2.48, 6.2, 0), (2.87, 6.36, 0.8), WHITE)
    t.cyl((2.9, 4.4, 1.2), (0, 0.6, 0), 0.015, 0.015, STEEL, sides=8)
    t.box((2.84, 4.45, 0.6), (2.88, 4.95, 1.18), (220, 120, 120))  # a towel
    # Utility: washing machine and dryer, a boiler, shelves.
    for x in (3.1, 3.72):
        t.box((x, 5.8, 0), (x + 0.6, 6.36, 0.85), WHITE)
        t.cyl((x + 0.3, 5.79, 0.45), (0, -0.01, 0), 0.17, 0.17, (120, 130, 140), sides=20)
    t.box((4.4, 6.0, 1.3), (4.9, 6.36, 2.0), (230, 230, 230))
    t.box((3.05, 4.05, 1.4), (3.4, 5.6, 1.43), OAK); t.box((3.05, 4.05, 1.8), (3.4, 5.6, 1.83), OAK)
    # Hall: coat hooks and coats, a shoe rack, a console table with a plant, a runner.
    t.box((5.35, 0.3, 0.004), (5.95, 5.9, 0.008), (150, 70, 70))
    t.box((6.18, 0.4, 0.0), (6.29, 1.3, 0.4), OAK)
    t.box((6.25, 0.45, 1.5), (6.29, 1.25, 1.55), OAK)
    for k, col in enumerate(((60, 80, 110), (150, 110, 70), (90, 60, 60))):
        t.box((6.12, 0.5 + 0.25 * k, 0.75), (6.26, 0.7 + 0.25 * k, 1.5), col)
    t.box((6.0, 2.8, 0.0), (6.28, 3.8, 0.8), OAK)
    pot_plant(t, (6.14, 3.5, 0.8), pot_r=0.07, pot_h=0.12, size=0.08, seed=30)
    # Study: a desk with a laptop, a chair, a bookcase, a plant.
    t.box((7.0, 2.55, 0.72), (8.6, 3.15, 0.76), OAK)
    for x, y in ((7.03, 2.58), (8.53, 2.58), (7.03, 3.08), (8.53, 3.08)):
        t.box((x, y, 0), (x + 0.04, y + 0.04, 0.72), OAK)
    laptop(t, (7.8, 2.85, 0.76), yaw=math.pi)
    mug(t, (8.3, 2.75, 0.76), colour=(60, 120, 170))
    t.cyl((7.8, 2.1, 0), (0, 0, 0.45), 0.25, 0.25, DARK, sides=5, caps=(False, False))
    t.box((7.55, 1.85, 0.45), (8.05, 2.35, 0.52), DARK); t.box((7.55, 1.82, 0.52), (8.05, 1.88, 1.0), DARK)
    t.box((9.15, 0.3, 0), (9.48, 1.6, 2.0), OAK)
    for k, z in enumerate((0.05, 0.5, 0.95, 1.4)):
        books(t, (9.46, 0.35, z + 0.02), n=10, yaw=math.pi / 2, seed=40 + k)
    floor_plant(t, (6.7, 0.4, 0.0), seed=5)
    # Spare bedroom: a double bed, bedside tables and lamps, a wardrobe.
    t.box((7.3, 4.6, 0), (8.9, 6.38, 0.5), (240, 236, 226))
    t.box((7.32, 4.62, 0.5), (8.88, 5.9, 0.58), (180, 150, 200))
    t.box((7.4, 5.95, 0.5), (8.05, 6.3, 0.62), PILLOW); t.box((8.15, 5.95, 0.5), (8.8, 6.3, 0.62), PILLOW)
    for x in (6.8, 8.95):
        t.box((x, 5.9, 0), (x + 0.45, 6.36, 0.55), OAK)
        t.cyl((x + 0.22, 6.13, 0.55), (0, 0, 0.3), 0.04, 0.02, DARK, sides=8)
        t.cyl((x + 0.22, 6.13, 0.8), (0, 0, 0.16), 0.13, 0.08, LAMPSHADE, caps=(False, False))
    t.box((9.0, 3.3, 0), (9.48, 4.6, 2.1), (220, 210, 196))
    # Outside: the lawn, the path to the front door (the living room's left wall), bushes, a bench.
    o = parts["outside"]
    o.quad((-5, -3, -0.01), (16.5, 0, 0), (0, 11.5, 0), (110, 160, 90))
    o.quad((-1.1, 0.45, 0.0), (1.1, 0, 0), (0, 1.25, 0), (190, 185, 176))       # the porch slab
    for k in range(5):
        o.quad((-1.9 - 0.75 * k, 0.75, 0.002), (0.55, 0, 0), (0, 0.65, 0), (176, 170, 160))
    for k, (x, y, r) in enumerate(((-1.5, 2.4, 0.45), (-2.6, -0.8, 0.55), (-0.8, -1.5, 0.4), (10.6, 1.0, 0.6), (10.4, 5.4, 0.5), (3.0, 7.6, 0.55))):
        o.sphere((x, y, r * 0.6), r, (LEAF, LEAF2, LEAF3)[k % 3], rings=6, sides=12, squash=(1, 1, 0.8))
    o.box((-1.0, 2.0, 0.0), (-0.6, 3.2, 0.45), OAK)
    labels = [{"name": n, "at": [(x0 + x1) / 2, (y0 + y1) / 2, 2.7]} for n, (x0, y0, x1, y1), _ in ROOMS]
    labels.append({"name": "Front path", "at": [-2.4, 1.1, 0.4]})
    return parts, labels
