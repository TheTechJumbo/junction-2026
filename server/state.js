// In-memory incident state: responders ("blue force") and pings.
// Emits change events; the transport layer (server/index.js) fans them out.

import { EventEmitter } from 'node:events';
import { PING_TYPES, pingStatus } from '../demo/shared/pings.js';

const STALE_MS = 20e3; // no position update for this long -> "last known position"
const RESOLVED_LINGER_MS = 2 * 60e3; // resolved pings stay visible on the dashboard briefly
const NOTE_MAX = 140;
const CS_MAX = 16;

const isLat = (v) => Number.isFinite(v) && v >= -90 && v <= 90;
const isLon = (v) => Number.isFinite(v) && v >= -180 && v <= 180;
const cleanText = (s, max) => String(s ?? '').replace(/[\u0000-\u001f]/g, ' ').trim().slice(0, max);
const cleanId = (s) => (typeof s === 'string' && /^[\w.-]{3,64}$/.test(s) ? s : null);

let seq = 0;
const newId = (prefix) => `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

export class Incident extends EventEmitter {
  constructor({ name = 'Incident', center, now = () => Date.now() } = {}) {
    super();
    this.name = name;
    this.center = center; // command post (CP) position
    this.now = now;
    this.responders = new Map();
    this.pings = new Map();
  }

  snapshot() {
    return {
      name: this.name,
      center: this.center,
      responders: [...this.responders.values()],
      pings: [...this.pings.values()],
      serverTime: this.now(),
    };
  }

  setCenter(lat, lon) {
    if (!isLat(lat) || !isLon(lon)) return;
    this.center = { lat, lon };
    this.emit('incident', { name: this.name, center: this.center });
  }

  // ---- responders -------------------------------------------------------

  join({ id, cs, role = 'responder', team = '' }) {
    id = cleanId(id) || newId('r');
    const prev = this.responders.get(id);
    const r = {
      ...(prev || { status: 'ok', lat: null, lon: null, acc: null, hdg: null, bat: null, ts: null }),
      id,
      cs: cleanText(cs, CS_MAX).toUpperCase() || prev?.cs || `UNIT-${id.slice(-3).toUpperCase()}`,
      role: role === 'command' ? 'command' : 'responder',
      team: cleanText(team, 8).toUpperCase(),
      online: true,
      stale: false,
    };
    this.responders.set(id, r);
    this.emit('responder', r);
    return r;
  }

  leave(id) {
    const r = this.responders.get(id);
    if (!r) return;
    r.online = false;
    this.emit('responder', r);
  }

  updatePos(id, { lat, lon, acc, hdg, bat }) {
    const r = this.responders.get(id);
    if (!r || !isLat(lat) || !isLon(lon)) return null;
    r.lat = lat;
    r.lon = lon;
    r.acc = Number.isFinite(acc) ? Math.round(acc) : null;
    r.hdg = Number.isFinite(hdg) ? Math.round(hdg) : r.hdg;
    r.bat = Number.isFinite(bat) ? Math.round(bat) : r.bat;
    r.ts = this.now();
    r.stale = false;
    this.emit('responder', r);
    // A MAYDAY follows the person in distress, so rescuers home in on where they are now.
    for (const p of this.pings.values()) {
      if (p.track === id && pingStatus(p) !== 'resolved') {
        p.lat = lat;
        p.lon = lon;
        this.emit('ping', p);
      }
    }
    return r;
  }

  // ---- pings ------------------------------------------------------------

  addPing(byId, { id, type, lat, lon, note } = {}) {
    const by = this.responders.get(byId);
    if (!by || !PING_TYPES[type]) return null;
    if (!isLat(lat) || !isLon(lon)) return null;
    if (type === 'mayday') return this.mayday(byId, { reason: note, lat, lon, id });
    // Client-generated ids make offline-queued pings idempotent on reconnect.
    const pid = cleanId(id) || newId('p');
    if (this.pings.has(pid)) return this.pings.get(pid);
    const p = {
      id: pid,
      type,
      lat,
      lon,
      note: cleanText(note, NOTE_MAX),
      by: by.id,
      byCs: by.cs,
      ts: this.now(),
      responses: [],
      status: 'open',
    };
    this.pings.set(pid, p);
    this.emit('ping', p, { isNew: true });
    return p;
  }

  respond(byId, pingId, state) {
    const by = this.responders.get(byId);
    const p = this.pings.get(pingId);
    if (!by || !p) return null;
    if (state === 'remove') {
      // Only the author or command may delete. A MAYDAY is cancelled, not deleted.
      if (p.type === 'mayday') return p.track === byId ? this.cancelMayday(byId) : null;
      if (p.by !== byId && by.role !== 'command') return null;
      this.pings.delete(pingId);
      this.emit('ping_del', pingId);
      return null;
    }
    if (!['ack', 'enroute', 'resolved'].includes(state)) return null;
    p.responses = p.responses.filter((r) => r.by !== byId);
    p.responses.push({ by: byId, cs: by.cs, state, ts: this.now() });
    p.status = pingStatus(p);
    if (p.status === 'resolved') {
      p.resolvedAt = this.now();
      if (p.type === 'mayday') this._setStatus(p.track, 'ok');
    }
    this.emit('ping', p, { response: { by: byId, cs: by.cs, state } });
    return p;
  }

  mayday(byId, { reason = '', lat, lon, id } = {}) {
    const r = this.responders.get(byId);
    if (!r) return null;
    const existing = [...this.pings.values()].find(
      (p) => p.type === 'mayday' && p.track === byId && p.status !== 'resolved',
    );
    if (existing) return existing;
    const pos = isLat(lat) && isLon(lon) ? { lat, lon } : { lat: r.lat, lon: r.lon };
    if (!isLat(pos.lat) || !isLon(pos.lon)) return null;
    const p = {
      id: cleanId(id) || newId('m'),
      type: 'mayday',
      lat: pos.lat,
      lon: pos.lon,
      note: cleanText(reason, NOTE_MAX),
      by: byId,
      byCs: r.cs,
      track: byId,
      ts: this.now(),
      responses: [],
      status: 'open',
    };
    this.pings.set(p.id, p);
    this._setStatus(byId, 'mayday');
    this.emit('ping', p, { isNew: true });
    return p;
  }

  cancelMayday(byId) {
    for (const p of [...this.pings.values()]) {
      if (p.type === 'mayday' && p.track === byId) {
        this.pings.delete(p.id);
        this.emit('ping_del', p.id);
      }
    }
    this._setStatus(byId, 'ok');
    return null;
  }

  _setStatus(id, status) {
    const r = this.responders.get(id);
    if (!r || r.status === status) return;
    r.status = status;
    this.emit('responder', r);
  }

  /** Remove responders matching `pred(id)` and every ping they authored (used to end a drill). */
  purge(pred) {
    for (const p of [...this.pings.values()]) {
      if (pred(p.by) || pred(p.track)) {
        this.pings.delete(p.id);
        this.emit('ping_del', p.id);
      }
    }
    for (const id of [...this.responders.keys()]) {
      if (pred(id)) {
        this.responders.delete(id);
        this.emit('responder_del', id);
      }
    }
  }

  /** Periodic housekeeping: expire pings, flag responders whose position went stale. */
  tick() {
    const now = this.now();
    for (const p of [...this.pings.values()]) {
      const ttl = PING_TYPES[p.type]?.ttlMs;
      const expired = ttl && now - p.ts > ttl;
      const gone = p.status === 'resolved' && now - (p.resolvedAt || 0) > RESOLVED_LINGER_MS;
      if (expired || gone) {
        this.pings.delete(p.id);
        this.emit('ping_del', p.id);
      }
    }
    for (const r of this.responders.values()) {
      const stale = r.ts != null && now - r.ts > STALE_MS;
      if (stale !== r.stale) {
        r.stale = stale;
        this.emit('responder', r);
      }
    }
  }
}
