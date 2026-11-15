"""Draws every SVG in themes/talviryijy/assets from knot charts.

Talviryijy is a ryijy rug knotted with the Kaamos courtyard: snowy tin roofs, chimneys, the
cathedral dome far off, the neighbours' windows and Anna's lamp-lit window. Everything sits on a
knot grid (one knot = 10 units in scene.svg), so every edge is a step.

    python source/gen.py            (run from themes/talviryijy)

Writes assets/scene.svg, assets/mark.svg, assets/icons.svg, assets/band.svg, assets/band-notice.svg.
"""
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, '..', 'assets')
random.seed(1411)

# ---------- Wools (theme.css takes its tokens from these) ----------
NIGHT_SKY = '#0b1029'   # sky at the top of the rug at night
SKY_MID = '#16204a'
BLUE_HOUR = '#1d2a4a'   # sky by the roofs at night
WALL = '#141c38'        # Anna's house and the house on the right
WALL_FAR = '#1b2547'    # the houses further back
GLASS = '#0d1430'       # unlit windows
SLATE = '#3b4763'       # tin roofs
SEAM = '#2c3650'        # roof seams, chimneys, gutters, window frames
FAR = '#26345c'         # the cathedral and the far blocks
FAR_2 = '#202c52'
SNOW = '#eef2f6'
SNOW_SHADE = '#c3cee0'
LAMP = '#f2b04a'        # Anna's lamp: the one warm accent
LAMP_DEEP = '#d9822b'   # the corners of her lit room; her curtains
LAMP_HOT = '#ffe2a6'    # the bulb
SHADE = '#fff1cf'       # the lampshade
WIN_COLD = '#8fa3c8'    # the neighbours' lit windows
ROSE = '#d99a9e'        # horizon at low sun
GOLD = '#f0c27e'
SUN = '#f7d9a0'
MOON = '#e9edf5'
JUNIPER = '#2e4a36'     # the courtyard spruces; calm fields
JUNIPER_LIT = '#5f8a68'
INDIGO = '#2c3d6b'      # border band ground
MADDER = '#9c2f2a'      # border diamonds on a day to notice
BARK = '#3a2f2a'

K = 10                  # one knot
COLS, ROWS = 160, 160   # scene.svg is 1600 x 1600 units, anchored at the bottom of the hero


def runs_path(cells, size=K, ox=0, oy=0):
    """Merge a set of (col, row) knots into horizontal runs, as one path."""
    rows = {}
    for c, r in cells:
        rows.setdefault(r, []).append(c)
    d = []
    for r in sorted(rows):
        cs = sorted(set(rows[r]))
        start = prev = cs[0]
        for c in cs[1:] + [None]:
            if c is not None and c == prev + 1:
                prev = c
                continue
            w = (prev - start + 1) * size
            d.append(f'M{ox + start * size:g} {oy + r * size:g}h{w:g}v{size:g}h{-w:g}z')
            if c is not None:
                start = prev = c
    return ''.join(d)


def chart(rows, mapping, at=(0, 0)):
    """ASCII chart -> {class: set(cells)}; '.' and ' ' are empty."""
    out = {}
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            if ch in mapping:
                out.setdefault(mapping[ch], set()).add((at[0] + c, at[1] + r))
    return out


# =====================================================================================
# scene.svg
# =====================================================================================
class Canvas:
    """A knot raster where later knots cover earlier ones."""

    def __init__(self):
        self.k = {}

    def put(self, c, r, cls):
        if 0 <= c < COLS and 0 <= r < ROWS:
            self.k[(c, r)] = cls

    def rect(self, c0, r0, c1, r1, cls):  # inclusive
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                self.put(c, r, cls)

    def stamp(self, groups):
        for cls, cells in groups.items():
            for c, r in cells:
                self.put(c, r, cls)

    def by_class(self):
        out = {}
        for cell, cls in self.k.items():
            out.setdefault(cls, set()).add(cell)
        return out


