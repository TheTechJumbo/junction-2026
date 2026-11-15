#!/usr/bin/env python3
"""Sharing a pair of glasses: the pairing code round-trips, and the watch-only key reads the glasses
display and nothing else.

    python live/test_share.py
"""
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import relay  # noqa: E402
import share  # noqa: E402


class NoCluster:
    def call(self, header, blob=b""):
        return {"ok": False, "error": "no cluster in this test"}, b""


def main():
    code = share.pairing_code("https://abc-def.trycloudflare.com/", "v1ew&key=1", "Seb's Ray-Ban Meta")
    assert code.startswith("DL1-") and "=" not in code and "+" not in code and "/" not in code, code
    assert share.read_code(code) == ("https://abc-def.trycloudflare.com", "v1ew&key=1", "Seb's Ray-Ban Meta")

    handler = relay.make_handler(NoCluster(), Path(tempfile.mkdtemp(prefix="share-test-")), "full-key", None, pick=None, view_token="view-key")
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def status(path, key, body=None):
        request = urllib.request.Request(url + path, data=body, headers={"X-Key": key}, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as err:
            return err.code

    assert status("/hud", "view-key") == 200, "the watch-only key opens the glasses display"
    assert status("/reminders", "view-key") == 200, "... and what it polls"
    assert status("/history", "view-key") == 403, "but nothing else"
    assert status("/", "view-key") == 403, "not the phone page that records clips"
    assert status("/ask", "view-key", b'{"question": "Did I take my pills?"}') == 403, "and never a POST"
    assert status("/reminder", "view-key", b'{"text": "x", "time": "10:00"}') == 403
    assert status("/hud", "wrong") == 403
    assert status("/history", "full-key") == 200, "the full key still opens everything"
    server.shutdown()
    print("share: all checks passed")


if __name__ == "__main__":
    main()
