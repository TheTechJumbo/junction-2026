"""Replay explicitly synthetic events into a local UNO Q store; no cloud destination."""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import urllib.request
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8091')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    url = urlparse(args.url)
    # Only a loopback endpoint or explicitly private IP is allowed. No DNS names/redirects.
    try:
        ip = ipaddress.ip_address(url.hostname)
        local = ip.is_loopback or (ip.is_private and not ip.is_unspecified and not ip.is_link_local)
    except ValueError:
        local = False
    if not local or url.scheme not in ('http', 'https') or url.username or url.password or url.query:
        parser.error('use a loopback/private IP endpoint, preferably USB forwarding')
    token = os.environ.get('UNOQ_TOKEN', '')
    if not args.dry_run and len(token) < 32:
        parser.error('set UNOQ_TOKEN privately before replay')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
    for event in json.loads(Path(__file__).with_name('events.json').read_text()):
        if args.dry_run:
            print(json.dumps(event))
        else:
            req = urllib.request.Request(args.url.rstrip('/') + '/events', data=json.dumps(event).encode(),
                                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            with opener.open(req, timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError('replay rejected')
            print('Stored synthetic event: ' + event['id'])


if __name__ == '__main__':
    main()
