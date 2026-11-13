// Beacon server: serves the glasses HUD + command dashboard and relays pings over WebSocket.
//
//   npm start                     -> http://localhost:8080
//   DRILL=1 npm start             -> also starts the simulated drill on boot
//   ACCESS_KEY=secret npm start   -> clients must connect with ?key=secret
//
// The glasses only load HTTPS URLs, so expose this with a tunnel for on-device testing
// (see README: `cloudflared tunnel --url http://localhost:8080`).

import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { createWriteStream, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';
import { Incident } from './state.js';
import { snapshotToCot } from './cot.js';
import { startDrill } from './sim.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PUBLIC = path.join(ROOT, 'public');
const PORT = Number(process.env.PORT) || 8080;
const ACCESS_KEY = process.env.ACCESS_KEY || '';
const incident = new Incident({
  name: process.env.INCIDENT_NAME || 'DRILL / OTANIEMI',
  center: {
    lat: Number(process.env.INCIDENT_LAT) || 60.18555,
    lon: Number(process.env.INCIDENT_LON) || 24.83245,
  },
});

// ---- after-action log (NDJSON, one line per event) ------------------------------------
mkdirSync(path.join(ROOT, 'logs'), { recursive: true });
const logFile = path.join(ROOT, 'logs', `incident-${new Date().toISOString().replace(/[:.]/g, '-')}.ndjson`);
const logStream = createWriteStream(logFile, { flags: 'a' });
const audit = (ev, data) => logStream.write(JSON.stringify({ ts: Date.now(), ev, data }) + '\n');

// ---- static files ---------------------------------------------------------------------
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.webmanifest': 'application/manifest+json',
};

async function serveStatic(req, res, urlPath) {
  let rel = decodeURIComponent(urlPath);
  if (rel.endsWith('/')) rel += 'index.html';
  const file = path.normalize(path.join(PUBLIC, rel));
  if (!file.startsWith(PUBLIC + path.sep)) return send(res, 403, 'forbidden');
  try {
    const body = await readFile(file);
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(file)] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
    });
    res.end(body);
  } catch {
    // "/glasses" -> "/glasses/"
    if (!path.extname(rel)) {
      res.writeHead(301, { Location: urlPath + '/' });
      return res.end();
    }
    send(res, 404, 'not found');
  }
}

const send = (res, code, body, type = 'text/plain; charset=utf-8') => {
  res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-cache' });
  res.end(body);
};
const keyOk = (url) => !ACCESS_KEY || url.searchParams.get('key') === ACCESS_KEY;

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://local');
  if (req.method !== 'GET' && req.method !== 'HEAD') return send(res, 405, 'method not allowed');
  if (url.pathname === '/healthz') return send(res, 200, 'ok');
  if (url.pathname.startsWith('/api/')) {
    if (!keyOk(url)) return send(res, 401, 'bad key');
    if (url.pathname === '/api/state')
      return send(res, 200, JSON.stringify({ ...incident.snapshot(), drill: !!drill }), MIME['.json']);
    if (url.pathname === '/api/cot') return send(res, 200, snapshotToCot(incident), 'application/xml; charset=utf-8');
    return send(res, 404, 'not found');
  }
  serveStatic(req, res, url.pathname);
});

// ---- realtime relay -------------------------------------------------------------------
const wss = new WebSocketServer({ noServer: true, maxPayload: 16 * 1024 });
const socketsById = new Map(); // responder id -> number of open sockets (tabs)
let drill = null;

server.on('upgrade', (req, socket, head) => {
  const url = new URL(req.url, 'http://local');
  if (url.pathname !== '/ws' || !keyOk(url)) return socket.destroy();
  wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws));
});

const broadcast = (msg) => {
  const data = JSON.stringify(msg);
  for (const ws of wss.clients) if (ws.readyState === 1 && ws.joined) ws.send(data);
};

