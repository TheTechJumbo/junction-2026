"""Generate the Almanakka theme SVGs (scene, mark, icons) with a slight hand-drawn wobble.

Every line is a single-weight ink stroke with round caps. Straight segments become very gentle
quadratic curves; round things are sampled and smoothed with Catmull-Rom. Seeded, so reruns match.
"""
import math, random, sys
from pathlib import Path

OUT = Path(sys.argv[1])
INK, RED, PAPER, WASH, SUN = '#1f3a5f', '#c8322b', '#e9edf0', '#bdd6e3', '#f7f8f4'
rnd = random.Random(1914)


def f(v):
    s = f'{v:.1f}'
    return s[:-2] if s.endswith('.0') else s


def seg(p, q, amp):
    (x1, y1), (x2, y2) = p, q
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1
    nx, ny = -dy / L, dx / L
    o = rnd.uniform(-amp, amp) * min(1, L / 40)
    cx, cy = (x1 + x2) / 2 + nx * o, (y1 + y2) / 2 + ny * o
    return f'Q{f(cx)} {f(cy)} {f(x2)} {f(y2)}'


def poly(pts, closed=False, amp=1.4):
    pts = [(x + rnd.uniform(-amp, amp) * .3, y + rnd.uniform(-amp, amp) * .3) for x, y in pts]
    d = [f'M{f(pts[0][0])} {f(pts[0][1])}']
    seq = pts + ([pts[0]] if closed else [])
    for a, b in zip(seq, seq[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, math.ceil(L / 64))
        nx, ny = (-(b[1] - a[1]) / (L or 1), (b[0] - a[0]) / (L or 1))
        prev = a
        for i in range(1, n + 1):
            t = i / n
            j = 0 if i == n else rnd.uniform(-amp, amp) * .45
            q = (a[0] + (b[0] - a[0]) * t + nx * j, a[1] + (b[1] - a[1]) * t + ny * j)
            d.append(seg(prev, q, amp))
            prev = q
    return ''.join(d)


def rect(x, y, w, h, amp=1.4):
    return poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True, amp)


def curve(pts, amp=0.6, closed=False):
    pts = [(x + rnd.uniform(-amp, amp), y + rnd.uniform(-amp, amp)) for x, y in pts]
    if closed:
        pts = pts + pts[:3]
    d = [f'M{f(pts[0][0])} {f(pts[0][1])}']
    P = [pts[0]] + pts + [pts[-1]]
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f'C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p2[0])} {f(p2[1])}')
    return ''.join(d)


def ellipse(cx, cy, rx, ry, amp=0.5, a0=None, over=0.35, n=None):
    a0 = rnd.uniform(0, 2 * math.pi) if a0 is None else a0
    n = n or max(8, int((rx + ry) / 2.2))
    span = 2 * math.pi + over
    pts = []
    for i in range(n + 1):
        a = a0 + span * i / n
        k = 1 + rnd.uniform(-amp, amp) / max(rx, ry)
        pts.append((cx + math.cos(a) * rx * k, cy + math.sin(a) * ry * k))
    return curve(pts, 0)


def arc(cx, cy, rx, ry, a0, a1, amp=0.4, n=None):
    n = n or max(4, int(abs(a1 - a0) * (rx + ry) / 6))
    return curve([(cx + math.cos(a0 + (a1 - a0) * i / n) * rx, cy + math.sin(a0 + (a1 - a0) * i / n) * ry) for i in range(n + 1)], amp)


def P(d, cls='al-draw', extra=''):
    return f'<path class="{cls}" d="{d}" pathLength="1"{extra}/>'