town = Canvas()


def roof(x1, x2, x3, x4, ye, yr, chimneys=(), far=False):
    """Pitched tin roof seen side on: eaves x1..x4 on row ye, ridge x2..x3 on row yr."""
    tin = 'tr-roof'
    for cc, cw, ch in chimneys:  # chimneys stand behind the roof
        top = yr - ch
        town.rect(cc, top, cc + cw - 1, yr + 1, 'tr-chimney')
        town.rect(cc - 1, top - 1, cc + cw, top - 1, 'tr-snow')
    h = ye - yr
    # snow line: a slow random walk across the roof
    line = {}
    depth = h * 0.5
    for c in range(x1, x4 + 1):
        if random.random() < .45:
            depth = min(h * .72, max(h * .3, depth + random.choice((-1, 1))))
        line[c] = yr + round(depth)
    for r in range(yr, ye):
        t = (r + .5 - yr) / h
        left = x2 + (x1 - x2) * t
        right = x3 + (x4 - x3) * t
        for c in range(x1, x4 + 1):
            if left <= c + .5 <= right:
                if r < line[c]:
                    cls = 'tr-snow'
                elif r == line[c] and (c % 2 == 0):
                    cls = 'tr-snow'  # the snow edge, knotted ragged
                else:
                    cls = 'tr-seam' if c % 3 == 0 else tin
                town.put(c, r, cls)
    town.rect(x1, ye, x4, ye, 'tr-gutter')
    # a lip of snow on the gutter, broken
    for c in range(x1 + 1, x4):
        if random.random() < .35:
            town.put(c, ye, 'tr-snow-shade')
    wall_cls = 'tr-wall-far' if far else 'tr-wall'
    town.rect(x1 + 1, ye + 1, x4 - 1, ROWS - 1, wall_cls)


def windows(cols, row, w=3, h=4, lit=None):
    lit = lit or {}
    for i, c in enumerate(cols):
        cls = 'tr-win' + ((' ' + lit[i]) if i in lit else '')
        town.rect(c, row, c + w - 1, row + h - 1, cls)
        town.rect(c, row + h, c + w - 1, row + h, 'tr-sill')


# Building A (left, low), B (left, overlapping), C (low, in front of the cathedral)
roof(-2, 4, 25, 31, 151, 145, chimneys=[(7, 2, 3), (20, 2, 2)], far=True)
windows([2, 9, 16, 23], 154, lit={2: 'tr-win-early'})
roof(25, 31, 55, 61, 152, 146, chimneys=[(33, 2, 4), (50, 2, 3)], far=True)
windows([29, 36, 43, 50, 56], 155, lit={0: 'tr-win-late', 3: 'tr-win-early'})

# Far skyline and the cathedral (Tuomiokirkko), behind C
far = Canvas()
sky_line = [(0, 150), (7, 149), (15, 150), (24, 148), (30, 149), (42, 147), (47, 148), (56, 147), (64, 149),
            (90, 147), (98, 148), (108, 146), (116, 147), (130, 145), (140, 146), (160, 146)]
for (c0, r0), (c1, _) in zip(sky_line, sky_line[1:] + [(160, 0)]):
    far.rect(c0, r0, c1 - 1, ROWS - 1, 'tr-far-2')
CATHEDRAL = [
    '..........X..........',
    '.........XXX.........',
    '.........XXX.........',
    '.......XXXXXXX.......',
    '......XXXXXXXXX......',
    '.....XXXXXXXXXXX.....',
    '.....XXXXXXXXXXX.....',
    '.....XXXXXXXXXXX.....',
    '......XXXXXXXXX......',
    '.X....XX.XXX.XX....X.',
    'XXX...XXXXXXXXX...XXX',
    'XXX..XXXXXXXXXXX..XXX',
    'XXX.XXXXXXXXXXXXX.XXX',
    'XXXXXXXXXXXXXXXXXXXXX',
    'XXXX.XX.XX.XX.XX.XXXX',
    'XXXXXXXXXXXXXXXXXXXXX',
    'XXXXXXXXXXXXXXXXXXXXX',
    'XXXXXXXXXXXXXXXXXXXXX',
    'XXXXXXXXXXXXXXXXXXXXX',
]
far.stamp(chart(CATHEDRAL, {'X': 'tr-cathedral'}, at=(65, 134)))

