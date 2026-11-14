"""Integration checks for the compiled Rust CLI; build --release first."""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from unoq.store import Store, handler

BINARY = Path(__file__).resolve().parents[1] / 'sensor-sim/target/release/sensor-sim'
TOKEN = 'test-only-token-' + 'x' * 32


@contextmanager
def server_for(handler_class):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@unittest.skipUnless(BINARY.exists(), 'build unoq/sensor-sim with cargo build --release first')
class SensorSimulatorTests(unittest.TestCase):
    def run_cli(self, *args, token=TOKEN):
        env = {k: v for k, v in os.environ.items() if k not in ('UNOQ_TOKEN', 'UNOQ_BASE_URL')}
        if token is not None:
            env['UNOQ_TOKEN'] = token
        return subprocess.run([str(BINARY), *args], env=env, capture_output=True, text=True, timeout=20)

    def test_dry_run_matches_contract_and_needs_no_token(self):
        result = self.run_cli('pillbox', '--dry-run', token=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        event = json.loads(result.stdout)
        self.assertTrue(event['id'].startswith('sim-'))
        self.assertEqual(event['type'], 'pillbox_open')
        self.assertEqual(event['object'], 'pillbox')
        self.assertEqual(event['source'], 'sensor')
        self.assertEqual(event['zone'], 'kitchen')
        self.assertEqual(event['confidence'], 1)
        self.assertIsNone(event['evidence_url'])
        self.assertLess(abs((datetime.now(timezone.utc) - datetime.fromisoformat(event['ts'])).total_seconds()), 5)

    def test_real_store_timeline_replay_and_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            with server_for(handler(store, TOKEN)) as base:
                common = ['--base-url', base, '--event-id', 'sim-replay',
                          '--timestamp', '2026-11-14T08:00:00Z']
                for _ in range(2):
                    result = self.run_cli('pillbox', *common)
                    self.assertEqual(result.returncode, 0, result.stderr)
                conflict = self.run_cli('door', *common)
                self.assertNotEqual(conflict.returncode, 0)
                self.assertIn('409', conflict.stderr)
                door = self.run_cli('door', '--zone', 'entrance', '--base-url', base)
                self.assertEqual(door.returncode, 0, door.stderr)
                from urllib.request import Request, urlopen
                with urlopen(Request(base + '/timeline', headers={'Authorization': f'Bearer {TOKEN}'})) as response:
                    events = json.load(response)['events']
                self.assertEqual(len(events), 2)
                self.assertEqual(events[0]['type'], 'pillbox_open')
                self.assertEqual(events[1]['type'], 'door_open')
                self.assertEqual(events[1]['zone'], 'entrance')
                self.assertEqual(Store(root).events(), events)

    def test_lost_response_after_commit_retries_without_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            base_handler = handler(store, TOKEN)
            attempts = []

            class LoseFirstReply(base_handler):
                def respond(self, code, data, mime='application/json'):
                    attempts.append(code)
                    if len(attempts) == 1:
                        self.close_connection = True
                        return  # Store committed, but the client receives no response.
                    super().respond(code, data, mime)

            with server_for(LoseFirstReply) as base:
                result = self.run_cli('pillbox', '--base-url', base)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(attempts, [200, 200])
            self.assertEqual(len(store.events()), 1)

    def test_retry_policy_stable_payload_and_secret_redaction(self):
        for statuses, expected, success in [([503, 200], 2, True), ([429, 200], 2, True),
                                             ([401], 1, False), ([409], 1, False),
                                             ([302], 1, False), ([503] * 3, 3, False)]:
            with self.subTest(statuses=statuses):
                requests = []

                class Mock(BaseHTTPRequestHandler):
                    def log_message(self, *_):
                        pass

                    def do_POST(self):
                        body = self.rfile.read(int(self.headers['Content-Length']))
                        requests.append((self.path, self.headers.get('Authorization'), body))
                        self.send_response(statuses[min(len(requests) - 1, len(statuses) - 1)])
                        self.send_header('Location', '/should-not-follow')
                        self.send_header('Content-Length', '0')
                        self.end_headers()

                with server_for(Mock) as base:
                    result = self.run_cli('pillbox', '--base-url', base)
                self.assertEqual(result.returncode == 0, success, result.stderr)
                self.assertEqual(len(requests), expected)
                self.assertEqual(len({r[2] for r in requests}), 1)
                self.assertTrue(all(r[:2] == ('/events', f'Bearer {TOKEN}') for r in requests))
                self.assertNotIn(TOKEN, result.stdout + result.stderr)
                self.assertNotIn(base, result.stdout + result.stderr)

    def test_bad_inputs_and_missing_token_fail(self):
        for args in [('pillbox', '--dry-run', '--event-id', 'real-event'),
                     ('pillbox', '--dry-run', '--timestamp', '2026-11-14T08:00:00'),
                     ('pillbox', '--dry-run', '--zone', ''),
                     ('pillbox', '--attempts', '0'),
                     ('pillbox', '--base-url', 'http://user:secret@localhost'),
                     ('pillbox', '--base-url', 'http://localhost/?token=secret')]:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_cli(*args).returncode, 0)
        self.assertNotEqual(self.run_cli('pillbox', token=None).returncode, 0)


if __name__ == '__main__':
    unittest.main()
