#!/usr/bin/env python3
"""The relay as it runs on the cluster, in the servers job (cluster/servers.slurm): live/relay.py, unchanged, with three
things added around it. Every relay option works as before; run it with relay.py's own options:

    RELAY_KEY=... python live/relay_cluster.py --local /scratch/$USER/junction-recon --quick --train-host local --host 0.0.0.0

  RELAY_KEY           the access key, read from the environment and passed as --token. It is not on the command line:
                      on a shared compute node every user can read every process's command line (/proc is not hidden
                      there). It is taken out of the environment before the gateway and the training jobs start.
  --train-host local  recordings are trained (live/remote_train.py) on this node itself: its ssh and scp steps run here
                      as local commands, so cluster/video_map.slurm is submitted with sbatch from the deployed folder
                      (BEACON_REMOTE_ROOT), with no SSH hop. The recording's frames are used where the gateway put
                      them (BEACON_ROOT/recordings/ZONE/SESSION/frames), not copied.
  RELAY_ANONYMISE=1   the anonymisation hook: every photo and zip of frames that reaches /locate, /recording, /baseline,
                      /check or /enroll goes through live/anonymise.py (faces and people blurred, metadata gone) before
                      the relay keeps it or hands it to the gateway (the queue, recordings/, baselines/, the workers).
                      Videos are cleaned frame by frame: the relay picks their frames into zips, and every zip or photo
                      that goes to the gateway and was not cleaned on arrival is cleaned there. A picture that cannot be
                      read is refused, never sent on as it came (fail closed). Off by default; with it on and
                      anonymise.py missing, the relay does not start.

What the hook does not cover: a whole video sent to /recording is written to a temporary file (TMPDIR, on scratch)
while its frames are taken out, and deleted right after.
"""
import hmac
import inspect
import io
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
for folder in (HERE.parent, HERE):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

LOCAL_HOST = "local"
# Kept out of the environment of anything started from here (the gateway, sbatch and the training jobs).
SECRETS = ("RELAY_KEY", "API_TOKEN", "EVENT_API_TOKEN", "VISION_RELAY_KEY")
ANONYMISED_PATHS = {"/locate", "/recording", "/baseline", "/check", "/enroll"}


def local_env():
    """What a fresh login on the cluster would have: no variables of the servers job's own allocation (a job
    submitted from inside a job would otherwise inherit them), no relay temp folder, no secrets."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("SLURM_", "SBATCH_", "SRUN_")) and k not in ("TMPDIR", *SECRETS)}


def train_here():
    """--train-host local: live/remote_train.py's SSH steps run as commands on this machine."""
    import remote_train
    ssh, scp, fetch, train = remote_train._ssh, remote_train._scp, remote_train._fetch, remote_train.train

    def _ssh(host, command, timeout=120):
        if host != LOCAL_HOST:
            return ssh(host, command, timeout)
        # The same text a login shell at the far end of ssh would be given, so quoting works as it does there.
        done = subprocess.run(["bash", "-c", f'bash -lc "{command}"'], capture_output=True, text=True, timeout=timeout,
                              env=local_env())
        if done.returncode != 0:
            raise RuntimeError(f"{command.split()[0]} failed here: {(done.stderr or done.stdout).strip()[-300:]}")
        return done.stdout

    def _scp(host, sources, target, cwd, timeout=900):
        if host != LOCAL_HOST:
            return scp(host, sources, target, cwd, timeout)
        return remote_train._run(["cp", "--", *sources, target], cwd=cwd, timeout=timeout)

    def _fetch(host, source, cwd, timeout=600):
        if host != LOCAL_HOST:
            return fetch(host, source, cwd, timeout)
        # source may hold a * (renders/*.jpg): let bash expand it, as scp's far end does
        return subprocess.run(["bash", "-c", f"cp -- {source} ."], cwd=cwd, capture_output=True, timeout=timeout,
                              env=local_env()).returncode == 0

    def _train(*args, **kwargs):
        call = inspect.signature(train).bind(*args, **kwargs)
        call.apply_defaults()
        given = call.arguments
        host = given.get("host") or os.environ.get("BEACON_TRAIN_HOST")
        if host == LOCAL_HOST and not given.get("remote_frames") and Path(given["source"]).is_dir():
            given["remote_frames"] = str(Path(given["source"]).resolve())  # already in place: nothing to copy
        return train(*call.args, **call.kwargs)

    remote_train._ssh, remote_train._scp, remote_train._fetch, remote_train.train = _ssh, _scp, _fetch, _train