roof(59, 64, 87, 92, 153, 148, chimneys=[(81, 2, 3)], far=True)
windows([63, 70, 77, 84], 156, h=3, lit={1: 'tr-win-late'})

# Building E (right)
roof(124, 129, 162, 166, 143, 137, chimneys=[(134, 2, 4), (152, 3, 3)])
windows([129, 136, 143, 150, 157], 146, lit={1: 'tr-win-late', 4: 'tr-win-early'})
windows([129, 136, 143, 150, 157], 153, lit={2: 'tr-win-late'})

# Building D: Anna's house, in front
roof(88, 94, 122, 128, 140, 132, chimneys=[(97, 2, 4), (117, 3, 5)])
town.rect(88, 141, 88, ROWS - 1, 'tr-seam')
town.rect(128, 141, 128, ROWS - 1, 'tr-seam')
windows([92, 98], 143, w=4, h=5, lit={1: 'tr-win-late'})
windows([114, 120], 143, w=4, h=5, lit={0: 'tr-win-early'})
windows([92, 98, 106, 114, 120], 152, w=4, h=5, lit={0: 'tr-win-late', 3: 'tr-win-late'})

# Two snowy spruces in the courtyard, between B and C
SPRUCE = [
    '...S...',
    '..SJS..',
    '..JLJ..',
    '.SJJJS.',
    '.JJLJJ.',
    'SJJJJJS',
    'JJLJJLJ',
    '.JJJJJ.',
    '...T...',
]
town.stamp(chart(SPRUCE, {'S': 'tr-snow', 'J': 'tr-juniper', 'L': 'tr-juniper-lit', 'T': 'tr-bark'}, at=(55, 151)))
town.stamp(chart(SPRUCE[2:], {'S': 'tr-snow', 'J': 'tr-juniper', 'L': 'tr-juniper-lit', 'T': 'tr-bark'}, at=(61, 153)))

# Anna's window: 9 x 10 knots with a frame, the lamp, her curtains and a plant
AWX, AWY = 104, 142
ANNA = [
    'FFFFFFFFF',
    'FCLLLLLCF',
    'FCLHHLLCF',
    'FCHSSHLCF',
    'FCLSSLLCF',
    'FCLLDLPCF',
    'FCLDDDPCF',
    'FCLLLLLCF',
    'FFFFFFFFF',
]
anna = chart(ANNA, {'F': 'tr-anna-frame', 'C': 'tr-curtain', 'L': 'tr-anna-room', 'H': 'tr-anna-hot',
                    'S': 'tr-lampshade', 'D': 'tr-anna-deep', 'P': 'tr-plant'}, at=(AWX, AWY))
anna['tr-snow'] = {(c, AWY + 9) for c in range(AWX - 1, AWX + 10)}

# Lamp halo: stepped, dithered rings of lamp-coloured knots round her window
halo = {}
hcx, hcy = AWX + 4.5, AWY + 4.5
BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]  # ordered dither, as a weaver would space knots
RINGS = (('tr-halo-1', 5.5, 7.5, 9), ('tr-halo-2', 7.5, 10, 5), ('tr-halo-3', 10, 12.5, 3), ('tr-halo-4', 12.5, 16, 2))
for r in range(ROWS):
    for c in range(COLS):
        dx, dy = abs(c + .5 - hcx), abs(r + .5 - hcy) * 1.1
        d = max(dx, dy) + .45 * min(dx, dy)
        for name, lo, hi, density in RINGS:
            if lo <= d < hi and BAYER[r % 4][c % 4] < density:
                halo.setdefault(name, set()).add((c, r))

