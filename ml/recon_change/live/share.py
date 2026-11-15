#!/usr/bin/env python3
"""Share a pair of glasses with the Tuttu apps: put the relay on the internet through a free Cloudflare
quick tunnel and print a pairing code that carries its address, the WATCH-ONLY key and the pair's name.

    python live/share.py --port 8090 --view-token VIEW_KEY --name "Seb's Ray-Ban Meta"
    python live/share.py --url https://my-relay.example --view-token VIEW_KEY   # already public: no tunnel

The relay prints its view key when it starts (--view-token). The code only lets the apps show the glasses
display and read what it reads (relay.VIEW_PATHS); it cannot spend GPU time or change anything.
Paste the code in either app's glasses list (Add glasses), or open the printed link on the phone.
Needs `cloudflared` on the PATH for the tunnel (winget install Cloudflare.cloudflared, brew install cloudflared).
The quick tunnel's address changes every time it starts: share the new code after a restart.
"""
import argparse
import base64
import re
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, quote, urlencode

SITE = "https://thetechjumbo.github.io/junction-2026/pair.html"
PREFIX = "TU1-"


def pairing_code(url, key, name):
    """The code the apps read: TU1- then the address, key and name, base64url without padding."""
    raw = urlencode({"u": url.rstrip("/"), "k": key, "n": name}, quote_via=quote)
    return PREFIX + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def read_code(code):
    """The other way round (what the apps do), for checking a code by hand."""
    body = code.strip()[len(PREFIX):]
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
    q = parse_qs(raw)
    return q["u"][0], q["k"][0], q.get("n", [""])[0]


def tunnel(port):
    exe = shutil.which("cloudflared")
    if not exe:
        sys.exit("cloudflared is not installed: winget install Cloudflare.cloudflared (or brew install cloudflared), "
                 "or pass --url if the relay is already public.")
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        found = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if found:
            return proc, found.group(0)
    sys.exit("cloudflared stopped before it gave an address.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8090, help="the relay's port on this laptop")
    ap.add_argument("--view-token", required=True, help="the relay's watch-only key (it prints it at start)")
    ap.add_argument("--name", default="Seb's Ray-Ban Meta", help="the pair's name in the apps")
    ap.add_argument("--url", help="the relay's public address, if it already has one (no tunnel then)")
    ap.add_argument("--token", help="the relay's full key: also print the OWNER code, for the wearer's own phone only")
    args = ap.parse_args()
    proc, url = (None, args.url) if args.url else tunnel(args.port)
    code = pairing_code(url, args.view_token, args.name)
    print(f"\n{args.name} is shared at {url}\n\nwatch code (family app, judges; paste in Glasses > Add glasses):\n\n  {code}\n")
    print(f"or open on the phone: {SITE}#{code}\n", flush=True)
    if args.token:
        owner = pairing_code(url, args.token, args.name)
        print(f"owner code (the wearer app on the wearer's own phone; keep it private, it can spend GPU time):\n\n  {owner}\n", flush=True)
    if proc:
        print("Keep this running; Ctrl+C stops sharing.", flush=True)
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()


if __name__ == "__main__":
    main()
