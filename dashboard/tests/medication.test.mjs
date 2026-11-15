import test from 'node:test';
import assert from 'node:assert/strict';
import {medicationOpenings} from '../client.js';
test('opening records exclude inference, deduplicate retries, retain nearby events and respect Helsinki day', () => {
  const event = {id:'a', source:'sensor', type:'pillbox_open', ts:'2026-11-14T22:01:00Z'};
  const events = [event, event, {...event,id:'b',ts:'2026-11-14T22:02:00Z'}, {...event,id:'vision',source:'vision'}, {...event,id:'door',type:'door_open'}, {...event,id:'yesterday',ts:'2026-11-14T21:59:00Z'}];
  assert.deepEqual(medicationOpenings({day:'2026-11-15',events}).map(e=>e.id), ['a','b']);
  assert.deepEqual(medicationOpenings({day:'2026-11-15',events:[]}), []);
});
