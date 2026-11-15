import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parsePly, vertexNormals} from '../space.js';

function ply({comments = [], vertices, faces}) {
  const header = ['ply', 'format binary_little_endian 1.0', ...comments.map(c => `comment ${c}`), `element vertex ${vertices.length}`,
    'property float x', 'property float y', 'property float z', 'property uchar red', 'property uchar green', 'property uchar blue',
    `element face ${faces.length}`, 'property list uchar int vertex_indices', 'end_header', ''].join('\n');
  const head = new TextEncoder().encode(header);
  const body = new DataView(new ArrayBuffer(vertices.length * 15 + faces.reduce((n, f) => n + 1 + 4 * f.length, 0)));
  let at = 0;
  for (const [x, y, z, r, g, b] of vertices) { body.setFloat32(at, x, true); body.setFloat32(at + 4, y, true); body.setFloat32(at + 8, z, true); body.setUint8(at + 12, r); body.setUint8(at + 13, g); body.setUint8(at + 14, b); at += 15; }
  for (const f of faces) { body.setUint8(at++, f.length); for (const i of f) { body.setInt32(at, i, true); at += 4; } }
  const out = new Uint8Array(head.length + body.byteLength); out.set(head); out.set(new Uint8Array(body.buffer), head.length);
  return out.buffer;
}

test('a quad becomes two triangles with its colours, z-up turned to y-up', () => {
  const mesh = parsePly(ply({comments: ['up z'], vertices: [[0, 0, 0, 255, 0, 0], [1, 0, 0, 0, 255, 0], [1, 1, 0, 0, 0, 255], [0, 1, 0, 9, 9, 9]], faces: [[0, 1, 2, 3]]}));
  assert.deepEqual([...mesh.indices], [0, 1, 2, 0, 2, 3]);
  assert.deepEqual([...mesh.colours.slice(0, 3)], [255, 0, 0]);
  assert.deepEqual([...mesh.positions.slice(6, 9)], [1, 0, -1]); // (x, y, z) z-up -> (x, z, -y)
  const n = vertexNormals(mesh.positions, mesh.indices);
  assert.ok(Math.abs(Math.abs(n[1]) - 1) < 1e-6); // a floor quad's normal is vertical
});

test('VGGT maps (no comment) are y-down and get turned upright', () => {
  const mesh = parsePly(ply({vertices: [[0, 2, 3, 1, 1, 1], [1, 2, 3, 1, 1, 1], [0, 2, 4, 1, 1, 1]], faces: [[0, 1, 2]]}));
  assert.equal(mesh.up, '-y');
  assert.deepEqual([...mesh.positions.slice(0, 3)], [0, -2, -3]);
});

test('damaged or foreign files are refused', () => {
  assert.throws(() => parsePly(new TextEncoder().encode('hello').buffer));
  const good = new Uint8Array(ply({vertices: [[0, 0, 0, 1, 1, 1], [1, 0, 0, 1, 1, 1], [0, 1, 0, 1, 1, 1]], faces: [[0, 1, 2]]}));
  assert.throws(() => parsePly(good.slice(0, good.length - 6).buffer));
  assert.throws(() => parsePly(ply({vertices: [[0, 0, 0, 1, 1, 1]], faces: [[0, 1, 2]]})));
});

test('the example room parses: every index valid, up is z', () => {
  const buffer = readFileSync(new URL('../fixtures/home.ply', import.meta.url));
  const mesh = parsePly(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
  assert.equal(mesh.up, 'z');
  assert.ok(mesh.indices.length > 100000);
  assert.ok(mesh.indices.every(i => i < mesh.positions.length / 3));
});