# ---------- the sky ----------
SKY_BANDS = [(0, 'tr-sky-0'), (92, 'tr-sky-1'), (104, 'tr-sky-2'), (114, 'tr-sky-3'), (123, 'tr-sky-4'), (131, 'tr-sky-5')]
sky_dither = {}
for (b0, cls0), (b1, cls1) in zip(SKY_BANDS, SKY_BANDS[1:]):
    for c in range(COLS):
        if c % 2 == b1 % 2:
            sky_dither.setdefault(cls1, set()).add((c, b1 - 1))
        if c % 4 == 0:
            sky_dither.setdefault(cls1, set()).add((c, b1 - 2))
        if c % 4 == 2:
            sky_dither.setdefault(cls0, set()).add((c, b1))


def dithered(rows_full, rows_half, rows_quarter, cls, shift=0):
    cells = set()
    for r in rows_full:
        cells |= {(c, r) for c in range(COLS)}
    for r in rows_half:
        cells |= {(c, r) for c in range(COLS) if (c + r + shift) % 2 == 0}
    for r in rows_quarter:
        cells |= {(c, r) for c in range(COLS) if (c + shift) % 4 == (r % 2) * 2}
    return cells


glow_dusk = {
    'tr-glow-rose': dithered(range(134, 141), range(131, 134), range(128, 131), 'r'),
    'tr-glow-gold': dithered(range(143, 160), range(140, 143), range(138, 140), 'g', shift=1),
}
glow_day = {'tr-glow-day': dithered(range(142, 160), range(137, 142), range(131, 137), 'd')}

# Stars: single knots and a few bright crosses, kept off the text. tr-stars-wide only shows on wide
# screens, where the headline sits to the left of them; on a phone that sky is behind the words.
stars, twinkles, wide = set(), set(), set()
spots = [(76, 124), (84, 121), (90, 127), (79, 130), (101, 123), (108, 126), (112, 121), (125, 125),
         (132, 104), (139, 112), (145, 106), (151, 117), (156, 103), (136, 123), (148, 128), (154, 133),
         (12, 140), (40, 141), (25, 137), (52, 139), (64, 133), (70, 140)]
for i, (c, r) in enumerate(spots):
    (twinkles if i % 4 == 0 else stars).add((c, r))
for c, r in [(146, 116), (95, 119)]:  # bright ones, as small crosses
    stars |= {(c, r), (c - 1, r), (c + 1, r), (c, r - 1), (c, r + 1)}
for c, r in [(80, 92), (88, 104), (95, 96), (103, 110), (110, 99), (117, 114), (123, 106), (99, 88), (85, 113)]:
    wide.add((c, r))

MOON_CHART = [
    '..MMMM.',
    '.MMM...',
    'MMM....',
    'MMM....',
    'MMM....',
    'MMM....',
    '.MMM...',
    '..MMMM.',
]
moon = chart(MOON_CHART, {'M': 'tr-moon-disc'}, at=(119, 109))
moon_halo = set()
for r in range(100, 127):
    for c in range(110, 132):
        d = math.hypot(c + .5 - 121.5, (r + .5 - 113) * 1.0)
        if 5.5 <= d < 8.5 and BAYER[r % 4][c % 4] < 3:
            moon_halo.add((c, r))

# The sun, drawn round the origin; theme.css moves it along the day's arc, snapped to whole knots
sun_disc, sun_halo_in, sun_halo_out = set(), set(), set()
for r in range(-7, 8):
    for c in range(-7, 8):
        d = math.hypot(c + .5, r + .5)
        if d < 2.9:
            sun_disc.add((c, r))
        elif d < 4.6 and (c + r) % 2 == 0:
            sun_halo_in.add((c, r))
        elif d < 6.8 and (c % 2 == 0 and r % 2 == 0):
            sun_halo_out.add((c, r))

