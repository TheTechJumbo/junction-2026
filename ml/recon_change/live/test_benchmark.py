"""Exercise the benchmark client against local HTTP servers, including redirect isolation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT = Path(__file__).with_name('benchmark.py')

class BenchmarkTests(unittest.TestCase):
    def test_fresh_and_redirect_does_not_forward_token(self):
        seen = []
        mode = ['success']
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.headers.get('X-Key')))
                self.rfile.read(int(self.headers['Content-Length']))
                if self.path.startswith('/redirect'):
                    self.send_response(307)
                    self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/stolen')
                    self.end_headers()
                    return
                self.send_response(200); self.end_headers()
                result = {'ok': True, 'seconds': {'total': 1}, 'rust_enabled': True,
                                             'revision': 'abc', 'source_dirty': False, 'rust_threads': 4, 'rust_disabled_functions': [], 'device': 'mock', 'on_gpu': True, 'settings': {}, 'images': ['secret-image']}
                if mode[0] == 'reused': result['reused'] = True
                if mode[0] == 'missing': result.pop('rust_enabled')
                if mode[0] == 'revision': result.pop('revision')
                if mode[0] == 'seconds': result.pop('seconds')
                self.wfile.write(json.dumps(result).encode())
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                f = Path(d)/'fixture.jpg'; f.write_bytes(b'public')
                args = [sys.executable, str(SCRIPT), '--base-url', f'http://127.0.0.1:{server.server_port}',
                        '--zone', 'public', '--check', str(f), '--locate', str(f), '--expect-rust', 'on']
                env = {**os.environ, 'RELAY_TOKEN': 'token-value'}
                result = subprocess.run(args, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('fresh=1', seen[1][0]); self.assertNotIn('secret-image', result.stdout)
                self.assertNotIn('token-value', result.stdout)
                for bad in ('reused', 'missing', 'revision', 'seconds'):
                    mode[0] = bad
                    self.assertNotEqual(subprocess.run(args, env=env, capture_output=True).returncode, 0, bad)
                mode[0] = 'success'
                args[args.index('--base-url')+1] += '/redirect'
                result = subprocess.run(args, env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(path.startswith('/stolen') for path, _ in seen))
                self.assertNotIn('token-value', result.stderr)
        finally:
            server.shutdown(); thread.join(); server.server_close()

if __name__ == '__main__': unittest.main()
