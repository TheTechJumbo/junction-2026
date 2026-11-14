import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import reader


class QueueTests(unittest.TestCase):
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