# Smoke from Anna's warm chimney, a few loose knots that rise
smoke = [(118, 124), (119, 121), (118, 118), (120, 115)]

# Loose snow knots, drifting down over the roofs (never over the words)
flakes = []
while len(flakes) < 16:
    c, r = random.randrange(2, COLS - 2), random.randrange(100, 148)
    if r < 122 and not (c >= 128 and r >= 102):
        continue
    if c < 76 and r < 137:
        continue
    flakes.append((c, r))

# ---------- write scene.svg ----------
o = []
a = o.append
W = H = COLS * K
a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMax meet" class="tr-scene" shape-rendering="crispEdges" role="presentation">')
a('<defs>')
a('<radialGradient id="talviryijy-pile-g" cx=".45" cy=".4" r=".75">'
  '<stop offset="0" stop-color="#ffffff" stop-opacity=".09"/>'
  '<stop offset=".55" stop-color="#ffffff" stop-opacity="0"/>'
  '<stop offset="1" stop-color="#000000" stop-opacity=".26"/></radialGradient>')
a(f'<pattern id="talviryijy-pile" width="{K}" height="{K}" patternUnits="userSpaceOnUse">'
  f'<rect width="{K}" height="{K}" fill="url(#talviryijy-pile-g)"/></pattern>')
a('</defs>')

SKY_FILLS = {'tr-sky-0': NIGHT_SKY, 'tr-sky-1': '#111843', 'tr-sky-2': SKY_MID, 'tr-sky-3': '#18234b',
             'tr-sky-4': '#1b2749', 'tr-sky-5': BLUE_HOUR}
a('<g class="tr-sky">')
for (b0, cls), nxt in zip(SKY_BANDS, SKY_BANDS[1:] + [(ROWS, None)]):
    a(f'<rect class="{cls}" x="0" y="{b0 * K}" width="{W}" height="{(nxt[0] - b0) * K}" fill="{SKY_FILLS[cls]}"/>')
for cls, cells in sky_dither.items():
    a(f'<path class="{cls}" fill="{SKY_FILLS[cls]}" d="{runs_path(cells)}"/>')
a('</g>')

a('<g class="tr-glow tr-glow-dusk">')
a(f'<path class="tr-glow-rose" fill="{ROSE}" d="{runs_path(glow_dusk["tr-glow-rose"])}"/>')
a(f'<path class="tr-glow-gold" fill="{GOLD}" d="{runs_path(glow_dusk["tr-glow-gold"])}"/>')
a('</g>')
a(f'<g class="tr-glow tr-glow-day"><path fill="{SNOW}" d="{runs_path(glow_day["tr-glow-day"])}"/></g>')

a('<g class="tr-stars">')
a(f'<path class="tr-star" fill="{SNOW}" d="{runs_path(stars)}"/>')
a(f'<path class="tr-star tr-twinkle" fill="{SNOW}" d="{runs_path(twinkles)}"/>')
a(f'<path class="tr-star tr-stars-wide" fill="{SNOW}" d="{runs_path(wide)}"/>')
a('</g>')
a('<g class="tr-moon">')
a(f'<path class="tr-moon-halo" fill="{MOON}" opacity=".16" d="{runs_path(moon_halo)}"/>')
a(f'<path class="tr-moon-disc" fill="{MOON}" d="{runs_path(moon["tr-moon-disc"])}"/>')
a('</g>')

a('<g class="tr-sun">')
a(f'<path class="tr-sun-halo-out" fill="{ROSE}" opacity=".55" d="{runs_path(sun_halo_out)}"/>')
a(f'<path class="tr-sun-halo-in" fill="{GOLD}" opacity=".8" d="{runs_path(sun_halo_in)}"/>')
a(f'<path class="tr-sun-disc" fill="{SUN}" d="{runs_path(sun_disc)}"/>')
a('</g>')

