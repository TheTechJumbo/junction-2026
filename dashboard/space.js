// The home in 3D: a small WebGL2 viewer for the mesh the vision stage builds (splat_mesh.py writes
// a binary PLY with vertex colours). No libraries, so it fits the page's Content-Security-Policy.
// Drag to turn, pinch or scroll to zoom, double-tap to reset; arrow keys and +/- do the same.
// It draws only while something moves, and turns slowly on its own until touched (not with
// reduced motion).

/** Binary little-endian PLY -> {positions, colours, indices, up}. Throws on anything else. */
export function parsePly(buffer) {
  const bytes = new Uint8Array(buffer);
  const end = findHeaderEnd(bytes);
  const header = new TextDecoder().decode(bytes.subarray(0, end)).split('\n').map(line => line.trim());
  if (header[0] !== 'ply' || !header.includes('format binary_little_endian 1.0')) throw new Error('Not a binary PLY mesh.');
  const sizes = {char: 1, uchar: 1, int8: 1, uint8: 1, short: 2, ushort: 2, int16: 2, uint16: 2, int: 4, uint: 4, int32: 4, uint32: 4, float: 4, float32: 4, double: 8, float64: 8};
  const elements = []; let up = '-y', cull = false;
  for (const line of header) {
    const word = line.split(/\s+/);
    if (word[0] === 'comment' && word[1] === 'up' && /^-?[xyz]$/.test(word[2] || '')) up = word[2];
    else if (word[0] === 'comment' && word[1] === 'cull') cull = true; // faces wound consistently: back faces can be skipped
    else if (word[0] === 'element') elements.push({name: word[1], count: Number(word[2]), props: []});
    else if (word[0] === 'property') elements.at(-1)?.props.push(word[1] === 'list' ? {list: true, countType: word[2], type: word[3], name: word[4]} : {type: word[1], name: word[2]});
  }
  const view = new DataView(buffer); let at = end;
  const read = (type, offset) => {
    switch (type) {
      case 'float': case 'float32': return view.getFloat32(offset, true);
      case 'double': case 'float64': return view.getFloat64(offset, true);
      case 'uchar': case 'uint8': return view.getUint8(offset);
      case 'char': case 'int8': return view.getInt8(offset);
      case 'short': case 'int16': return view.getInt16(offset, true);
      case 'ushort': case 'uint16': return view.getUint16(offset, true);
      case 'int': case 'int32': return view.getInt32(offset, true);
      case 'uint': case 'uint32': return view.getUint32(offset, true);
      default: throw new Error(`Unsupported PLY type ${type}.`);
    }
  };
  let positions, colours, indices;
  for (const element of elements) {
    if (element.name === 'vertex') {
      const n = element.count; positions = new Float32Array(n * 3); colours = new Uint8Array(n * 3).fill(200);
      const slot = {x: [positions, 0], y: [positions, 1], z: [positions, 2], red: [colours, 0], green: [colours, 1], blue: [colours, 2]};
      const stride = element.props.reduce((sum, p) => sum + sizes[p.type], 0);
      if (element.props.some(p => p.list) || !Number.isFinite(stride) || at + stride * n > buffer.byteLength) throw new Error('The mesh file is damaged.');
      for (let i = 0; i < n; i++) {
        let offset = at + i * stride;
        for (const p of element.props) { const target = slot[p.name]; if (target) target[0][i * 3 + target[1]] = read(p.type, offset); offset += sizes[p.type]; }
      }
      at += stride * n;
    } else if (element.name === 'face') {
      const list = element.props.find(p => p.list); const out = [];
      for (let i = 0; i < element.count; i++) {
        for (const p of element.props) {
          if (!p.list) { at += sizes[p.type]; continue; }
          const k = read(p.countType, at); at += sizes[p.countType];
          const first = read(p.type, at);
          for (let j = 1; j + 1 < k; j++) out.push(first, read(p.type, at + j * sizes[p.type]), read(p.type, at + (j + 1) * sizes[p.type]));
          at += k * sizes[p.type];
        }
        if (at > buffer.byteLength) throw new Error('The mesh file is damaged.');
      }
      if (!list) throw new Error('The mesh has no faces.');
      indices = Uint32Array.from(out);
    } else {
      throw new Error(`Unexpected PLY element ${element.name}.`);
    }
  }
  if (!positions || !indices || !indices.length) throw new Error('The mesh is empty.');
  if (indices.some(i => i >= positions.length / 3)) throw new Error('The mesh file is damaged.');
  return {positions: toYUp(positions, up), colours, indices, up, cull};
}
function findHeaderEnd(bytes) {
  const marker = [101, 110, 100, 95, 104, 101, 97, 100, 101, 114]; // "end_header"
  for (let i = 0; i < Math.min(bytes.length, 65536); i++) {
    if (marker.every((c, j) => bytes[i + j] === c)) {
      let j = i + marker.length; while (bytes[j] === 13) j++;
      if (bytes[j] === 10) return j + 1;
    }
  }
  throw new Error('Not a PLY mesh.');
}
/** Rotate so the file's "up" becomes +y (VGGT maps are y-down, so -y is up by default). */
function toYUp(p, up) {
  const out = new Float32Array(p.length);
  for (let i = 0; i < p.length; i += 3) {
    const [x, y, z] = [p[i], p[i + 1], p[i + 2]];
    const v = {'y': [x, y, z], '-y': [x, -y, -z], 'z': [x, z, -y], '-z': [x, -z, y], 'x': [y, x, -z], '-x': [-y, -x, -z]}[up];
    out[i] = v[0]; out[i + 1] = v[1]; out[i + 2] = v[2];
  }
  return out;
}
/** Smooth vertex normals from the triangles (area-weighted), so lighting does not depend on the file. */
export function vertexNormals(positions, indices) {
  const n = new Float32Array(positions.length);
  for (let t = 0; t < indices.length; t += 3) {
    const a = indices[t] * 3, b = indices[t + 1] * 3, c = indices[t + 2] * 3;
    const ux = positions[b] - positions[a], uy = positions[b + 1] - positions[a + 1], uz = positions[b + 2] - positions[a + 2];
    const vx = positions[c] - positions[a], vy = positions[c + 1] - positions[a + 1], vz = positions[c + 2] - positions[a + 2];
    const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    for (const k of [a, b, c]) { n[k] += nx; n[k + 1] += ny; n[k + 2] += nz; }
  }
  for (let i = 0; i < n.length; i += 3) { const l = Math.hypot(n[i], n[i + 1], n[i + 2]) || 1; n[i] /= l; n[i + 1] /= l; n[i + 2] /= l; }
  return n;
}

