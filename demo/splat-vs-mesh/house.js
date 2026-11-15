// The rest of the house, drawn see-through around the scanned room, with the rooms' names.
// The glasses only ever scanned the living room: the other rooms come from the floor plan (home.py).
import {parsePly} from '../../dashboard/space.js';

// How see-through each part is: walls barely there, their tops drawn like a plan's lines.
const ALPHA = {floors: 0.32, walls: 0.07, edges: 0.6, things: 0.3, outside: 0.2};
const LIGHT = (() => { const l = [0.35, 1, 0.45], n = Math.hypot(...l); return l.map(v => v / n); })();

/** Load the house parts (viewer axes): {parts: {name: {positions, colours, alphas}}, labels, raw}. */
export async function loadHouse(dir) {
  const plan = await (await fetch(dir + 'house.json')).json();
  const parts = {}, raw = {}, light = {};
  await Promise.all(plan.parts.map(async name => {
    const mesh = parsePly(await (await fetch(`${dir}house_${name}.ply`)).arrayBuffer());
    raw[name] = mesh;
    parts[name] = triangles(mesh, ALPHA[name] ?? 0.3);
    light[name] = inked(parts[name], name);
  }));
  // Room names sit above each room; z up in the file, y up in the viewer.
  const labels = plan.labels.map(l => ({name: l.name, at: [l.at[0], l.at[2], -l.at[1]]}));
  return {parts, light, labels, raw, alpha: ALPHA};
}

// On a light page pale walls vanish: draw the house in the page's ink instead, a little stronger.
const INK = [0.12, 0.23, 0.37];
function inked(s, name) {
  const mix = name === 'outside' ? 0.15 : name === 'things' ? 0.3 : 0.75;
  const more = {walls: 1.8, edges: 1.2, floors: 0.8}[name] ?? 1;
  return {positions: s.positions, colours: s.colours.map((c, i) => c * 0.8 * (1 - mix) + INK[i % 3] * mix), alphas: s.alphas.map(a => Math.min(1, a * more))};
}

// Indexed mesh to the overlay's flat triangle list, each face shaded by how it faces the light
// (overlays are drawn unlit, so the shading is baked into the colour).
function triangles(mesh, alpha) {
  const {positions: p, colours: c, indices} = mesh;
  const s = {positions: [], colours: [], alphas: []};
  for (let i = 0; i < indices.length; i += 3) {
    const [a, b, d] = [indices[i], indices[i + 1], indices[i + 2]];
    const u = [0, 1, 2].map(k => p[b * 3 + k] - p[a * 3 + k]), v = [0, 1, 2].map(k => p[d * 3 + k] - p[a * 3 + k]);
    const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const len = Math.hypot(...n) || 1;
    const shade = 0.62 + 0.38 * Math.abs((n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2]) / len);
    for (const j of [a, b, d]) {
      s.positions.push(p[j * 3], p[j * 3 + 1], p[j * 3 + 2]);
      s.colours.push(c[j * 3] / 255 * shade, c[j * 3 + 1] / 255 * shade, c[j * 3 + 2] / 255 * shade);
      s.alphas.push(alpha);
    }
  }
  return s;
}

/** Show (or hide) the house on a Space, with its room names floating over a labels box. */
export function showHouse(space, house, labelBox, on, {light = false, focus = 'Living room'} = {}) {
  for (const [name, s] of Object.entries(light ? house.light : house.parts)) {
    if (on) space.layer('house-' + name, s, {blend: true}); else space.dropLayer('house-' + name);
  }
  space.reach = on ? 2.6 : 1;
  labelBox.replaceChildren();
  if (!on) { space.requestDraw(); return; }
  const tags = house.labels.map(l => {
    const tag = document.createElement('span');
    tag.className = 'room-tag' + (l.name === focus ? ' room-tag-focus' : '');
    tag.textContent = l.name === focus ? `${l.name} · scanned` : l.name;
    labelBox.append(tag);
    return [tag, l.at];
  });
  const place = () => {
    const width = labelBox.clientWidth, height = labelBox.clientHeight;
    for (const [tag, at] of tags) {
      const xy = space.project(at);
      tag.hidden = !xy || xy[1] < 0 || xy[1] > height + 20;
      if (tag.hidden) continue;
      const half = tag.offsetWidth / 2 + 6; // keep the name inside the panel
      const x = Math.min(width - half, Math.max(half, xy[0]));
      tag.style.transform = `translate(${x.toFixed(1)}px, ${xy[1].toFixed(1)}px) translate(-50%, -100%)`;
    }
  };
  if (!space.ownDraw) {
    space.ownDraw = space.draw.bind(space);
    space.draw = () => { space.ownDraw(); space.afterDraw?.(); };
  }
  space.afterDraw = place;
  space.requestDraw();
}
