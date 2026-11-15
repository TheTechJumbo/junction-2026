import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from unoq.store import Store, handler

EVENT = dict(id='test-1', type='pillbox_open', source='sensor', ts='2026-11-14T08:00:00Z', zone='kitchen', object='pillbox', confidence=1, evidence_url=None)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_restart_duplicate_and_conflict(self):
        self.store.put(EVENT)
        self.store.put(EVENT)
        other = Store(self.tmp.name)
        self.assertEqual(other.events(), [EVENT])
        with self.assertRaises(FileExistsError):
            other.put({**EVENT, 'object': 'door'})

    def test_evidence_export_and_retention(self):
        self.store.put(EVENT)
        upload = self.store.upload(b'private image', 'image/jpeg', EVENT['id'])
        self.assertEqual(self.store.artifact(upload['id'])[0], b'private image')
        self.assertEqual(self.store.export(), [EVENT])
        self.assertNotIn(upload['id'], json.dumps(self.store.export()))
        self.store.retention = -1
        self.store.cleanup()
        self.assertIsNone(self.store.events()[0]['evidence_url'])
        with self.assertRaises(KeyError):
            self.store.artifact(upload['id'])

    def test_disk_full_and_orphan_recovery(self):
        self.store.reserve = 10**20
        with self.assertRaises(OSError):
            self.store.upload(b'x', 'image/jpeg')
        orphan = self.store.files / 'crash.part'
        orphan.write_bytes(b'incomplete')
        Store(self.tmp.name)
        self.assertFalse(orphan.exists())

    def test_invalid_event_and_path(self):
        for event in ({**EVENT, 'image': 'private'}, {**EVENT, 'confidence': float('nan')}, {**EVENT, 'evidence_url': 'https://cloud/image'}, {**EVENT, 'id': '../secret'}):
            with self.assertRaises(ValueError):
                self.store.put(event)
        with self.assertRaises(KeyError):
            self.store.artifact('../events.sqlite')

    def test_http_auth_upload_and_restart_independence(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.store, 't' * 32))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with self.assertRaises(HTTPError) as err:
                urlopen(base + '/health')
            self.assertEqual(err.exception.code, 401)
            def call(path, body=None, mime='application/json'):
                return urlopen(Request(base + path, data=body, headers={'Authorization': 'Bearer ' + 't'*32, 'Content-Type': mime}))
            self.assertEqual(call('/events', json.dumps(EVENT).encode()).status, 200)
            result = json.load(call('/events/test-1/evidence', b'jpeg', 'image/jpeg'))
            self.assertEqual(call(result['evidence_url']).read(), b'jpeg')
            self.assertEqual(json.load(call('/export/events'))['events'], [EVENT])
            self.assertEqual(json.load(call('/health'))['database'], 'ok')
            # Oversized/incomplete requests must never create an artifact.
            import socket
            with socket.create_connection(server.server_address) as s:
                s.sendall(b'POST /artifacts HTTP/1.0\r\nAuthorization: Bearer ' + b't'*32 + b'\r\nContent-Type: image/jpeg\r\nContent-Length: 10\r\n\r\nxx')
                s.shutdown(socket.SHUT_WR)
                self.assertIn(b'400', s.recv(1024))
            self.assertEqual(len(list(self.store.files.iterdir())), 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