const VERTEX = `#version 300 es
in vec3 position; in vec3 normal; in vec3 colour; in float alpha;
uniform mat4 viewProjection; uniform float time; uniform float wave;
out vec3 vNormal; out vec3 vColour; out float vHeight; out float vAlpha;
void main() {
  vec3 p = position; float a = alpha;
  if (wave > 0.0) {                       // a slow sway and drift, and a soft brightening that travels along
    float phase = p.x * 2.1 + p.z * 1.7;
    p.y += wave * sin(time * 1.3 + phase);
    p.x += wave * 0.6 * sin(time * 0.9 + p.z * 2.3);
    p.z += wave * 0.6 * cos(time * 1.1 + p.x * 2.0);
    a *= 0.8 + 0.2 * sin(time * 2.0 - phase * 1.5);
  }
  vNormal = normal; vColour = colour; vHeight = p.y; vAlpha = a;
  gl_Position = viewProjection * vec4(p, 1.0);
}`;
const FRAGMENT = `#version 300 es
precision mediump float;
in vec3 vNormal; in vec3 vColour; in float vHeight; in float vAlpha;
uniform vec3 light; uniform vec3 eye; uniform float glow;
out vec4 colour;
void main() {
  vec3 n = normalize(vNormal);
  if (dot(n, eye) < 0.0) n = -n;                // surfaces may face either way; light the side we see
  float key = max(dot(n, light), 0.0);
  float sky = 0.5 + 0.5 * n.y;                  // hemisphere: lit from above, darker below
  vec3 c = vColour * (0.38 + 0.22 * sky + 0.55 * key);
  colour = vec4(mix(pow(c, vec3(0.95)), vColour, glow), vAlpha); // overlays (glow 1) keep their own colour
}`;

/** A point from the file's axes into the viewer's (y up), like the mesh itself. */
export function upright(p, up = '-y') { return Array.from(toYUp(Float32Array.from(p), up)); }

