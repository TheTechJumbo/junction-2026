"""Boot-started UNO Q Bridge reader with durable, idempotent event delivery."""
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

LOG = logging.getLogger('pillbox')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Queue:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, body TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)')
        self.db.commit()

    def record(self, session, sequence, age_ms, now=None):
        event_id = f'unoq-pillbox-{session}-{sequence}'
        timestamp = (time.time() if now is None else now) - age_ms / 1000
        body = dict(id=event_id, type='pillbox_open', source='sensor',
                    ts=datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                    zone='kitchen', object='pillbox', evidence_url=None, confidence=1)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO events(id,body) VALUES (?,?)',
                            (event_id, json.dumps(body, separators=(',', ':'))))
        return event_id

    def pending(self):
        return self.db.execute('SELECT id,body FROM events WHERE delivered=0 ORDER BY rowid LIMIT 1').fetchone()

    def accepted(self, event_id):
        with self.db:
            self.db.execute('UPDATE events SET delivered=1 WHERE id=?', (event_id,))


def deliver(queue, endpoint, token):
    pending = queue.pending()
    if pending is None:
        return False
    event_id, body = pending
    opener = build_opener(NoRedirect(), ProxyHandler({}))
    started = time.monotonic()
    try:
        request = Request(endpoint.rstrip('/') + '/events', data=body.encode(),
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with opener.open(request, timeout=5) as response:
            receipt = json.loads(response.read(65537))
            if response.status != 200 or receipt.get('durability') not in ('local', 'replicated') or not any(event.get('id') == event_id for event in receipt.get('events', [])):
                raise ValueError('unexpected acceptance receipt')
        observed_at = datetime.fromisoformat(json.loads(body)['ts']).timestamp()
        queue.accepted(event_id)
        LOG.info('accepted event=%s sensor_to_api_ack_ms=%.1f http_ms=%.1f', event_id,
                 (time.time() - observed_at) * 1000, (time.monotonic() - started) * 1000)
        return True
    except HTTPError as exc:
        LOG.error('delivery HTTP %s; event retained', exc.code)
    except Exception as exc:
        LOG.warning('delivery %s; event retained', type(exc).__name__)
    return False


def clock_ready():
    # RTC on this board starts at 1970. Network time must be established on each boot.
    result = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                            capture_output=True, text=True, timeout=3)
    return result.returncode == 0 and result.stdout.strip() == 'yes'


def sender(path, endpoint, token, stop):
    queue = Queue(path)
    delay = 1
    while not stop.is_set():
        if deliver(queue, endpoint, token):
            delay = 1
        else:
            stop.wait(delay)
            delay = min(delay * 2, 30) if queue.pending() else 1


def main():
    from arduino.router_bridge import Bridge
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    endpoint = os.environ['PILLBOX_API_URL']
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('invalid API base URL')
    token = os.environ['API_TOKEN']
    if not token or any(c.isspace() for c in token):
        raise ValueError('invalid API token')
    path = Path(os.environ.get('PILLBOX_QUEUE', str(Path.home() / '.local/share/junction/pillbox.sqlite')))
    path.parent.mkdir(parents=True, exist_ok=True)
    queue = Queue(path)
    stop = threading.Event()
    worker = threading.Thread(target=sender, args=(path, endpoint, token, stop), daemon=True)
    worker.start()
    bridge = Bridge()
    bridge.connect(timeout=5)
    synchronized = False
    try:
        while True:
            try:
                if not synchronized:
                    synchronized = clock_ready()
                    if not synchronized:
                        LOG.warning('waiting for network clock synchronization; MCU retains queued openings')
                        time.sleep(5)
                        continue
                session = bridge.call('pillbox_session', uuid.uuid4().hex, timeout=3)
                reading = bridge.call('pillbox_peek', timeout=3)
                # seq, state, elapsed millis, MCU queue overflow count
                sequence, state, age, lost = map(int, reading.split(','))
                if lost:
                    LOG.error('MCU queue overflow count=%s', lost)
                if sequence:
                    if state == 1:
                        event_id = queue.record(session, sequence, age)
                        LOG.info('persisted event=%s sensor_to_queue_ms=%s', event_id, age)
                    bridge.call('pillbox_ack', str(sequence), timeout=3)
                stop.wait(0.1)
            except Exception as exc:
                LOG.warning('Bridge/queue %s; retrying', type(exc).__name__)
                stop.wait(1)
    finally:
        stop.set()
        bridge.disconnect()
        worker.join(timeout=6)


if __name__ == '__main__':
    main()
