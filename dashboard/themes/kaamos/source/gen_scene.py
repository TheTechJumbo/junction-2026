"""Generates themes/kaamos/assets/scene.svg: a Helsinki courtyard at roof height in the blue hour."""
import random, sys

random.seed(14)
OUT = sys.argv[1]
W, H = 1600, 600

# Palette (night defaults; theme.css recolours by class)
NIGHT_SKY = '#0b1029'
WALL = '#121a35'
ROOF = '#3b4763'
SEAM = '#2c3650'
SNOW = '#eef2f6'
SNOW_SHADE = '#c3cee0'
FAR = '#26345c'
FAR_2 = '#1f2b50'
LAMP = '#f2b04a'
LAMP_DEEP = '#d9822b'
GLASS = '#0d1430'
WIN_COLD = '#8fa3c8'
ROSE = '#d99a9e'
GOLD = '#f0c27e'
SUN = '#f7d9a0'
MOON = '#e9edf5'
CURTAIN = '#b23a48'
CURTAIN_LIT = '#f08c98'

o = []
a = o.append
a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMax meet" class="kaamos-scene" role="presentation">')
a('<defs>')
a(f'<linearGradient id="kaamos-glow-dusk" x1="0" y1="0" x2="0" y2="1">'
  f'<stop offset="0" stop-color="{ROSE}" stop-opacity="0"/>'
  f'<stop offset=".55" stop-color="{ROSE}" stop-opacity=".55"/>'
  f'<stop offset="1" stop-color="{GOLD}" stop-opacity=".95"/></linearGradient>')
a(f'<linearGradient id="kaamos-glow-day" x1="0" y1="0" x2="0" y2="1">'
  f'<stop offset="0" stop-color="#ffffff" stop-opacity="0"/>'
  f'<stop offset="1" stop-color="#ffffff" stop-opacity=".7"/></linearGradient>')
a(f'<radialGradient id="kaamos-sun-halo"><stop offset="0" stop-color="{GOLD}" stop-opacity=".75"/>'
  f'<stop offset=".35" stop-color="{GOLD}" stop-opacity=".28"/><stop offset="1" stop-color="{ROSE}" stop-opacity="0"/></radialGradient>')
a(f'<radialGradient id="kaamos-moon-halo"><stop offset="0" stop-color="{MOON}" stop-opacity=".22"/>'
  f'<stop offset="1" stop-color="{MOON}" stop-opacity="0"/></radialGradient>')
a(f'<radialGradient id="kaamos-lamp-halo"><stop offset="0" stop-color="{LAMP}" stop-opacity=".55"/>'
  f'<stop offset=".45" stop-color="{LAMP}" stop-opacity=".16"/><stop offset="1" stop-color="{LAMP}" stop-opacity="0"/></radialGradient>')
a(f'<radialGradient id="kaamos-lamp-room" cx=".3" cy=".75" r=".9"><stop offset="0" stop-color="#ffe2a6"/>'
  f'<stop offset=".45" stop-color="{LAMP}"/><stop offset="1" stop-color="{LAMP_DEEP}"/></radialGradient>')
a(f'<pattern id="kaamos-tin" width="16" height="8" patternUnits="userSpaceOnUse">'
  f'<rect width="16" height="8" class="kaamos-roof" fill="{ROOF}"/>'
  f'<rect width="1.6" height="8" class="kaamos-seam" fill="{SEAM}"/></pattern>')
a(f'<mask id="kaamos-crescent" maskUnits="userSpaceOnUse" x="1180" y="140" width="120" height="120">'
  f'<circle cx="1240" cy="200" r="24" fill="#fff"/><circle cx="1251" cy="192" r="21" fill="#000"/></mask>')
a('</defs>')

# Horizon light, behind everything
a(f'<rect class="kaamos-glow kaamos-glow-dusk" x="0" y="250" width="{W}" height="320" fill="url(#kaamos-glow-dusk)"/>')
a(f'<rect class="kaamos-glow kaamos-glow-day" x="0" y="300" width="{W}" height="270" fill="url(#kaamos-glow-day)"/>')