// Overlay shapes, in the viewer's axes (y up), as {positions, colours} triangle lists.
const shape = () => ({positions: [], colours: [], alphas: []});
function tri(s, a, b, c, colour, alphas = [1, 1, 1]) {
  s.positions.push(...a, ...b, ...c); for (let i = 0; i < 3; i++) s.colours.push(...colour); s.alphas.push(...alphas);
}
function quad(s, a, b, c, d, colour, [aa, ab, ac, ad]) { tri(s, a, b, c, colour, [aa, ab, ac]); tri(s, a, c, d, colour, [aa, ac, ad]); }
// Across a soft band: the edges fade to nothing, the middle holds `core` (no hard line anywhere).
const ACROSS = [[-1, 0], [-0.35, 1], [0.35, 1], [1, 0]];
/** A wide, see-through flat band along points, fading out at both edges. */
export function softRibbon(s, points, width, colourAt, core = 0.4) {
  for (let i = 0; i + 1 < points.length; i++) {
    const [ax, ay, az] = points[i], [bx, by, bz] = points[i + 1];
    const dx = bx - ax, dz = bz - az, l = Math.hypot(dx, dz); if (l < 1e-4) continue;
    const ox = (-dz / l) * width / 2, oz = (dx / l) * width / 2, c = colourAt(i);
    for (let k = 0; k + 1 < ACROSS.length; k++) {
      const [f0, a0] = ACROSS[k], [f1, a1] = ACROSS[k + 1];
      quad(s, [ax + ox * f0, ay, az + oz * f0], [bx + ox * f0, by, bz + oz * f0], [bx + ox * f1, by, bz + oz * f1], [ax + ox * f1, ay, az + oz * f1],
        c, [a0 * core, a0 * core, a1 * core, a1 * core]);
    }
  }
  return s;
}
/** A sphere (see-through when alpha < 1): the neon orb's core and its glow shells. */
export function sphere(s, [x, y, z], radius, colour, alpha = 1, rings = 10, sides = 16) {
  const p = (i, j) => {
    const th = (i / rings) * Math.PI, ph = (j / sides) * Math.PI * 2;
    return [x + radius * Math.sin(th) * Math.cos(ph), y + radius * Math.cos(th), z + radius * Math.sin(th) * Math.sin(ph)];
  };
  for (let i = 0; i < rings; i++) for (let j = 0; j < sides; j++) quad(s, p(i, j), p(i + 1, j), p(i + 1, j + 1), p(i, j + 1), colour, [alpha, alpha, alpha, alpha]);
  return s;
}
/** A round glow on the floor: strongest in the middle, nothing at the edge. */
export function softDisc(s, [x, y, z], radius, colour, core = 0.5, segments = 32) {
  for (let i = 0; i < segments; i++) {
    const a0 = (i / segments) * Math.PI * 2, a1 = ((i + 1) / segments) * Math.PI * 2;
    tri(s, [x, y, z], [x + radius * Math.cos(a0), y, z + radius * Math.sin(a0)], [x + radius * Math.cos(a1), y, z + radius * Math.sin(a1)], colour, [core, 0, 0]);
  }
  return s;
}
/** The same band standing upright; crossed with softRibbon it reads as a soft glowing volume. */
export function softWall(s, points, height, colourAt, core = 0.4) {
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i], b = points[i + 1], c = colourAt(i);
    if (Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) < 1e-4) continue;
    for (let k = 0; k + 1 < ACROSS.length; k++) {
      const [f0, a0] = ACROSS[k], [f1, a1] = ACROSS[k + 1], h0 = f0 * height / 2, h1 = f1 * height / 2;
      quad(s, [a[0], a[1] + h0, a[2]], [b[0], b[1] + h0, b[2]], [b[0], b[1] + h1, b[2]], [a[0], a[1] + h1, a[2]], c, [a0 * core, a0 * core, a1 * core, a1 * core]);
    }
  }
  return s;
}
/** A flat ring on the floor around a point. */
export function ring(s, [x, y, z], radius, width, colour, segments = 40) {
  for (let i = 0; i < segments; i++) {
    const a0 = (i / segments) * Math.PI * 2, a1 = ((i + 1) / segments) * Math.PI * 2;
    const p = (r, a) => [x + r * Math.cos(a), y, z + r * Math.sin(a)];
    tri(s, p(radius, a0), p(radius + width, a0), p(radius + width, a1), colour);
    tri(s, p(radius, a0), p(radius + width, a1), p(radius, a1), colour);
  }
  return s;
}
/** A flat disc on the floor. */
export function disc(s, [x, y, z], radius, colour, segments = 24) {
  for (let i = 0; i < segments; i++) {
    const a0 = (i / segments) * Math.PI * 2, a1 = ((i + 1) / segments) * Math.PI * 2;
    tri(s, [x, y, z], [x + radius * Math.cos(a0), y, z + radius * Math.sin(a0)], [x + radius * Math.cos(a1), y, z + radius * Math.sin(a1)], colour);
  }
  return s;
}
/** A flat band along points on the floor; colourAt(i) colours each piece. */
export function ribbon(s, points, width, colourAt) {
  for (let i = 0; i + 1 < points.length; i++) {
    const [ax, ay, az] = points[i], [bx, by, bz] = points[i + 1];
    const dx = bx - ax, dz = bz - az, l = Math.hypot(dx, dz); if (l < 1e-4) continue;
    const ox = (-dz / l) * width / 2, oz = (dx / l) * width / 2, c = colourAt(i);
    tri(s, [ax + ox, ay, az + oz], [bx + ox, by, bz + oz], [bx - ox, by, bz - oz], c);
    tri(s, [ax + ox, ay, az + oz], [bx - ox, by, bz - oz], [ax - ox, ay, az - oz], c);
  }
  return s;
}
/** An upright band along points (each at its own height): seen edge-on from above, as a strip from the side.
 * With a ribbon through the same points it reads as a tube from any angle. */
