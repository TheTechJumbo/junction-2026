import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
import reader


class QueueTests(unittest.TestCase):
    def test_reader_persists_before_string_acknowledgment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.sqlite'
            bridge = Mock()

            def call(method, *args, **kwargs):
                if method == 'pillbox_session':
                    return 'boot'
                if method == 'pillbox_peek':
                    return '1,1,50,0'
                self.assertEqual((method, args), ('pillbox_ack', ('1',)))
                queue = reader.Queue(path)
                self.assertEqual(queue.pending()[0], 'unoq-pillbox-boot-1')
                queue.db.close()
                raise KeyboardInterrupt

            bridge.call.side_effect = call
            module = types.ModuleType('arduino.router_bridge')
            module.Bridge = Mock(return_value=bridge)
            environment = {'PILLBOX_API_URL': 'http://local', 'API_TOKEN': 'private',
                           'PILLBOX_QUEUE': str(path)}
            with patch.dict(os.environ, environment), patch.dict('sys.modules', {'arduino.router_bridge': module}), \
                    patch.object(reader, 'clock_ready', return_value=True), patch.object(reader.threading, 'Thread'), \
                    patch.object(reader.os, 'umask'):
                with self.assertRaises(KeyboardInterrupt):
                    reader.main()
            bridge.disconnect.assert_called_once()

    def test_durable_retry_identity_and_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queue.sqlite'
            q = reader.Queue(path)
            key = q.record('boot', 1, 200, now=2000000000)
            original = q.pending()
            q.db.close()
            q = reader.Queue(path)
            q.record('boot', 1, 100, now=2000000001)
            self.assertEqual(q.pending(), original)
            self.assertTrue(json.loads(original[1])['ts'].endswith('+00:00'))
            q.accepted(key)
            self.assertIsNone(q.pending())
            q.db.close()

    def test_failed_or_malformed_receipt_retains_event(self):
        with tempfile.TemporaryDirectory() as directory:
            q = reader.Queue(Path(directory) / 'queue.sqlite')
            q.record('boot', 1, 0)
            with patch.object(reader, 'build_opener') as build:
                build.return_value.open.side_effect = HTTPError('http://local', 401, 'auth', {}, None)
                self.assertFalse(reader.deliver(q, 'http://local', 'private'))
                self.assertIsNotNone(q.pending())
                build.return_value.open.side_effect = None
                response = build.return_value.open.return_value.__enter__.return_value
                response.status = 200
                response.read.return_value = b'{"events":[],"durability":"local"}'
                self.assertFalse(reader.deliver(q, 'http://local', 'private'))
                self.assertIsNotNone(q.pending())
                response.read.return_value = json.dumps({'events':[{'id':q.pending()[0]}], 'durability':'local'}).encode()
                self.assertTrue(reader.deliver(q, 'http://local', 'private'))
                self.assertIsNone(q.pending())
            q.db.close()


if __name__ == '__main__':
    unittest.main()