FILLS = {
    'tr-far-2': FAR_2, 'tr-cathedral': FAR, 'tr-roof': SLATE, 'tr-seam': SEAM, 'tr-gutter': SEAM, 'tr-chimney': SLATE,
    'tr-snow': SNOW, 'tr-snow-shade': SNOW_SHADE, 'tr-wall': WALL, 'tr-wall-far': WALL_FAR, 'tr-sill': SNOW_SHADE,
    'tr-juniper': JUNIPER, 'tr-juniper-lit': JUNIPER_LIT, 'tr-bark': BARK,
    'tr-anna-frame': SEAM, 'tr-curtain': LAMP_DEEP, 'tr-anna-room': LAMP, 'tr-anna-hot': LAMP_HOT,
    'tr-lampshade': SHADE, 'tr-anna-deep': LAMP_DEEP, 'tr-plant': JUNIPER,
}
a('<g class="tr-far">')
for cls, cells in sorted(far.by_class().items()):
    a(f'<path class="{cls}" fill="{FILLS[cls]}" d="{runs_path(cells)}"/>')
a('</g>')

a('<g class="tr-town">')
order = ['tr-wall-far', 'tr-wall', 'tr-chimney', 'tr-roof', 'tr-seam', 'tr-gutter', 'tr-snow', 'tr-snow-shade',
         'tr-sill', 'tr-juniper', 'tr-juniper-lit', 'tr-bark']
groups = town.by_class()
for cls in order:
    if cls in groups:
        a(f'<path class="{cls}" fill="{FILLS[cls]}" d="{runs_path(groups[cls])}"/>')
for cls in sorted(k for k in groups if k.startswith('tr-win')):
    a(f'<path class="{cls}" fill="{GLASS}" d="{runs_path(groups[cls])}"/>')
missing = set(groups) - set(order) - {k for k in groups if k.startswith('tr-win')}
assert not missing, missing
a('</g>')

a('<g class="tr-halo">')
for name, op in (('tr-halo-4', .45), ('tr-halo-3', .5), ('tr-halo-2', .6), ('tr-halo-1', .7)):
    a(f'<path class="{name}" fill="{LAMP}" opacity="{op}" d="{runs_path(halo[name])}"/>')
a('</g>')

a('<g class="tr-anna">')
for cls in ['tr-anna-frame', 'tr-anna-room', 'tr-anna-deep', 'tr-curtain', 'tr-anna-hot', 'tr-lampshade', 'tr-plant', 'tr-snow']:
    a(f'<path class="{cls}" fill="{FILLS[cls]}" d="{runs_path(anna[cls])}"/>')
a('</g>')

a('<g class="tr-smoke">')
for i, (c, r) in enumerate(smoke):
    a(f'<rect class="tr-puff tr-puff-{i + 1}" x="{c * K}" y="{r * K}" width="{K}" height="{K}" fill="{SNOW_SHADE}"/>')
a('</g>')

# the pile: every knot a little raised, the backing showing between them
a(f'<rect class="tr-pile" x="0" y="0" width="{W}" height="{H}" fill="url(#talviryijy-pile)"/>')

a('<g class="tr-snowfall">')
for i, (c, r) in enumerate(flakes):
    a(f'<rect class="tr-flake tr-flake-{i % 6 + 1}" x="{c * K}" y="{r * K}" width="{K}" height="{K}" fill="{SNOW}"/>')
a('</g>')
a('</svg>')
with open(os.path.join(ASSETS, 'scene.svg'), 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(o) + '\n')

# =====================================================================================
# mark.svg: a knotted roof with a lamp window (12 x 12 knots, no ids: it is inserted twice)
# =====================================================================================
MARK = [
    '..IIIIIIII..',
    '.IIIIIIIIWI.',
    'IIIIIIICCIII',
    'IIIIISSCCIII',
    'IIIISSSSIIII',
    'IIISSSSSSIII',
    'IISSSSSSSSII',
    'ITTTTTTTTTTI',
    'IIDDDDDDDDII',
    'IIDDLLDDDDII',
    'IIDDHLDDDDII',
    '.IDDDDDDDDI.',
]
mk = chart(MARK, {'I': ('tr-mark-ground', INDIGO), 'W': ('tr-mark-star', SNOW), 'S': ('tr-mark-snow', SNOW),
                  'C': ('tr-mark-chimney', SLATE), 'T': ('tr-mark-roof', SLATE), 'D': ('tr-mark-wall', WALL),
                  'L': ('tr-mark-lamp', LAMP), 'H': ('tr-mark-hot', LAMP_HOT)})
m = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" class="tr-mark" shape-rendering="crispEdges">']
for (cls, fill), cells in mk.items():
    m.append(f'<path class="{cls}" fill="{fill}" d="{runs_path(cells, 8)}"/>')
m.append('</svg>')
with open(os.path.join(ASSETS, 'mark.svg'), 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(m) + '\n')

# =====================================================================================
# icons.svg: 14 symbols on a 12 x 12 stitch grid, filled with currentColor
# =====================================================================================
ICONS = {
    'pill': [
        '............',
        '............',
        '............',
        '..XXXXXXXX..',
        '.XXXXX...XX.',
        '.XXXXX....X.',
        '.XXXXX....X.',
        '.XXXXX...XX.',
        '..XXXXXXXX..',
        '............',
        '............',
        '............',
    ],
    'door': [
        '............',
        '..XXXXXXXX..',
        '..X......X..',
        '..X......X..',
        '..X......X..',
        '..X......X..',
        '..X....XXX..',
        '..X......X..',
        '..X......X..',
        '..X......X..',
        'XXXXXXXXXXXX',
        '............',
    ],
    'eye': [
        '............',
        '............',
        '....XXXX....',
        '..XX....XX..',
        '.X...XX...X.',
        'X...XXXX...X',
        'X...XXXX...X',
        '.X...XX...X.',
        '..XX....XX..',
        '....XXXX....',
        '............',
        '............',
    ],
    'question': [
        '...XXXXXX...',
        '..XX....XX..',
        '..XX....XX..',
        '........XX..',
        '.......XX...',
        '......XX....',
        '.....XX.....',
        '.....XX.....',
        '............',
        '.....XX.....',
        '.....XX.....',
        '............',
    ],
    'alert': [
        '.....XX.....',
        '....XXXX....',
        '...XX..XX...',
        '..XX.XX.XX..',
        '.XX..XX..XX.',
        'XX...XX...XX',
        'XX........XX',
        '.XX..XX..XX.',
        '..XX.XX.XX..',
        '...XX..XX...',
        '....XXXX....',
        '.....XX.....',
    ],
    'sensor': [
        '............',
        '.X........X.',
        'X..X....X..X',
        'X.X......X.X',
        'X.X.XXXX.X.X',
        'X.X.XXXX.X.X',
        'X.X.XXXX.X.X',
        'X.X.XXXX.X.X',
        'X.X......X.X',
        'X..X....X..X',
        '.X........X.',
        '............',
    ],
    'calm': [
        '............',
        '.XXX....XXX.',
        'XXXXX..XXXXX',
        'XXXXXXXXXXXX',
        'XXXXXXXXXXXX',
        '.XXXXXXXXXX.',
        '..XXXXXXXX..',
        '...XXXXXX...',
        '....XXXX....',
        '.....XX.....',
        '............',
        '............',
    ],
    'sun': [
        '.....XX.....',
        '.X...XX...X.',
        '..X......X..',
        '....XXXX....',
        '...XXXXXX...',
        'XX.XXXXXX.XX',
        'XX.XXXXXX.XX',
        '...XXXXXX...',
        '....XXXX....',
        '..X......X..',
        '.X...XX...X.',
        '.....XX.....',
    ],
    'moon': [
        '...XXXX.....',
        '..XXX.......',
        '.XXX........',
        '.XX.........',
        'XXX.........',
        'XXX.........',
        'XXX.........',
        'XXXX........',
        '.XXXX.....X.',
        '.XXXXXX.XXX.',
        '..XXXXXXXX..',
        '....XXXX....',
    ],
    'theme': [
        '.X..X..X..X.',
        '.X..X..X..X.',
        'XXXXXXXXXXXX',
        'X..........X',
        'X....XX....X',
        'X...X..X...X',
        'X..X.XX.X..X',
        'X...X..X...X',
        'X....XX....X',
        'X..........X',
        'XXXXXXXXXXXX',
        '.X..X..X..X.',
    ],
    'refresh': [
        '...XXXXX....',
        '..X.....X.X.',
        '.X.......XX.',
        '.X......XXX.',
        'X...........',
        'X...........',
        '...........X',
        '...........X',
        '.XXX......X.',
        '.XX.......X.',
        '.X.X.....X..',
        '....XXXXX...',
    ],
    'install': [
        '.....XX.....',
        '.....XX.....',
        '.....XX.....',
        '.....XX.....',
        '..X..XX..X..',
        '...X.XX.X...',
        '....XXXX....',
        '.....XX.....',
        '............',
        'X..........X',
        'X..........X',
        'XXXXXXXXXXXX',
    ],
    'chevron-left': [
        '............',
        '.......XX...',
        '......XX....',
        '.....XX.....',
        '....XX......',
        '...XX.......',
        '...XX.......',
        '....XX......',
        '.....XX.....',
        '......XX....',
        '.......XX...',
        '............',
    ],
}
ICONS['chevron-right'] = [row[::-1] for row in ICONS['chevron-left']]
assert len(ICONS) == 14
s = ['<svg xmlns="http://www.w3.org/2000/svg">']
for name, rows in ICONS.items():
    assert len(rows) == 12 and all(len(r) == 12 for r in rows), name
    cells = chart(rows, {'X': 'x'}).get('x', set())
    s.append(f'<symbol id="i-{name}" viewBox="0 0 24 24"><path fill="currentColor" d="{runs_path(cells, 2)}"/></symbol>')