def anonymise_on_arrival(relay, token):
    """RELAY_ANONYMISE=1: the hook described at the top. Raises ImportError when anonymise.py is not there."""
    import anonymise

    cleaned_here = threading.local()  # set while a request whose body was cleaned on arrival is being handled

    def clean(data):
        """A photo or a zip of frames, anonymised; None for anything else (a video). ValueError: unreadable."""
        if data[:2] == b"PK":
            return anonymise.anonymise_zip(data)[0]
        if relay.is_photo(data):
            return anonymise.anonymise_jpeg(data)[0]
        return None

    make_handler = relay.make_handler

    def make_anonymising_handler(*args, **kwargs):
        handler = make_handler(*args, **kwargs)

        class Anonymising(handler):
            def do_POST(self):
                url = urlparse(self.path)
                given = parse_qs(url.query).get("k", [""])[0] or self.headers.get("X-Key", "")
                size = int(self.headers.get("Content-Length", 0) or 0)
                if url.path not in ANONYMISED_PATHS or not 0 < size <= relay.MAX_BODY \
                        or not hmac.compare_digest(given.encode(), token.encode()):
                    return super().do_POST()  # nothing to clean here, or a wrong key: relay.py answers as it does
                body, rfile = self.rfile.read(size), self.rfile
                try:
                    clean_body = clean(body)
                except ValueError as err:
                    return self.send_json(400, {"ok": False, "error": f"Not sent on: the picture could not be anonymised ({err})."})
                del self.headers["Content-Length"]
                self.headers["Content-Length"] = str(len(clean_body if clean_body is not None else body))
                self.rfile = io.BytesIO(clean_body if clean_body is not None else body)
                cleaned_here.on = clean_body is not None
                try:
                    return super().do_POST()
                finally:
                    cleaned_here.on, self.rfile = False, rfile

        return Anonymising

    call = relay.Link.call

    def anonymised_call(self, header, blob=b""):
        if blob and not getattr(cleaned_here, "on", False):
            try:
                blob = clean(blob)
            except ValueError as err:
                return {"ok": False, "error": f"Not sent on: the pictures could not be anonymised ({err})."}, b""
            if blob is None:
                return {"ok": False, "error": "Not sent on: a video cannot be anonymised as it is; send photos or frames."}, b""
        return call(self, header, blob)

    relay.make_handler, relay.Link.call = make_anonymising_handler, anonymised_call


def main():
    token = os.environ.pop("RELAY_KEY", "")
    for name in ("API_TOKEN", "VISION_RELAY_KEY"):  # the relay itself reads only EVENT_API_TOKEN
        os.environ.pop(name, None)
    if not token or "--token" in sys.argv:
        sys.exit("relay_cluster.py: give the access key as RELAY_KEY in the environment, not as --token")
    import relay
    train_here()
    if os.environ.get("RELAY_ANONYMISE", "") not in ("", "0"):
        anonymise_on_arrival(relay, token)
        print("anonymisation: on (live/anonymise.py) for every photo and frame that arrives", flush=True)
    else:
        print("anonymisation: off (RELAY_ANONYMISE=1 turns it on)", flush=True)
    sys.argv = [str(HERE / "relay.py"), *sys.argv[1:], "--token", token]
    relay.main()


if __name__ == "__main__":
    main()
