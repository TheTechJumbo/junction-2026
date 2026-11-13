// Drill simulator: synthetic responders so the system can be demoed indoors (no GPS,
// one pair of glasses). Bots wander a search area, run a scripted scenario
// (hazard -> victim -> rescue -> man-down MAYDAY -> rescue) and react to pings
// from real wearers, so a solo demo still shows the full ping/ack/en-route loop.

import { offset, distance, bearing } from '../demo/shared/geo.js';
import { PING_TYPES } from '../demo/shared/pings.js';

const BOTS = [
  { id: 'sim-alpha-2', cs: 'ALPHA-2', team: 'A' },
  { id: 'sim-alpha-3', cs: 'ALPHA-3', team: 'A' },
  { id: 'sim-bravo-1', cs: 'BRAVO-1', team: 'B' },
  { id: 'sim-bravo-2', cs: 'BRAVO-2', team: 'B' },
];

const isBot = (id) => typeof id === 'string' && id.startsWith('sim-');

export function startDrill(incident, { tickMs = 1000, radius = 110, log = () => {} } = {}) {
  const c = incident.center;
  const rnd = (a, b) => a + Math.random() * (b - a);
  const randomPoint = () => offset(c, rnd(15, radius), rnd(0, 360));

  const bots = new Map(
    BOTS.map((b, i) => {
      incident.join({ ...b, role: 'responder' });
      const bot = {
        ...b,
        pos: offset(c, 18 + i * 8, 40 + i * 85),
        hdg: 0,
        target: randomPoint(),
        frozen: false,
        goal: null, // { where: () => {lat,lon}, onArrive, speed }
        bat: Math.round(rnd(70, 95)),
      };
      return [b.id, bot];
    }),
  );
  const bot = (cs) => [...bots.values()].find((b) => b.cs === cs);
  const timers = new Set();
  const later = (ms, fn) => {
    const t = setTimeout(() => {
      timers.delete(t);
      fn();
    }, ms);
    timers.add(t);
  };

  const drop = (b, type, note, ahead = 0) => {
    const at = ahead ? offset(b.pos, ahead, b.hdg) : b.pos;
    return incident.addPing(b.id, { type, lat: at.lat, lon: at.lon, note });
  };

  /** Send a bot to a ping: ACK, then EN ROUTE, walk there, then RESOLVED. */
  const dispatch = (b, ping, { resolve = true } = {}) => {
    b.busy = true;
    later(2000, () => incident.respond(b.id, ping.id, 'ack'));
    later(4500, () => {
      if (!incident.pings.has(ping.id)) return (b.busy = false);
      incident.respond(b.id, ping.id, 'enroute');
      b.goal = {
        speed: 2.2,
        where: () => incident.pings.get(ping.id) || null,
        onArrive: () => {
          b.goal = null;
          later(3000, () => {
            if (resolve && incident.pings.has(ping.id)) incident.respond(b.id, ping.id, 'resolved');
            b.busy = false;
          });
        },
      };
    });
  };

  // Scripted scenario, in seconds from drill start.
  const script = [
    [6, () => drop(bot('ALPHA-2'), 'hazard', 'Gas smell, stairwell B', 10)],
    [12, () => drop(bot('BRAVO-1'), 'clear', 'Ground floor east wing')],
    [18, () => {
      const p = drop(bot('ALPHA-3'), 'victim', 'Conscious, leg trapped');
      if (p) dispatch(bot('BRAVO-1'), p);
    }],
    [40, () => (bot('BRAVO-2').frozen = true)],
    [55, () => {
      const b = bot('BRAVO-2');
      const p = incident.mayday(b.id, { reason: 'NO MOTION (auto)' });
      if (p) dispatch(bot('ALPHA-2'), p);
    }],
    [75, () => drop(bot('ALPHA-3'), 'backup', 'Need 2 for stretcher')],
    [100, () => (bot('BRAVO-2').frozen = false)],
    [110, () => drop(bot('BRAVO-1'), 'exit', 'North door clear')],
  ];

  // React to real wearers: nearest free bot answers MAYDAY / VICTIM / NEED HANDS.
  const onPing = (p, meta = {}) => {
    if (!meta.isNew || isBot(p.by)) return;
    if (!['mayday', 'victim', 'backup'].includes(p.type)) {
      if (PING_TYPES[p.type]?.respondable) {
        const any = [...bots.values()].find((b) => !b.busy);
        if (any) later(2500, () => incident.pings.has(p.id) && incident.respond(any.id, p.id, 'ack'));
      }
      return;
    }
    const free = [...bots.values()]
      .filter((b) => !b.busy && !b.frozen)
      .sort((a, b) => distance(a.pos, p) - distance(b.pos, p));
    if (free[0]) dispatch(free[0], p);
  };
  incident.on('ping', onPing);

  let t = 0;
  const step = () => {
    t += tickMs / 1000;
    for (const [at, fn] of script) if (at > t - tickMs / 1000 && at <= t) fn();
    if (t > 130 && Math.round(t) % 35 === 0) {
      const b = [...bots.values()][Math.floor(Math.random() * bots.size)];
      if (!b.busy && !b.frozen) drop(b, Math.random() < 0.7 ? 'clear' : 'mark');
    }

    for (const b of bots.values()) {
      if (!b.frozen) {
        let dest = b.target;
        let speed = 1.3;
        if (b.goal) {
          dest = b.goal.where();
          speed = b.goal.speed;
          if (!dest) {
            b.goal = null;
            b.busy = false;
            dest = b.target;
          }
        }
        const d = distance(b.pos, dest);
        const stepM = (speed * tickMs) / 1000;
        if (d <= Math.max(stepM, 2.5)) {
          if (b.goal) b.goal.onArrive();
          else b.target = randomPoint();
        } else {
          b.hdg = bearing(b.pos, dest);
          b.pos = offset(b.pos, stepM, b.hdg + rnd(-6, 6));
        }
      }
      if (Math.random() < 0.01) b.bat = Math.max(5, b.bat - 1);
      incident.updatePos(b.id, { lat: b.pos.lat, lon: b.pos.lon, acc: 6, hdg: b.hdg, bat: b.bat });
    }
  };
  const interval = setInterval(step, tickMs);
  step();
  log('drill started');

  return {
    stop() {
      clearInterval(interval);
      for (const tm of timers) clearTimeout(tm);
      incident.off('ping', onPing);
      incident.purge((id) => isBot(id));
      log('drill stopped');
    },
  };
}
