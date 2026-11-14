import math, sys
cx = cy = 200
def pt(r, frac):
    th = math.radians(180 + frac * 360)
    return cx + r * math.sin(th), cy - r * math.cos(th)
out = []
a = out.append
a('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" class="sg-scene" role="presentation">')
a('<defs>')
a('<radialGradient id="signal-face" cx="50%" cy="46%" r="54%">'
  '<stop offset="0" class="sg-face-a" stop-color="#1b2124"/>'
  '<stop offset="1" class="sg-face-b" stop-color="#101416"/></radialGradient>')
a('<radialGradient id="signal-glow" cx="50%" cy="50%" r="50%">'
  '<stop offset="0" class="sg-glow-a" stop-color="#f0a35b" stop-opacity=".55"/>'
  '<stop offset="1" class="sg-glow-b" stop-color="#f0a35b" stop-opacity="0"/></radialGradient>')
a('</defs>')
# housing and bezel
a('<circle class="sg-housing" cx="200" cy="200" r="198" fill="#1c2124" stroke="#2c3437" stroke-width="2"/>')
a('<circle class="sg-bezel" cx="200" cy="200" r="189" fill="none" stroke="#323a3e" stroke-width="1"/>')
a('<circle class="sg-face" cx="200" cy="200" r="183" fill="url(#signal-face)"/>')
# day/night ring
a('<circle class="sg-track" cx="200" cy="200" r="152" fill="none" stroke="#232a2d" stroke-width="10"/>')
a('<circle class="sg-daylight" cx="200" cy="200" r="152" fill="none" stroke="#7fe0c4" stroke-width="10" stroke-opacity=".55" transform="rotate(90 200 200)" stroke-dasharray="304 955" stroke-dashoffset="-326"/>')
# ticks
minor, hour, major = [], [], []
for i in range(96):
    f = i / 96
    if i % 24 == 0:
        x1, y1 = pt(159, f); x2, y2 = pt(178, f); major.append(f'M{x1:.2f} {y1:.2f}L{x2:.2f} {y2:.2f}')
    elif i % 4 == 0:
        x1, y1 = pt(163, f); x2, y2 = pt(176, f); hour.append(f'M{x1:.2f} {y1:.2f}L{x2:.2f} {y2:.2f}')
    else:
        x1, y1 = pt(169, f); x2, y2 = pt(175, f); minor.append(f'M{x1:.2f} {y1:.2f}L{x2:.2f} {y2:.2f}')
a(f'<path class="sg-tick-minor" d="{"".join(minor)}" stroke="#3d6e62" stroke-width="1" fill="none"/>')
a(f'<path class="sg-tick-hour" d="{"".join(hour)}" stroke="#7fe0c4" stroke-opacity=".75" stroke-width="1.5" stroke-linecap="round" fill="none"/>')
a(f'<path class="sg-tick-major" d="{"".join(major)}" stroke="#7fe0c4" stroke-width="2.5" stroke-linecap="round" fill="none"/>')
# numerals
for h in range(0, 24, 3):
    f = h / 24
    big = h % 6 == 0
    x, y = pt(130 if big else 132, f)
    cls = 'sg-num sg-num-major' if big else 'sg-num sg-num-minor'
    size = 19 if big else 11
    fill = '#7fe0c4' if big else '#8b9895'
    a(f'<text class="{cls}" x="{x:.2f}" y="{y:.2f}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="{size}" fill="{fill}" text-anchor="middle" dominant-baseline="central">{h:02d}</text>')
# legend
a('<text class="sg-legend" x="200" y="134" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="8.5" letter-spacing="2.4" fill="#8b9895" text-anchor="middle">DAYLIGHT</text>')
a('<text class="sg-legend" x="200" y="240" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="8.5" letter-spacing="2.4" fill="#8b9895" text-anchor="middle">24 H · 60°N</text>')
# status lamp
a('<circle class="sg-lamp-halo" cx="200" cy="266" r="12" fill="#7fe0c4" fill-opacity="0"/>')
a('<circle class="sg-lamp" cx="200" cy="266" r="4" fill="#3a4246" stroke="#14181a" stroke-width="1"/>')
# hand (drawn pointing at noon; CSS rotates it with --now)
a('<g class="sg-hand">')
a('<line class="sg-hand-line" x1="200" y1="216" x2="200" y2="28" stroke="#f0a35b" stroke-width="2" stroke-linecap="round"/>')
a('<circle class="sg-now-halo" cx="200" cy="48" r="20" fill="url(#signal-glow)"/>')
a('<circle class="sg-now-point" cx="200" cy="48" r="5.5" fill="#f0a35b" stroke="#14181a" stroke-width="1.5"/>')
a('</g>')
a('<circle class="sg-hub" cx="200" cy="200" r="8" fill="#2c3437" stroke="#f0a35b" stroke-width="1.5"/>')
a('<circle class="sg-hub-dot" cx="200" cy="200" r="2.5" fill="#f0a35b"/>')
a('</svg>')
open(sys.argv[1], 'w', encoding='utf-8').write('\n'.join(out) + '\n')