# Stars
a('<g class="kaamos-stars">')
stars = [(90, 60), (210, 140), (330, 40), (470, 110), (610, 30), (700, 160), (860, 70), (980, 130),
         (1080, 40), (1150, 250), (1330, 90), (1420, 170), (1530, 50), (1570, 230), (540, 230), (380, 300),
         (130, 280), (1010, 230), (760, 260), (1460, 300)]
for i, (x, y) in enumerate(stars):
    if x < 640 and y < 330:
        continue  # keep the sky behind the headline clear
    r = random.choice([1.1, 1.4, 1.8])
    twinkle = ' kaamos-twinkle' if i % 4 == 0 else ''
    a(f'<circle class="kaamos-star{twinkle}" cx="{x}" cy="{y}" r="{r}" fill="{SNOW}"/>')
a('</g>')

# Moon
a('<g class="kaamos-moon">')
a('<circle cx="1240" cy="200" r="90" fill="url(#kaamos-moon-halo)"/>')
a(f'<rect x="1180" y="140" width="120" height="120" fill="{MOON}" mask="url(#kaamos-crescent)" class="kaamos-moon-disc"/>')
a('</g>')

# Sun: base position is on the horizon at sunrise; theme.css moves it with --sun-progress / --sun-altitude
a('<g class="kaamos-sun">')
a('<circle cx="200" cy="470" r="150" fill="url(#kaamos-sun-halo)"/>')
a(f'<circle cx="200" cy="470" r="26" fill="{SUN}" class="kaamos-sun-disc"/>')
a('</g>')

# Far skyline: low blocks and the cathedral dome across the city
a('<g class="kaamos-far">')
a(f'<path class="kaamos-far-2" fill="{FAR_2}" d="M0 500 H70 V486 H150 V494 H240 V478 H300 V490 H420 V470 H470 V484 H560 V476 H640 V490 H900 V472 H980 V486 H1080 V470 H1160 V480 H1300 V466 H1400 V476 H1600 V600 H0 Z"/>')
# Cathedral (Tuomiokirkko): drum, dome, lantern, four corner domes
a(f'<g class="kaamos-cathedral" fill="{FAR}">')
a('<rect x="686" y="458" width="148" height="80"/>')
a('<path d="M700 458 L760 434 L820 458 Z"/>')
a('<rect x="728" y="414" width="64" height="30"/>')
a('<path d="M724 416 C724 378 744 362 760 362 C776 362 796 378 796 416 Z"/>')
a('<rect x="754" y="342" width="12" height="22"/><path d="M752 344 L760 334 L768 344 Z"/><rect x="759" y="322" width="2" height="14"/>')
for cx in (692, 828):
    a(f'<rect x="{cx-10}" y="436" width="20" height="24"/><path d="M{cx-12} 438 C{cx-12} 424 {cx-6} 418 {cx} 418 C{cx+6} 418 {cx+12} 424 {cx+12} 438 Z"/>')
a('</g>')
a('</g>')


