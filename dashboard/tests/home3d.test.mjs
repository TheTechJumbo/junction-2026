import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {homeSummary} from '../home3d.js';

const fixture = name => JSON.parse(readFileSync(new URL(`../fixtures/${name}`, import.meta.url)));
const notFound = () => Promise.reject(Object.assign(new Error('missing'), {status: 404}));

test('the hero says how many things to clear up and how the night went', async () => {
  const api = {hazards: async () => fixture('hazards.json'), night: async () => fixture('night.json')};
  assert.deepEqual(await homeSummary({api, live: true, day: '2026-11-15'}), {clearUp: '1 thing to clear up', night: 'Up twice last night'});
});

test('nothing in the way and a quiet night say so, or nothing at all when there is no data', async () => {
  const quiet = {hazards: async () => ({hazards: [{in_the_way: false}]}), night: async () => ({summary: {trips: 0}})};
  assert.deepEqual(await homeSummary({api: quiet, live: true, day: '2026-11-15'}), {clearUp: '', night: 'Slept through last night'});
  const none = {hazards: notFound, night: notFound};
  assert.deepEqual(await homeSummary({api: none, live: true, day: '2026-11-15'}), {clearUp: '', night: ''});
});

test('other errors are not hidden as "no data"', async () => {
  const broken = {hazards: () => Promise.reject(Object.assign(new Error('down'), {status: 503})), night: notFound};
  await assert.rejects(homeSummary({api: broken, live: true, day: '2026-11-15'}));
});

test('the example files agree with what the pipeline writes', () => {
  const h = fixture('hazards.json'), n = fixture('night.json');
  assert.equal(h.up, 'z'); assert.ok(h.example); assert.ok(h.hazards.every(z => z.words && z.centre.length === 3));
  assert.equal(n.summary.trips, n.trips.length); assert.ok(n.points.length > 100);
  assert.ok(n.points.every((p, i, all) => i === 0 || all[i - 1].t <= p.t));
});