s.append('</svg>')
with open(os.path.join(ASSETS, 'icons.svg'), 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(s) + '\n')

# =====================================================================================
# band.svg / band-notice.svg: one tile of the border band (18 x 11 knots, 6 units each),
# snowflake stars and stepped diamonds between two rows of knot check
# =====================================================================================
STAR = ['X..X..X', '.X.X.X.', '..XXX..', 'XXX.XXX', '..XXX..', '.X.X.X.', 'X..X..X']
DIAMOND = ['...X...', '..XAX..', '.XAAAX.', 'XAACAAX', '.XAAAX.', '..XAX..', '...X...']


def band(accent):
    g = {}
    for c in range(18):
        g.setdefault(SNOW if c % 2 == 0 else INDIGO, set()).add((c, 0))
        g.setdefault(INDIGO if c % 2 == 0 else SNOW, set()).add((c, 10))
    for col0, art in ((1, STAR), (10, DIAMOND)):
        for r, line in enumerate(art):
            for c, ch in enumerate(line):
                fill = {'X': SNOW, 'A': accent, 'C': SNOW}.get(ch)
                if fill:
                    g.setdefault(fill, set()).add((col0 + c, 2 + r))
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 66" width="108" height="66" shape-rendering="crispEdges">',
           f'<rect width="108" height="66" fill="{INDIGO}"/>']
    for fill, cells in g.items():
        if fill != INDIGO:
            out.append(f'<path fill="{fill}" d="{runs_path(cells, 6)}"/>')
    out.append('</svg>')
    return '\n'.join(out) + '\n'


for name, accent in (('band.svg', JUNIPER), ('band-notice.svg', MADDER)):
    with open(os.path.join(ASSETS, name), 'w', encoding='utf-8', newline='\n') as f:
        f.write(band(accent))

print('wrote scene.svg, mark.svg, icons.svg, band.svg, band-notice.svg')