def roof(x1, x2, x3, x4, ye, yr, chimneys=(), cls=''):
    """A pitched tin roof seen side on: eaves (x1..x4, ye), ridge (x2..x3, yr), with a snow cap."""
    a(f'<g class="kaamos-roofset{(" " + cls) if cls else ""}">')
    for cx, cw, ch in chimneys:  # chimneys stand behind the roof face
        top = yr - ch
        a(f'<rect class="kaamos-chimney" x="{cx}" y="{top}" width="{cw}" height="{ch + 20}" fill="{SEAM}"/>')
        a(f'<rect class="kaamos-chimney-cap" x="{cx - 3}" y="{top - 3}" width="{cw + 6}" height="5" fill="{ROOF}"/>')
        a(f'<path class="kaamos-snow" fill="{SNOW}" d="M{cx - 3} {top - 3} Q{cx + cw / 2} {top - 11} {cx + cw + 3} {top - 3} Z"/>')
    a(f'<path class="kaamos-tin" fill="url(#kaamos-tin)" d="M{x1} {ye} L{x2} {yr} L{x3} {yr} L{x4} {ye} Z"/>')
    # Snow cap: covers the upper part of the roof with a soft, uneven lower edge
    t = 0.58
    lx, ly = x2 + (x1 - x2) * t, yr + (ye - yr) * t
    rx, ry = x3 + (x4 - x3) * t, yr + (ye - yr) * t
    span = rx - lx
    pts = []
    n = max(4, int(span // 70))
    for i in range(1, n):
        px = rx - span * i / n
        py = yr + (ye - yr) * (0.42 + random.uniform(-0.1, 0.12))
        pts.append((px, py))
    d = f'M{lx:.0f} {ly:.0f} L{x2} {yr - 3} L{x3} {yr - 3} L{rx:.0f} {ry:.0f}'
    prev = (rx, ry)
    for px, py in pts + [(lx, ly)]:
        mx = (prev[0] + px) / 2
        d += f' Q{mx:.0f} {max(py, prev[1]) + 7:.0f} {px:.0f} {py:.0f}'
        prev = (px, py)
    d += ' Z'
    a(f'<path class="kaamos-snow" fill="{SNOW}" d="{d}"/>')
    a(f'<path class="kaamos-snow-shade" fill="none" stroke="{SNOW_SHADE}" stroke-width="2" d="M{x2 + 4} {yr + 2} L{x3 - 4} {yr + 2}"/>')
    # a lip of snow on the gutter
    a(f'<path class="kaamos-snow" fill="{SNOW}" d="M{x1 - 2} {ye} L{x4 + 2} {ye} L{x4 - 2} {ye + 4} L{x1 + 2} {ye + 4} Z"/>')
    a('</g>')


def wall(x, y, w, cls=''):
    a(f'<rect class="kaamos-wall{(" " + cls) if cls else ""}" x="{x}" y="{y}" width="{w}" height="{H - y}" fill="{WALL}"/>')


def windows(xs, y, w, h, lit=None):
    lit = lit or {}
    for i, x in enumerate(xs):
        extra = lit.get(i, '')
        a(f'<rect class="kaamos-win{(" " + extra) if extra else ""}" x="{x}" y="{y}" width="{w}" height="{h}" fill="{GLASS}"/>')
        a(f'<rect class="kaamos-sill" x="{x - 4}" y="{y + h}" width="{w + 8}" height="4" fill="{SNOW}"/>')


# Building C (low, middle, in front of the cathedral)
wall(590, 532, 320)
roof(586, 632, 860, 906, 532, 494, chimneys=[(800, 16, 30)])
windows([620, 690, 760, 830], 556, 34, 44, lit={1: 'kaamos-win-late'})

# Building A (left, low)
wall(-20, 508, 320)
roof(-24, 36, 248, 304, 508, 456, chimneys=[(70, 18, 30), (200, 22, 26)])
windows([24, 96, 168, 240], 532, 36, 50, lit={2: 'kaamos-win-early'})

# Building B (left, a little taller, overlapping A)
wall(262, 520, 330)
roof(254, 306, 548, 600, 522, 474, chimneys=[(330, 22, 36), (500, 18, 26)])
windows([292, 362, 432, 502], 546, 36, 50, lit={0: 'kaamos-win-late', 3: 'kaamos-win-early'})

# Building E (right)
wall(1262, 432, 360)
roof(1252, 1302, 1620, 1640, 434, 382, chimneys=[(1352, 22, 34), (1520, 26, 30)])
windows([1290, 1360, 1430, 1500, 1570], 460, 38, 52, lit={1: 'kaamos-win-late', 4: 'kaamos-win-early'})
windows([1290, 1360, 1430, 1500, 1570], 540, 38, 52, lit={2: 'kaamos-win-late'})

# Building D: Anna's house, in front
wall(888, 402, 376, 'kaamos-wall-near')
a(f'<rect class="kaamos-edge" x="886" y="402" width="2" height="198" fill="{SEAM}"/>')
a(f'<rect class="kaamos-edge" x="1262" y="402" width="2" height="198" fill="{SEAM}"/>')
roof(878, 930, 1222, 1274, 404, 334, chimneys=[(964, 20, 34), (1172, 26, 44)])
windows([914, 980], 432, 40, 58, lit={1: 'kaamos-win-late'})
windows([1128, 1196], 432, 40, 58, lit={0: 'kaamos-win-early'})
windows([914, 980, 1048, 1128, 1196], 522, 40, 58, lit={0: 'kaamos-win-late', 3: 'kaamos-win-late'})

# Anna's window: a lamp, curtains, a plant, snow on the sill
a('<g class="kaamos-anna">')
a('<ellipse class="kaamos-lamp-halo" cx="1072" cy="462" rx="130" ry="110" fill="url(#kaamos-lamp-halo)"/>')
a(f'<rect class="kaamos-anna-frame" x="1040" y="424" width="64" height="74" fill="{SEAM}"/>')
a('<rect class="kaamos-anna-room" x="1045" y="429" width="54" height="64" fill="url(#kaamos-lamp-room)"/>')
a(f'<path class="kaamos-curtain" fill="{CURTAIN}" d="M1045 429 H1056 C1053 450 1054 470 1050 493 H1045 Z"/>')
a(f'<path class="kaamos-curtain" fill="{CURTAIN}" d="M1099 429 H1088 C1091 450 1090 470 1094 493 H1099 Z"/>')
a(f'<path class="kaamos-curtain-lit" fill="{CURTAIN_LIT}" d="M1053 429 H1056 C1053 450 1054 470 1050 493 H1048 C1051 470 1051 450 1053 429 Z"/>')
a(f'<path class="kaamos-curtain-lit" fill="{CURTAIN_LIT}" d="M1088 429 H1091 C1093 450 1093 470 1096 493 H1094 C1090 470 1091 450 1088 429 Z"/>')
# lamp: shade, stem, base
a(f'<path class="kaamos-lampshade" fill="#fff1cf" d="M1060 462 L1076 462 L1080 474 L1056 474 Z"/>')
a(f'<rect class="kaamos-lampstem" x="1067" y="474" width="2" height="15" fill="{LAMP_DEEP}"/>')
a(f'<rect class="kaamos-lampstem" x="1062" y="489" width="12" height="3" fill="{LAMP_DEEP}"/>')
# plant on the sill
a(f'<path class="kaamos-plant" fill="#5b4a2c" d="M1084 486 h10 l-1 7 h-8 Z"/>')
a(f'<path class="kaamos-plant" fill="none" stroke="#5b4a2c" stroke-width="2" stroke-linecap="round" d="M1089 486 C1088 478 1084 476 1082 472 M1089 486 C1090 477 1095 476 1096 470 M1089 486 V474"/>')
# mullions
a(f'<rect class="kaamos-anna-frame" x="1070" y="429" width="3" height="64" fill="{SEAM}"/>')
a(f'<rect class="kaamos-anna-frame" x="1045" y="455" width="54" height="3" fill="{SEAM}"/>')
a(f'<path class="kaamos-snow" fill="{SNOW}" d="M1034 498 H1110 V502 C1100 506 1080 503 1072 505 C1060 507 1046 504 1034 503 Z"/>')
a('</g>')

# Chimney smoke above Anna's house (the warm one)
a('<g class="kaamos-smoke">')
for i in range(4):
    a(f'<circle class="kaamos-puff kaamos-puff-{i + 1}" cx="{1185 + i * 8}" cy="{284 - i * 20}" r="{7 + i * 3}" fill="{SNOW_SHADE}" opacity="{0.5 - i * 0.1:.1f}"/>')
a('</g>')

# Bottom band: the facade the page continues into
a(f'<rect class="kaamos-wall kaamos-base" x="0" y="588" width="{W}" height="12" fill="{WALL}"/>')

# Snow falling, in front
a('<g class="kaamos-snowfall">')
for i in range(34):
    x = random.uniform(10, W - 10)
    y = random.uniform(-40, 380)
    if x < 640 and y < 300 and i % 3:
        x += 700  # fewer flakes over the headline
    r = random.choice([1.4, 1.8, 2.2, 2.8])
    a(f'<circle class="kaamos-flake kaamos-flake-{i % 6 + 1}" cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="{SNOW}"/>')
a('</g>')

a('</svg>')
open(OUT, 'w', encoding='utf-8', newline='\n').write('\n'.join(o) + '\n')
print('wrote', OUT)