export function wall(s, points, height, colourAt) {
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i], b = points[i + 1], c = colourAt(i), h = height / 2;
    if (Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) < 1e-4) continue;
    tri(s, [a[0], a[1] - h, a[2]], [b[0], b[1] - h, b[2]], [b[0], b[1] + h, b[2]], c);
    tri(s, [a[0], a[1] - h, a[2]], [b[0], b[1] + h, b[2]], [a[0], a[1] + h, a[2]], c);
  }
  return s;
}
/** A dashed band between two points on the floor. */
export function dashes(s, a, b, width, colour, dash = 0.18) {
  const l = Math.hypot(b[0] - a[0], b[2] - a[2]), n = Math.max(1, Math.floor(l / dash));
  for (let i = 0; i < n; i += 2) {
    const f0 = i / n, f1 = Math.min(1, (i + 1) / n), at = f => a.map((v, k) => v + (b[k] - v) * f);
    ribbon(s, [at(f0), at(f1)], width, () => colour);
  }
  return s;
}
/** An upright eight-sided column: the wearer, where they are at the chosen time. */
export function pillar(s, [x, y, z], radius, height, colour, top = colour) {
  const sides = 8, p = (a, h) => [x + radius * Math.cos(a), y + h, z + radius * Math.sin(a)];
  for (let i = 0; i < sides; i++) {
    const a0 = (i / sides) * Math.PI * 2, a1 = ((i + 1) / sides) * Math.PI * 2;
    tri(s, p(a0, 0), p(a1, 0), p(a1, height), colour); tri(s, p(a0, 0), p(a1, height), p(a0, height), colour);
    tri(s, [x, y + height, z], p(a0, height), p(a1, height), top);
  }
  return s;
}
export { shape };

