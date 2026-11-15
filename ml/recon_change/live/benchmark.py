#!/usr/bin/env python3
"""Benchmark explicit public fixtures against an already running relay and baseline."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', required=True)
    p.add_argument('--zone', required=True)
    p.add_argument('--check', type=Path, required=True, help='public check video or frame zip')
    p.add_argument('--locate', type=Path, required=True, help='public locate image or frame zip')
    p.add_argument('--repeat', type=int, default=1)
    p.add_argument('--expect-rust', choices=('on', 'off'), required=True)
    a = p.parse_args()
    u = urlsplit(a.base_url)
    if u.scheme not in ('http', 'https') or not u.netloc or u.username or u.password or u.query or u.fragment:
        p.error('base URL must be an HTTP(S) origin without credentials, query or fragment')
    if a.repeat < 1:
        p.error('repeat must be positive')
    token = os.environ.get('RELAY_TOKEN')
    if not token:
        p.error('set RELAY_TOKEN in the environment')
    opener = build_opener(NoRedirect())
    for op, fixture in [('check', a.check), ('locate', a.locate)]:
        data = fixture.read_bytes()
        for n in range(a.repeat):
            query = {'zone': a.zone}
            if op == 'locate':
                query['fresh'] = '1'
            req = Request(a.base_url.rstrip('/') + '/' + op + '?' + urlencode(query), data=data,
                          headers={'X-Key': token, 'Content-Type': 'application/octet-stream'}, method='POST')
            start = time.perf_counter()
            with opener.open(req, timeout=600) as response:
                result = json.load(response)
            elapsed = time.perf_counter() - start
            if not result.get('ok') or result.get('reused') or result.get('view', {}).get('model_asked') is False:
                raise RuntimeError(f'{op} did not produce a fresh successful model trial')
            if (result.get('rust_enabled') is not (a.expect_rust == 'on') or not result.get('revision') or
                not isinstance(result.get('rust_disabled_functions'), list) or
                (a.expect_rust == 'on' and result['rust_disabled_functions'])):
                raise RuntimeError('worker Rust state or source revision missing or unexpected')
            seconds = result.get('seconds')
            if (not isinstance(seconds, dict) or not seconds or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
                    for v in seconds.values()) or not result.get('device') or
                not isinstance(result.get('on_gpu'), bool) or not isinstance(result.get('settings'), dict) or
                not isinstance(result.get('source_dirty'), bool) or
                (a.expect_rust == 'on' and not isinstance(result.get('rust_threads'), int))):
                raise RuntimeError('worker timing or runtime metadata is incomplete')
            # Whitelist metadata: never persist returned images, paths, tokens, or the fixture.
            print(json.dumps({'operation': op, 'trial': n + 1, 'fixture_sha256': hashlib.sha256(data).hexdigest(), 'client_wall_s': elapsed,
                              'revision': result['revision'], 'source_dirty': result.get('source_dirty'),
                              'server_seconds': result.get('seconds'), 'device': result.get('device'),
                              'on_gpu': result.get('on_gpu'), 'rust_enabled': result.get('rust_enabled'), 'rust_threads': result.get('rust_threads'), 'rust_disabled_functions': result.get('rust_disabled_functions'),
                              'settings': result.get('settings'), 'client_python': platform.python_version(),
                              'client_platform': platform.platform()}), flush=True)

if __name__ == '__main__':
    main()
