// Cursor-on-Target (CoT) export, the XML event format spoken by ATAK / WinTAK / TAK Server,
// which many fire, police and SAR teams already run. This lets Beacon feed an existing
// common operating picture instead of replacing it.
//
// Type codes follow common ATAK conventions (a-f-G-U-C friendly ground unit, b-m-p-s-m spot
// marker, b-a-o-tbl 911 alert). Verify against your TAK deployment before relying on it.

import { PING_TYPES } from '../demo/shared/pings.js';

const esc = (s) =>
  String(s ?? '').replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' })[c]);

const iso = (ms) => new Date(ms).toISOString();

// ATAK marker colours are signed 32-bit ARGB integers.
function argb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return ((0xff << 24) | n) | 0;
}

function event({ uid, type, how, lat, lon, ce = 9999999, time, staleMs, detail }) {
  return (
    `<event version="2.0" uid="${esc(uid)}" type="${esc(type)}" how="${how}" ` +
    `time="${iso(time)}" start="${iso(time)}" stale="${iso(time + staleMs)}">` +
    `<point lat="${lat}" lon="${lon}" hae="9999999.0" ce="${ce}" le="9999999.0"/>` +
    `<detail>${detail}</detail></event>`
  );
}

export function responderToCot(r, now = Date.now()) {
  if (r.lat == null) return null;
  return event({
    uid: `beacon-${r.id}`,
    type: 'a-f-G-U-C',
    how: 'm-g',
    lat: r.lat,
    lon: r.lon,
    ce: r.acc ?? 9999999,
    time: r.ts || now,
    staleMs: 60e3,
    detail:
      `<contact callsign="${esc(r.cs)}"/>` +
      (r.team ? `<__group name="${esc(r.team)}" role="Team Member"/>` : '') +
      (r.hdg != null ? `<track course="${r.hdg}" speed="0"/>` : '') +
      (r.status === 'mayday' ? `<emergency type="911 Alert">${esc(r.cs)}</emergency>` : ''),
  });
}

export function pingToCot(p) {
  const t = PING_TYPES[p.type] || PING_TYPES.mark;
  const callsign = `${t.label}${p.note ? ` - ${p.note}` : ''}`;
  const detail =
    `<contact callsign="${esc(callsign)}"/>` +
    `<remarks>${esc(`${t.label} by ${p.byCs} (${p.status})${p.note ? `: ${p.note}` : ''}`)}</remarks>` +
    `<color argb="${argb(t.color)}"/>` +
    (p.type === 'mayday' ? `<emergency type="911 Alert">${esc(p.byCs)}</emergency>` : '');
  return event({
    uid: `beacon-${p.id}`,
    type: t.cot,
    how: 'h-e',
    lat: p.lat,
    lon: p.lon,
    time: p.ts,
    staleMs: t.ttlMs ?? 24 * 3600e3,
    detail,
  });
}

/** All current state as newline-separated CoT events. */
export function snapshotToCot(incident) {
  const now = Date.now();
  const out = ['<?xml version="1.0" encoding="UTF-8"?>', '<events>'];
  for (const r of incident.responders.values()) {
    if (r.role === 'responder') {
      const ev = responderToCot(r, now);
      if (ev) out.push(ev);
    }
  }
  for (const p of incident.pings.values()) out.push(pingToCot(p));
  out.push('</events>');
  return out.join('\n');
}
