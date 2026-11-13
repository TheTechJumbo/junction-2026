// Small-area geodesy helpers shared by the server, the HUD and the command dashboard.
// Plain ES module, no dependencies (the glasses have a ~300 KB first-load budget).

const R = 6371008.8; // mean Earth radius, metres
const toRad = (d) => (d * Math.PI) / 180;
const toDeg = (r) => (r * 180) / Math.PI;

/** Normalise any angle to [0, 360). */
export const norm360 = (d) => ((d % 360) + 360) % 360;

/** Normalise any angle to (-180, 180]. */
export const norm180 = (d) => {
  const x = norm360(d);
  return x > 180 ? x - 360 : x;
};

/** Great-circle distance in metres between {lat, lon} points. */
export function distance(a, b) {
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
}

/** Initial bearing from a to b, degrees clockwise from true north, [0, 360). */
export function bearing(a, b) {
  const p1 = toRad(a.lat);
  const p2 = toRad(b.lat);
  const dL = toRad(b.lon - a.lon);
  const y = Math.sin(dL) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dL);
  return norm360(toDeg(Math.atan2(y, x)));
}

/** Point reached by travelling `dist` metres from p on bearing `brg` degrees. */
export function offset(p, dist, brg) {
  const d = dist / R;
  const t = toRad(brg);
  const p1 = toRad(p.lat);
  const l1 = toRad(p.lon);
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(t));
  const l2 = l1 + Math.atan2(Math.sin(t) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return { lat: toDeg(p2), lon: ((toDeg(l2) + 540) % 360) - 180 };
}

/** Bearing of a target relative to where the wearer is facing, (-180, 180]. Negative = left. */
export const relBearing = (targetBrg, heading) => norm180(targetBrg - heading);

const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
const CARDINAL_WORDS = ['north', 'north east', 'east', 'south east', 'south', 'south west', 'west', 'north west'];

export const cardinal = (deg) => CARDINALS[Math.round(norm360(deg) / 45) % 8];
export const cardinalWords = (deg) => CARDINAL_WORDS[Math.round(norm360(deg) / 45) % 8];

/**
 * Military-style clock position of a target relative to the wearer's facing direction.
 * 12 = dead ahead, 3 = right, 6 = behind, 9 = left.
 */
export function clock(rel) {
  const h = Math.round(norm360(rel) / 30) % 12;
  return h === 0 ? 12 : h;
}

/** Compact distance label for a tiny display. */
export function fmtDist(m) {
  if (!Number.isFinite(m)) return '--';
  if (m < 10) return `${m.toFixed(0)}m`;
  if (m < 1000) return `${Math.round(m / 5) * 5}m`;
  return `${(m / 1000).toFixed(m < 10000 ? 1 : 0)}km`;
}

/** Spoken distance ("about 40 metres"). */
export function speakDist(m) {
  if (m < 10) return 'close by';
  if (m < 1000) return `${Math.round(m / 10) * 10} metres`;
  return `${(m / 1000).toFixed(1)} kilometres`;
}

/** Compact age label: "now", "45s", "3m", "1h". */
export function fmtAge(ms) {
  const s = Math.max(0, ms / 1000);
  if (s < 10) return 'now';
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

/** Callsign as it should be spoken: "BRAVO-2" -> "Bravo 2". */
export const speakCallsign = (cs = '') =>
  cs
    .split(/[-_\s]+/)
    .map((w) => (/^\d+$/.test(w) ? w : w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()))
    .join(' ');
