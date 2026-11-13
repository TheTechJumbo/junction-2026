// The ping vocabulary. One table drives the HUD, the server and the command dashboard,
// so adding a ping type is a one-line change.
//
// Design rules for the glasses (additive 600x600 display, one eye):
//  - every type has a distinct SHAPE as well as a colour (never colour alone),
//  - colours are bright and saturated because black renders as transparent,
//  - priority decides sort order, auto-focus and whether it is read aloud.

export const PING_TYPES = {
  mayday: {
    label: 'MAYDAY', color: '#ff3b3b', shape: 'sos', priority: 0,
    speak: true, ttlMs: null, respondable: true,
    cot: 'b-a-o-tbl', // TAK "911 alert"
    hint: 'Responder in distress',
  },
  victim: {
    label: 'VICTIM', color: '#ff5cf0', shape: 'cross', priority: 1,
    speak: true, ttlMs: null, respondable: true,
    cot: 'b-m-p-s-m',
    hint: 'Person found / needs extraction',
  },
  backup: {
    label: 'NEED HANDS', color: '#ff9f1c', shape: 'rings', priority: 2,
    speak: true, ttlMs: 30 * 60e3, respondable: true,
    cot: 'b-m-p-s-m',
    hint: 'Request more responders here',
  },
  hazard: {
    label: 'HAZARD', color: '#ffd60a', shape: 'triangle', priority: 3,
    speak: true, ttlMs: null, respondable: true,
    cot: 'b-m-p-s-m',
    hint: 'Fire, gas, collapse, live wire...',
  },
  regroup: {
    label: 'REGROUP', color: '#9ad7ff', shape: 'flag', priority: 4,
    speak: true, ttlMs: 30 * 60e3, respondable: false,
    cot: 'b-m-p-w-GOTO',
    hint: 'Rally point',
  },
  exit: {
    label: 'EXIT', color: '#3cf0c8', shape: 'arrow', priority: 5,
    speak: false, ttlMs: null, respondable: false,
    cot: 'b-m-p-w-GOTO',
    hint: 'Safe way out',
  },
  mark: {
    label: 'MARK', color: '#ffffff', shape: 'diamond', priority: 6,
    speak: false, ttlMs: 15 * 60e3, respondable: false,
    cot: 'b-m-p-s-m',
    hint: 'Look here',
  },
  clear: {
    label: 'CLEARED', color: '#5cff7a', shape: 'x', priority: 7,
    speak: false, ttlMs: null, respondable: false,
    cot: 'b-m-p-s-m',
    hint: 'Area searched, nobody here (FEMA "X")',
  },
};

export const PING_TYPE_IDS = Object.keys(PING_TYPES);

/**
 * Ping wheel layout: open with pinch (Enter), then one swipe drops the ping.
 * Two gestures for the common cases; MAYDAY sits behind "MORE" plus a confirm
 * so it is fast but never accidental.
 */
export const WHEEL = [
  { center: 'mark', up: 'victim', right: 'hazard', left: 'clear', down: 'MORE' },
  { center: 'mark', up: 'backup', right: 'exit', left: 'regroup', down: 'mayday' },
];

/** Responses a responder can attach to someone else's ping. */
export const RESPONSES = {
  ack: { label: 'ACK', spoken: 'acknowledged' },
  enroute: { label: 'EN ROUTE', spoken: 'en route' },
  resolved: { label: 'RESOLVED', spoken: 'resolved' },
};

/** Derive the headline status of a ping from its responses. */
export function pingStatus(p) {
  const rs = p.responses || [];
  if (rs.some((r) => r.state === 'resolved')) return 'resolved';
  if (rs.some((r) => r.state === 'enroute')) return 'enroute';
  if (rs.some((r) => r.state === 'ack')) return 'ack';
  return 'open';
}

/** Sort key: open high-priority first, then nearest. `dist` is optional. */
export function pingRank(p, dist = 0) {
  const t = PING_TYPES[p.type] || PING_TYPES.mark;
  const resolved = pingStatus(p) === 'resolved' ? 100 : 0;
  return resolved + t.priority * 10 + Math.min(9.9, dist / 1000);
}

export const TEAM_COLOR = '#4fc3ff'; // blue force
export const CP_COLOR = '#ffffff';
