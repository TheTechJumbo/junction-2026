#!/usr/bin/env python3
"""Offline test: trainings the relay was waiting on when it stopped are registered when it starts again: one already
fetched completely, one that ended on the GPU machine meanwhile (fetched now), one left half fetched (keyframes/ not
renamed: fetched again). remote_train stands in for the GPU machine. No cluster.

    python live/test_resume_training.py
"""
import json
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402

fetched = []


def results(out, keyframes=True):
    out.mkdir(parents=True, exist_ok=True)
    (out / "trained.splat").write_bytes(bytes(32))
    (out / "summary.json").write_text(json.dumps({"psnr_median": 21.5, "keyframes": 3, "trained_gaussians": 1, "median_depth": 0.7}))
    (out / "views.json").write_text("{}")
    if keyframes:
        for k in range(3):
            (out / f"kf{k:03d}.jpg").write_bytes(b"x")


def fake_fetch(name, out, host=None, root=None, log=print):
    fetched.append(name)
    out = Path(out)
    if (out / "keyframes").exists():  # bring_back renames them and removes the folder
        for f in (out / "keyframes").iterdir():
            f.unlink()
        (out / "keyframes").rmdir()
    results(out)
    return {"state": "done"}


refused = {"flaky-s5": 2}  # this one's GPU machine refuses the connection twice first


def fake_finished(name, host=None, root=None):
    if refused.get(name):
        refused[name] -= 1
        raise RuntimeError("ssh -o failed: kex_exchange_identification: Connection reset")
    return True


sys.modules["remote_train"] = types.SimpleNamespace(finished=fake_finished, fetch=fake_fetch, WAIT_S=60)
relay.RESUME_POLL_S = 0.05


def main():
    evidence = Path(tempfile.mkdtemp(prefix="beacon-resume-test-"))
    running = {"state": "running", "runs": 1, "started_at": time.time()}
    (evidence / "recordings.json").write_text(json.dumps({
        "done-here": {"session": "s1", "training": dict(running)},     # fetched completely before the relay stopped
        "ended-there": {"session": "s2", "training": dict(running)},   # ended on the GPU machine while it was down
        "half": {"session": "s3", "training": dict(running)},          # the relay stopped while fetching
        "finished": {"session": "s4", "training": {"state": "done"}},  # nothing to do
        "flaky": {"session": "s5", "training": dict(running)},         # the connection is refused twice, then it works
    }))
    results(evidence / "train-done-here-s1-1")
    results(evidence / "train-half-s3-1", keyframes=False)
    (evidence / "train-half-s3-1" / "keyframes").mkdir()
    relay.make_handler(types.SimpleNamespace(call=lambda *a, **k: ({"ok": True}, b"")), evidence, "k", None, pick=None)
    for _ in range(100):
        try:  # the relay's threads write it while this reads
            rooms = json.loads((evidence / "rooms.json").read_text()) if (evidence / "rooms.json").exists() else {}
        except ValueError:
            rooms = {}
        if all((rooms.get(z) or {}).get("trained") for z in ("done-here", "ended-there", "half", "flaky")):
            break
        time.sleep(0.05)
    for zone, session in (("done-here", "s1"), ("ended-there", "s2"), ("half", "s3"), ("flaky", "s5")):
        trained = rooms.get(zone, {}).get("trained") or {}
        assert trained.get("splat") == f"/evidence/train-{zone}-{session}-1/trained.splat" and trained.get("psnr") == 21.5, (zone, rooms.get(zone))
    assert sorted(fetched) == ["ended-there-s2", "flaky-s5", "half-s3"], fetched  # the complete one is not fetched again
    recs = json.loads((evidence / "recordings.json").read_text())
    assert all(recs[z]["training"]["state"] == "done" for z in ("done-here", "ended-there", "half")), recs
    assert "finished" not in rooms, rooms
    print("ok: trainings left running by a restart are registered (from here, or fetched once they end, through refused connections)")


if __name__ == "__main__":
    main()