# ------------------------------------------------------------------ scene
def scene():
    o = []
    A = o.append
    A(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 380" class="al-scene" role="img" aria-label="An ink drawing of a kitchen corner: a window, a wall almanac, a kettle, a weekly pill box and the front door">')
    A('<defs><clipPath id="almanakka-pane"><rect x="56" y="46" width="188" height="178"/></clipPath></defs>')

    # window: wash, sky, town, frame
    A('<g class="al-window al-g1">')
    A(f'<rect class="al-pane" x="56" y="46" width="188" height="178" fill="{WASH}"/>')
    A('<g clip-path="url(#almanakka-pane)">')
    # stars (night only)
    stars = ''.join(f'M{x - 3} {y}h6M{x} {y - 3}v6' for x, y in [(84, 70), (122, 98), (170, 64), (226, 112), (100, 132)])
    A(f'<path class="al-stars" d="{stars}" fill="none" stroke="{PAPER}" stroke-width="1.6" stroke-linecap="round"/>')
    # moon: crescent (outer circle minus inner offset circle)
    A(f'<g class="al-moon" transform="translate(196 84)"><path class="al-moon-body" d="M8 -15 A16 16 0 1 0 15 7 A12.5 12.5 0 1 1 8 -15 Z" fill="{SUN}" stroke="{INK}" stroke-width="2" stroke-linejoin="round"/></g>')
    # sun: disc and rays, moved by theme.css
    rays = ''.join(f'M{f(math.cos(a) * 21)} {f(math.sin(a) * 21)}L{f(math.cos(a) * 28)} {f(math.sin(a) * 28)}' for a in [i * math.pi / 4 + .2 for i in range(8)])
    A(f'<g class="al-sun" transform="translate(150 110)"><path d="{ellipse(0, 0, 15, 15, .5, 0, .2)}Z" fill="{SUN}" stroke="{INK}" stroke-width="2"/><path class="al-rays" d="{rays}" fill="none" stroke="{INK}" stroke-width="2" stroke-linecap="round"/></g>')
    # town: rooftops and a church spire, filled with paper so the sun sets behind them
    town = [(50, 196), (64, 196), (64, 184), (78, 176), (92, 184), (92, 192), (104, 192), (104, 172), (110, 172), (110, 160), (114, 140), (118, 160), (118, 172), (124, 172), (124, 190), (138, 190), (138, 180), (156, 180), (156, 188), (170, 188), (176, 178), (186, 170), (198, 178), (198, 194), (212, 194), (212, 186), (224, 186), (224, 196), (250, 196)]
    A(f'<path class="al-town" d="{poly(town, False, .5)}L250 232L50 232Z" fill="{PAPER}" stroke="none"/>')
    A(P(poly(town, False, .5)))
    win = ''.join(f'M{f(x)} {f(y)}h3' for x, y in [(70, 200), (80, 200), (146, 196), (182, 186), (190, 186)])
    A(P(win))
    A('</g>')
    A(P(rect(50, 40, 200, 190)))
    A(P(rect(56, 46, 188, 178, .6)))
    A(P(poly([(150, 46), (150, 224)], amp=.6)))
    A(P(poly([(56, 128), (244, 128)], amp=.6)))
    A(P(poly([(40, 230), (260, 230), (262, 239), (38, 239)], True)))
    A('</g>')
    # plant on the sill
    A('<g class="al-plant al-g2">')
    A(P(poly([(210, 230), (214, 212), (238, 212), (242, 230)], False, .5)))
    A(P(poly([(212, 217), (240, 217)], amp=.4)))
    A(P(curve([(224, 212), (220, 200), (212, 192), (204, 190)], .3) + curve([(225, 212), (225, 196), (230, 184)], .3) + curve([(227, 212), (234, 202), (244, 198)], .3)))
    A(P(curve([(204, 190), (210, 186), (215, 194)], .2) + curve([(230, 184), (236, 186), (232, 193)], .2) + curve([(244, 198), (244, 204), (237, 203)], .2)))
    A('</g>')

    # wall almanac
    A('<g class="al-almanac al-g2">')
    A(P(ellipse(335, 44, 2.6, 2.6, .1, 0, .3)))
    A(P(poly([(301, 65), (335, 46), (369, 65)], False, .5)))
    A(P(rect(292, 64, 86, 132)))
    A(P(poly([(292, 74), (378, 74)], amp=.5)))
    A('<text class="al-print al-month" x="335" y="89" text-anchor="middle" font-size="9.5" letter-spacing="1.2" fill="' + INK + '">MARRASKUU</text>')
    A(P(poly([(298, 95), (372, 95)], amp=.4)))
    cols = [301 + i * 11.5 for i in range(7)]
    rows = [107 + r * 15.5 for r in range(6)]
    ink_d, red_d = [], []
    for day in range(1, 31):
        idx = day + 5  # 1 Nov 2026 is a Sunday: column 6 of the first row
        r, c = divmod(idx, 7)
        x, y = cols[c], rows[r]
        w = 2.4 if day < 10 else 5
        dd = f'M{f(x - w / 2)} {f(y + rnd.uniform(-.3, .3))}h{f(w)}'
        (red_d if c == 6 else ink_d).append(dd)
    A(P(''.join(ink_d)))
    A(P(''.join(red_d), 'al-draw al-sunday'))
    cx, cy = cols[5], rows[2]  # Saturday 14 November
    A(P(ellipse(cx, cy, 8, 6.5, .4, -2.2, .6), 'al-ring'))
    A(P(poly([(cx - 3.5, cy - 7), (cx - .8, cy - 4.4), (cx + 4.5, cy - 11)], False, .2), 'al-tick'))
    A('</g>')

    # counter and cupboards
    A('<g class="al-counter al-g1">')
    A(P(poly([(18, 300), (462, 300), (462, 309), (18, 309)], True)))
    A(P(rect(28, 309, 132, 60) + rect(166, 309, 132, 60) + rect(304, 309, 148, 60)))
    A(P(poly([(146, 326), (146, 344)], amp=.3) + poly([(180, 326), (180, 344)], amp=.3) + poly([(318, 326), (318, 344)], amp=.3)))
    A(P(poly([(0, 370), (472, 370)], amp=.8) + poly([(582, 370), (600, 370)], amp=.4)))
    A('</g>')

    # kettle
    A('<g class="al-kettle al-g3">')
    A(P(curve([(90, 300), (86, 288), (88, 272), (98, 259), (113, 253), (129, 253), (144, 259), (153, 272), (155, 288), (151, 300)], .4) + poly([(151, 300), (90, 300)], amp=.4)))
    A(P(curve([(106, 253), (110, 247), (121, 245), (132, 247), (136, 253)], .3)))
    A(P(ellipse(121, 241, 3.4, 3.4, .1, 1, .3)))
    A(P(curve([(100, 258), (97, 240), (106, 228), (121, 225), (136, 228), (145, 240), (142, 258)], .3)))
    A(P(curve([(153, 268), (164, 262), (172, 250), (176, 243)], .3) + poly([(176, 243), (182, 245)], amp=.2) + curve([(182, 245), (176, 262), (166, 276), (155, 284)], .3)))
    A(P(curve([(96, 290), (121, 293), (146, 290)], .3)))
    A('</g>')
    # steam (a gentle loop, not drawn in)
    for i, x0 in enumerate([176, 186, 168]):
        pts = [(x0, 236), (x0 - 5, 226), (x0 + 3, 214), (x0 - 3, 202), (x0 + 2, 190)]
        A(f'<path class="al-steam al-steam-{i + 1}" d="{curve(pts, .3)}" fill="none" stroke="{INK}" stroke-width="2" stroke-linecap="round"/>')

    # weekly pill box: MA TI KE TO PE LA SU, Saturday's lid open
    A('<g class="al-pillbox al-g3">')
    x0, x1, yt, yf, yb = 252, 448, 270, 279, 300
    w = (x1 - x0) / 7
    bw = (x1 - x0 - 12) / 7
    A(P(rect(x0, yf, x1 - x0, yb - yf)))
    top = [(x0, yf), (x0 + 6, yt)] + [(x0 + 6 + bw * 5, yt)]
    A(P(poly(top, False, .4) + poly([(x0 + 6 + bw * 6, yt), (x1 - 6, yt), (x1, yf)], False, .4)))
    divs = ''
    for k in range(1, 7):
        divs += poly([(x0 + k * w, yf), (x0 + k * w, yb)], amp=.25)
        divs += poly([(x0 + k * w, yf), (x0 + 6 + k * bw, yt)], amp=.2)
    A(P(divs))
    # the open lid, standing up behind
    hx0, hx1 = x0 + 6 + bw * 5, x0 + 6 + bw * 6
    A(P(poly([(hx0, yt), (hx0 + 3, yt - 17), (hx1 + 3, yt - 17), (hx1, yt)], False, .3)))
    A(P(ellipse(hx0 + bw * .38, yt + 4.5, 3, 2, .1, 0, .3) + ellipse(hx0 + bw * .68, yt + 5, 3, 2, .1, 1, .3)))
    for k, name in enumerate(['MA', 'TI', 'KE', 'TO', 'PE', 'LA', 'SU']):
        cls = 'al-print al-day al-red-letter' if name == 'SU' else 'al-print al-day'
        A(f'<text class="{cls}" x="{f(x0 + (k + .5) * w)}" y="294" text-anchor="middle" font-size="11" letter-spacing=".5" fill="{RED if name == "SU" else INK}">{name}</text>')
    A('</g>')

    # front door with letterbox
    A('<g class="al-door al-g2">')
    A(P(poly([(472, 370), (472, 26), (582, 26), (582, 370)], False)))
    A(P(rect(480, 34, 94, 336)))
    A(P(rect(492, 50, 62, 124, .7) + rect(492, 252, 62, 100, .7)))
    A(P(rect(498, 198, 50, 13, .4) + poly([(501, 204.5), (545, 204.5)], amp=.2)))
    A(P(poly([(508, 199), (512, 186), (536, 186), (540, 199)], False, .3) + poly([(512, 186), (524, 194), (536, 186)], False, .2)))
    A(P(ellipse(564, 226, 3.6, 3.6, .1, 0, .3) + poly([(564, 226), (552, 227)], amp=.2)))
    A(P(ellipse(564, 242, 1.6, 2.4, .05, 0, .3)))
    A(P(poly([(484, 374), (570, 374), (578, 379), (476, 379)], True, .4)))
    A('</g>')

    # wall hook with Anna's key
    A('<g class="al-key al-g3">')
    A(P(poly([(434, 150), (444, 150)], amp=.2) + curve([(439, 150), (439, 158), (443, 160)], .2)))
    A(P(ellipse(440, 168, 4, 4, .1, 0, .3) + poly([(440, 172), (440, 192)], amp=.2) + poly([(440, 186), (444, 186)], amp=.1) + poly([(440, 191), (443, 191)], amp=.1)))
    A('</g>')

    A('</svg>')
    s = '\n'.join(o)
    # shared stroke attributes for the drawn lines (CSS can override)
    s = s.replace('pathLength="1"/>', f'pathLength="1" fill="none" stroke="{INK}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
    s = s.replace(f'class="al-draw al-sunday" d=', 'class="al-draw al-sunday" data-red="1" d=')
    s = s.replace('data-red="1"', '')
    # red strokes for Sundays and the ring
    out = []
    for line in s.split('\n'):
        if 'al-sunday' in line or 'al-ring' in line:
            line = line.replace(f'stroke="{INK}"', f'stroke="{RED}"')
        out.append(line)
    return '\n'.join(out) + '\n'


# ------------------------------------------------------------------ mark
def mark():
    # an almanac leaf: two binding rings, the sun on the horizon, a red-letter square
    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="none" stroke-linecap="round" stroke-linejoin="round">']
    o.append(f'<path class="al-mark-ink" d="{rect(8, 9, 32, 33, .5)}" stroke="{INK}" stroke-width="2.4"/>')
    o.append(f'<path class="al-mark-ink" d="M17 5v7M31 5v7" stroke="{INK}" stroke-width="2.4"/>')
    o.append(f'<path class="al-mark-ink" d="{arc(24, 32, 8, 8, math.pi, 2 * math.pi, .15)}{poly([(12, 32), (36, 32)], amp=.3)}" stroke="{INK}" stroke-width="2.4"/>')
    o.append(f'<path class="al-mark-ink" d="M24 19.5v-2M16.5 23l-1.4-1.4M31.5 23l1.4-1.4" stroke="{INK}" stroke-width="2.4"/>')
    o.append(f'<rect class="al-mark-red" x="29" y="35" width="6" height="3.4" rx=".6" fill="{RED}"/>')
    o.append('</svg>')
    return '\n'.join(o) + '\n'


# ------------------------------------------------------------------ icons
def icons():
    a = .35  # tiny wobble at icon size
    S = {}
    # capsule, tilted
    def capsule():
        cx, cy, r, ang = 12, 12, 4, -math.pi / 4
        ux, uy = math.cos(ang), math.sin(ang)
        px, py = -uy, ux
        h = 4.6
        e1, e2 = (cx - ux * h, cy - uy * h), (cx + ux * h, cy + uy * h)
        pts = []
        for i in range(7):
            t = ang - math.pi / 2 + math.pi * i / 6
            pts.append((e2[0] + math.cos(t) * r, e2[1] + math.sin(t) * r))
        pts.append(((e2[0] + e1[0]) / 2 + px * r, (e2[1] + e1[1]) / 2 + py * r))
        for i in range(7):
            t = ang + math.pi / 2 + math.pi * i / 6
            pts.append((e1[0] + math.cos(t) * r, e1[1] + math.sin(t) * r))
        pts.append(((e2[0] + e1[0]) / 2 - px * r, (e2[1] + e1[1]) / 2 - py * r))
        d = curve(pts, .05, closed=True)
        mid = f'M{f(cx + px * r)} {f(cy + py * r)}L{f(cx - px * r)} {f(cy - py * r)}'
        return d + mid
    S['pill'] = f'<path d="{capsule()}"/>'
    S['door'] = f'<path d="{poly([(6, 21), (6, 3.5), (18, 3.5), (18, 21)], False, a)}{poly([(3.5, 21), (20.5, 21)], amp=a)}"/><path d="M14.6 12.4h.1" stroke-width="2.6"/><path d="{rect(8.6, 6, 6.8, 3.6, .2)}"/>'
    S['eye'] = f'<path d="{curve([(2.8, 12), (7, 6.8), (12, 5.6), (17, 6.8), (21.2, 12)], .1)}{curve([(21.2, 12), (17, 17.2), (12, 18.4), (7, 17.2), (2.8, 12)], .1)}"/><path d="{ellipse(12, 12, 3, 3, .05, 0, .25, 9)}"/>'
    S['question'] = f'<path d="{ellipse(12, 12, 9, 9, .15, -1.2, .3)}"/><path d="{curve([(9.4, 9.6), (10, 7.6), (12, 7), (14, 7.6), (14.6, 9.4), (13.8, 11), (12.2, 12.2), (12, 14)], .08)}"/><path d="M12 17.2h.1" stroke-width="2.4"/>'
    S['alert'] = f'<path d="{poly([(12, 3.6), (21, 19.6), (3, 19.6)], True, a)}"/><path d="M12 9.4v5" /><path d="M12 17.2h.1" stroke-width="2.4"/>'
    S['sensor'] = f'<path d="{rect(7.5, 11, 9, 9.5, .25)}"/><path d="M10.4 15.8h3.2"/><path d="{arc(12, 11, 4, 4, -math.pi * .78, -math.pi * .22, .05, 4)}"/><path d="{arc(12, 11, 7.5, 7.5, -math.pi * .8, -math.pi * .2, .08, 6)}"/>'
    S['calm'] = f'<path d="{ellipse(12, 12, 9, 9, .15, -.6, .3)}"/><path d="{poly([(7.8, 12.4), (10.8, 15.2), (16.4, 8.8)], False, .15)}"/>'
    rays = ''.join(f'M{f(12 + math.cos(t) * 6.8)} {f(12 + math.sin(t) * 6.8)}L{f(12 + math.cos(t) * 9.4)} {f(12 + math.sin(t) * 9.4)}' for t in [i * math.pi / 4 for i in range(8)])
    S['sun'] = f'<path d="{ellipse(12, 12, 3.9, 3.9, .05, 0, .25, 9)}"/><path d="{rays}"/>'
    S['moon'] = '<path d="M15.2 3.9A8.6 8.6 0 1 0 20.4 15.4A6.6 6.6 0 1 1 15.2 3.9Z"/>'
    S['theme'] = f'<path d="{poly([(12, 6.4), (8, 4.6), (3, 4.6), (3, 18.4), (8, 18.4), (12, 20.2), (16, 18.4), (21, 18.4), (21, 4.6), (16, 4.6), (12, 6.4)], False, a)}"/><path d="M12 6.4v13.8"/><path d="M5.6 8.6h3.4M5.6 11.6h3.4M15 8.6h3.4"/><path d="M15 12.4h3.4v3.2H15z" fill="currentColor" stroke-width="1.2"/>'
    S['refresh'] = f'<path d="{arc(12, 12, 7.6, 7.6, -math.pi * .3, math.pi * 1.45, .1, 12)}"/><path d="{poly([(19.6, 3.8), (19.4, 8.2), (15, 8)], False, .15)}"/>'
    S['install'] = f'<path d="M12 3.5v11.5"/><path d="{poly([(7.4, 10.6), (12, 15.2), (16.6, 10.6)], False, .15)}"/><path d="{poly([(4, 15.5), (4, 20.4), (20, 20.4), (20, 15.5)], False, a)}"/>'
    S['chevron-left'] = f'<path d="{poly([(15, 5.4), (8.4, 12), (15, 18.6)], False, .2)}"/>'
    S['chevron-right'] = f'<path d="{poly([(9, 5.4), (15.6, 12), (9, 18.6)], False, .2)}"/>'
    order = ['pill', 'door', 'eye', 'question', 'alert', 'sensor', 'calm', 'sun', 'moon', 'theme', 'refresh', 'install', 'chevron-left', 'chevron-right']
    o = ['<svg xmlns="http://www.w3.org/2000/svg">']
    for name in order:
        o.append(f'<symbol id="i-{name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{S[name]}</symbol>')
    o.append('</svg>')
    return '\n'.join(o) + '\n'


OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'scene.svg').write_text(scene(), encoding='utf-8')
(OUT / 'mark.svg').write_text(mark(), encoding='utf-8')
(OUT / 'icons.svg').write_text(icons(), encoding='utf-8')
print('ok')