export class Space {
  constructor(canvas) {
    this.canvas = canvas;
    // Phones: a dense screen does not need antialiasing (each pixel is tiny) and drawing at 1.5x instead
    // of 3x is less than a quarter of the pixels; the difference is hard to see on a moving 3D view.
    const dense = (window.devicePixelRatio || 1) >= 2, touch = matchMedia('(pointer: coarse)').matches;
    this.maxScale = touch ? 1.5 : 2;
    const gl = canvas.getContext('webgl2', {antialias: !dense, alpha: true, powerPreference: 'low-power'});
    if (!gl) throw new Error('This browser cannot show 3D.');
    this.gl = gl;
    this.program = link(gl, VERTEX, FRAGMENT);
    this.view = {yaw: 0.6, pitch: 0.55, distance: 1, target: [0, 0, 0]};
    this.still = matchMedia('(prefers-reduced-motion: reduce)').matches; // no turning, no wobble
    this.home = null; this.frame = 0; this.spinning = !this.still;
    this.pointers = new Map(); this.pinch = 0; this.layers = new Map();
    this.listen();
    document.addEventListener('visibilitychange', () => this.loop()); // stop drawing while hidden, carry on when back
    this.resize = new ResizeObserver(() => this.requestDraw());
    this.resize.observe(canvas);
  }
  /** Show a parsed mesh, framed to fit. */
  show(mesh) {
    const gl = this.gl;
    this.release();
    const normals = vertexNormals(mesh.positions, mesh.indices);
    const colours = new Float32Array(mesh.colours.length); for (let i = 0; i < colours.length; i++) colours[i] = mesh.colours[i] / 255;
    this.vao = gl.createVertexArray(); gl.bindVertexArray(this.vao);
    this.buffers = [['position', mesh.positions], ['normal', normals], ['colour', colours]].map(([name, data]) => {
      const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      const at = gl.getAttribLocation(this.program, name); gl.enableVertexAttribArray(at); gl.vertexAttribPointer(at, 3, gl.FLOAT, false, 0, 0);
      return buffer;
    });
    // 16-bit indices when the mesh is small enough (half the memory), 32-bit otherwise.
    const small = mesh.positions.length / 3 <= 65536;
    this.indexType = small ? gl.UNSIGNED_SHORT : gl.UNSIGNED_INT; this.cull = Boolean(mesh.cull);
    this.indexBuffer = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.indexBuffer); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, small ? Uint16Array.from(mesh.indices) : mesh.indices, gl.STATIC_DRAW);
    this.count = mesh.indices.length;
    gl.bindVertexArray(null);
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < mesh.positions.length; i += 3) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], mesh.positions[i + k]); hi[k] = Math.max(hi[k], mesh.positions[i + k]); }
    const size = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) || 1;
    this.home = {target: lo.map((v, k) => (v + hi[k]) / 2), distance: size * 1.4, size};
    this.reset();
  }
  /** Draw on the next frame: many changes in one frame (a slider step rebuilds several layers) draw once. */
  requestDraw() {
    if (this.pending) return;
    this.pending = requestAnimationFrame(() => { this.pending = 0; this.draw(); });
  }
  reset() { if (!this.home) return; Object.assign(this.view, {yaw: 0.6, pitch: 0.55, distance: this.home.distance, target: [...this.home.target]}); this.draw(); }
  /** The whole room from higher up, for floor drawings like the night's trail. */
  overview(pitch = 1.05, scale = 0.8) {
    if (!this.home) return;
    this.spinning = false; cancelAnimationFrame(this.frame);
    Object.assign(this.view, {yaw: 0.35, pitch, distance: this.home.distance * scale, target: [...this.home.target]}); this.draw();
  }
  /** Look at a point from closer in (scale: share of the whole-room distance). */
  focus(point, scale = 0.55) {
    if (!this.home) return;
    this.spinning = false; cancelAnimationFrame(this.frame);
    Object.assign(this.view, {target: [...point], distance: this.home.distance * scale, pitch: Math.max(this.view.pitch, 0.7)}); this.draw();
  }
  /** Overlays on the mesh: a named layer of {positions, colours} triangles (viewer axes, colours 0-1).
   * depth false draws it over everything (markers that must not hide behind furniture). */
  layer(name, s, {depth = true, blend = false, wave = 0} = {}) {
    const gl = this.gl;
    if (!s || !s.positions.length) { this.dropLayer(name); this.requestDraw(); return; }
    const positions = Float32Array.from(s.positions), colours = Float32Array.from(s.colours);
    const alphas = s.alphas?.length === positions.length / 3 ? Float32Array.from(s.alphas) : new Float32Array(positions.length / 3).fill(1);
    // A layer keeps its vertex array and buffers: a slider step refills them instead of making new ones
    // (no garbage for the phone to collect while a night plays). Overlays all face up, so their normal is
    // one constant (set in draw), not a buffer.
    let l = this.layers.get(name);
    if (!l) {
      const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
      const buffers = [['position', 3], ['colour', 3], ['alpha', 1]].map(([attr, size]) => {
        const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        const at = gl.getAttribLocation(this.program, attr); gl.enableVertexAttribArray(at); gl.vertexAttribPointer(at, size, gl.FLOAT, false, 0, 0);
        return buffer;
      });
      gl.bindVertexArray(null);
      l = {vao, buffers};
      this.layers.set(name, l);
    }
    [positions, colours, alphas].forEach((data, i) => { gl.bindBuffer(gl.ARRAY_BUFFER, l.buffers[i]); gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW); });
    Object.assign(l, {count: positions.length / 3, depth, blend, wave: this.still ? 0 : wave});
    this.requestDraw(); this.loop();
  }
  /** Where a point (viewer axes) is on the canvas, in CSS pixels, as last drawn; null when behind the eye. */
  project([x, y, z]) {
    const m = this.viewProjection; if (!m) return null;
    const w = m[3] * x + m[7] * y + m[11] * z + m[15]; if (w <= 0.01) return null;
    const cx = (m[0] * x + m[4] * y + m[8] * z + m[12]) / w, cy = (m[1] * x + m[5] * y + m[9] * z + m[13]) / w;
    return [(cx * 0.5 + 0.5) * this.canvas.clientWidth, (0.5 - cy * 0.5) * this.canvas.clientHeight];
  }
  dropLayer(name) {
    const l = this.layers.get(name); if (!l) return;
    l.buffers.forEach(b => this.gl.deleteBuffer(b)); this.gl.deleteVertexArray(l.vao); this.layers.delete(name);
  }
  clearLayers() { [...this.layers.keys()].forEach(name => this.dropLayer(name)); this.requestDraw(); }
  turn(dYaw, dPitch) { this.view.yaw += dYaw; this.view.pitch = Math.min(1.45, Math.max(-0.2, this.view.pitch + dPitch)); this.requestDraw(); }
  zoom(factor) { if (!this.home) return; this.view.distance = Math.min(this.home.size * 4, Math.max(this.home.size * 0.15, this.view.distance * factor)); this.requestDraw(); }
  start() { this.running = true; this.loop(); }
  stop() { this.running = false; cancelAnimationFrame(this.frame); }
  loop() {
    cancelAnimationFrame(this.frame);
    const waving = [...this.layers.values()].some(l => l.wave > 0);
    if (!this.running || document.hidden || (!this.spinning && !waving)) return;
    this.frame = requestAnimationFrame(time => {
      // A slow sway needs no more than 30 frames a second: half the work (and battery) on a phone.
      if (this.spinning || time - (this.lastFrame || 0) >= 32) {
        this.lastFrame = time;
        if (this.spinning) this.view.yaw += 0.0025;
        this.draw();
      }
      this.loop();
    });
  }
  draw() {
    const gl = this.gl, canvas = this.canvas;
    const scale = Math.min(window.devicePixelRatio || 1, this.maxScale);
    const width = Math.max(1, Math.round(canvas.clientWidth * scale)), height = Math.max(1, Math.round(canvas.clientHeight * scale));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    gl.viewport(0, 0, width, height); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    if (!this.vao) return;
    const {yaw, pitch, distance, target} = this.view;
    const eye = [target[0] + distance * Math.cos(pitch) * Math.sin(yaw), target[1] + distance * Math.sin(pitch), target[2] + distance * Math.cos(pitch) * Math.cos(yaw)];
    const reach = this.reach || 1; // how far past the mesh there is something to draw (a house around the room)
    const near = Math.max(0.01, distance - this.home.size * reach), far = distance + this.home.size * 2 * reach;
    const aspect = width / height;
    const fovy = aspect < 1 ? 2 * Math.atan(Math.tan(0.4) / aspect) : 0.8; // a tall phone screen keeps the room's width in view
    const viewProjection = multiply(perspective(fovy, aspect, near, far), lookAt(eye, target, [0, 1, 0]));
    const toEye = normalise(eye.map((v, k) => v - target[k]));
    this.viewProjection = viewProjection;
    gl.useProgram(this.program); gl.enable(gl.DEPTH_TEST);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.program, 'viewProjection'), false, viewProjection);
    gl.uniform3fv(gl.getUniformLocation(this.program, 'light'), normalise([toEye[0] + 0.4, 1.2, toEye[2] + 0.2]));
    gl.uniform3fv(gl.getUniformLocation(this.program, 'eye'), toEye);
    const glow = gl.getUniformLocation(this.program, 'glow'), wave = gl.getUniformLocation(this.program, 'wave');
    gl.uniform1f(gl.getUniformLocation(this.program, 'time'), performance.now() / 1000);
    gl.uniform1f(glow, 0); gl.uniform1f(wave, 0);
    gl.vertexAttrib1f(gl.getAttribLocation(this.program, 'alpha'), 1); // the mesh has no alpha buffer: solid
    // Back faces culled when the file says its faces are wound consistently: half the work, and the wall
    // between the camera and the room (it faces inwards) disappears by itself.
    if (this.cull) { gl.enable(gl.CULL_FACE); gl.cullFace(gl.BACK); }
    gl.bindVertexArray(this.vao); gl.drawElements(gl.TRIANGLES, this.count, this.indexType, 0);
    gl.disable(gl.CULL_FACE);
    gl.uniform1f(glow, 1);
    gl.vertexAttrib3f(gl.getAttribLocation(this.program, 'normal'), 0, 1, 0); // overlays all face up
    // Solid layers, then see-through ones (blended, depth-tested but not hiding each other), then markers drawn over everything.
    const order = l => (!l.depth ? 2 : l.blend ? 1 : 0);
    for (const l of [...this.layers.values()].sort((a, b) => order(a) - order(b))) {
      if (l.depth) gl.enable(gl.DEPTH_TEST); else gl.disable(gl.DEPTH_TEST);
      if (l.blend) { gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false); }
      gl.uniform1f(wave, l.wave);
      gl.bindVertexArray(l.vao); gl.drawArrays(gl.TRIANGLES, 0, l.count);
      if (l.blend) { gl.disable(gl.BLEND); gl.depthMask(true); }
    }
    gl.enable(gl.DEPTH_TEST); gl.bindVertexArray(null);
  }
  listen() {
    const c = this.canvas;
    const still = () => { this.spinning = false; cancelAnimationFrame(this.frame); };
    c.addEventListener('pointerdown', e => { still(); c.setPointerCapture(e.pointerId); this.pointers.set(e.pointerId, [e.clientX, e.clientY]); this.pinch = 0; });
    c.addEventListener('pointermove', e => {
      const last = this.pointers.get(e.pointerId); if (!last) return;
      this.pointers.set(e.pointerId, [e.clientX, e.clientY]);
      if (this.pointers.size === 1) this.turn(-(e.clientX - last[0]) * 0.008, (e.clientY - last[1]) * 0.008);
      else if (this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()]; const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (this.pinch) this.zoom(this.pinch / d); this.pinch = d;
      }
    });
    const end = e => { this.pointers.delete(e.pointerId); this.pinch = 0; };
    c.addEventListener('pointerup', end); c.addEventListener('pointercancel', end);
    c.addEventListener('wheel', e => { e.preventDefault(); still(); this.zoom(Math.exp(e.deltaY * 0.001)); }, {passive: false});
    c.addEventListener('dblclick', () => { still(); this.reset(); });
    c.addEventListener('keydown', e => {
      const keys = {ArrowLeft: () => this.turn(0.12, 0), ArrowRight: () => this.turn(-0.12, 0), ArrowUp: () => this.turn(0, 0.08), ArrowDown: () => this.turn(0, -0.08),
        '+': () => this.zoom(0.85), '=': () => this.zoom(0.85), '-': () => this.zoom(1.18), '0': () => this.reset()};
      if (keys[e.key]) { e.preventDefault(); still(); keys[e.key](); }
    });
  }
  release() {
    const gl = this.gl;
    this.buffers?.forEach(b => gl.deleteBuffer(b)); if (this.indexBuffer) gl.deleteBuffer(this.indexBuffer); if (this.vao) gl.deleteVertexArray(this.vao);
    this.buffers = null; this.indexBuffer = null; this.vao = null;
  }
}

function link(gl, vertex, fragment) {
  const program = gl.createProgram();
  for (const [type, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]]) {
    const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error('3D view could not start.');
    gl.attachShader(program, shader);
  }
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('3D view could not start.');
  return program;
}
function normalise(v) { const l = Math.hypot(...v) || 1; return v.map(x => x / l); }
function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), r = 1 / (near - far);
  return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * r, -1, 0, 0, 2 * far * near * r, 0]);
}
function lookAt(eye, target, up) {
  const z = normalise(eye.map((v, k) => v - target[k]));
  const x = normalise([up[1] * z[2] - up[2] * z[1], up[2] * z[0] - up[0] * z[2], up[0] * z[1] - up[1] * z[0]]);
  const y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1]);
}
function multiply(a, b) {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col++) for (let row = 0; row < 4; row++) {
    let sum = 0; for (let k = 0; k < 4; k++) sum += a[k * 4 + row] * b[col * 4 + k]; out[col * 4 + row] = sum;
  }
  return out;
}