incident.on('responder', (r) => broadcast({ t: 'responder', r }));
incident.on('responder_del', (id) => broadcast({ t: 'responder_del', id }));
incident.on('incident', (info) => broadcast({ t: 'incident', ...info }));
incident.on('ping', (p, meta = {}) => {
  broadcast({ t: 'ping', p, isNew: !!meta.isNew, response: meta.response || null });
  if (meta.isNew) audit('ping', p);
  if (meta.response) audit('response', { id: p.id, ...meta.response });
});
incident.on('ping_del', (id) => {
  broadcast({ t: 'ping_del', id });
  audit('ping_del', { id });
});

function setDrill(on) {
  if (on && !drill) drill = startDrill(incident, { log: (m) => console.log(`[drill] ${m}`) });
  if (!on && drill) {
    drill.stop();
    drill = null;
  }
  broadcast({ t: 'drill', on: !!drill });
  audit('drill', { on: !!drill });
}

wss.on('connection', (ws) => {
  ws.alive = true;
  ws.on('pong', () => (ws.alive = true));

  ws.on('message', (raw) => {
    let m;
    try {
      m = JSON.parse(raw);
    } catch {
      return;
    }
    if (!m || typeof m.t !== 'string') return;

    if (m.t === 'hello') {
      if (ws.joined) return;
      const r = incident.join({ id: m.id, cs: m.cs, role: m.role, team: m.team });
      ws.id = r.id;
      ws.role = r.role;
      ws.joined = true;
      socketsById.set(r.id, (socketsById.get(r.id) || 0) + 1);
      ws.send(JSON.stringify({ t: 'welcome', you: r, drill: !!drill, ...incident.snapshot() }));
      audit('join', { id: r.id, cs: r.cs, role: r.role });
      return;
    }
    if (!ws.joined) return;
    const isCmd = ws.role === 'command';

    switch (m.t) {
      case 'pos':
        incident.updatePos(ws.id, m);
        break;
      case 'ping':
        incident.addPing(ws.id, m.ping || {});
        break;
      case 'respond':
        incident.respond(ws.id, m.id, m.state);
        break;
      case 'mayday':
        incident.mayday(ws.id, { reason: m.reason, lat: m.lat, lon: m.lon, id: m.id });
        break;
      case 'mayday_cancel':
        incident.cancelMayday(ws.id);
        audit('mayday_cancel', { id: ws.id });
        break;
      case 'msg':
        if (!isCmd) break;
        {
          const text = String(m.text || '').trim().slice(0, 80);
          if (!text) break;
          const msg = { t: 'msg', text, level: m.level === 'urgent' ? 'urgent' : 'info', from: 'CMD', ts: Date.now() };
          broadcast(msg);
          audit('msg', msg);
        }
        break;
      case 'center':
        if (isCmd) incident.setCenter(Number(m.lat), Number(m.lon));
        break;
      case 'drill':
        if (isCmd) setDrill(!!m.on);
        break;
    }
  });

  ws.on('close', () => {
    if (!ws.joined) return;
    const n = (socketsById.get(ws.id) || 1) - 1;
    socketsById.set(ws.id, n);
    if (n <= 0) {
      socketsById.delete(ws.id);
      incident.leave(ws.id);
    }
  });
});

// Heartbeat (drop dead sockets) + state housekeeping.
setInterval(() => {
  for (const ws of wss.clients) {
    if (!ws.alive) {
      ws.terminate();
      continue;
    }
    ws.alive = false;
    ws.ping();
  }
}, 15e3);
setInterval(() => incident.tick(), 2e3);

server.listen(PORT, () => {
  const lan = Object.values(os.networkInterfaces())
    .flat()
    .filter((i) => i && i.family === 'IPv4' && !i.internal)
    .map((i) => `http://${i.address}:${PORT}`);
  console.log(`Beacon up: http://localhost:${PORT}  ${lan.join('  ')}`);
  console.log(`  HUD (desktop preview): http://localhost:${PORT}/glasses/?preview=1`);
  console.log(`  Command dashboard:     http://localhost:${PORT}/command/`);
  console.log(`  Audit log:             ${path.relative(ROOT, logFile)}`);
  if (process.env.DRILL === '1') setDrill(true);
});
