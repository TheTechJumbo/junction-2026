"""Local-only household store. Python standard library; no outbound network calls."""
import argparse
import hmac
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

FIELDS = {'id', 'type', 'source', 'ts', 'zone', 'object', 'evidence_url', 'confidence'}


def validate(event):
    if not isinstance(event, dict) or set(event) - FIELDS:
        raise ValueError('unknown event fields')
    for key in ('id', 'type', 'source', 'ts', 'zone', 'object'):
        if not isinstance(event.get(key), str) or not 0 < len(event[key]) <= 256:
            raise ValueError('missing or invalid event field')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', event['id']):
        raise ValueError('invalid id')
    if event['source'] not in ('sensor', 'vision', 'voice'):
        raise ValueError('invalid source')
    dt = datetime.fromisoformat(event['ts'].replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp needs timezone')
    c = event.get('confidence')
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or not 0 <= c <= 1:
        raise ValueError('invalid confidence')
    if event.get('evidence_url') is not None:
        raise ValueError('attach evidence through local artifact API')
    return {**event, 'evidence_url': None}


class Store:
    def __init__(self, root, retention=86400, reserve=128 * 1024**2):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.files = self.root / 'artifacts'
        self.files.mkdir(exist_ok=True, mode=0o700)
        self.retention, self.reserve = retention, reserve
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, event_id TEXT, mime TEXT NOT NULL, created REAL NOT NULL);
            ''')
        for path in self.root.glob('events.sqlite*'):
            path.chmod(0o600)
        self.cleanup()

    def db(self):
        return sqlite3.connect(self.root / 'events.sqlite', timeout=10)

    def put(self, event):
        event = validate(event)
        with self.lock, self.db() as db:
            previous = db.execute('SELECT body FROM events WHERE id=?', (event['id'],)).fetchone()
            if previous:
                old = json.loads(previous[0])
                if {**old, 'evidence_url': None} != event:
                    raise FileExistsError('event id conflict')
                return old
            db.execute('INSERT INTO events VALUES(?,?,?)', (event['id'], json.dumps(event), time.time()))
        return event

    def events(self):
        with self.lock, self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM events ORDER BY created,id')]

    def upload(self, data, mime, event_id=None):
        if mime not in ('image/jpeg', 'image/png', 'application/octet-stream', 'application/zip'):
            raise ValueError('unsupported artifact type')
        with self.lock, self.db() as db:
            row = db.execute('SELECT body FROM events WHERE id=?', (event_id,)).fetchone() if event_id else None
            if event_id and not row:
                raise KeyError('event not found')
            if shutil.disk_usage(self.root).free < self.reserve + len(data):
                raise OSError('insufficient storage')
            aid = uuid.uuid4().hex
            tmp, dest = self.files / (aid + '.part'), self.files / aid
            try:
                with tmp.open('xb') as f:
                    os.chmod(tmp, 0o600)
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                tmp.rename(dest)
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?)', (aid, event_id, mime, time.time()))
                if row:
                    event = json.loads(row[0])
                    event['evidence_url'] = '/artifacts/' + aid
                    db.execute('UPDATE events SET body=? WHERE id=?', (json.dumps(event), event_id))
            except Exception:
                tmp.unlink(missing_ok=True)
                dest.unlink(missing_ok=True)
                raise
            return {'id': aid, 'evidence_url': '/artifacts/' + aid}

    def artifact(self, aid):
        if not re.fullmatch('[a-f0-9]{32}', aid):
            raise KeyError('artifact not found')
        with self.lock, self.db() as db:
            row = db.execute('SELECT mime FROM artifacts WHERE id=?', (aid,)).fetchone()
            if not row:
                raise KeyError('artifact not found')
            return (self.files / aid).read_bytes(), row[0]

    def cleanup(self):
        with self.lock, self.db() as db:
            expired = db.execute('SELECT id FROM artifacts WHERE created<=?', (time.time() - self.retention,)).fetchall()
            for (aid,) in expired:
                (self.files / aid).unlink(missing_ok=True)
                for eid, body in db.execute('SELECT id,body FROM events').fetchall():
                    event = json.loads(body)
                    if event.get('evidence_url') == '/artifacts/' + aid:
                        event['evidence_url'] = None
                        db.execute('UPDATE events SET body=? WHERE id=?', (json.dumps(event), eid))
                db.execute('DELETE FROM artifacts WHERE id=?', (aid,))
            known = {r[0] for r in db.execute('SELECT id FROM artifacts')}
            for p in self.files.iterdir():
                if p.name not in known:
                    p.unlink()

    def export(self):
        # Explicit allowlist: never export local URLs, paths, attachments or arbitrary fields.
        return [{k: e[k] for k in ('id', 'type', 'source', 'ts', 'zone', 'object', 'confidence')} | {'evidence_url': None}
                for e in self.events()]


def handler(store, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # URLs, tokens and household content must not reach logs.

        def respond(self, code, data, mime='application/json'):
            raw = json.dumps(data, allow_nan=False).encode() if mime == 'application/json' else data
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.dispatch(False)

        def do_POST(self):
            self.dispatch(True)

        def dispatch(self, post):
            self.connection.settimeout(10)
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                return self.respond(401, {'error': 'unauthorized'})
            try:
                store.cleanup()
                url = urlparse(self.path)
                path = url.path
                if not post:
                    if path == '/health':
                        with store.db() as db:
                            db.execute('SELECT 1').fetchone()
                        return self.respond(200, {'database': 'ok', 'disk_free_bytes': shutil.disk_usage(store.root).free,
                                                  'inference': 'unavailable: run VGGT feasibility probe'})
                    if path in ('/events', '/timeline'):
                        return self.respond(200, {'events': store.events()})
                    if path == '/export/events':
                        return self.respond(200, {'events': store.export()})
                    if path.startswith('/artifacts/'):
                        data, mime = store.artifact(path.split('/')[-1])
                        return self.respond(200, data, mime)
                else:
                    if self.headers.get('Transfer-Encoding'):
                        raise ValueError('chunked uploads unsupported')
                    n = int(self.headers.get('Content-Length', '-1'))
                    if n < 1 or n > 32 * 1024**2:
                        return self.respond(413, {'error': 'body must be 1 byte to 32 MiB'})
                    if shutil.disk_usage(store.root).free < store.reserve + n:
                        return self.respond(507, {'error': 'insufficient storage'})
                    data = self.rfile.read(n)
                    if len(data) != n:
                        raise ValueError('incomplete upload')
                    if path == '/events':
                        return self.respond(200, store.put(json.loads(data)))
                    if path == '/artifacts' or re.fullmatch(r'/events/[A-Za-z0-9_-]+/evidence', path):
                        event_id = path.split('/')[2] if path.startswith('/events/') else None
                        return self.respond(201, store.upload(data, self.headers.get('Content-Type', ''), event_id))
                self.respond(404, {'error': 'not found'})
            except FileExistsError:
                self.respond(409, {'error': 'event id conflict'})
            except (KeyError, FileNotFoundError):
                self.respond(404, {'error': 'not found'})
            except (ValueError, TypeError, UnicodeError):
                self.respond(400, {'error': 'invalid request'})
            except (OSError, sqlite3.Error):
                self.respond(503, {'error': 'storage unavailable'})
    return Handler


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8091)
    args = parser.parse_args()
    token = os.environ.get('UNOQ_TOKEN', '')
    if len(token) < 32:
        parser.error('UNOQ_TOKEN must contain at least 32 characters')
    store = Store(os.environ.get('UNOQ_DATA', str(Path.home() / '.local/share/junction')),
                  retention=int(os.environ.get('UNOQ_RETENTION_SECONDS', '86400')))
    server = ThreadingHTTPServer((args.host, args.port), handler(store, token))
    def reap():
        while True:
            time.sleep(60)
            try:
                store.cleanup()
            except (OSError, sqlite3.Error):
                pass
    threading.Thread(target=reap, daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
